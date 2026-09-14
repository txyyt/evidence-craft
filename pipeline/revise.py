"""⑥ 退回重写：judge 点名的节，带原稿与评审意见重新生成。

仅重写被点名的节（其余保留），数字约束不变；标题可被整体修订。
修订目标按 Spec v2 的章节 id 匹配；范文走 fewshot_for 解析链
（槽位级 → 章节级），重写要求取自对应章节的 style。
"""

from typing import Any

from pipeline.llm import chat_json, tier_for
from pipeline.sections import _citations_resolvable, _default_facts, _fact_block
from template_factory.schema import SpecV2

REVISE_TMPL = """【本节原稿】
小标题：{heading}
正文：{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

【允许引用的事实】（正文数字只能出自这里）
{facts}

在保持事实准确的前提下重写此节（小标题可微调）。只输出 JSON：
{{"heading": "...", "body": "...", "cited_fact_ids": [...]}}
"""

TITLE_TMPL = """当前标题：{title}
评审意见：{problem}
修改要求：{instruction}
标题要求：{title_style}
只输出 JSON：{{"title": "..."}}"""

FORECAST_REVISE_TMPL = """【预测说明原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

预测表（数字以此为准）：
{table}

按以下要求重写说明文字：
{style}

只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}"""

RISK_REVISE_TMPL = """【风险提示原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

按以下要求重写风险提示：
{style}

只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}"""

TEXT_REVISE_TMPL = """【本节原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

【允许引用的事实】（正文数字只能出自这里）
{facts}

在保持事实准确的前提下重写此节。只输出 JSON：
{{"body": "...", "cited_fact_ids": [...]}}
"""

TABLE_NOTE_REVISE_TMPL = """【表格说明原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

表格（数字以此为准）：
{table}

按以下要求重写说明文字：
{style}

只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}"""


def _system(spec: SpecV2, fewshot: str,
            doc: dict[str, Any] | None = None) -> str:
    from pipeline.sections import _TIMELINESS_TMPL, _era_line
    from pipeline.stylecards import apply as style_apply
    timeliness = ""
    if doc is not None:
        from datetime import datetime
        timeliness = _TIMELINESS_TMPL.format(
            today=datetime.now().strftime("%Y-%m-%d"),
            era_line=_era_line(doc))
        timeliness += "\n"
    style_block, fewshot = style_apply(spec, fewshot)
    if style_block:
        style_block = style_block + "\n"
    return (f"你是{spec.writer_role}，修订报告的一个部分。\n\n"
            f"报告类型：{spec.description}\n{timeliness}{style_block}"
            f"\n写作规范：\n{spec.rules_text()}\n\n"
            f"【范文】\n{fewshot}\n")


def _revise_issue(spec: SpecV2, fewshot: str, instruction: dict[str, str],
                  template: str, doc: dict[str, Any] | None = None,
                  **kwargs) -> dict[str, Any]:
    return chat_json(_system(spec, fewshot, doc),
                     template.format(**kwargs, **instruction),
                     schema_hint="只输出一个合法 JSON 对象。",
                     tier=tier_for("write"))


def apply(doc: dict[str, Any], outline: dict[str, Any],
          written: list[dict[str, Any]], forecast: dict[str, Any],
          risks: dict[str, Any],
          judge_report: dict[str, Any], spec: SpecV2,
          texts: list[dict[str, Any]] | None = None,
          notes: dict[str, dict[str, Any]] | None = None) -> tuple:
    """按 issues 修订，返回 (written, forecast, risks, outline, texts, notes)。

    views/table/risk 章节均可缺省（地学等报告类型无观点章/风险章）。"""
    views_sec = spec.section("views")
    table_sec = spec.section("table")
    risk_sec = spec.section("risk")
    texts = list(texts or [])
    notes = dict(notes or {})

    for issue in judge_report.get("issues", []):
        target = issue.get("target", "")
        fb = {"problem": issue.get("problem", ""),
              "instruction": issue.get("instruction", "")}
        if target == "title":
            outline["title"] = _revise_issue(
                spec, spec.fewshot_for(views_sec) if views_sec else "",
                fb, TITLE_TMPL, title=outline["title"],
                title_style=spec.title_style)["title"]
            continue
        if views_sec is not None and target.startswith(views_sec.id + "."):
            views_prefix = views_sec.id + "."
            key = target[len(views_prefix):]
            # judge 偶发用序号（<views>.1）代替 slot_id：按位置兜底映射
            if key not in {v["slot_id"] for v in written} and key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(written):
                    target = views_prefix + written[idx]["slot_id"]
            if target.startswith(views_prefix):
                slot = target[len(views_prefix):]
                for i, v in enumerate(written):
                    if v["slot_id"] != slot:
                        continue
                    cited = v.get("cited_fact_ids") or _default_facts(doc, spec, slot)
                    facts_text, _ = _fact_block(doc, cited)
                    out = _revise_issue(spec, spec.fewshot_for(views_sec, slot),
                                        fb, REVISE_TMPL, doc=doc,
                                        heading=v["heading"],
                                        body=v["body"], facts=facts_text)
                    written[i] = {**out, "slot_id": slot, "cited_fact_ids": cited}
            continue
        if table_sec is not None and target == table_sec.id and target not in notes:
            from pipeline.sections import render_table
            out = _revise_issue(spec, spec.fewshot_for(table_sec),
                                fb, FORECAST_REVISE_TMPL, doc=doc, body=forecast["body"],
                                table=render_table(doc, spec),
                                style=table_sec.style or "")
            forecast = {**forecast, **out}
            continue
        if target in notes:
            tsec = spec.section_by_id(target)
            from pipeline.sections import render_table_sec
            note = notes[target]
            out = _revise_issue(spec, spec.fewshot_for(tsec) if tsec else "",
                                fb, TABLE_NOTE_REVISE_TMPL, doc=doc, body=note.get("body", ""),
                                table=render_table_sec(doc, tsec, spec) if tsec else "",
                                style=(tsec.style if tsec else "") or "")
            out["section_id"] = target
            out["cited_fact_ids"] = _citations_resolvable(
                doc, out.get("cited_fact_ids") or [])
            notes[target] = out
            continue
        if risk_sec is not None and target == risk_sec.id:
            out = _revise_issue(spec, spec.fewshot_for(risk_sec),
                                fb, RISK_REVISE_TMPL, doc=doc, body=risks["body"],
                                style=risk_sec.style or "")
            out["cited_fact_ids"] = _citations_resolvable(
                doc, out.get("cited_fact_ids") or [])
            risks = {**risks, **out}
            continue
        # text 章节：按 section_id 匹配重写
        for ti, t in enumerate(texts):
            if t.get("section_id") != target:
                continue
            sec = spec.section_by_id(target)
            facts_text, _ = _fact_block(doc, t.get("cited_fact_ids") or [])
            out = _revise_issue(spec, spec.fewshot_for(sec) if sec else "",
                                fb, TEXT_REVISE_TMPL, doc=doc, body=t["body"],
                                facts=facts_text)
            out["section_id"] = target
            out["cited_fact_ids"] = _citations_resolvable(
                doc, out.get("cited_fact_ids") or [])
            texts[ti] = out
            break
    return written, forecast, risks, outline, texts, notes
