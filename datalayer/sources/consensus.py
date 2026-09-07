"""券商盈利预测一致预期（东财 reportapi）。

附带产出：90 天内各券商对本股的预测 EPS/PE 与评级分布，以及个股研报元数据
（M5 评估时用 infoCode 拉对标范文全文）。
"""

from collections import Counter
from typing import Any

from datalayer.cache import cached
from datalayer.sources.base import get_json

from datetime import date, timedelta

LIST_URL = "https://reportapi.eastmoney.com/report/list"


def fetch(stock: str, days: int = 90) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        end = date.today()
        data = get_json(LIST_URL, params={
            "pageSize": 50, "pageNo": 1, "qType": 0, "code": stock,
            "beginTime": (end - timedelta(days=days)).isoformat(),
            "endTime": end.isoformat(),
        })
        reports = [{
            "title": r["title"], "org": r["orgSName"],
            "date": r["publishDate"][:10], "rating": r.get("sRatingName") or r.get("emRatingName"),
            "info_code": r["infoCode"],
            "eps_forecast": {k: _f(r.get(k)) for k in
                             ("predictThisYearEps", "predictNextYearEps", "predictNextTwoYearEps")},
            "pe_forecast": {k: _f(r.get(k)) for k in
                            ("predictThisYearPe", "predictNextYearPe", "predictNextTwoYearPe")},
        } for r in data.get("data") or []]
        return {"reports": reports, "count": data.get("hits", len(reports))}

    return cached("consensus", f"list_{stock}_{days}", _fetch)


def consensus(stock: str) -> dict[str, Any]:
    """一致预期 = 各券商预测均值（按预测年份分桶）+ 评级分布。"""
    doc = fetch(stock)
    reports = [r for r in doc["reports"] if any(v for v in r["eps_forecast"].values())]
    means: dict[str, float | None] = {}
    for key in ("predictThisYearEps", "predictNextYearEps", "predictNextTwoYearEps"):
        vals = [r["eps_forecast"][key] for r in reports if r["eps_forecast"][key]]
        means[key] = round(sum(vals) / len(vals), 3) if vals else None
    ratings = Counter(r["rating"] for r in doc["reports"] if r["rating"])
    return {
        "n_orgs": len(reports),
        "mean_eps_forecast": means,
        "rating_dist": dict(ratings),
        "reports": doc["reports"],
    }


def _f(v: Any) -> float | None:
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None
