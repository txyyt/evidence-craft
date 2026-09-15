"""树对话 agent（M2）：需求 → 结构树（generate），自然语言 → 树操作（edit）。

设计边界：
- agent 只产"结构"，不产事实——data_needs 是语义需求声明，取数仍由 planner
  规划、adapters 抽取并对账（治理不旁路）
- generate 的树必须过 SpecV2 校验才能落盘；需求太糊先反问（questions），
  用户补充后再生成——对话状态由前端携带（服务端无会话）
- edit 每轮读当前树（D5：对话改与手动改共用版本链），输出受限操作集并自动应用
- LLM 失败/输出不合规 → 抛错给上层，树保持不变（树永远可读）
"""

import json
import re
from typing import Any

from pipeline.llm import chat_json, tier_for

Kind = ("text", "views", "table", "risk", "figures")

_GENERATE_SYSTEM = """你是报告结构设计师。用户给出报告需求（可能分多轮补充对话），
你产出报告结构树。规则：
1. 用户点名的章节/表格/图必须照办并标 origin="user"；你补充的章节标 origin="agent"
   （惯用构件按形态补：论文体可补摘要/关键词，研报体可补核心观点/风险提示，
   简报体可补结论与建议）。
2. 每节必须有 style（brief：写什么、写多长、什么口径，含字数区间）与
   data_needs（要哪类数据，2~4 个语义短语）。用户没细说的由你补全。
3. 全文 4~10 节；节 id 默认就用节标题本身（中文短名，如"市场供需"），仅当标题
   含斜杠等路径字符时才清洗；heading 用中文式编号（"1　市场供需"，
   摘要/关键词类不编号）。
4. 图表只用这两类数据源：图 source 取 "facts:rag" 或 "facts:web"（type 用
   bar/line/pie）；表格放顶层 tables，renderer 固定 "facts_rows"，
   source_prefix 取 "rag" 或 "web"。用户点名"表"必须给三件套：一个 kind=table
   的节 + tables[] 里的模板 + 该节 table 字段引用模板 id——三者缺一不可，
   不允许存在没有任何节引用的表格模板。
5. 视角节（views）须带 view_slots（每槽 id/brief/data_needs）与 n_views。
6. 需求信息足够时直接给树；只有关键信息缺失（如报告主题完全不明）才反问，
   至多 3 问。文风卡按需求形态选一个：journal_paper / industry_research / exec_brief。
7. 按需求形态给出基础禁用词 forbidden_words（2~6 个，如口语语气词、夸张词
   "非常/极其/绝对/众所周知"，学术体另加"我觉得/我们认为"）。

只输出 JSON（二选一）：
{"kind": "tree", "name": "树名", "subject": "报告主体", "description": "一句话描述",
 "genre": "journal|research|brief", "writer_role": "写作角色", "title_style": "标题要求",
 "writing_rules": ["行文规则", "..."], "forbidden_words": ["禁用词", "..."],
 "style_card": "journal_paper|industry_research|exec_brief",
 "sections": [{"id": "中文短名或英文小写", "title": "...", "kind": "text",
   "heading": "1　xxx", "style": "写作要求（含字数）", "data_needs": ["..."],
   "origin": "user|agent",
   "charts": [{"id": "...", "title": "...", "type": "bar", "source": "facts:rag", "unit": ""}],
   "view_slots": [{"id": "...", "brief": "...", "data_needs": ["..."]}], "n_views": 2}],
 "tables": [{"id": "...", "renderer": "facts_rows", "source_prefix": "rag"}]}
或 {"kind": "questions", "questions": ["问题1", "问题2"]}"""

_EDIT_SYSTEM = """你是报告结构编辑助手。给你当前结构树 JSON 与用户修改意见，
输出受限操作集 ops。操作类型（严格按此枚举）：
- {"action": "set_meta", "field": "name|subject|status|description|writer_role|title_style|genre|style_card|forbidden_words", "value": "..."}
  （style_card 只能取 null 或 journal_paper/industry_research/exec_brief；
   forbidden_words 的 value 是字符串数组）
- {"action": "add_section", "section": {同生成schema的节对象}, "after": "某节id或null(追加末尾)"}
- {"action": "remove_section", "section_id": "..."}
- {"action": "update_section", "section_id": "...", "fields": {"title|heading|style|data_needs": 新值}}
- {"action": "move_section", "section_id": "...", "direction": "up|down"}
- {"action": "add_chart", "section_id": "...", "chart": {"id","title","type","source","unit"}}
- {"action": "remove_chart", "section_id": "...", "chart_id": "..."}
约束：不新增表格/数据源类型；chart.source 只能是 "facts:rag" 或 "facts:web"；
意见指向不明的字段按最合理理解处理。只输出 JSON：
{"summary": "一句话告诉用户改了什么（或追问）", "ops": [...]}


【当前结构树】
{tree_json}"""


