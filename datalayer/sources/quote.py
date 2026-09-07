"""行情与估值（东财 push2，实时）。

fltt=2 让接口直接返回小数值；价格字段原始值乘以 100/1000 的坑通过 fltt 规避。
"""

from typing import Any

from datalayer.cache import cached
from datalayer.sources.base import get_json
from datalayer.sources.base import pct

# 主站限频时降级到延时行情节点（约15分钟延迟，报告生成场景可接受）
HOSTS = ["https://push2.eastmoney.com", "https://push2delay.eastmoney.com"]
PATH = "/api/qt/stock/get"

# 字段 → 语义映射（东财 push2 字段表）
FIELDS = {
    "f57": "code", "f58": "name",
    "f43": "price", "f44": "high", "f45": "low", "f46": "open",
    "f60": "prev_close", "f170": "pct_change",
    "f116": "total_mktcap", "f117": "float_mktcap",
    "f162": "pe_dynamic", "f163": "pe_ttm", "f164": "pe_static",
    "f167": "pb", "f168": "turnover_rate", "f127": "industry",
}


def _get(host_i: int, params: dict) -> dict:
    from datalayer.sources.base import get_json, SourceError
    if host_i >= len(HOSTS):
        raise SourceError(f"行情源全部不可用: {HOSTS}")
    try:
        return get_json(HOSTS[host_i] + PATH, params=params,
                        referer="https://quote.eastmoney.com/")
    except SourceError:
        return _get(host_i + 1, params)


def fetch(stock: str) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        params = {
            "secid": f"{'1' if stock.startswith('6') else '0'}.{stock}",
            "invt": "2", "fltt": "2",
            "fields": ",".join(FIELDS),
        }
        data = _get(0, params)["data"]
        out = {FIELDS[k]: data.get(k) for k in FIELDS if data.get(k) is not None}
        out["total_mktcap_yi"] = round(out["total_mktcap"] / 1e8, 2)
        out["float_mktcap_yi"] = round(out["float_mktcap"] / 1e8, 2)
        return out

    return cached("quote", f"stock_get_{stock}", _fetch)


def pct_change(stock: str) -> Any:
    return pct(fetch(stock).get("pct_change"))
