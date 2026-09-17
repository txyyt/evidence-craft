"""CLI 入口。

用法：
  python run_pipeline.py --data-only --type company_review --stock 000803
  python run_pipeline.py --full --type company_review --stock 000803
  python run_pipeline.py --full --type geology_demo_review --project GM-1 --period 2026H1

--department 为 --type 的兼容别名。--spec 可显式覆盖报告结构文件（调试用）。

progress 回调：每个阶段/每节完成时调用 progress(stage, message, data|None)，
data 恒含 llm_calls / llm_seconds（本次运行累计）；server 层 SSE 直接转发。
cancel_event 置位后在阶段边界干净退出（PipelineCancelled）。
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from datalayer.settings import settings


class PipelineCancelled(RuntimeError):
    """用户取消（server 层捕获后标记运行状态）。"""


def execute_section_tasks(tasks: list[dict[str, Any]], concurrency: int,
                          emit: Callable[[str, str, dict | None], None],
                          check_cancel: Callable[[], None]) -> None:
    """F13 分节并发执行器：wave1=观点/综述/表格说明（互相独立，线程池），
    wave2=风险章（引用观点产出，单独跑）。结果存回 tasks[i]["result"]。
    concurrency=1 时退化为串行；完成乱序只影响日志，不影响回填顺序。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    wave1 = [t for t in tasks if t.get("kind") != "risk"]
    wave2 = [t for t in tasks if t.get("kind") == "risk"]

    def _run_wave(batch: list[dict[str, Any]], n_workers: int) -> None:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(t["fn"]): t for t in batch}
            for fut in as_completed(futures):
                check_cancel()
                t = futures[fut]
                t["result"] = fut.result()
                emit("sections", f"  {t['label']} 完成"
                     + (f" {len(t['result'].get('body', ''))} 字"
                        if isinstance(t.get("result"), dict)
                        and t["result"].get("body") else ""),
                     {"task": t["key"]})

    if concurrency <= 1:
        for t in wave1:
            check_cancel()
            t["result"] = t["fn"]()
            emit("sections", f"  {t['label']} 完成", {"task": t["key"]})
    else:
        _run_wave(wave1, concurrency)
    for t in wave2:
        check_cancel()
        t["result"] = t["fn"]()
        emit("sections", f"  {t['label']} 完成", {"task": t["key"]})


def save(path: Path, payload) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"    已写入 {path.name}")


def route_structure_issues(doc: dict[str, Any], spec, texts, notes,
                           issues: list[dict[str, Any]],
                           ) -> tuple[list[dict[str, Any]], list[str]]:
    """E1 组合段（可测）：结构类 issue 先经 handle_structure_issues 确定性
    处理（删重复节/插表/补缺失节），没能确定性处理的原样退回——kind 改写为
    "style"、附转写说明，并入本轮 revise.apply 的重写列表。结构类从此只有
    确定性处理或重写两种出口，不再丢弃。返回 (待重写 issues, 处理摘要)。"""
    from pipeline import revise
    handled, unhandled = revise.handle_structure_issues(
        doc, spec, texts, notes, issues)
    unhandled_ids = {id(it) for it in unhandled}
    for it in unhandled:
        it["kind"] = "style"
        it["instruction"] = ((it.get("instruction") or "")
                             + "\n（结构类问题未能确定性处理，已转写作通道修复）"
                             ).strip()
    to_rewrite = [i for i in issues
                  if i.get("kind") != "structure" or id(i) in unhandled_ids]
    return to_rewrite, handled


def _safe_artifact_name(subject: str) -> str:
    """subject → 磁盘目录名（V4-01，P1）：清洗 Windows 禁止字符与路径分隔符。
    报告展示标题继续用原始 subject（meta.params.project / outline.title 不受影响）；
    这里只负责产物目录落在 artifacts/ 直接子目录。"""
    import re
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", str(subject))
    s = re.sub(r"-{2,}", "-", s).strip(" .-")
    if not s:
        s = "report"
    # 目录名 UTF-8 字节上限（时间戳 16 字符另计，整路径留足余量防 MAX_PATH）
    while len(s.encode("utf-8")) > 160:
        s = s[:-1].rstrip(" .-")
    return s or "report"


