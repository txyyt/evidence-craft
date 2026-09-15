"""反馈回路（M3）：一段总意见 → 路由 agent → 三类分发执行 → 治理重跑 → 重渲染。

意见分三类（路由 agent 分类，走完全不同的路）：
- style      文风/表述 → 伪 judge_report 喂 revise.apply（复用既有修订模板）
- data       数据补充/纠错 → 追加 web/rag 查询重取数（同 id 覆盖、其余续号）
             → 命中节带"旧引用+新事实"重写
- structure  结构调整 → 树操作（tree_agent._apply_ops）+ 脏区追踪：
             只重生成受影响节，未点名节逐字保留

每轮：reconcile + validate 自动全量重跑（治理不旁路）；轮次快照 rounds/<n>/
（sections/outline/facts 副本 + diff.json 按节新旧对照）；feedback_ledger.jsonl 台账；
rollback 恢复轮次快照。深度评审 = judge.run 全量按需触发（写 judge_report.json）。

边界：树的结构回滚用树自身的版本链；反馈回滚只恢复报告稿与取数结果。
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from datalayer import registry
from datalayer.planner import plan_to_bindings
from pipeline import reconcile, revise, sections, tree_agent, validate
from trees import store as tree_store


# ---------- 上下文装配 ----------

def _load_state(run_dir: Path) -> dict[str, Any]:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    doc = json.loads((run_dir / "facts.json").read_text(encoding="utf-8"))
    outline = json.loads((run_dir / "outline.json").read_text(encoding="utf-8"))
    sec = json.loads((run_dir / "sections.json").read_text(encoding="utf-8"))
    tree_id = meta.get("tree_id")
    if not tree_id or not tree_store.exists(tree_id):
        hint = ""
        if tree_id:
            # 树可能已改名（如 id 中文化迁移）：给出新树线索
            from datalayer.settings import settings
            root = settings.resolve("config/trees")
            siblings = [d.name for d in root.iterdir() if d.is_dir()] \
                if root.exists() else []
            hint = f"（现存树：{'、'.join(siblings[:8])}——树若已改名，请从新树继续）"
        raise ValueError(f"找不到该报告对应的结构树 tree_id={tree_id}，"
                         f"反馈回路仅支持树模式运行{hint}")
    loaded = tree_store.load_tree(tree_id)
    return {"meta": meta, "doc": doc, "outline": outline,
            "views": sec.get("views") or [], "texts": sec.get("texts") or [],
            "notes": sec.get("notes") or {}, "risks": sec.get("risks") or {},
            "spec": loaded["spec"], "tree_id": tree_id,
            "sources": tree_store.synthetic_sources(loaded["meta"], loaded["spec"])}


def _anchors(st: dict[str, Any]) -> str:
    """带锚点的全文清单（路由 agent 的定位依据）。"""
    spec = st["spec"]
    lines = [f"- title（标题）：{st['outline'].get('title', '')}"]
    views_sec = spec.section("views")
    if views_sec is not None:
        for v in st["views"]:
            lines.append(f"- {views_sec.id}.{v['slot_id']}（观点 {v.get('heading', '')}）"
                         f"：{v.get('body', '')[:60]}…")
    for t in st["texts"]:
        sec = spec.section_by_id(t.get("section_id") or "")
        lines.append(f"- {t['section_id']}（{sec.title if sec else ''}）"
                     f"：{t.get('body', '')[:60]}…")
    for sid, n in st["notes"].items():
        sec = spec.section_by_id(sid)
        lines.append(f"- {sid}（表格说明 {sec.title if sec else ''}）"
                     f"：{(n.get('body') if isinstance(n, dict) else n) or ''[:60]}…")
    risk_sec = spec.section("risk")
    if risk_sec is not None and st["risks"].get("body"):
        lines.append(f"- {risk_sec.id}（{risk_sec.title}）：{st['risks']['body'][:60]}…")
    return "\n".join(lines)


def _ledger_summary(run_dir: Path) -> str:
    entries = read_ledger(run_dir)
    if not entries:
        return "（无）"
    return "\n".join(f"- 第{e['round']}轮：{e['text'][:60]} → {e.get('applied_summary', '')[:80]}"
                     for e in entries[-3:])


# ---------- 路由 ----------

_ROUTER_SYSTEM = """你是报告反馈路由器。用户对已生成报告提了一段总意见，你把它拆解成
结构化操作 ops。每个 op：
{"target": "锚点", "kind": "style|data|structure", "action": "rewrite|expand|trim|
 delete|add|move|refetch", "instruction": "给执行者的具体修改要求"}

target 取值（只能用【可用锚点】里列出的）：
- 文风/表述/增删内容 → 该节锚点，kind="style"
- 数据补充/数据纠错/要新数据 → 该节锚点，kind="data"，并给 web 查询词列表
  "web_queries": ["查询词", ...]（2~4 个，具体含主题与年份）
- 加节/删节/节改名/节顺序/节内容方向大改 → kind="structure"，action 直接用树操作动词：
  add_section（给 section 节对象与 after 锚点节id或 null）/ remove_section（section_id）/
  update_section（section_id + fields:{title|heading|style|data_needs}）/
  move_section（section_id + direction:"up"|"down"）；structure op 的 target 填相关节锚点或 "global"

【可用锚点】
{anchors}

【历史反馈】（避免重复执行或与既往意见打架）
{ledger}

无法定位的意见并入最接近的锚点，并在 ambiguities 里说明。只输出 JSON：
{"ops": [...], "ambiguities": ["...", ...]}"""


def parse(run_dir: Path, text: str) -> dict[str, Any]:
    """路由 agent：意见原文 → 结构化 ops（同步单次 LLM，不执行）。"""
    st = _load_state(run_dir)
    from pipeline.llm import chat_json, tier_for
    system = (_ROUTER_SYSTEM
              .replace("{anchors}", _anchors(st))
              .replace("{ledger}", _ledger_summary(run_dir)))
    out = chat_json(system, f"用户意见：{text}",
                    schema_hint="只输出一个合法 JSON 对象：ops/ambiguities。",
                    tier=tier_for("extract"))
    ops = [o for o in (out.get("ops") or []) if isinstance(o, dict)
           and o.get("kind") in ("style", "data", "structure")]
    return {"ops": ops,
            "ambiguities": [str(a)[:120] for a in (out.get("ambiguities") or [])[:5]]}


# ---------- 执行 ----------

def _forecast_of(st: dict[str, Any]) -> dict[str, Any]:
    """首个 table 节的说明即 forecast（run_pipeline 同口径）；无则空。"""
    tsec = st["spec"].section("table")
    if tsec is not None and tsec.id in st["notes"]:
        n = st["notes"][tsec.id]
        return n if isinstance(n, dict) else {"body": n or "", "cited_fact_ids": []}
    return {"body": "", "cited_fact_ids": []}


def _merge_facts(old: list[dict[str, Any]], new: list[dict[str, Any]]
                 ) -> list[dict[str, Any]]:
    """同 id 覆盖（口径更新）、新 id 续号追加。"""
    by_id = {f["id"]: f for f in old}
    merged = list(old)
    for f in new:
        if f["id"] in by_id:
            merged[merged.index(by_id[f["id"]])] = f
        else:
            merged.append(f)
    return merged


def _run_data_ops(st: dict[str, Any], ops: list[dict[str, Any]],
                  run_dir: Path, progress, cancel_event) -> list[str]:
    """data 类：追加查询重取数 → 合并事实 → 命中节带新事实重写。"""
    applied = []
    web_queries, rag_queries = [], []
    for op in ops:
        for q in (op.get("web_queries") or [])[:3]:
            q = str(q).strip()
            if q:
                web_queries.append(q)
        for q in (op.get("rag_queries") or [])[:2]:
            q = str(q).strip()
            if q:
                rag_queries.append(q)
    new_facts: list[dict[str, Any]] = []
    if web_queries or rag_queries:
        progress("feedback", f"  数据类意见：追加取数（web {len(web_queries)}"
                             f" / rag {len(rag_queries)}）...", None)
        bindings = []
        for i, q in enumerate(web_queries):
            bindings.append({"need": f"web_fb{i}", "adapter": "web_search",
                             "params": {"query": q, "top_k": 6, "fetch_pages": 2,
                                        "cache_ttl_h": 1}})
        corpus_rel = (json.loads((run_dir / "plan.json").read_text(encoding="utf-8")
                                 ).get("corpus") or {}).get("fragments_rel") \
            if (run_dir / "plan.json").exists() else None
        for i, q in enumerate(rag_queries):
            params: dict[str, Any] = {"query": q, "top_k": 5}
            if corpus_rel:
                params["mock_fragments"] = corpus_rel
            if params.get("mock_fragments") or corpus_rel is None:
                bindings.append({"need": f"rag_fb{i}", "adapter": "rag_client",
                                 "params": params})
        try:
            result_doc, _ = registry.run_data_layer(
                st["tree_id"], st["meta"].get("params") or {},
                bindings_override=bindings, sources_override=st["sources"])
            new_facts = result_doc["facts"]
        except Exception as e:  # noqa: BLE001 —— 取数失败不阻塞其他意见
            applied.append(f"取数失败（{e}），相关意见按原事实处理")
    if new_facts:
        st["doc"]["facts"] = _merge_facts(st["doc"]["facts"], new_facts)
        applied.append(f"新取事实 {len(new_facts)} 条并入")

    for op in ops:
        target = str(op.get("target") or "")
        sid = target[4:] if target.startswith("sec:") else target
        t = next((t for t in st["texts"] if t.get("section_id") == sid), None)
        if t is None:
            continue
        old_cited = t.get("cited_fact_ids") or []
        fresh_ids = [f["id"] for f in new_facts][:10]
        instruction = op.get("instruction") or ""
        if not new_facts:
            # F4：取不到新事实——防"堆参数凑数"，注入定性声明
            instruction += ("\n（未取到新数据）本节数据稀薄，以定性论述为主，"
                            "禁止堆砌物性参数凑字数，不得新增任何数字。")
        from pipeline.sections import _fact_block
        facts_text, _ = _fact_block(st["doc"], old_cited + fresh_ids)
        out = revise._revise_issue(
            st["spec"], st["spec"].fewshot_for(st["spec"].section_by_id(sid) or st["spec"].sections[0]),
            {"problem": "数据更新", "instruction": op.get("instruction") or ""},
            revise.TEXT_REVISE_TMPL, doc=st["doc"], body=t["body"], facts=facts_text)
        out["section_id"] = sid
        out["cited_fact_ids"] = sections._citations_resolvable(
            st["doc"], out.get("cited_fact_ids") or [])
        ti = st["texts"].index(t)
        st["texts"][ti] = out
        applied.append(f"节「{sid}」已带新数据重写")
    return applied


def _run_structure_ops(st: dict[str, Any], ops: list[dict[str, Any]],
                       run_dir: Path, progress, cancel_event) -> list[str]:
    """structure 类：改树（动词与树操作一致，直接透传）→ 脏区追踪重生成。"""
    loaded = tree_store.load_spec_dict(st["tree_id"])
    spec_dict, meta = loaded["spec_dict"], dict(loaded["meta"])
    norm_ops = []
    for o in ops:
        o = dict(o)
        if o.get("action") == "add_section":
            if o.get("section"):
                # 路由产的节对象可能字段形态不合规（如 data_needs 是字符串）：先归一化
                cleaned = tree_agent._clean_section(o["section"])
                if cleaned is None:
                    continue
                o["section"] = cleaned
            else:
                # 兜底：路由只给了意见没给节对象——用 instruction 构造最小新节
                title = re.sub(r"[，。；！？（）()「」]", " ",
                               (o.get("instruction") or "").strip())[:20].strip() \
                    or "新增节"
                o["section"] = {"id": title, "title": title, "kind": "text",
                                "style": (o.get("instruction") or "")[:200],
                                "data_needs": []}
        norm_ops.append(o)
    applied, meta = tree_agent._apply_ops(spec_dict, meta, norm_ops)
    result = tree_store.save_tree(st["tree_id"], spec_dict, meta,
                                  actor="agent", summary="反馈结构调整",
                                  op={"action": "feedback_structure"})
    st["spec"] = tree_store.load_tree(st["tree_id"])["spec"]
    progress("feedback", f"  树已更新到 v{result['meta']['version']}，"
                         f"脏区重生成 {len(ops)} 处 ...", None)

    # 脏区追踪：新增/更新的节重生成；删除的节从稿件剔除；纯移动不重写
    kept = {s.id for s in st["spec"].sections}
    st["texts"] = [t for t in st["texts"] if t.get("section_id") in kept]
    for nid in [k for k in st["notes"] if k not in kept]:
        del st["notes"][nid]
    for op in ops:
        act = op.get("action")
        if act not in ("add_section", "update_section"):
            continue
        sec_obj = op.get("section") or {}
        sid = op.get("section_id") or sec_obj.get("id")
        sec = st["spec"].section_by_id(sid or "")
        if sec is None and sec_obj.get("title"):
            # 路由给中文 id 时 _apply_ops 会改写 id——按标题兜底定位
            sec = next((s for s in st["spec"].sections
                        if s.title == str(sec_obj["title"])), None)
        if sec is None or sec.kind != "text":
            continue
        old = next((t for t in st["texts"] if t.get("section_id") == sid), None)
        plan = {"guidance": op.get("instruction") or sec.style or "",
                "cited_fact_ids": (old or {}).get("cited_fact_ids") or []}
        t = sections.gen_text_section(st["doc"], sec, plan, st["spec"])
        if old is not None:
            st["texts"][st["texts"].index(old)] = t
        else:
            st["texts"].append(t)
        a2 = f"节「{sec.title}」已重生成"
        if a2 not in applied:
            applied.append(a2)
    order = {s.id: i for i, s in enumerate(st["spec"].sections)}
    st["texts"].sort(key=lambda t: order.get(t.get("section_id"), 999))
    return applied


def _diff_snapshot(old_state: dict[str, Any], st: dict[str, Any]) -> dict[str, Any]:
    """按节产出新旧正文对照（前端 diff 视图数据）。"""
    def bodies(texts):
        return {t.get("section_id"): t.get("body", "") for t in texts}

    old_bodies = bodies(old_state["texts"])
    new_bodies = bodies(st["texts"])
    old_views = {v.get("slot_id"): v.get("body", "") for v in old_state["views"]}
    new_views = {v.get("slot_id"): v.get("body", "") for v in st["views"]}
    diff = {"sections": {}, "views": {}, "title": {
        "old": old_state["outline"].get("title", ""),
        "new": st["outline"].get("title", "")}}
    for sid in set(old_bodies) | set(new_bodies):
        if old_bodies.get(sid) != new_bodies.get(sid):
            diff["sections"][sid] = {
                "old": old_bodies.get(sid, ""),
                "new": new_bodies.get(sid, ""),
                "changed": old_bodies.get(sid, "") != new_bodies.get(sid, "")}
    for slot in set(old_views) | set(new_views):
        if old_views.get(slot) != new_views.get(slot):
            diff["views"][slot] = {"old": old_views.get(slot, ""),
                                   "new": new_views.get(slot, "")}
    return diff


def _rerun_governance_and_render(st: dict[str, Any], run_dir: Path,
                                 progress, cancel_event) -> tuple[dict, dict]:
    """对账 + 规则校验全量重跑 + 图表/HTML/DOCX 重渲染（治理不旁路）。"""
    from pipeline.rating import rule_rating
    from render import html_report
    from render.charts import render_charts
    forecast = _forecast_of(st)
    progress("feedback", "  对账 + 规则校验重跑 ...", None)
    rating = rule_rating(st["doc"])
    report = reconcile.reconcile(st["doc"], st["outline"], st["views"], forecast,
                                 st["risks"], st["spec"], texts=st["texts"],
                                 notes=st["notes"])
    validate_report = validate.run(st["doc"], st["outline"], st["views"], forecast,
                                   st["risks"], rating, st["spec"], None,
                                   texts=st["texts"], notes=st["notes"])
    progress("feedback", "  重渲染图表与成品 ...", None)
    import json as _json
    chart_jobs, chart_failures = render_charts(
        st["doc"], st["spec"], run_dir, lambda m: progress("feedback", m, None))
    # F2：反馈轮重渲染后同步刷新 meta.json 的图表失败清单（数据补齐→失败消失）
    try:
        meta_path = run_dir / "meta.json"
        meta_now = _json.loads(meta_path.read_text(encoding="utf-8"))
        if chart_failures:
            meta_now["chart_failures"] = chart_failures
        else:
            meta_now.pop("chart_failures", None)
        meta_path.write_text(
            _json.dumps(meta_now, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except (OSError, ValueError):
        pass
    html = html_report.render(st["doc"], st["outline"], st["views"], forecast,
                              st["risks"], st["spec"], rating=rating,
                              texts=st["texts"], notes=st["notes"], charts=chart_jobs)
    (run_dir / "final.html").write_text(html, encoding="utf-8")
    try:
        from render.docx_report import render_docx
        notes_bodies = {tid: (n.get("body") if isinstance(n, dict) else n) or ""
                        for tid, n in st["notes"].items()}
        render_docx(st["doc"], st["outline"], st["views"], forecast,
                    st["risks"], st["spec"], rating=rating,
                    out_path=run_dir / "final.docx",
                    texts=st["texts"], notes=notes_bodies if st["notes"] else None,
                    charts=chart_jobs)
    except Exception as e:  # noqa: BLE001
        progress("feedback", f"  [警告] docx 渲染失败：{e}", None)
    return report, validate_report


def _next_round(run_dir: Path) -> int:
    run_dir = Path(run_dir)
    rounds = run_dir / "rounds"
    n_dirs = len([d for d in rounds.iterdir() if d.is_dir()]) \
        if rounds.exists() else 0
    led_max = max((e.get("round") or 0) for e in read_ledger(run_dir)) \
        if read_ledger(run_dir) else 0
    return max(n_dirs, led_max) + 1


def apply(run_dir: Path, ops: list[dict[str, Any]], text: str,
          progress=None, cancel_event=None) -> dict[str, Any]:
    """执行一轮反馈：三类分发 → 治理重跑 → 快照/台账。返回轮次摘要。"""
    progress = progress or (lambda *a: None)
    run_dir = Path(run_dir)
    st = _load_state(run_dir)
    old_state = {"texts": [dict(t) for t in st["texts"]],
                 "views": [dict(v) for v in st["views"]],
                 "outline": dict(st["outline"])}
    round_no = _next_round(run_dir)
    # 轮前快照（回滚 = 恢复本轮 prev_*）
    rd = run_dir / "rounds" / str(round_no)
    rd.mkdir(parents=True, exist_ok=True)
    for name in ("sections.json", "facts.json", "outline.json"):
        if (run_dir / name).exists():
            shutil.copy2(run_dir / name, rd / f"prev_{name}")

    applied: list[str] = []
    style_ops = [o for o in ops if o.get("kind") == "style"]
    data_ops = [o for o in ops if o.get("kind") == "data"]
    struct_ops = [o for o in ops if o.get("kind") == "structure"]
    tree_lint_summary: dict[str, Any] | None = None

    if style_ops:
        progress("feedback", f"  文风类 {len(style_ops)} 条 → 定向改写 ...", None)
        issues = [{"target": o.get("target", "global"),
                   "problem": "用户反馈",
                   "instruction": o.get("instruction") or ""} for o in style_ops]
        forecast = _forecast_of(st)
        (st["views"], forecast, st["risks"], st["outline"],
         st["texts"], st["notes"]) = revise.apply(
            st["doc"], st["outline"], st["views"], forecast, st["risks"],
            {"issues": issues}, st["spec"], texts=st["texts"], notes=st["notes"])
        tsec = st["spec"].section("table")   # revise 可能改写 forecast：写回 notes
        if tsec is not None and tsec.id in st["notes"]:
            st["notes"][tsec.id] = forecast
        applied.append(f"文风改写 {len(style_ops)} 处")
    if data_ops:
        applied += _run_data_ops(st, data_ops, run_dir, progress, cancel_event)
    if struct_ops:
        applied += _run_structure_ops(st, struct_ops, run_dir, progress, cancel_event)
        # F12：结构操作改了树——执行后附带树健康摘要（前端立即可见）
        try:
            from trees.lint import lint as tree_lint_fn
            loaded = tree_store.load_spec_dict(st["tree_id"])
            lr = tree_lint_fn(loaded["spec_dict"])
            tree_lint_summary = {"errors": len(lr["errors"]),
                                 "warnings": len(lr["warnings"]),
                                 "items": (lr["errors"] + lr["warnings"])[:5]}
            applied.append(f"树健康：{tree_lint_summary['errors']} 错误 / "
                           f"{tree_lint_summary['warnings']} 提醒")
        except Exception as e:  # noqa: BLE001 —— lint 摘要失败不阻塞
            applied.append(f"树健康摘要失败：{e}")
    if not applied:
        return {"round": round_no, "applied": [], "summary": "没有可执行的意见"}

    report, validate_report = _rerun_governance_and_render(
        st, run_dir, progress, cancel_event)

    # 稿件落盘 + 轮次快照
    (run_dir / "sections.json").write_text(json.dumps(
        {"views": st["views"], "texts": st["texts"], "notes": st["notes"],
         "risks": st["risks"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "facts.json").write_text(json.dumps(
        st["doc"], ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "outline.json").write_text(json.dumps(
        st["outline"], ensure_ascii=False, indent=2), encoding="utf-8")
    rd = run_dir / "rounds" / str(round_no)
    rd.mkdir(parents=True, exist_ok=True)
    diff = _diff_snapshot(old_state, st)
    (rd / "diff.json").write_text(json.dumps(diff, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    (rd / "ops.json").write_text(json.dumps(ops, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    (rd / "reconcile.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    (rd / "validate.json").write_text(json.dumps(validate_report, ensure_ascii=False,
                                                 indent=2), encoding="utf-8")
    entry = {"round": round_no, "ts": datetime.now().isoformat(timespec="seconds"),
             "text": text, "ops": ops, "applied": applied,
             "applied_summary": "；".join(applied),
             "reconcile": report.get("status"), "validate": validate_report.get("status"),
             "rolled_back": False}
    if tree_lint_summary is not None:
        entry["tree_lint"] = tree_lint_summary    # F12
    with open(run_dir / "feedback_ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    progress("feedback", f"  第 {round_no} 轮完成：{'；'.join(applied)}"
                         f"｜对账 {report.get('status', '').upper()}", None)
    return entry


# ---------- 轮次 / 回滚 / 深度评审 ----------

def rounds(run_dir: Path) -> list[dict[str, Any]]:
    """轮次列表：rounds/<n>/ 目录 ∪ 台账轮号（回滚条目无目录也要可见）。"""
    run_dir = Path(run_dir)
    rd = run_dir / "rounds"
    out: dict[int, dict[str, Any]] = {}
    if rd.exists():
        for d in rd.iterdir():
            if d.is_dir() and d.name.isdigit():
                out[int(d.name)] = {"round": int(d.name),
                                    "has_diff": (d / "diff.json").exists()}
    for e in read_ledger(run_dir):
        n = e.get("round")
        if isinstance(n, int) and n not in out:
            out[n] = {"round": n, "has_diff": False}
    for entry in out.values():
        match = [e for e in read_ledger(run_dir)
                 if e.get("round") == entry["round"]]
        if match:
            entry.update({"text": match[0].get("text"),
                          "applied": match[0].get("applied"),
                          "rolled_back": match[0].get("rolled_back")})
            # C3：当轮对账/校验状态上界面
            entry["reconcile"] = match[0].get("reconcile")
            entry["validate"] = match[0].get("validate")
            if match[0].get("tree_lint"):
                entry["tree_lint"] = match[0]["tree_lint"]   # F12
    return [out[k] for k in sorted(out)]


def read_ledger(run_dir: Path) -> list[dict[str, Any]]:
    p = Path(run_dir) / "feedback_ledger.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def rollback(run_dir: Path, round_no: int, progress=None) -> dict[str, Any]:
    """撤销第 round_no 轮：恢复该轮 prev_* 快照并重渲染（树改动用树版本链回）。"""
    progress = progress or (lambda *a: None)
    run_dir = Path(run_dir)
    src = run_dir / "rounds" / str(int(round_no))
    if int(round_no) < 1 or not (src / "prev_sections.json").exists():
        raise ValueError(f"第 {round_no} 轮没有轮前快照可回滚")
    for name in ("sections.json", "facts.json", "outline.json"):
        if (src / f"prev_{name}").exists():
            shutil.copy2(src / f"prev_{name}", run_dir / name)
    st = _load_state(run_dir)
    report, validate_report = _rerun_governance_and_render(st, run_dir, progress, None)
    entry = {"round": _next_round(run_dir),
             "ts": datetime.now().isoformat(timespec="seconds"),
             "text": f"[回滚] 撤销第 {round_no} 轮", "ops": [],
             "applied": [f"已回滚第 {round_no} 轮并重渲染"],
             "applied_summary": f"回滚第 {round_no} 轮",
             "reconcile": report.get("status"), "validate": validate_report.get("status"),
             "rolled_back": True, "rollback_of": int(round_no)}
    with open(run_dir / "feedback_ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def judge_deep(run_dir: Path, progress=None) -> dict[str, Any]:
    """按需深度评审（F8）：线程并行三次 judge.run，各维分数取中位数、
    总分按中位分数重算，附三次原始分与极差（>4 标注"评分不稳定"）。"""
    progress = progress or (lambda *a: None)
    run_dir = Path(run_dir)
    st = _load_state(run_dir)
    from concurrent.futures import ThreadPoolExecutor
    from pipeline import judge
    from pipeline.rating import rule_rating
    progress("feedback", "深度评审（judge 三评取中位）...", None)
    rating = rule_rating(st["doc"])
    forecast = (st["notes"].get(next(iter(st["notes"]), ""))
                or {"body": "", "cited_fact_ids": []}) if st["notes"] \
        else {"body": "", "cited_fact_ids": []}
    report = reconcile.reconcile(st["doc"], st["outline"], st["views"], forecast,
                                 st["risks"], st["spec"], texts=st["texts"],
                                 notes=st["notes"])
    validate_report = validate.run(st["doc"], st["outline"], st["views"], forecast,
                                   st["risks"], rating, st["spec"], None,
                                   texts=st["texts"], notes=st["notes"])

    def _one(_i: int) -> dict[str, Any]:
        return judge.run(st["doc"], st["outline"], st["views"], forecast,
                         st["risks"], report, validate_report, st["spec"],
                         rating, texts=st["texts"], notes=st["notes"])

    with ThreadPoolExecutor(max_workers=3) as ex:
        runs = list(ex.map(_one, range(3)))
    merged = merge_median_judges(runs)
    (run_dir / "judge_report.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    progress("feedback", f"深度评审完成：中位总分 {merged['total']}，"
                         f"三次原始分 {[r.get('total') for r in runs]}", None)
    return merged


_DIMS = ("structure", "professionalism", "data_support", "compliance",
         "readability")


def merge_median_judges(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """F8 中位合并：各维取三次评审的中位数、总分按中位分重算；附三次原始分
    与极差（>4 标"评分不稳定"）。verdict 按中位总分 + 单维>4 判定。"""
    from datalayer.settings import settings as _settings

    def _score_of(r: dict, d: str) -> int | None:
        v = (r.get("scores") or {}).get(d)
        return v.get("score") if isinstance(v, dict) else None

    median_scores: dict[str, dict[str, Any]] = {}
    spread: dict[str, int] = {}
    unstable: list[str] = []
    for d in _DIMS:
        vals = [s for s in (_score_of(r, d) for r in runs) if s is not None]
        if not vals:
            continue
        vals_sorted = sorted(vals)
        med = vals_sorted[len(vals_sorted) // 2]
        spread[d] = max(vals) - min(vals)
        comment = next((c.get("comment") for c in
                        (r.get("scores", {}).get(d) or {} for r in runs)
                        if isinstance(c, dict) and c.get("score") == med
                        and c.get("comment")), "")
        median_scores[d] = {"score": med, "comment": comment}
        if spread[d] > 4:
            unstable.append(f"{d}（极差 {spread[d]}）")
    total = sum(v["score"] for v in median_scores.values())
    min_score = min((v["score"] for v in median_scores.values()), default=0)
    threshold = int((_settings.pipeline or {}).get("judge_threshold", 36))
    verdict = "pass" if (total >= threshold and min_score > 4) else "fail"
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in runs:
        for it in r.get("issues") or []:
            key = str(it.get("target")) + "|" + str(it.get("problem"))[:40]
            if key not in seen:
                seen.add(key)
                issues.append(it)
    merged = {"scores": median_scores, "total": total, "verdict": verdict,
              "threshold": threshold, "issues": issues if verdict == "fail" else [],
              "method": "median_of_3",
              "runs": [{k: r.get(k) for k in ("total", "verdict")}
                       for r in runs],
              "raw_runs": [{"scores": {d: _score_of(r, d) for d in _DIMS}}
                           for r in runs],
              "spread": spread,
              "unstable": unstable}
    if unstable:
        merged["unstable_note"] = ("以下维度评分不稳定（三次极差>4），"
                                   "结论仅供参考：" + "、".join(unstable))
    return merged
