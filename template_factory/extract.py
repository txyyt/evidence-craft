"""模板工厂②~⑥：样例 → Spec v2 草案。

流程（对应《多部门泛化方案》M6）：
  ② 章节骨架提取（LLM×1）：功能归类 → sections[] 草案（kind 判定）
  ③ 槽位细化（LLM×N）：叙述模式 / data_needs / 槽位级范文（附选段理由）
  ④ 表格/图件模板提取：列 schema、renderer 判定、图件 provider
  ⑤ 规则与风格提取：行文规则、禁用词、受控词表
  ⑥ 交叉合并（多样例）：逐字段比对，一致采纳、分歧标"待人工裁决"

硬约束：LLM 全部输出经 pydantic 校验落到 Spec v2，不允许自由文本 spec。
用法：
  python -m template_factory.extract --samples 样例1.docx 样例2.pdf \
      --out config/report_types/xxx_draft.yaml [--compare config/report_types/xxx.yaml]
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

from pipeline.llm import chat_json
from template_factory import parsers
from template_factory.schema import SpecV2

A_SYSTEM = """你是报告模板工程师。从样例报告的骨架（标题层级+段落统计+表格/图件标记）
推断该类报告的模板结构。要求：
- 章节按"功能"归类合并（同类叙述合并为一章），kind 只能是
  views（结论观点类，逐条小标题+论述）/ table（数据表格+说明文字）/
  risk（风险提示或不确定性说明）/ text（综述背景类）/ figures（图件集）
- views 章节全篇恰好一个；数据表较多且独立的可各自成 table 章节
- 输出 JSON：
{"report_type": "英文snake_case", "description": "一句话",
 "title_style": "标题形态归纳（含范例）",
 "sections": [{"id": "英文snake_case", "title": "章节标题", "kind": "...", "reason": "判定理由"}]}
"""

A_USER = """【样例骨架】
{skeleton}

请推断模板结构，只输出 JSON。"""

B_SYSTEM = """你是报告模板工程师。针对样例报告的一个章节做"槽位细化"：把正文按叙述
角色拆分为固定视角槽位（view_slots），供后续生成时逐槽位独立成文。要求：
- 相邻且叙述角色相同的段落归为一个槽位；槽位 id 用英文 snake_case
- brief：该槽位的职责描述（写什么、用什么数据、如何论证）
- data_needs：该槽位依赖的数据类别，2~6 字语义标签（如"钻孔进尺""化验品位""收入占比"）
- fewshot：从原文中逐字摘选 1~2 段最能示范该槽位写法的段落（≤300字），
  并给选段理由（fewshot_reason）——这段范文将直接注入生成 prompt
- view_style：该章段落形态总结（小标题形态、字数分布、论证结构、语气）
- body_len：单个槽位单段正文的字数区间 [下限, 上限]。观测对象是每个观点段
  （P1、P2……各自）的字数，不是整章合计；取各段字数的中位数 ±30%
只输出 JSON：
{"view_slots": [{"id": "...", "brief": "...", "data_needs": ["..."],
  "fewshot": "...", "fewshot_reason": "..."}],
 "view_style": "...", "body_len": [lo, hi]}
"""

B_USER = """【章节】{title}（{kind}）

【正文】（段落编号 P1..Pn 便于引用）
{paras}

只输出 JSON。"""

RISK_USER = """【章节】{title}（risk 风险提示/不确定性说明）

【正文】
{paras}

判断写作策略：mirror（逐条审视前文观点，其假设的反面即风险）或
enumerate（按固定清单逐项列举）。归纳 style（条数、每条字数、结尾词、
分隔符等形态要求，含范例），并给出条数区间 count: [lo, hi] 与每条结尾词
item_suffix（如"风险"）。
只输出 JSON：{{"strategy": "...", "style": "...", "count": [lo, hi],
"item_suffix": "..."}}"""

TABLE_USER = """【章节】{title}（table 数据表格+说明文字）