def _meta_plan_fingerprint(plan: dict | None):
    """V4-07：meta.json 记录计划内容指纹（容错——指纹失败不阻塞生成）。"""
    try:
        if not plan:
            return None
        from trees.store import plan_fingerprint
        return plan_fingerprint(plan)
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str] | None = None,
         progress: Callable[[str, str, dict | None], None] | None = None,
         cancel_event=None) -> None:
    parser = argparse.ArgumentParser(description="EvidenceCraft 报告生成流水线")
    parser.add_argument("--type", "--department", dest="type_id",
                        default="company_review",
                        help="报告类型 id（config/report_types/<id>/，默认 company_review）")
    parser.add_argument("--stock", default=None, help="运行参数：股票代码")
    parser.add_argument("--project", default=None, help="运行参数：项目编号（地学等场景）")
    parser.add_argument("--period", default=None, help="运行参数：报告期次")
    parser.add_argument("--spec", default=None, help="报告结构 yaml 路径（覆盖报告类型缺省）")
    parser.add_argument("--tree", default=None,
                        help="结构树 id（config/trees/<id>/tree.yaml；树模式：结构从树加载，"
                             "与 --intent/--folder/--plan 组合规划取数）")
    parser.add_argument("--plan", default=None,
                        help="数据采集计划（--tree 模式：树目录 plans/ 下的计划文件名或路径；"
                             "给出时跳过规划直接执行该计划）")
    parser.add_argument("--intent", default=None,
                        help="写作意图一段话（意图规划：自动生成数据采集计划）")
    parser.add_argument("--folder", default=None,
                        help="本地资料文件夹（现场建语料库作为本次检索库）")
    parser.add_argument("--reuse-data", default=None,
                        help="复用某次运行的数据层结果（目录名，跳过规划与取数）"
                             "——只调写作规则/模板时秒级进入写作阶段")
    parser.add_argument("--model-tier", default=None,
                        help="固定本次全部 LLM 调用走该档（settings.model_tiers "
                             "的档名，如 fast/quality；缺省按 tier_roles 分工）")
    parser.add_argument("--allow-qualitative", action="store_true",
                        help="树模式门禁显式放行：--tree 且无任何取数来源时"
                             "（--plan/--intent/--folder 全空）默认拒绝，"
                             "加此旗标才允许生成全定性报告")
    parser.add_argument("--data-only", action="store_true", help="只跑数据层")
    parser.add_argument("--full", action="store_true", help="全流程生成报告")
    args = parser.parse_args(argv)

    _progress = progress or (lambda *_: None)
    from pipeline.llm import reset_stats, set_tier_override, stats
    reset_stats()
    set_tier_override(args.model_tier)   # None 时清掉进程内遗留覆盖（server 同进程并发场景）
    _stage_t0 = datetime.now().timestamp()

    def emit(stage: str, msg: str, data: dict | None = None) -> None:
        nonlocal _stage_t0
        now = datetime.now().timestamp()
        payload = {"llm_calls": stats()["calls"],
                   "llm_seconds": round(stats()["seconds"], 1),
                   "llm_tiers": stats()["tiers"],
                   "stage_seconds": round(now - _stage_t0, 1)}
        _stage_t0 = now
        if data:
            payload.update(data)
        print(msg)
        _progress(stage, msg, payload)

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise PipelineCancelled("用户取消")

    from template_factory.schema import load_spec
    from datalayer import registry

    tree_meta: dict | None = None
    tree_fp: str | None = None
    if args.tree:
        # 树模式：结构从 config/trees/<id>/tree.yaml 加载（含 tree: 元信息块），
        # sources 为合成 dict（无静态绑定，取数靠 planner 计划/--plan/资料夹）
        from trees import store as tree_store
        loaded = tree_store.load_tree(args.tree)    # 不存在/不合规在此报错
        spec, tree_meta, tree_fp = loaded["spec"], loaded["meta"], loaded["fingerprint"]
        sources = tree_store.synthetic_sources(tree_meta, spec)
        spec_file = str(loaded["path"])
        type_id = args.tree
    else:
        type_id = args.type_id
        registry.type_dir(args.type_id)
        if not registry.type_exists(args.type_id):
            raise SystemExit(f"报告类型不存在：{args.type_id}")
        sources = registry.load_sources(args.type_id)
        spec_file = args.spec or str(registry.spec_path(args.type_id))
        spec = load_spec(spec_file)

    # judge 对标范文解析链：模板级 → 报告类型级
    if not spec.judge_reference and sources.get("judge_reference"):
        spec.judge_reference = sources["judge_reference"]

    # stock 仅股票类报告取 settings 缺省；其余以 --project 为主参数
    stock = args.stock or (settings.stock if args.type_id == "company_review" else None) or None
    run_params = {k: v for k, v in
                  {"stock": stock, "project": args.project,
                   "period": args.period}.items() if v is not None}
    if args.tree and not run_params.get("project"):
        run_params["project"] = tree_meta.get("subject") or args.tree

    subject = run_params.get("project") or run_params.get("stock") or args.type_id
    run_dir = settings.resolve(settings.artifacts_dir) / \
        f"{_safe_artifact_name(subject)}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    emit("data", f"[1/6] 数据层：按报告类型「{sources.get('name', args.type_id)}」绑定拉取 {subject} 数据 ...")
    check_cancel()
    plan = None
    if args.reuse_data:
        src = settings.resolve(settings.artifacts_dir) / args.reuse_data
        if not (src / "facts.json").exists():
            raise SystemExit(f"复用目录缺少 facts.json：{src}")
        import shutil
        for name in ("facts.json", "crosscheck_report.json", "plan.json"):
            if (src / name).exists():
                shutil.copy2(src / name, run_dir / name)
        doc = json.loads((run_dir / "facts.json").read_text(encoding="utf-8"))
        crosscheck = json.loads((run_dir / "crosscheck_report.json")
                                .read_text(encoding="utf-8")) \
            if (run_dir / "crosscheck_report.json").exists() else None
        if (run_dir / "plan.json").exists():
            plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
        emit("data", f"  [复用] 数据层结果取自 {args.reuse_data}"
             f"（事实 {len(doc['facts'])} 条，跳过规划与取数）",
             {"reuse": args.reuse_data})
    elif args.tree and args.plan:
        from datalayer.planner import plan_to_bindings
        from trees import store as tree_store
        plan = tree_store.load_plan(args.tree, args.plan)
        if plan.get("needs_folder") and not args.folder:
            raise SystemExit("[门禁] 该计划有缺口裁决为『我提供资料』：请补 --folder 资料文件夹"
                             "（A5：needs_folder 必须消费）")
        if args.folder:
            # A5+F11：缺口裁决「我提供资料」的消费路径——PDF 现场建语料、
            # Excel 生成确定性绑定，计划副本带上两者，rag 查询指向新库
            from datalayer.planner import ensure_corpus, xlsx_bindings
            corpus = ensure_corpus(args.folder)
            if corpus:
                emit("data", f"  [资料] 语料库就绪：{corpus['n_fragments']} 片段"
                             f"（{'新建' if corpus['rebuilt'] else '缓存复用'}）", None)
            fbindings, fmetas = xlsx_bindings(args.folder)
            plan = {**plan, "corpus": corpus,
                    "corpus_switched": bool(corpus),
                    "file_bindings": fbindings, "files": fmetas}
            if fmetas:
                emit("data", f"  [资料] Excel 绑定 {len(fbindings)} 条："
                     + "、".join(m.get("file", "") for m in fmetas[:5]), None)
        save(run_dir / "plan.json", plan)
        emit("data", f"  [计划] 沿用数据计划 {args.plan}"
             f"（rag {len(plan.get('rag') or [])} / web {len(plan.get('web') or [])}"
             f" / db {len(plan.get('db') or [])}）", {"plan_mode": "plan"})
        doc, crosscheck = registry.run_data_layer(
            type_id, run_params, bindings_override=plan_to_bindings(plan, sources),
            sources_override=sources)
        doc["meta"]["intent"] = plan.get("focus") or args.intent
        doc["meta"]["plan_mode"] = plan.get("mode", "plan")
    elif args.intent or args.folder:
        from datalayer.planner import make_plan, plan_to_bindings
        emit("data", f"  意图规划：{args.intent or '（以资料文件夹为主题）'}"
             + (f"｜本地资料 {args.folder}" if args.folder else ""))
        plan = make_plan(spec, sources, args.intent or "", args.folder or None)
        save(run_dir / "plan.json", plan)
        emit("data", f"  采集计划（{plan['mode']}）：rag {len(plan.get('rag') or [])} 条"
             f" / web {len(plan.get('web') or [])} 条"
             f" / db {len(plan.get('db') or [])} 条"
             f" / 沿用表格 {len(plan.get('tables_kept') or [])} 个"
             + (f"｜语料 {plan['corpus']['n_fragments']} 片段（"
                f"{'新建' if plan['corpus']['rebuilt'] else '缓存复用'}）"
                if plan.get("corpus") else ""),
             {"plan_mode": plan["mode"]})
        for w in plan.get("warnings") or []:
            print(f"  [规划告警] {w}")
        doc, crosscheck = registry.run_data_layer(
            type_id, run_params, bindings_override=plan_to_bindings(plan, sources),
            sources_override=sources if args.tree else None)
        doc["meta"]["intent"] = plan.get("focus") or args.intent
        doc["meta"]["plan_mode"] = plan["mode"]
    else:
        if args.tree and not args.allow_qualitative:
            # A4 生成门禁（硬线）：树模式无 plan/intent/folder = 0 事实空谈稿
            raise SystemExit(
                "[门禁] 树模式未提供任何取数来源（--plan/--intent/--folder 全空）："
                "0 事实的空谈稿已被拦截。请先在工作台出数据计划并完成缺口裁决，"
                "或补 --intent/--folder；确需全定性生成请显式加 --allow-qualitative")
        if args.tree:
            print("[门禁] --allow-qualitative 已显式放行：本次报告无数据支撑（全定性），"
                  "请在交付时注明")
        doc, crosscheck = registry.run_data_layer(
            type_id, run_params, sources_override=sources if args.tree else None)
    save(run_dir / "facts.json", doc)
    if crosscheck:
        save(run_dir / "crosscheck_report.json", crosscheck)
    meta = doc["meta"]
    emit("data", f"  {meta['stock']} {meta.get('name', '')}  "
                 f"事实 {len(doc['facts'])} 条  "
                 f"交叉校验 {crosscheck['status'].upper() if crosscheck else 'N/A'}",
         {"facts": len(doc["facts"]),
          "crosscheck": crosscheck["status"] if crosscheck else "none"})
    for w in meta["warnings"]:
        print(f"  [警告] {w}")
    if crosscheck and crosscheck["status"] == "fail":
        raise SystemExit(1)
    if args.data_only:
        print(f"\n完成（仅数据层）。产物目录: {run_dir}")
        return

    from pipeline import judge, outline, reconcile, revise, sections, validate
    from pipeline.rating import rule_rating
    from render import html_report

    emit("outline", "[2/6] 大纲生成（标题 + 观点规划）...")
    check_cancel()
    spec_outline = outline.build_outline(doc, spec)
    save(run_dir / "outline.json", spec_outline)
    emit("outline", f"  标题: {spec_outline['title']}",
         {"title": spec_outline["title"]})
    save(run_dir / "meta.json", {
        "type_id": type_id,
        "type_name": sources.get("name", args.type_id),
        "template_fingerprint": tree_fp if args.tree
        else registry.template_fingerprint(args.type_id),
        "params": run_params,
        "spec": spec_file if (args.spec or args.tree) else "report.yaml",
        "tree_id": args.tree,
        "tree_version": (tree_meta or {}).get("version"),
        "tree_fingerprint": tree_fp,
        # V4-07：数据复用回环——计划文件、计划内容指纹、事实数与数据生成时刻
        "plan_file": args.plan,
        "plan_fingerprint": _meta_plan_fingerprint(plan) if (args.tree and plan) else None,
        "facts_count": len(doc["facts"]),
        "data_generated_at": datetime.now().isoformat(timespec="seconds"),
        "style_card": spec.style_card,
        "intent": doc["meta"].get("intent"),
        "plan_mode": (plan or {}).get("mode"),
        "reuse_data": args.reuse_data,
        "model_tier": args.model_tier,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": spec_outline["title"]})

    emit("sections", "[3/6] 分节生成（按 spec 章节顺序：观点/综述/表格说明/风险"
                     f"，并行 {int((settings.pipeline or {}).get('section_concurrency', 3))}）...")
    written: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    notes: dict[str, dict[str, Any]] = {}
    risks: dict[str, Any] | None = None
    thin_warnings: list[str] = []          # F4：数据稀薄节记录

    # F13：节与节上下文隔离——建任务表后线程池并发（缺省 3，置 1 回退串行）。
    # 风险章引用观点产出，第二波执行；结果按 spec 顺序回填，完成乱序只影响日志。
    concurrency = max(1, int((settings.pipeline or {}).get("section_concurrency",
                                                            3)))
    tasks: list[dict[str, Any]] = []       # {key,label,kind,sec,fn,result}
    for sec in spec.sections:
        if sec.kind == "views":
            for i, view in enumerate(spec_outline["views"], 1):
                tasks.append({"key": f"views.{view['slot_id']}", "kind": "views",
                              "label": f"(观点 {i}/{len(spec_outline['views'])}) {view['heading']}",
                              "sec": sec,
                              "fn": lambda v=view: sections.gen_view(doc, v, spec)})
        elif sec.kind == "text":
            plan_ = (spec_outline.get("section_plans") or {}).get(sec.id) or {}
            # F4 数据稀薄前置：可引用事实 <3 且 brief 要求数据支撑时，
            # 临时注入定性声明（只改本节写作要求，不动树）
            sec, thin = sections.thin_section_if_needed(sec, doc, plan_)
            if thin:
                thin_warnings.append(f"节「{sec.title}」可引用事实不足，已注入定性声明")
                emit("sections", f"  [数据稀薄] {sec.title}：定性论述模式", None)
            tasks.append({"key": f"text.{sec.id}", "kind": "text", "sec": sec,
                          "label": f"(综述) {sec.title}",
                          "plan": plan_,
                          "fn": lambda s2=sec, p2=plan_: sections.gen_text_section(
                              doc, s2, p2, spec)})
        elif sec.kind == "table":
            stock_path = doc["collections"].get("periods") \
                and doc["collections"].get("consensus")
            tasks.append({"key": f"table.{sec.id}", "kind": "table", "sec": sec,
                          "label": f"(表格说明) {sec.title}",
                          "fn": (lambda: sections.gen_forecast_note(doc, spec))
                          if stock_path
                          else (lambda s2=sec: sections.gen_table_note(doc, s2, spec))})
        elif sec.kind == "risk":
            tasks.append({"key": "risk", "kind": "risk", "sec": sec,
                          "label": f"(风险) {sec.title}",
                          "fn": lambda: sections.gen_risks(doc, spec,
                                                           views=written)})
    wave1 = [t for t in tasks if t["kind"] != "risk"]
    wave2 = [t for t in tasks if t["kind"] == "risk"]
    execute_section_tasks(tasks, concurrency, emit, check_cancel)
    # 结果按 spec 顺序回填
    for t in tasks:
        if t["kind"] == "views":
            written.append(t["result"])
        elif t["kind"] == "text":
            texts.append(t["result"])
        elif t["kind"] == "table":
            notes[t["sec"].id] = t["result"]
    if spec.section("risk") is not None and wave2:
        risks = wave2[0]["result"]
    if spec.section("risk") is not None and risks is None:
        check_cancel()
        risks = sections.gen_risks(doc, spec, views=written)
    if risks is None:
        risks = {"body": "", "cited_fact_ids": []}
    forecast = notes[spec.section("table").id] if spec.section("table") is not None \
        else {"body": "", "cited_fact_ids": []}
    notes_bodies = {tid: n.get("body", "") for tid, n in notes.items()}
    # E4：残句重试后仍短的节，警示并入 thin_warnings 落盘
    for t in tasks:
        r = t.get("result")
        if isinstance(r, dict) and r.get("thin"):
            who = r.get("section_id") or r.get("slot_id") \
                or (t.get("sec").title if t.get("sec") is not None else "（节）")
            thin_warnings.append(f"{who}：{r['thin']}")
            emit("sections", f"  [残句] {who}：{r['thin']}", None)
    save(run_dir / "sections.json", {"views": written, "texts": texts,
                                     "notes": notes, "risks": risks})

    emit("review", "[5/6] 对账 + 规则校验 + judge 评审（不合格退回重写，至多"
                   f"{int((settings.pipeline or {}).get('revise_rounds', 3))} 轮）...")
    check_cancel()
    rating = rule_rating(doc)
    report = reconcile.reconcile(doc, spec_outline, written, forecast, risks, spec,
                                 texts=texts, notes=notes)
    validate_report = validate.run(doc, spec_outline, written, forecast, risks,
                                   rating, spec, crosscheck,
                                   texts=texts, notes=notes)
    judge_report = judge.run(doc, spec_outline, written, forecast, risks,
                             report, validate_report, spec, rating,
                             texts=texts, notes=notes)

    def _print_status() -> None:
        print(f"  对账 {report['status'].upper()}（索引 {report['index_size']}）"
              f" | 规则 {validate_report['status'].upper()}"
              f"（{len(validate_report['items'])} 项告警）"
              f" | judge {judge_report['total']} 分 {judge_report['verdict'].upper()}")
        for c in report["checks"]:
            if c["unknown_numbers"] or c["cited_missing"]:
                print(f"  [{c['section']}] 未匹配数字 {c['unknown_numbers']}"
                      f"  缺失引用 {c['cited_missing']}")
        for i in validate_report["items"]:
            print(f"  [规则 {i['status']}] {i['rule']}: {i['detail']}")

    _print_status()
    # F6：修订轮数缺省 2→3（settings.pipeline.revise_rounds 可覆盖）
    max_rounds = int((settings.pipeline or {}).get("revise_rounds", 3))
    revision_history: list[dict[str, Any]] = []   # F1：每轮意见与稿件快照
    structure_notes: list[str] = []               # F3：结构类确定性处理摘要
    round_no = 1
    while judge_report["verdict"] != "pass" and round_no <= max_rounds:
        check_cancel()
        issues = judge_report.get("issues") or []
        emit("review", f"  —— 第 {round_no} 轮修订（{len(issues)} 个问题）——")
        for it in issues:
            print(f"    [{it.get('target')}][{it.get('kind', 'style')}] "
                  f"{it.get('problem')}")
        # F2 字数确定性手术：超限 text 节在喂模型前先做确定性预算
        texts, surgery_notes = revise.enforce_body_limits(
            doc, spec, texts, revision_history=revision_history)
        for n in surgery_notes:
            emit("review", f"  [字数手术] {n}", None)
        # E1：结构类 issue 先确定性处理，处理不了的转写作通道（不再丢弃）
        to_rewrite, struct_handled = route_structure_issues(
            doc, spec, texts, notes, issues)
        for h in struct_handled:
            emit("review", f"  [结构处理] {h}", None)
        structure_notes += struct_handled
        # F1：记录本轮意见，注入下一轮修订提示词
        revision_history.append({
            "round": round_no, "issues": issues,
            "lens": {"texts": {t.get("section_id"): len(t.get("body") or "")
                               for t in texts},
                     "views": {v.get("slot_id"): len(v.get("body") or "")
                               for v in written}}})
        written, forecast, risks, spec_outline, texts, notes = revise.apply(
            doc, spec_outline, written, forecast, risks, judge_report, spec,
            texts=texts, notes=notes, revision_history=revision_history,
            issues_override=to_rewrite)
        report = reconcile.reconcile(doc, spec_outline, written, forecast, risks, spec,
                                     texts=texts, notes=notes)
        validate_report = validate.run(doc, spec_outline, written, forecast,
                                       risks, rating, spec, crosscheck,
                                       texts=texts, notes=notes)
        judge_report = judge.run(doc, spec_outline, written, forecast, risks,
                                 report, validate_report, spec, rating,
                                 texts=texts, notes=notes)
        _print_status()
        save(run_dir / f"revision_round{round_no}.json", {
            "reconcile": report["status"], "validate": validate_report,
            "judge": {"total": judge_report["total"],
                      "verdict": judge_report["verdict"],
                      "issues": judge_report.get("issues")}})
        round_no += 1

    # 修订后的最终稿回写 sections.json（此前只存修订前版本，溯源不便）
    save(run_dir / "sections.json", {"views": written, "texts": texts,
                                     "notes": notes, "risks": risks})
    # F1/F3/F4：修订轨迹与确定性处理留痕（收敛性基线的数据源）
    save(run_dir / "revision_history.json",
         {"rounds": revision_history, "structure_notes": structure_notes,
          "thin_warnings": thin_warnings})

    save(run_dir / "reconcile_report.json", report)
    save(run_dir / "validate_report.json", validate_report)
    save(run_dir / "judge_report.json", judge_report)
    emit("review", f"  评审结束：judge {judge_report['total']} 分 "
                   f"{judge_report['verdict'].upper()}",
         {"judge_total": judge_report["total"],
          "judge_verdict": judge_report["verdict"],
          "reconcile": report["status"]})

    emit("render", "[6/6] 渲染 final.html ...")
    check_cancel()
    chart = None
    if (sources.get("features") or {}).get("kline_chart"):
        from render.kline_chart import make_chart
        chart = make_chart("1.000001", run_dir / "index_kline.png")
        print(f"    头图已生成 {chart.name}")

    from render.charts import render_charts
    chart_jobs, chart_failures = render_charts(doc, spec, run_dir,
                                               lambda m: emit("render", m))
    # F2：图表渲染失败清单落 meta.json（meta 在大纲阶段已落盘，读入→补字段
    # →写回；树/经典两模式都写）——供详情页治理体检"图表状态"展示
    if chart_failures:
        emit("render", f"  [图表] {len(chart_failures)} 张渲染失败"
                       "（原因见报告详情-治理体检）", None)
        try:
            meta_path = run_dir / "meta.json"
            meta_now = json.loads(meta_path.read_text(encoding="utf-8"))
            meta_now["chart_failures"] = chart_failures
            meta_path.write_text(
                json.dumps(meta_now, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except (OSError, ValueError) as e:
            print(f"    [警告] chart_failures 写入 meta.json 失败：{e}")

    html = html_report.render(
        doc, spec_outline, written, forecast, risks, spec,
        rating=rating, kline_png=chart.name if chart else None,
        texts=texts, notes=notes if notes else None, charts=chart_jobs)
    html_path = run_dir / "final.html"
    html_path.write_text(html, encoding="utf-8")

    # docx 终稿（规范简洁版）；渲染失败不影响流水线结果
    try:
        from render.docx_report import render_docx
        render_docx(doc, spec_outline, written, forecast, risks, spec,
                    rating=rating, kline_png=str(chart) if chart else None,
                    out_path=run_dir / "final.docx",
                    texts=texts, notes=notes_bodies if notes else None,
                    charts=chart_jobs)
        print("    docx 已生成 final.docx")
    except Exception as e:  # noqa: BLE001
        print(f"    [警告] docx 渲染失败：{e}")

    tiers = stats()["tiers"]
    tier_line = "、".join(
        f"{k}档 {v['calls']} 次/{round(v['seconds'], 1)}s（{v['model']}）"
        for k, v in tiers.items())
    print(f"""
完成。产物目录: {run_dir}
  报告标题: {spec_outline['title']}
  对账: {report['status'].upper()}    预览: {html_path}
  LLM 分档调用: {tier_line or f"默认档 {stats()['calls']} 次"}
""")
    emit("render", f"完成。产物目录: {run_dir}",
         {"run_dir": str(run_dir), "llm_tiers": tiers})


if __name__ == "__main__":
    main()
