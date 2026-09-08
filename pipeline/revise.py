"""⑥ 退回重写：judge 点名的节，带原稿与评审意见重新生成。

仅重写被点名的节（其余保留），数字约束不变；标题可被整体修订。
修订目标按 Spec v2 的章节 id 匹配；范文走 fewshot_for 解析链
（槽位级 → 章节级），重写要求取自对应章节的 style。
"""

from typing import Any

from pipeline.llm import chat_json
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


def _system(spec: SpecV2, fewshot: str) -> str:
    return (f"你是{spec.writer_role}，修订报告的一个部分。\n\n"
            f"报告类型：{spec.description}\n\n写作规范：\n{spec.rules_text()}\n\n"
            f"【范文】\n{fewshot}\n")


def _revise_issue(spec: SpecV2, fewshot: str, instruction: dict[str, str],
                  template: str, **kwargs) -> dict[str, Any]:
    return chat_json(_system(spec, fewshot),
                     template.format(**kwargs, **instruction),
                     schema_hint="只输出一个合法 JSON 对象。")


def apply(doc: dict[str, Any], outline: dict[str, Any],
          written: list[dict[str, Any]], forecast: dict[str, Any],
          risks: dict[str, Any],
          judge_report: dict[str, Any], spec: SpecV2) -> tuple:
    """按 issues 修订，返回 (written, forecast, risks, outline)。"""
    views_sec = spec.section("views")
    table_sec = spec.section("table")
    risk_sec = spec.section("risk")
    if views_sec is None:
        raise ValueError("spec 缺少 views 章节")

    for issue in judge_report.get("issues", []):
        target = issue.get("target", "")
        fb = {"problem": issue.get("problem", ""),
              "instruction": issue.get("instruction", "")}
        views_prefix = views_sec.id + "."
        # judge 偶发用序号（<views>.1）代替 slot_id：按位置兜底映射
        if target.startswith(views_prefix):
            key = target[len(views_prefix):]
            if key not in {v["slot_id"] for v in written} and key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(written):
                    target = views_prefix + written[idx]["slot_id"]
        if target == "title":
            outline["title"] = _revise_issue(spec, spec.fewshot_for(views_sec),
                fb, TITLE_TMPL, title=outline["title"],
                title_style=spec.title_style)["title"]
        elif target.startswith(views_prefix):
            slot = target[len(views_prefix):]
            for i, v in enumerate(written):
                if v["slot_id"] != slot:
                    continue
                cited = v.get("cited_fact_ids") or _default_facts(doc, spec, slot)
                facts_text, _ = _fact_block(doc, cited)
                out = _revise_issue(spec, spec.fewshot_for(views_sec, slot),
                                    fb, REVISE_TMPL, heading=v["heading"],
                                    body=v["body"], facts=facts_text)
                written[i] = {**out, "slot_id": slot, "cited_fact_ids": cited}
        elif target == table_sec.id and table_sec is not None:
            from pipeline.sections import render_table
            out = _revise_issue(spec, spec.fewshot_for(table_sec),
                                fb, FORECAST_REVISE_TMPL, body=forecast["body"],
                                table=render_table(doc, spec),
                                style=table_sec.style or "")
            forecast = {**forecast, **out}
        elif target == risk_sec.id and risk_sec is not None:
            out = _revise_issue(spec, spec.fewshot_for(risk_sec),
                                fb, RISK_REVISE_TMPL, body=risks["body"],
                                style=risk_sec.style or "")
            out["cited_fact_ids"] = _citations_resolvable(
                doc, out.get("cited_fact_ids") or [])
            risks = {**risks, **out}
    return written, forecast, risks, outline
