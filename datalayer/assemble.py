"""组装 facts.json：标量事实（可对账）+ 集合数据 + 交叉校验。"""

from datetime import datetime
from typing import Any

from datalayer.sources import announcement, consensus, financial, mainop, news, peer, quote, statements
from datalayer.sources.base import SourceError


def build(stock: str, industry_keywords: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (facts_doc, crosscheck)。任何单个源失败不阻塞整体，记入 warnings。"""
    warnings: list[str] = []

    q: dict[str, Any] = {}
    try:
        q = quote.fetch(stock)
    except SourceError as e:
        warnings.append(f"行情源失败: {e}")

    periods: list[dict[str, Any]] = []
    try:
        periods = financial.latest_periods(stock)
    except SourceError as e:
        warnings.append(f"财务源失败: {e}")

    board_code = periods[0]["board_code"] if periods else None
    peers: list[dict[str, Any]] = []
    if board_code:
        try:
            peers = peer.fetch(board_code)
        except SourceError as e:
            warnings.append(f"同行源失败: {e}")

    anns: list[dict[str, Any]] = []
    try:
        anns = announcement.fetch_key_with_content(stock)
        for a in anns:  # 长公告预生成行业摘要（募集说明书等行业章节段落）
            a["digest"] = announcement.industry_digest(a["content"]["text"])
    except SourceError as e:
        warnings.append(f"公告源失败: {e}")

    industry_news: list[dict[str, Any]] = []
    if industry_keywords:
        try:
            industry_news = news.fetch_industry(industry_keywords)
        except SourceError as e:
            warnings.append(f"行业新闻源失败: {e}")

    cons: dict[str, Any] = {}
    try:
        cons = consensus.consensus(stock)
    except SourceError as e:
        warnings.append(f"研报预期源失败: {e}")

    composition: dict[str, Any] = {}
    try:
        composition = mainop.fetch(stock)
    except SourceError as e:
        warnings.append(f"主营构成源失败: {e}")

    stmts: dict[str, list[dict[str, Any]]] = {}
    try:
        stmts = statements.fetch(stock)
    except SourceError as e:
        warnings.append(f"三表明细源失败: {e}")

    doc = {
        "meta": {
            "stock": stock,
            "name": q.get("name"),
            "industry": q.get("industry") or (periods[0]["board_name"] if periods else None),
            "board_code": board_code,
            "latest_period": periods[0]["period"] if periods else None,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "warnings": warnings,
        },
        "facts": _scalar_facts(q, periods, stmts),
        "collections": {
            "periods": periods,
            "mainop": composition,
            "statements": stmts,
            "announcements": anns,
            "consensus": cons,
            "peers": peers,
            "news": news.fetch(stock),
            "industry_news": industry_news,
        },
    }
    return doc, check_financial(stock)


def _scalar_facts(q: dict[str, Any], periods: list[dict[str, Any]],
                  stmts: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """标量事实：M2 的对账层逐条核验正文数字。id 全局唯一、稳定可引用。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    facts: list[dict[str, Any]] = []

    quote_facts = [
        ("price", "现价", "元", q.get("price")),
        ("pe_ttm", "市盈率TTM", "倍", q.get("pe_ttm")),
        ("pe_dynamic", "动态市盈率", "倍", q.get("pe_dynamic")),
        ("pb", "市净率", "倍", q.get("pb")),
        ("mktcap_total", "总市值", "亿元", q.get("total_mktcap_yi")),
        ("mktcap_float", "流通市值", "亿元", q.get("float_mktcap_yi")),
    ]
    for fid, name, unit, value in quote_facts:
        if value is not None:
            facts.append({"id": f"quote.{fid}", "name": name, "value": value,
                          "unit": unit, "source": "eastmoney/push2", "as_of": now})

    metric_facts = [
        ("revenue_yi", "营业收入", "亿元"),
        ("netprofit_yi", "归母净利润", "亿元"),
        ("revenue_yoy_pct", "营收同比", "%"),
        ("netprofit_yoy_pct", "归母净利同比", "%"),
        ("gross_margin_pct", "毛利率", "%"),
        ("roe_weighted_pct", "加权ROE", "%"),
        ("eps", "基本每股收益", "元"),
    ]
    for p in periods:
        for key, name, unit in metric_facts:
            v = p.get(key)
            if v is not None:
                facts.append({
                    "id": f"fin.{p['period']}.{key}", "name": name,
                    "value": v, "unit": unit,
                    "source": "eastmoney/datacenter/RPT_LICO_FN_CPD",
                    "as_of": p["notice_date"],
                })

    # 三表补充：现金流质量与费用结构是研报常用论述点（最新期）
    # 注意期间格式差异：主指标接口为 "2026Q2"，三表接口为 "2026-06-30"
    if stmts.get("income") and periods:
        period = stmts["income"][0]["period"]
        for stmt_key, fact_id, fact_name in [
            ("cashflow", "netcash_operate_yi", "经营现金流净额"),
            ("income", "finance_expense_yi", "财务费用"),
            ("income", "deduct_parent_netprofit_yi", "扣非归母净利润"),
        ]:
            for row in stmts[stmt_key]:
                if row["period"] != period:
                    continue
                for item in row["items"]:
                    if item["name"] == fact_name and item["value_yi"] is not None:
                        facts.append({
                            "id": f"fin.{period}.{fact_id}", "name": fact_name,
                            "value": item["value_yi"], "unit": "亿元",
                            "source": "eastmoney/datacenter/RPT_F10_FINANCE_G*",
                            "as_of": period,
                        })
    return facts


def check_financial(stock: str, tolerance_pct: float = 0.5) -> dict[str, Any]:
    """交叉校验：业绩报表(RPT_LICO_FN_CPD) vs F10 主要指标，最新报告期。

    两表来自东财不同数据管线，字段口径独立维护；营收/归母净利偏差超过
    tolerance_pct 记 fail。巨潮权威源当前 502，接入后在此追加公告数校验。
    """
    raw = financial.fetch(stock)
    perf = raw["performance"][0] if raw["performance"] else {}
    f10 = raw["f10_main"][0] if raw["f10_main"] else {}
    items: list[dict[str, Any]] = []

    pairs = [
        ("revenue", "营业收入", perf.get("TOTAL_OPERATE_INCOME"), f10.get("TOTALOPERATEREVE")),
        ("netprofit", "归母净利润", perf.get("PARENT_NETPROFIT"), f10.get("PARENTNETPROFIT")),
    ]
    for fid, name, a, b in pairs:
        if a is None or b is None:
            items.append({"id": fid, "name": name, "status": "warn", "reason": "一方缺失"})
            continue
        diff_pct = abs(a - b) / max(abs(a), abs(b)) * 100
        items.append({
            "id": fid, "name": name,
            "source_a": a, "source_b": b,
            "diff_pct": round(diff_pct, 4),
            "status": "pass" if diff_pct <= tolerance_pct else "fail",
        })

    status = "fail" if any(i["status"] == "fail" for i in items) else \
             "warn" if any(i["status"] == "warn" for i in items) else "pass"
    return {
        "stock": stock,
        "period": (perf.get("REPORTDATE") or "")[:10],
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "tolerance_pct": tolerance_pct,
        "authoritative_source": "cninfo（当前不可用，待接入）",
        "items": items,
        "status": status,
    }
