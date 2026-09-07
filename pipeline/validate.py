"""⑥ 规则校验器（纯代码，零 LLM）：结构/字数/术语/口径四类规则。

与对账层（reconcile）分工：对账管"数字对不对"，这里管"报告合不合格"。
"""

import re
from typing import Any

FORBIDDEN = ["我们认为", "我们维持", "我司", "笔者", "推荐买入", "！"]
RATING_WORDS = {"买入", "增持", "中性", "减持", "卖出", "未评级"}


def _charlen(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def run(doc: dict[str, Any], outline: dict[str, Any],
        views: list[dict[str, Any]], forecast: dict[str, Any],
        risks: dict[str, Any], rating: str,
        crosscheck: dict[str, Any] | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    def rule(rid: str, ok: bool | None, detail: str,
             level: str = "fail") -> None:
        if ok is not None and not ok:
            items.append({"rule": rid, "status": level, "detail": detail})

    # —— 结构 ——
    rule("structure.title_len", 10 <= _charlen(outline["title"]) <= 40,
         f"标题 {len(outline['title'])} 字")
    rule("structure.title_comma", "，" in outline["title"],
         "结论式标题应以逗号分隔两个判断")
    rule("structure.n_views", len(views) == 4,
         f"核心观点 {len(views)} 条（应为 4）")
    all_text = outline["title"] + "".join(v["body"] for v in views) + \
        forecast["body"] + risks["body"]
    for v in views:
        rule(f"structure.{v['slot_id']}.heading_len",
             6 <= len(v["heading"]) <= 20, f"小标题{len(v['heading'])}字")
        n = _charlen(v["body"])
        rule(f"structure.{v['slot_id']}.body_len", 80 <= n <= 230,
             f"正文 {n} 字（要求 100~180，容差）")

    # —— 字数 ——
    rule("length.forecast", 60 <= _charlen(forecast["body"]) <= 200,
         f"盈利预测说明 {len(forecast['body'])} 字")

    # —— 风险提示 ——
    risk_items = [r for r in re.split(r"[;；]", risks["body"]) if r.strip()]
    rule("risk.count", 3 <= len(risk_items) <= 5, f"{len(risk_items)} 条")
    risk_shaped = sum(1 for r in risk_items if r.strip().endswith("风险"))
    rule("risk.format", risk_shaped >= 3,
         f"{risk_shaped}/{len(risk_items)} 条以'风险'结尾")

    # —— 术语口径 ——
    hits = [w for w in FORBIDDEN if w in all_text]
    rule("style.forbidden", not hits, f"禁用词 {hits}", level="warn")

    # —— 评级 ——
    rule("rating.valid", rating in RATING_WORDS, f"评级 '{rating}'")

    # —— PE 口径互查（衍生值 vs 接口值）——
    q = {f["id"].split(".")[1]: f["value"] for f in doc["facts"]
         if f["id"].startswith("quote.")}
    pe_ttm, pe_static, pe_dyn = (q.get("pe_ttm"), q.get("pe_static"),
                                 q.get("pe_dynamic"))
    latest = doc["collections"]["periods"][0] if doc["collections"]["periods"] else {}
    growing = (latest.get("netprofit_yoy_pct") or 0) > 10
    if pe_ttm and pe_static and pe_dyn:
        # 增长期正常排序 dynamic ≤ ttm ≤ static（利润越新越高）；ttm 超 static
        # 说明接口 TTM 口径可疑（本例按 2025 全年利润计）
        if growing and pe_ttm > pe_static * 1.05:
            items.append({"rule": "derived.pe_ttm_suspect", "status": "warn",
                          "detail": f"PE-TTM({pe_ttm}) 高于静态PE({pe_static})，"
                                    f"接口TTM口径疑点，正文避免引用该字段"})
        if abs(pe_ttm - pe_dyn) / max(pe_dyn, 1e-9) > 3:
            items.append({"rule": "derived.pe_spread", "status": "warn",
                          "detail": f"PE-TTM({pe_ttm}) 与动态PE({pe_dyn}) 相差超3倍，"
                                    f"利润基数低所致，正文引用估值需注明口径"})
    mean_pe = doc["collections"]["consensus"].get("mean_eps_forecast", {})
    if q.get("price") and mean_pe.get("predictThisYearEps"):
        implied = q["price"] / mean_pe["predictThisYearEps"]
        report_pe = (doc["collections"]["consensus"]["reports"][0]
                     .get("pe_forecast", {}).get("predictThisYearPe"))
        if report_pe and abs(implied - report_pe) / report_pe > 0.5:
            items.append({"rule": "derived.pe_vs_consensus", "status": "warn",
                          "detail": f"推算PE({implied:.1f}) 与券商预测PE({report_pe})"
                                    f"偏差>50%，多为EPS口径差异（摊薄/全面摊薄）"})

    if crosscheck and crosscheck.get("status") == "fail":
        items.append({"rule": "data.crosscheck", "status": "fail",
                      "detail": "数据层交叉校验失败"})

    hard = [i for i in items if i["status"] == "fail"]
    return {
        "status": "fail" if hard else ("warn" if items else "pass"),
        "items": items,
    }
