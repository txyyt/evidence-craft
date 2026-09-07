"""财务数据（东财 datacenter）。

两个独立报表名：
- RPT_LICO_FN_CPD        业绩报表（ quarterly 主指标）
- RPT_F10_FINANCE_MAINFINADATA  F10 主要指标（更全：扣非、负债率等）

两源重叠字段（营收/归母净利）供 assemble 做交叉校验。
"""

from typing import Any

from datalayer.cache import cached
from datalayer.settings import settings
from datalayer.sources.base import get_json
from datalayer.sources.base import pct
from datalayer.sources.base import yi

DC = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


def _query(report_name: str, stock: str, referer: str, page_size: int = 8) -> list[dict]:
    secucode = f"{stock}.{'SH' if stock.startswith('6') else 'SZ'}"
    data = get_json(DC, params={
        "reportName": report_name, "columns": "ALL",
        "filter": f'(SECUCODE="{secucode}")' if "F10" in report_name
        else f'(SECURITY_CODE="{stock}")',
        "pageSize": page_size,
        "sortColumns": "REPORT_DATE" if "F10" in report_name else "REPORTDATE",
        "sortTypes": -1,
    }, referer=referer)
    return (data.get("result") or {}).get("data") or []


def fetch(stock: str) -> dict[str, Any]:
    """返回 {"performance": [...], "f10_main": [...]}，按报告期倒序。"""

    def _fetch() -> dict[str, Any]:
        perf = _query("RPT_LICO_FN_CPD", stock, "https://data.eastmoney.com/")
        f10 = _query("RPT_F10_FINANCE_MAINFINADATA", stock,
                     "https://emweb.securities.eastmoney.com/")
        return {"performance": perf, "f10_main": f10}

    return cached("financial", f"dc_{stock}", _fetch)


def latest_periods(stock: str, n: int = 4) -> list[dict[str, Any]]:
    """整理最近 n 个报告期的核心指标（用于事实抽取与同行对比）。"""
    raw = fetch(stock)
    out = []
    for row in raw["performance"][:n]:
        out.append({
            "period": row.get("QDATE") or row["REPORTDATE"][:10],
            "period_name": row.get("DATATYPE"),
            "revenue_yi": yi(row.get("TOTAL_OPERATE_INCOME")),
            "netprofit_yi": yi(row.get("PARENT_NETPROFIT")),
            "revenue_yoy_pct": pct(row.get("YSTZ")),
            "netprofit_yoy_pct": pct(row.get("SJLTZ")),
            "gross_margin_pct": pct(row.get("XSMLL")),
            "roe_weighted_pct": pct(row.get("WEIGHTAVG_ROE")),
            "eps": row.get("BASIC_EPS"),
            "bps": row.get("BPS"),
            "board_code": row.get("BOARD_CODE"),
            "board_name": row.get("BOARD_NAME"),
            "notice_date": (row.get("NOTICE_DATE") or "")[:10],
        })
    # F10 补充扣非与财务结构（仅最新期）
    if raw["f10_main"] and out:
        f = raw["f10_main"][0]
        out[0].update({
            "deduct_netprofit_yi": yi(f.get("KCFJCXSYJLR")),
            "net_margin_pct": pct(f.get("XSJLL")),
            "debt_ratio_pct": pct(f.get("ZCFZL")),
        })
    return out


def board_code(stock: str) -> str:
    rows = latest_periods(stock, 1)
    if not rows:
        raise RuntimeError(f"无财务数据，取不到行业板块: {stock}")
    return rows[0]["board_code"]
