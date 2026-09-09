"""web 类 adapter 扩展：开放出口验证（登录墙策略第一层）。

USGS 地震 API 为无鉴权公开接口——用它验证"跨厂商 web 源"的接入模式
（公开 API > 公开文件 > 免登录网页 > 登录网页，见《多部门泛化方案》4.2）。
默认不绑定到任何部门，注册在册供需要全球地震数据的报告使用。
"""

from datetime import datetime, timedelta
from typing import Any

from datalayer.adapters.base import AdapterResult, SourceAdapter
from datalayer.sources.base import get_json


class UsgsEarthquakeAdapter(SourceAdapter):
    """美国地质调查局（USGS）地震摘要 API。params：
    min_magnitude（默认 4.5）、days（回溯天数，默认 30）。
    产出事实：条数、最大震级及其条目。"""
    key = "usgs_earthquakes"
    kind = "web"
    summary = "USGS 全球地震统计：指定震级与回溯天数，产出条数与最大地震"
    param_schema = [
        {"k": "min_magnitude", "label": "最小震级", "type": "number",
         "ph": "4.5"},
        {"k": "days", "label": "回溯天数", "type": "number", "ph": "30"},
    ]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        min_mag = float(params.get("min_magnitude", 4.5))
        days = int(params.get("days", 30))
        end = datetime.utcnow().strftime("%Y-%m-%d")
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = ("https://earthquake.usgs.gov/fdsnws/event/1/count"
               f"?format=text&starttime={start}&endtime={end}"
               f"&minmagnitude={min_mag}")
        count = get_json(url)
        if not str(count).strip().isdigit():
            raise ValueError(f"USGS count 接口返回异常：{str(count)[:50]}")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        facts = [{
            "id": f"usgs.count.{min_mag}", "name": f"全球 M{min_mag}+ 地震条数",
            "value": float(count), "unit": "条",
            "source": "USGS/earthquake.usgs.gov", "as_of": now}]
        if float(count) > 0:
            detail = get_json(
                "https://earthquake.usgs.gov/fdsnws/event/1/query"
                f"?format=geojson&starttime={start}&endtime={end}"
                f"&minmagnitude={min_mag}&orderby=magnitude")
            feats = detail.get("features") or []
            if feats:
                mag = feats[0]["properties"].get("mag")
                place = feats[0]["properties"].get("place", "")
                if mag is not None:
                    facts.append({
                        "id": f"usgs.maxmag.{min_mag}",
                        "name": f"本期最大地震（{place}）", "value": float(mag),
                        "unit": "级", "source": "USGS/earthquake.usgs.gov",
                        "as_of": now})
        return AdapterResult(facts=facts)
