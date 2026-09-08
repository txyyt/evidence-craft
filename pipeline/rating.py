"""规则评级（stock_demo 部门逻辑，M7 收进部门配置）。

v1 规则：今年预测 PE 相对板块中位数折价 15% 以上 → 增持，溢价 15% 以上 → 中性，
其余按业绩同比 >30% 分挡。从 html_report 拆出：评级是业务逻辑，不属于版式。
"""

from typing import Any


def rule_rating(doc: dict[str, Any]) -> str:
    q = {f["id"].split(".")[1]: f["value"] for f in doc["facts"]
         if f["id"].startswith("quote.")}
    mean = doc["collections"].get("consensus", {}).get("mean_eps_forecast", {})
    eps = mean.get("predictThisYearEps")
    peers_pe = sorted(p["pe_ttm"] for p in doc["collections"].get("peers") or []
                      if p.get("pe_ttm"))
    if not (eps and q.get("price") and peers_pe):
        return "未评级"
    pe = q["price"] / eps
    median = peers_pe[len(peers_pe) // 2]
    if pe < median * 0.85:
        return "增持"
    if pe > median * 1.15:
        return "中性"
    latest = (doc["collections"].get("periods") or [{}])[0]
    return "增持" if (latest.get("netprofit_yoy_pct") or 0) > 30 else "中性"
