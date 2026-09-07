"""⑥ 退回重写：judge 点名的节，带原稿与评审意见重新生成。

仅重写被点名的节（其余保留），数字约束不变；标题可被整体修订。
"""

from typing import Any

from pipeline.llm import chat_json
from pipeline.outline import load_spec
from pipeline.sections import _default_facts as sections_default
from pipeline.sections import _fact_block

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

FORECAST_REVISE_TMPL = """【盈利预测说明原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

预测表（数字以此为准）：
{table}

重写说明文字（80~150字）。只输出 JSON：
{{"body": "...", "cited_fact_ids": [...]}}"""

RISK_REVISE_TMPL = """【风险提示原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

重写 3~5 条风险提示（每条10~20字、以"风险"结尾、分号分隔成一行）。
只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}"""


def _spec_system() -> str:
    spec = load_spec()
    return f"你是资深卖方分析师，修订上市公司点评报告的一个部分。\n\n写作规范：\n{spec['rules']}\n\n【范文】\n{spec['fewshot']}\n"


def _revise_issue(instruction: str, template: str, **kwargs) -> dict[str, Any]:
    return chat_json(_spec_system(), template.format(**kwargs, **instruction),
                     schema_hint="只输出一个合法 JSON 对象。")


def apply(doc: dict[str, Any], outline: dict[str, Any],
          written: list[dict[str, Any]], forecast: dict[str, Any],
          risks: dict[str, Any],
          judge_report: dict[str, Any]) -> tuple:
    """按 issues 修订，返回 (written, forecast, risks, outline)。"""
    for issue in judge_report.get("issues", []):
        target = issue.get("target", "")
        fb = {"problem": issue.get("problem", ""),
              "instruction": issue.get("instruction", "")}
        # judge 偶发用序号（core_views.1）代替 slot_id：按位置兜底映射
        if target.startswith("core_views."):
            key = target[len("core_views."):]
            if key not in {v["slot_id"] for v in written} and key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(written):
                    target = "core_views." + written[idx]["slot_id"]
        if target == "title":
            spec = load_spec()
            outline["title"] = _revise_issue(fb, TITLE_TMPL,
                title=outline["title"], title_style=spec["title_style"])["title"]
        elif target.startswith("core_views."):
            slot = target[len("core_views."):]
            for i, v in enumerate(written):
                if v["slot_id"] != slot:
                    continue
                cited = v.get("cited_fact_ids") or sections_default(doc, slot)
                facts_text, _ = _fact_block(doc, cited)
                out = _revise_issue(fb, REVISE_TMPL, heading=v["heading"],
                                    body=v["body"], facts=facts_text)
                written[i] = {**out, "slot_id": slot, "cited_fact_ids": cited}
        elif target == "earnings_forecast":
            from pipeline.sections import forecast_table
            out = _revise_issue(fb, FORECAST_REVISE_TMPL, body=forecast["body"],
                                table=forecast_table(doc))
            forecast = {**forecast, **out}
        elif target == "risk_warning":
            out = _revise_issue(fb, RISK_REVISE_TMPL, body=risks["body"])
            risks = {**risks, **out}
    return written, forecast, risks, outline