【正文与表格】
{paras}

提取表格模板：columns（表头列名列表）、renderer（表格含预测期 EPS/PE 等
估值预测用 consensus_pe，其余通用数据行用 generic_rows）、style（说明文字
的写法要求，从周边段落归纳）。
只输出 JSON：{{"columns": ["..."], "renderer": "...", "style": "..."}}"""

C_SYSTEM = """你是报告模板工程师。从样例报告全文归纳行文规则：
- writing_rules：可从文中验证的硬性写法（数字精度口径、同比表述、专有名词
  来源约束等）；无法从单一样例确证的不要编造
- forbidden_words：从文体判断应禁用的词（第一人称、感叹号、夸大措辞等）
- controlled_vocab：受控词表（如评级行业有固定评级词汇；无则输出空对象）
只输出 JSON：{"writing_rules": ["..."], "forbidden_words": ["..."],
"controlled_vocab": {"字段名": ["词1", "词2"]}}
"""

C_USER = """【样例全文（截选）】
{text}

只输出 JSON。"""

MERGE_SYSTEM = """你是报告模板工程师。两份从不同样例独立提取的章节结构需要交叉合并：
逐个比对章节（按 kind 与标题语义），输出合并后的章节列表。规则：
- 两边一致的章节：合并，confidence=high，标注 agree
- 功能相同但细节分歧（标题不同/槽位不同）：取信息更全的一方，confidence=medium，
  divergence 写明分歧点（待人工裁决）
