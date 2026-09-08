"""组装 facts.json：标量事实（可对账）+ 集合数据 + 交叉校验。

M7 起组装逻辑迁至 datalayer/registry.py（按部门 profile 驱动）；本模块保留：
- check_financial：财务双源交叉校验（stock_demo profile 的 crosschecks 项）
- build：股票场景兼容入口（等价于 stock_demo 部门组装）
"""

from datetime import datetime
from typing import Any

from datalayer.sources import financial
from datalayer.sources.base import SourceError


def build(stock: str, industry_keywords: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """兼容入口：stock_demo 部门组装。industry_keywords 参数已废弃
    （关键词迁至部门 profile 的 vocabulary）。"""
    from datalayer.registry import run_department
    return run_department("stock_demo", {"stock": stock})


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
