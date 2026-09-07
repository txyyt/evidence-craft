"""同行对比（东财行业板块 clist）。

板块代码来自财务接口的 BOARD_CODE；按总市值取头部 n 家做估值对标样本。
"""

from typing import Any

from datalayer.cache import cached
from datalayer.sources.base import get_json

HOSTS = ["https://push2.eastmoney.com", "https://push2delay.eastmoney.com"]
PATH = "/api/qt/clist/get"


def fetch(board_code: str, size: int = 10) -> list[dict[str, Any]]:
    def _fetch() -> list[dict[str, Any]]:
        params = {
            "pn": 1, "pz": size, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f20",       # 按总市值降序
            "fs": f"b:{board_code}",
            "fields": "f2,f3,f9,f12,f14,f20,f23",     # 价/涨跌幅/PE-TTM/代码/名/总市值/PB
        }
        from datalayer.sources.base import get_json, SourceError
        data = None
        for host in HOSTS:
            try:
                data = get_json(host + PATH, params=params,
                                referer="https://quote.eastmoney.com/")
                break
            except SourceError:
                continue
        if data is None:
            raise SourceError("同行行情源不可用")
        return [{
            "code": d["f12"], "name": d["f14"],
            "price": d.get("f2"), "pct_change": d.get("f3"),
            "pe_ttm": d.get("f9"), "pb": d.get("f23"),
            "mktcap_yi": round(d["f20"] / 1e8, 1) if d.get("f20") not in (None, "-") else None,
        } for d in data["data"]["diff"]]

    return cached("peer", f"board_{board_code}_{size}", _fetch)