class TreeAgentError(RuntimeError):
    pass


def _clean_id(raw: Any, fallback: str = "section") -> str:
    """id 清洗（C5 中文化）：中文/字母/数字/下划线/连字符保留，其余折成 _；
    禁路径字符（/ \\ .. 前导点）——id 会成为对话与引用的稳定标识。"""
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(raw or "").strip())[:40]
    s = s.strip("_") or fallback
    return s if not s.startswith(".") else fallback


def _fmt_messages(messages: list[dict[str, str]]) -> str:
    lines = []
    for m in messages:
        role = "用户" if m.get("role") == "user" else "助手"
        lines.append(f"{role}：{m.get('text', '')}")
    return "\n".join(lines) or "（无对话）"


def _clean_section(s: Any) -> dict[str, Any] | None:
    """LLM 节对象 → 规整节 dict（id 清洗/字段截断/枚举兜底）；不合规返回 None。
    id 缺省取标题本身（C5 中文短名），仅当标题含非法字符时清洗。"""
    if not isinstance(s, dict) or not s.get("title"):
        return None
    kind = s.get("kind") if s.get("kind") in Kind else "text"
    sec = {"id": _clean_id(s.get("id") or s["title"]),
           "title": str(s["title"])[:40], "kind": kind}
    for k in ("heading", "subheading", "style", "table"):
        if s.get(k):
            sec[k] = str(s[k])
    if isinstance(s.get("data_needs"), list) and s["data_needs"]:
        sec["data_needs"] = [str(x)[:40] for x in s["data_needs"][:6]]
    sec["origin"] = "user" if s.get("origin") == "user" else "agent"
    charts = []
    for c in s.get("charts") or []:
        if isinstance(c, dict) and c.get("id") and c.get("source"):
            charts.append({"id": _clean_id(c["id"], "chart"),
                           "title": str(c.get("title") or c["id"])[:40],
                           "type": c.get("type") if c.get("type") in
                                   ("bar", "line", "pie", "scatter", "hist")
                                   else "bar",
                           "source": str(c["source"])[:60],
                           "unit": str(c.get("unit") or "")[:20]})
    if charts:
        sec["charts"] = charts
    if kind == "views":
        slots = []
        for v in s.get("view_slots") or []:
            if isinstance(v, dict) and v.get("id"):
                slots.append({"id": _clean_id(v["id"], "v"),
                              "brief": str(v.get("brief") or "")[:120],
                              "data_needs": [str(x)[:40] for x in
                                             (v.get("data_needs") or [])[:6]]})
        sec["view_slots"] = slots or [{"id": "v1", "brief": sec.get("style", ""),
                                       "data_needs": sec.get("data_needs", [])}]
        sec["n_views"] = len(sec["view_slots"])
    if kind == "risk":
        sec["strategy"] = "enumerate"
    return sec