- 只在一边出现的章节：保留，confidence=low（单样例特征，可能非模板本质）
只输出 JSON：{"sections": [{"id": "...", "title": "...", "kind": "...",
"confidence": "high|medium|low", "divergence": "...", "view_slots": [...],
"view_style": "...", "body_len": [lo, hi] | null, "style": "...",
"strategy": "...", "count": [lo, hi] | null, "item_suffix": "...",
"columns": [...] | null, "renderer": "..."}]}
"""


def _extract_one(parsed: dict[str, Any], report_type_hint: str | None) -> dict[str, Any]:
    """单样例提取：②结构 → ③④⑤细化。返回中间草案 dict。"""
    skel = parsers.skeleton(parsed)
    a = chat_json(A_SYSTEM, A_USER.format(skeleton=skel),
                  schema_hint="只输出一个合法 JSON 对象。")

    sections: list[dict[str, Any]] = []
    for sec in a["sections"]:
        paras = parsers.section_text(parsed, sec["title"])
        item: dict[str, Any] = {k: sec.get(k) for k in ("id", "title", "kind", "reason")}
        if sec["kind"] == "views" and paras:
            b = chat_json(
                B_SYSTEM,
                B_USER.format(title=sec["title"], kind=sec["kind"], paras=paras),
                schema_hint="只输出一个合法 JSON 对象。")
            item.update({"view_slots": b.get("view_slots"),
                         "view_style": b.get("view_style"),
                         "body_len": b.get("body_len")})
        elif sec["kind"] == "risk" and paras:
            b = chat_json(
                "你是报告模板工程师。只输出一个合法 JSON 对象。",
                RISK_USER.format(title=sec["title"], paras=paras),
                schema_hint="只输出一个合法 JSON 对象。")
            item.update({"strategy": b.get("strategy"), "style": b.get("style"),
                         "count": b.get("count"), "item_suffix": b.get("item_suffix")})
        elif sec["kind"] == "table" and paras:
            b = chat_json(
                "你是报告模板工程师。只输出一个合法 JSON 对象。",
                TABLE_USER.format(title=sec["title"], paras=paras),
                schema_hint="只输出一个合法 JSON 对象。")
            item.update({"columns": b.get("columns"), "renderer": b.get("renderer"),
                         "style": b.get("style")})
        elif sec["kind"] == "figures":
            caps = [b["caption"] for b in parsed["blocks"] if b["type"] == "figure"]
            item["figure_captions"] = caps
        sections.append(item)

    full = parsers.skeleton(parsed) + "\n" + "\n".join(
        b.get("text", "") for b in parsed["blocks"] if b["type"] == "para")[:4000]
    c = chat_json(C_SYSTEM, C_USER.format(text=full[:6000]),
                  schema_hint="只输出一个合法 JSON 对象。")
    return {"report_type": report_type_hint or a.get("report_type"),
            "description": a.get("description"),
            "title_style": a.get("title_style"),
            "sections": sections,
            "rules": c}


def _merge_extractions(drafts: list[dict[str, Any]]) -> dict[str, Any]:
    """⑥ 交叉合并：多例逐字段比对；单例直接标注低置信。"""
    base = json.loads(json.dumps(drafts[0]))  # deep copy
    if len(drafts) == 1:
        for s in base["sections"]:
            s["confidence"] = "medium"
            s["divergence"] = "单样例提取，结构性特征待第二篇样例交叉确认"
        return base

    others = drafts[1:]
    for s in base["sections"]:
        s["confidence"] = "high"
        s["divergence"] = ""
        for other in others:
            match = next((o for o in other["sections"]
                          if o["kind"] == s["kind"]
                          and _title_similar(o["title"], s["title"])), None)
            if match is None:
                s["confidence"] = "low"
                s["divergence"] += f"其他样例未见本章（可能为偶然特征）；"
                continue
            for field in ("view_slots", "view_style", "body_len", "style",
                          "strategy", "count", "item_suffix", "columns", "renderer"):
                a, b = s.get(field), match.get(field)
                if not a and not b:
                    continue
                if a and b and json.dumps(a, sort_keys=True, ensure_ascii=False) != \
                        json.dumps(b, sort_keys=True, ensure_ascii=False):
                    s["divergence"] += f"{field} 两例不一致（待人工裁决）；"
                    if s["confidence"] == "high":
                        s["confidence"] = "medium"
                elif b and not a:
                    s[field] = b
    return base


def _title_similar(a: str, b: str) -> bool:
    """标题相似：完全相等或互为子串或字符重合率 ≥0.5。"""
    a, b = a.strip(), b.strip()
    if a == b or a in b or b in a:
        return True
    common = set(a) & set(b)
    return len(common) / max(len(set(a)), len(set(b)), 1) >= 0.5


def _assemble(merged: dict[str, Any]) -> SpecV2:
    """中间草案 → SpecV2（Schema 校验失败抛错，由 CLI 提示人工修正）。"""
    sections: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    for s in merged["sections"]:
        item: dict[str, Any] = {"id": _snake(s["id"]), "title": s["title"],
                                "kind": s["kind"]}
        if s.get("body_len"):
            item["check"] = {"body_len": s["body_len"]}
        if s["kind"] == "views":
            slots = []
            seen_ids: set[str] = set()
            for i, v in enumerate(s.get("view_slots") or [], 1):
                sid = _snake(v["id"])
                # 模型偶发返回中文 id（被 _snake 清成 unnamed）或重复 id——
                # 槽位 id 是大纲/回放/兜底引用的主键，必须唯一且非空
                if sid == "unnamed":
                    sid = f"slot_{i}"
                n = 1
                while sid in seen_ids:
                    sid = f"slot_{i}_{n}"
                    n += 1
                seen_ids.add(sid)
                slots.append({"id": sid, "brief": v["brief"],
                              "data_needs": v.get("data_needs") or [],
                              "fewshot": (v.get("fewshot") or "").strip() or None})
            item.update({"n_views": len(slots), "view_slots": slots,
                         "view_style": s.get("view_style")})
        elif s["kind"] == "table":
            renderer = s.get("renderer") if s.get("renderer") in \
                ("consensus_pe", "generic_rows") else "generic_rows"
            tid = _snake(s["id"]) + "_table"
            item["table"] = tid
            tables.append({"id": tid, "renderer": renderer,
                           "columns": s.get("columns") or []})
            item["style"] = s.get("style")
        elif s["kind"] == "risk":
            item.update({"strategy": s.get("strategy") or "enumerate",
                         "style": s.get("style")})
            check: dict[str, Any] = {}
            if s.get("count"):
                check["count"] = s["count"]
            if s.get("item_suffix"):
                check.update({"item_suffix": s["item_suffix"],
                              "min_shaped": 3})
            if check:
                item["check"] = check
        sections.append(item)

    return SpecV2.model_validate({
        "report_type": _snake(merged["report_type"]),
        "description": merged.get("description") or "",
        "title_style": merged.get("title_style") or "",
        "sections": sections,
        "tables": tables,
        "writing_rules": (merged.get("rules") or {}).get("writing_rules") or [],
        "forbidden_words": (merged.get("rules") or {}).get("forbidden_words") or [],
        "controlled_vocab": (merged.get("rules") or {}).get("controlled_vocab") or {},
    })


def _snake(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", str(s)).strip("_").lower()
    return s or "unnamed"


def _compare_report(draft: SpecV2, reference_path: str) -> str:
    """与既有手写 v2 spec 比对（自测用）：结构/槽位/规则重合度。"""
    ref = SpecV2.model_validate(
        yaml.safe_load(Path(reference_path).read_text(encoding="utf-8")))
    lines = ["## 与手写 spec 比对（自测）", ""]
    ref_views, draft_views = ref.section("views"), draft.section("views")
    if ref_views and draft_views:
        ref_ids = {s.id for s in ref_views.view_slots}
        draft_ids = {s.id for s in draft_views.view_slots}
        lines.append(f"- views 槽位：手写 {sorted(ref_ids)} / 提取 {sorted(draft_ids)}，"
                     f"交集 {sorted(ref_ids & draft_ids) or '无'}（语义相近即算命中，"
                     f"id 命名不要求一致）")
        has_fewshot = sum(1 for s in draft_views.view_slots if s.fewshot)
        lines.append(f"- 槽位级范文：{has_fewshot}/{len(draft_views.view_slots)} 个槽位"
                     f"带逐字范文" + ("（达标）" if has_fewshot == len(draft_views.view_slots)
                                      else "（缺失槽位需人工补选）"))
        lines.append(f"- view_style：{'提取到' if draft_views.view_style else '缺失'}")
    for kind in ("table", "risk"):
        r, d = ref.section(kind), draft.section(kind)
        if r and d:
            lines.append(f"- {kind} 章节：双方均识别（手写《{r.title}》/ 提取《{d.title}》）")
        elif r and not d:
            lines.append(f"- {kind} 章节：手写有、提取缺失（提取器缺口，需人工补）")
    if ref.section_by_id("risk_warning") and draft.section("risk"):
        rs, ds = ref.section_by_id("risk_warning"), draft.section("risk")
        lines.append(f"- 风险策略：手写 {rs.strategy} / 提取 {ds.strategy}"
                     + ("（一致）" if rs.strategy == ds.strategy else "（需人工裁决）"))
    common_forbidden = set(ref.forbidden_words) & set(draft.forbidden_words)
    lines.append(f"- 禁用词：手写 {len(ref.forbidden_words)} 个 / 提取 "
                 f"{len(draft.forbidden_words)} 个，重合 {len(common_forbidden)} 个")
    lines.append(f"- 行文规则：手写 {len(ref.writing_rules)} 条 / 提取 "
                 f"{len(draft.writing_rules)} 条（提取值偏观测归纳，手写值偏工程约束，"
                 f"定稿以手写风格为准人工合并）")
    return "\n".join(lines)


def _report_text(source_files: list[str], merged: dict[str, Any],
                 draft: SpecV2, compare_md: str | None) -> str:
    lines = [f"# 模板提取报告（{draft.report_type}）", ""]
    lines.append("## 样例来源")
    lines.extend(f"- {s}" for s in source_files)
    lines.append("")
    lines.append("## 章节结构与置信度")
    for s in merged["sections"]:
        conf = s.get("confidence", "medium")
        lines.append(f"- **{s['title']}** → kind={s['kind']}（{s.get('reason', '')}）"
                     f" 置信度：{conf}"
                     + (f"；分歧：{s['divergence']}" if s.get("divergence") else ""))
    lines.append("")
    lines.append("## 槽位与范文选段")
    for sec in draft.sections:
        if sec.kind != "views":
            continue
        for v in sec.view_slots:
            lines.append(f"- 槽位 `{v.id}`：{v.brief}")
            lines.append(f"  - data_needs：{v.data_needs or '（未提取，M7 需绑定）'}")
            if v.fewshot:
                lines.append(f"  - 范文选段（生成时注入 prompt）："
                             f"《{v.fewshot[:60]}...》" if len(v.fewshot) > 60
                             else f"  - 范文选段：《{v.fewshot}》")
    lines.append("")
    lines.append("## 待人工裁决")
    pending = [s for s in merged["sections"] if s.get("divergence")]
    if pending:
        lines.extend(f"- {s['title']}：{s['divergence']}" for s in pending)
    else:
        lines.append("- 无（多例完全一致）")
    lines.append("- judge_reference 未配置：请提供本类报告的标杆范文路径（judge 评"
                 "审对标用，必配）")
    lines.append("- disclaimer 未配置：请补充本类报告的合规声明段")
    lines.append("")
    if compare_md:
        lines.append(compare_md)
    return "\n".join(lines)


def run(samples: list[str], out: str, report_type: str | None = None,
        compare: str | None = None,
        progress=None) -> dict[str, Any]:
    """模板工厂全流程（M8 工作台与 CLI 共用）。

    progress(stage, message, data|None)：stage ∈ parse/structure/slots/merge/write。
    返回 {draft_yaml, report_md, extractions, out}。
    """
    _p = progress or (lambda *_: None)
    drafts, parsed_docs = [], []
    for f in samples:
        _p("parse", f"[解析] {f}")
        parsed = parsers.parse(f)
        parsed_docs.append(f)
        _p("structure", f"[②结构提取] {f}")
        drafts.append(_extract_one(parsed, report_type))

    _p("merge", f"[⑥交叉合并] {len(drafts)} 篇样例")
    merged = _merge_extractions(drafts)
    draft = _assemble(merged)

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(json.loads(draft.model_dump_json()), f,
                  allow_unicode=True, sort_keys=False)
    _p("write", f"    草案已写入 {out_path}（Schema 校验通过）")

    compare_md = _compare_report(draft, compare) if compare else None
    report = _report_text(parsed_docs, merged, draft, compare_md)
    report_path = out_path.with_suffix(".extraction_report.md")
    report_path.write_text(report, encoding="utf-8")
    _p("write", f"    提取报告已写入 {report_path}")

    # 落盘中间草案（断点/人工复核用）
    mid = out_path.with_suffix(".extractions.json")
    mid.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    _p("write", f"    中间草案已写入 {mid}")
    return {"out": str(out_path), "report_md": report, "extractions": merged}


def main() -> None:
    ap = argparse.ArgumentParser(description="模板工厂：样例 → Spec v2 草案")
    ap.add_argument("--samples", nargs="+", required=True,
                    help="样例文件（docx/pdf/md/txt，建议 2~3 篇）")
    ap.add_argument("--out", required=True, help="草案输出路径（建议 *_draft.yaml）")
    ap.add_argument("--report-type", default=None, help="覆盖自动推断的报告类型名")
    ap.add_argument("--compare", default=None,
                    help="与既有手写 v2 spec 比对（自测模式）")
    args = ap.parse_args()
    run(args.samples, args.out, args.report_type, args.compare)


if __name__ == "__main__":
    main()
