"""共享 HTTP 基础：会话、重试、JSON 解析。

注意统一用 resp.content 按 UTF-8 解析 JSON（requests 对无 charset 响应的
自动推断在部分中文接口上会出错）。
"""

import json
import time
from typing import Any, Optional

import requests

from datalayer.settings import settings

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
})

REFERER = "https://data.eastmoney.com/"


class SourceError(RuntimeError):
    """数据源不可用或返回结构异常。"""


def get_json(
    url: str,
    params: Optional[dict] = None,
    referer: str = REFERER,
) -> Any:
    retries = settings.http["retries"]
    timeout = settings.http["timeout"]
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(
                url, params=params, timeout=timeout, headers={"Referer": referer}
            )
            resp.raise_for_status()
            return json.loads(resp.content)
        except Exception as e:  # noqa: BLE001 - 统一重试后抛 SourceError
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"GET {url} 失败: {last_err}")


def pct(v: Any) -> Optional[float]:
    """接口返回的百分数（如 2.62 表示 2.62%）原样转 float。"""
    return round(float(v), 4) if v is not None else None


def yi(v: Any) -> Optional[float]:
    """元 → 亿元，保留 4 位。"""
    return round(float(v) / 1e8, 4) if v is not None else None
