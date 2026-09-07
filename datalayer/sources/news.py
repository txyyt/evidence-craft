"""个股资讯/新闻（东财 getListInfo，quote 页"新闻"栏同源）。

只有标题+链接（无摘要字段）；M2 需要摘要时按 Art_Url 抓正文页再提取，
M1 先以标题流覆盖"公司最近在发生什么"的观察需求。
"""

from typing import Any

from datalayer.cache import cached
from datalayer.sources.base import get_json

URL = "https://np-listapi.eastmoney.com/comm/web/getListInfo"


def fetch(stock: str, page_size: int = 30) -> list[dict[str, Any]]:
    def _fetch() -> list[dict[str, Any]]:
        secid = f"{'1' if stock.startswith('6') else '0'}.{stock}"
        data = _fetch_page(secid, page_size)
        return [{
            "title": it["Art_Title"],
            "time": it["Art_ShowTime"],
            "url": it.get("Art_Url") or it.get("Art_OriginUrl"),
            "source": it.get("Np_dst"),
        } for it in items_of(data)]

    return cached("news", f"stock_{stock}_{page_size}", _fetch)


def _fetch_page(secid_or_none: str | None, page_size: int) -> dict:
    from datalayer.sources.base import get_json
    params = {
        "client": "web", "biz": "web_news_col", "column": 350,
        "order": 1, "needInteractData": 0,
        "page_index": 1, "page_size": page_size,
        "req_trace": str(int(__import__("time").time() * 1000)),
        "fields": "code,showTime,title,mediaName,uniqueUrl", "types": "1,20",
    }
    if secid_or_none:
        params.pop("column")
        params.pop("biz")
        params.pop("types")
        params.pop("fields")
        params.update({"mTypeAndCode": secid_or_none, "type": 1,
                       "pageSize": page_size, "pageIndex": 1})
        return get_json("https://np-listapi.eastmoney.com/comm/web/getListInfo",
                        params=params, referer="https://quote.eastmoney.com/")
    return get_json("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns",
                    params=params, referer="https://quote.eastmoney.com/")


def items_of(data: dict) -> list[dict]:
    d = data.get("data") or {}
    return d.get("list") or []


def fetch_industry(keywords: list[str], page_size: int = 100) -> list[dict[str, Any]]:
    """行业新闻扫描：全市场要闻流按行业关键词过滤标题。

    适合捕捉政策/价格类行业事件（CORSIA、SAF 投产等）；无命中返回空列表，
    上游按"行业素材不足"降级。
    """

    def _fetch() -> list[dict[str, Any]]:
        data = _fetch_page(None, page_size)
        out = []
        for it in items_of(data):
            title = it.get("title") or ""
            hit = next((k for k in keywords if k.lower() in title.lower()), None)
            if hit:
                out.append({"title": title, "time": it.get("showTime", ""),
                            "url": it.get("uniqueUrl"), "keyword": hit})
        return out

    return cached("news", f"industry_{'_'.join(keywords[:4])}_{page_size}", _fetch)
