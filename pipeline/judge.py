"""⑥ LLM-as-judge：以真研报为对标，五维评分 + 点名修改意见。

评分维度：structure 结构完整 / professionalism 专业性 / data_support 数据支撑 /
compliance 合规性 / readability 可读性，各 1~10 分。总分 ≥36 且无单维 ≤4 → pass；
否则输出 issues（点名到节），由 revise 循环重写。
"""

import json
from pathlib import Path
from typing import Any

from pipeline.llm import chat_json
from pipeline.outline import load_spec

REFERENCE_PATH = Path(__file__).resolve().parent.parent / \
    "config/reference/company_review_guosen_000803.md"

SYSTEM = """你是券商研报质量评审官。以同题材真研报为对标，对自动生成的点评报告
严格评分。评分要给证据（引用原文短语），问题要定位到具体节。

评分维度（1~10）：
- structure 结构完整：标题/核心观点/盈利预测/风险提示是否齐备且形态正确
- professionalism 专业性：论证方式、术语使用是否接近范文（对比/归因/口径）
- data_support 数据支撑：数据密度与精度，引用是否恰当（结合系统对账结果）
- compliance 合规性：无第一人称、无夸大、风险提示到位、表达克制
- readability 可读性：语句通顺、逻辑连贯、无模板腔

判定：总分≥36 且无单维≤4 → pass；否则 fail 并给出 issues。
target 取值只能是：title、earnings_forecast、risk_warning、core_views.
+以下之一：{slot_ids}（禁止用序号代替）。"""

USER_TMPL = """【对标范文】（真研报节选）
{reference}

【待评报告】
标题：{title}

核心观点：
{views}

盈利预测与估值说明：
{forecast}

风险提示：
{risks}

【系统校验摘要】
数字对账：{reconcile_status}（未匹配数字 {unknown_n} 个）
规则校验：{validate_status}（{validate_items}）

评审并只输出 JSON：
{{"scores": {{"structure": {{"score": 0, "comment": "..."}},
"professionalism": {{...}}, "data_support": {{...}}, "compliance": {{...}},
"readability": {{...}}}}, "total": 0,
"verdict": "pass|fail",
"issues": [{{"target": "core_views.<slot_id>|earnings_forecast|risk_warning|title",
"problem": "...", "instruction": "..."}}]}}
issues 仅在 fail 时给出，target 必须用上述取值。"""


def run(doc: dict[str, Any], outline: dict[str, Any],
        views: list[dict[str, Any]], forecast: dict[str, Any],
        risks: dict[str, Any], reconcile_report: dict[str, Any],
        validate_report: dict[str, Any]) -> dict[str, Any]:
    reference = REFERENCE_PATH.read_text(encoding="utf-8")
    unknown_n = sum(len(c["unknown_numbers"]) for c in reconcile_report["checks"])
    validate_items = "；".join(
        f"{i['rule']}({i['status']}): {i['detail']}" for i in validate_report["items"]) \
        or "无告警"
    views_text = "\n".join(
        f"{i}. {v['heading']}\n   {v['body']}" for i, v in enumerate(views, 1))
    user = USER_TMPL.format(
        reference=reference, title=outline["title"], views=views_text,
        forecast=forecast["body"], risks=risks["body"],
        reconcile_status=reconcile_report["status"].upper(),
        unknown_n=unknown_n,
        validate_status=validate_report["status"].upper(),
        validate_items=validate_items,
        slot_ids=", ".join(v["slot_id"] for v in views))
    out = chat_json(SYSTEM, user,
                    schema_hint="只输出一个合法 JSON 对象。", max_tokens=4000)
    # 兼容两种输出：{"structure": 8} 或 {"structure": {"score": 8, "comment": "..."}}
    scores: dict[str, dict[str, Any]] = {}
    for k, v in out.get("scores", {}).items():
        if isinstance(v, dict) and "score" in v:
            scores[k] = {"score": int(v["score"]),
                         "comment": str(v.get("comment", ""))}
        else:
            scores[k] = {"score": int(v), "comment": ""}
    out["scores"] = scores
    total = sum(d["score"] for d in scores.values())
    out["total"] = total if total else out.get("total", 0)
    min_score = min((d["score"] for d in scores.values()), default=0)
    out["verdict"] = "pass" if (out["total"] >= 36 and min_score > 4) else "fail"

    # 对账兜底（代码级强制）：存在未匹配数字的节必须 FAIL——评审不得主观放过
    dirty = [c for c in reconcile_report["checks"] if c["unknown_numbers"]]
    if dirty:
        out["verdict"] = "fail"
        have = {i.get("target") for i in out.get("issues") or []}
        for c in dirty:
            if c["section"] in have:
                continue
            out.setdefault("issues", []).append({
                "target": c["section"],
                "problem": f"正文存在 {len(c['unknown_numbers'])} 个无法对账的数字"
                           f"（{c['unknown_numbers']}），无事实出处",
                "instruction": "只允许引用事实编号中的数字；无出处的数字删除，"
                               "相关内容改为定性表述。"})
    if out["verdict"] == "pass":
        out["issues"] = []
    return out
