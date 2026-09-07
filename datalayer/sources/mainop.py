"""主营构成（东财 F10，分产品/地区/行业收入拆分）。

研报论述"哪个业务在修复"的数据基础：每个维度的收入、占比、毛利率。
"""

from typing import Any

from datalayer.cache import cached
from datalayer.sources.base import get_json
from datalayer.sources.base import pct
from datalayer.sources.base import yi

DC = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
TYPE_NAMES = {"1": "by_industry", "2": "by_product", "3": "by_region"}


def fetch(stock: str) -> dict[str, Any]:
    """返回最新报告期的主营构成：{"period", "report_name", "by_product": [...],
    "by_region": [...], "by_industry": [...]}。"""

    def _fetch() -> dict[str, Any]:
        secucode = f"{stock}.{'SH' if stock.startswith('6') else 'SZ'}"
        d = get_json(DC, params={
            "reportName": "RPT_F10_FN_MAINOP", "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")', "pageSize": 60,
            "sortColumns": "REPORT_DATE", "sortTypes": -1,
        }, referer="https://emweb.securities.eastmoney.com/")
        rows = (d.get("result") or {}).get("data") or []
        if not rows:
            return {}
        latest = rows[0]["REPORT_DATE"][:10]
        out: dict[str, Any] = {"period": latest,
                               "report_name": rows[0].get("REPORT_NAME")}
        for r in rows:
            if r["REPORT_DATE"][:10] != latest:
                continue
            # "其中:"行是父行的子集（嵌套明细），入集合会重复计算，剔除
            name = (r.get("ITEM_NAME") or "").strip()
            if name.startswith("其中"):
                continue
            dim = TYPE_NAMES.get(str(r.get("MAINOP_TYPE")))
            if not dim:
                continue
            raw_margin = (r.get("GROSS_RPOFIT_RATIO") or 0) * 100
            margin = pct(raw_margin) if raw_margin else None  # 0.0 = 未披露
            out.setdefault(dim, []).append({
                "name": name,
                "income_yi": yi(r.get("MAIN_BUSINESS_INCOME")),
                "income_ratio_pct": pct((r.get("MBI_RATIO") or 0) * 100),
                "gross_margin_pct": margin,
            })
        for dim in out:
            if dim.startswith("by_"):
                out[dim].sort(key=lambda x: x["income_yi"] or 0, reverse=True)
        return out

    return cached("financial", f"mainop_v2_{stock}", _fetch)