def _clean_tree_payload(out: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """LLM 树草案 → 规整 spec_dict（剥离 kind/questions 等非 Spec 字段）。
    A3 三件套强校验：表格模板必须有节引用（孤儿自动删除）；表格节引用缺失
    模板时自动补一个 facts_rows 兜底模板。返回 (spec_dict, meta)。"""
    sections = []
    for s in out.get("sections") or []:
        sec = _clean_section(s)
        if sec is not None:
            sections.append(sec)
    tables = []
    for t in out.get("tables") or []:
        if isinstance(t, dict) and t.get("id"):
            tables.append({"id": _clean_id(t["id"], "table"),
                           "renderer": "facts_rows",
                           "source_prefix": str(t.get("source_prefix") or "rag")[:30]})
    warnings: list[str] = []
    referenced = {s["table"] for s in sections
                  if s.get("kind") == "table" and s.get("table")}
    # 孤儿模板：无任何节引用 → 删除（模型被迫手写 markdown 表的根源）
    kept = [t for t in tables if t["id"] in referenced]
    for t in tables:
        if t["id"] not in referenced:
            warnings.append(f"已删除孤儿表格模板「{t['id']}」（没有任何节引用）")
    # 反向：表格节引用了不存在的模板 → 补兜底模板（否则 SpecV2 校验整体失败）
    have = {t["id"] for t in kept}
    for s in sections:
        if s.get("kind") == "table" and s.get("table") \
                and s["table"] not in have:
            kept.append({"id": s["table"], "renderer": "facts_rows",
                         "source_prefix": "rag"})
            have.add(s["table"])
            warnings.append(f"节「{s['title']}」引用的表格模板「{s['table']}」"
                            "缺失，已自动补 facts_rows 兜底模板")
    tables = kept
    spec = {
        "report_type": "tree",
        "description": str(out.get("description") or out.get("name") or "报告")[:200],
        "writer_role": str(out.get("writer_role") or "资深行业研究员")[:40],
        "title_style": str(out.get("title_style") or "短语式标题，15~30 字")[:200],
        "sections": sections,
    }
    rules = [str(r)[:120] for r in (out.get("writing_rules") or []) if str(r).strip()]
    if rules:
        spec["writing_rules"] = rules[:8]
    fwords = [str(w)[:20] for w in (out.get("forbidden_words") or [])
              if str(w).strip()]
    if fwords:
        spec["forbidden_words"] = fwords[:8]
    if tables:
        spec["tables"] = tables
    if out.get("genre") in ("journal", "research", "brief"):
        spec["genre"] = out["genre"]
    if out.get("style_card"):
        spec["style_card"] = str(out["style_card"])[:40]
    meta = {"name": str(out.get("name") or "对话生成树")[:40],
            "subject": str(out.get("subject") or out.get("name") or "报告主体")[:60]}
    return spec, meta, warnings


def generate(messages: list[dict[str, str]]) -> dict[str, Any]:
    """需求对话 → {"kind": "questions"} 或 {"kind": "tree", spec_dict, meta}。
    树草案已过 SpecV2 校验（不合规抛 TreeAgentError）。"""
    out = chat_json(_GENERATE_SYSTEM, _fmt_messages(messages),
                    schema_hint="只输出一个合法 JSON 对象。",
                    tier=tier_for("extract"))
    if out.get("kind") == "questions" or (out.get("questions")
                                          and not out.get("sections")):
        qs = [str(q)[:120] for q in (out.get("questions") or []) if str(q).strip()]
        if not qs:
            raise TreeAgentError("模型未产出树也未给出反问，请换种说法重试")
        return {"kind": "questions", "questions": qs[:3]}
    spec_dict, meta, warnings = _clean_tree_payload(out)
    if not spec_dict["sections"]:
        raise TreeAgentError("模型产出的树没有任何章节，请补充需求后重试")
    from trees import store as tree_store
    try:
        tree_store.validate_payload(spec_dict)
    except ValueError as e:
        raise TreeAgentError(f"生成的结构不合规：{e}") from e
    return {"kind": "tree", "spec_dict": spec_dict, "meta": meta,
            "warnings": warnings}


# ---------- edit：受限操作集与应用（纯函数，可单测） ----------

def _unique_id(base: str, existing: set[str]) -> str:
    tid, n = base, 2
    while tid in existing:
        tid = f"{base}_{n}"
        n += 1
    return tid


# B2：set_meta 字段分流白名单——meta 块字段 vs SpecV2 顶层字段
_META_FIELDS = ("name", "subject", "status")
_SPEC_FIELDS = ("description", "writer_role", "title_style", "genre",
                "style_card")


def _apply_ops(spec_dict: dict[str, Any], meta: dict[str, Any],
               ops: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    """把 ops 应用到 spec/meta（就地修改），返回人话描述列表。
    非法 op 静默跳过并记描述——树编辑不允许把树改坏，最终仍过 schema 校验。"""
    applied: list[str] = []
    sections = spec_dict.setdefault("sections", [])

    def _find(sid):
        return next((s for s in sections if s.get("id") == sid), None)

    for op in ops or []:
        if not isinstance(op, dict):
            continue
        action = op.get("action")
        if action == "set_meta" and op.get("field") in _META_FIELDS:
            meta[op["field"]] = str(op.get("value") or "")[:200]
            applied.append(f"修改{op['field']}为「{meta[op['field']]}」")
        elif action == "set_meta" and op.get("field") in _SPEC_FIELDS:
            # B2：SpecV2 字段写顶层（写进 meta 块会被 SpecV2 静默忽略）
            spec_dict[op["field"]] = str(op.get("value") or "")[:200]
            applied.append(f"修改{op['field']}为「{spec_dict[op['field']]}」")
        elif action == "set_meta" and op.get("field") == "forbidden_words":
            words = [str(w)[:20] for w in (op.get("value") or [])
                     if str(w).strip()] if isinstance(op.get("value"),
                                                      list) else []
            spec_dict["forbidden_words"] = words[:8]
            applied.append("更新禁用词表（" + "、".join(words[:8]) + "）"
                           if words else "清空禁用词表")
        elif action == "add_section" and isinstance(op.get("section"), dict):
            sec = dict(op["section"])
            cleaned = _clean_section(sec)     # 归一化（id 缺省取标题）
            if cleaned is None:
                continue
            sec = cleaned
            existing_titles = {s.get("title") for s in sections}
            if sec["title"] in existing_titles:
                # A2：同标题拒绝（重复节会让反馈定位与渲染歧义）
                applied.append(f"已存在同名节「{sec['title']}」，未重复添加")
                continue
            sec["id"] = _unique_id(sec["id"], {s.get("id") for s in sections})
            sec.setdefault("kind", "text")
            sec["origin"] = "user"
            idx = len(sections)
            anchor = op.get("after")
            for i, s in enumerate(sections):
                if s.get("id") == anchor:
                    idx = i + 1
                    break
            sections.insert(idx, sec)
            applied.append(f"新增节「{sec.get('title', sec['id'])}」")
        elif action == "remove_section" and _find(op.get("section_id")):
            sec = _find(op["section_id"])
            sections.remove(sec)
            applied.append(f"删除节「{sec.get('title', sec['id'])}」")
        elif action == "update_section" and isinstance(op.get("fields"), dict) \
                and _find(op.get("section_id")):
            sec = _find(op["section_id"])
            for k, v in op["fields"].items():
                if k in ("title", "heading", "subheading", "style", "table"):
                    sec[k] = str(v)[:400]
                elif k == "data_needs" and isinstance(v, list):
                    sec[k] = [str(x)[:40] for x in v[:6]]
            applied.append(f"修改节「{sec.get('title', sec['id'])}」")
        elif action == "move_section" and _find(op.get("section_id")) \
                and op.get("direction") in ("up", "down"):
            i = sections.index(_find(op["section_id"]))
            j = i - 1 if op["direction"] == "up" else i + 1
            if 0 <= j < len(sections):
                sections[i], sections[j] = sections[j], sections[i]
                applied.append(f"移动节「{op['section_id']}」")
        elif action == "add_chart" and isinstance(op.get("chart"), dict) \
                and _find(op.get("section_id")):
            sec = _find(op.get("section_id"))
            c = op["chart"]
            charts = sec.setdefault("charts", [])
            cid = _unique_id(_clean_id(c.get("id") or c.get("title") or "chart",
                                       "chart"),
                             {x.get("id") for x in charts})
            charts.append({"id": cid, "title": str(c.get("title") or cid)[:40],
                           "type": c.get("type") if c.get("type") in
                                   ("bar", "line", "pie", "scatter", "hist") else "bar",
                           "source": str(c.get("source") or "facts:rag")[:60],
                           "unit": str(c.get("unit") or "")[:20]})
            applied.append(f"节「{sec.get('title', sec['id'])}」加图「{charts[-1]['title']}」")
        elif action == "remove_chart" and _find(op.get("section_id")):
            sec = _find(op["section_id"])
            charts = sec.get("charts") or []
            sec["charts"] = [c for c in charts if c.get("id") != op.get("chart_id")]
            applied.append(f"节「{sec.get('title', sec['id'])}」删图")
    return applied, meta


def edit(tree_id: str, message: str,
         base_version: int | None = None) -> dict[str, Any]:
    """编辑对话：读当前树（D5）→ NL → ops → 应用 → 保存（actor=agent）。
    base_version（B5）：给出时乐观锁校验，冲突抛 trees.store.TreeConflict。"""
    from trees import store as tree_store
    from trees.lint import lint as tree_lint_fn
    loaded = tree_store.load_spec_dict(tree_id)
    spec_dict, meta = loaded["spec_dict"], dict(loaded["meta"])
    compact = dict(spec_dict)
    # 模板内含 JSON 示例花括号，不能用 str.format——用占位符替换
    system = _EDIT_SYSTEM.replace("{tree_json}",
                                  json.dumps(compact, ensure_ascii=False))
    out = chat_json(system,
                    f"用户意见：{message}", schema_hint="只输出一个合法 JSON 对象：summary/ops。",
                    tier=tier_for("extract"))
    ops = out.get("ops") if isinstance(out.get("ops"), list) else []
    applied, meta = _apply_ops(spec_dict, meta, ops)
    if not applied:
        return {"applied": [], "summary": str(out.get("summary") or
                "没有理解到可执行的修改，请换个说法"), "ops": [],
                "changed": False}
    summary = str(out.get("summary") or "；".join(applied))
    result = tree_store.save_tree(tree_id, spec_dict,
                                  {k: meta.get(k) for k in
                                   ("name", "subject", "status")},
                                  actor="agent", summary=summary,
                                  op={"action": "chat_edit", "ops": ops},
                                  base_version=base_version)
    return {"applied": applied, "summary": summary, "ops": ops,
            "version": result["meta"]["version"],
            "fingerprint": result["fingerprint"],
            "lint": tree_lint_fn(spec_dict), "changed": True}
