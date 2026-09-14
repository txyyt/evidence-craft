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


def save(path: Path, payload) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"    已写入 {path.name}")


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
        f"{subject}_{datetime.now():%Y%m%d_%H%M%S}"
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
        "style_card": spec.style_card,
        "intent": doc["meta"].get("intent"),
        "plan_mode": (plan or {}).get("mode"),
        "reuse_data": args.reuse_data,
        "model_tier": args.model_tier,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": spec_outline["title"]})

    emit("sections", "[3/6] 分节生成（按 spec 章节顺序：观点/综述/表格说明/风险）...")
    written: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    notes: dict[str, dict[str, Any]] = {}
    risks: dict[str, Any] | None = None
    for sec in spec.sections:
        check_cancel()
        if sec.kind == "views":
            for i, view in enumerate(spec_outline["views"], 1):
                check_cancel()
                s = sections.gen_view(doc, view, spec)
                written.append(s)
                emit("sections", f"  (观点 {i}/{len(spec_outline['views'])}) {s['heading']}",
                     {"slot_id": s["slot_id"], "heading": s["heading"]})
        elif sec.kind == "text":
            plan = (spec_outline.get("section_plans") or {}).get(sec.id) or {}
            t = sections.gen_text_section(doc, sec, plan, spec)
            texts.append(t)
            emit("sections", f"  (综述) {sec.title} {len(t['body'])} 字",
                 {"section_id": sec.id})
        elif sec.kind == "table":
            if doc["collections"].get("periods") and doc["collections"].get("consensus"):
                note = sections.gen_forecast_note(doc, spec)   # 股票模板原路径
            else:
                note = sections.gen_table_note(doc, sec, spec)
            notes[sec.id] = note
            emit("sections", f"  (表格说明) {sec.title}", {"section_id": sec.id})
        elif sec.kind == "risk":
            risks = sections.gen_risks(doc, spec, views=written)
            emit("sections", f"  (风险) {sec.title}", {"section_id": sec.id})
    if spec.section("risk") is not None and risks is None:
        check_cancel()
        risks = sections.gen_risks(doc, spec, views=written)
    if risks is None:
        risks = {"body": "", "cited_fact_ids": []}
    forecast = notes[spec.section("table").id] if spec.section("table") is not None \
        else {"body": "", "cited_fact_ids": []}
    notes_bodies = {tid: n.get("body", "") for tid, n in notes.items()}
    save(run_dir / "sections.json", {"views": written, "texts": texts,
                                     "notes": notes, "risks": risks})

    emit("review", "[5/6] 对账 + 规则校验 + judge 评审（不合格退回重写，至多"
                   f"{int((settings.pipeline or {}).get('revise_rounds', 2))} 轮）...")
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
    max_rounds = int((settings.pipeline or {}).get("revise_rounds", 2))
    round_no = 1
    while judge_report["verdict"] != "pass" and round_no <= max_rounds:
        check_cancel()
        issues = judge_report.get("issues") or []
        emit("review", f"  —— 第 {round_no} 轮修订（{len(issues)} 个问题）——")
        for it in issues:
            print(f"    [{it.get('target')}] {it.get('problem')}")
        written, forecast, risks, spec_outline, texts, notes = revise.apply(
            doc, spec_outline, written, forecast, risks, judge_report, spec,
            texts=texts, notes=notes)
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
    chart_jobs = render_charts(doc, spec, run_dir,
                               lambda m: emit("render", m))

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
