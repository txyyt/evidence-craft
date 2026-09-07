"""财务三表明细（东财 F10 通用三表）。

只整理研报常用科目（全科目 126 个字段意义不大，关键科目进 facts 供对账）：
利润表（费用结构）、资产负债表（资产/负债结构）、现金流量表（现金流质量）。
"""

from typing import Any

from datalayer.cache import cached
from datalayer.settings import settings
from datalayer.sources.base import get_json
from datalayer.sources.base import yi

DC = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

INCOME = [
    ("TOTAL_OPERATE_INCOME", "营业总收入"), ("OPERATE_COST", "营业成本"),
    ("SALE_EXPENSE", "销售费用"), ("MANAGE_EXPENSE", "管理费用"),
    ("FINANCE_EXPENSE", "财务费用"), ("RESEARCH_EXPENSE", "研发费用"),
    ("OPERATE_PROFIT", "营业利润"), ("TOTAL_PROFIT", "利润总额"),
    ("NETPROFIT", "净利润"), ("PARENT_NETPROFIT", "归母净利润"),
    ("DEDUCT_PARENT_NETPROFIT", "扣非归母净利润"),
]
BALANCE = [
    ("MONETARYFUNDS", "货币资金"), ("ACCOUNTS_RECE", "应收账款"),
    ("INVENTORY", "存货"), ("TOTAL_CURRENT_ASSETS", "流动资产"),
    ("TOTAL_ASSETS", "总资产"), ("SHORT_LOAN", "短期借款"),
    ("ACCOUNTS_PAYABLE", "应付账款"), ("TOTAL_CURRENT_LIAB", "流动负债"),
    ("TOTAL_LIABILITIES", "总负债"), ("TOTAL_PARENT_EQUITY", "归母股东权益"),
]
CASHFLOW = [
    ("SALES_SERVICES", "销售商品收现"), ("NETCASH_OPERATE", "经营现金流净额"),
    ("NETCASH_INVEST", "投资现金流净额"), ("NETCASH_FINANCE", "筹资现金流净额"),
    ("CONSTRUCT_LONG_ASSET", "购建长期资产支出"),
]


def _fetch_statement(report_name: str, mapping: list[tuple[str, str]],
                     stock: str, n_periods: int) -> list[dict[str, Any]]:
    secucode = f"{stock}.{'SH' if stock.startswith('6') else 'SZ'}"
    d = get_json(DC, params={
        "reportName": report_name, "columns": "ALL",
        "filter": f'(SECUCODE="{secucode}")', "pageSize": n_periods,
        "sortColumns": "REPORT_DATE", "sortTypes": -1,
    }, referer="https://emweb.securities.eastmoney.com/")
    rows = (d.get("result") or {}).get("data") or []
    return [{
        "period": r["REPORT_DATE"][:10],
        "period_name": r.get("REPORT_DATE_NAME"),
        "items": [{"id": fid, "name": name, "value_yi": yi(r.get(fid))}
                  for fid, name in mapping if r.get(fid) is not None],
    } for r in rows]


def fetch(stock: str, n_periods: int = 4) -> dict[str, list[dict[str, Any]]]:
    def _fetch() -> dict[str, list[dict[str, Any]]]:
        return {
            "income": _fetch_statement("RPT_F10_FINANCE_GINCOME", INCOME, stock, n_periods),
            "balance": _fetch_statement("RPT_F10_FINANCE_GBALANCE", BALANCE, stock, n_periods),
            "cashflow": _fetch_statement("RPT_F10_FINANCE_GCASHFLOW", CASHFLOW, stock, n_periods),
        }

    return cached("financial", f"statements_{stock}_{n_periods}", _fetch)
