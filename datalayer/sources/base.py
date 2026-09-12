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


def get_text(
    url: str,
    params: Optional[dict] = None,
    referer: str = REFERER,
    timeout: Optional[int] = None,
) -> str:
    """GET → 按内容推断编码的文本（HTML/搜索结果页）。"""
    retries = settings.http["retries"]
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(
                url, params=params, timeout=timeout or settings.http["timeout"],
                headers={"Referer": referer}
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except Exception as e:  # noqa: BLE001 - 统一重试后抛 SourceError
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"GET {url} 失败: {last_err}")


def post_json(url: str, body: dict, timeout: Optional[int] = None) -> Any:
    """POST JSON → JSON（M7：外部检索服务等自有 API，不带东财 Referer）。"""
    try:
        resp = _session.post(url, json=body,
                             timeout=timeout or settings.http["timeout"])
        resp.raise_for_status()
        return json.loads(resp.content)
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"POST {url} 失败: {e}")


def pct(v: Any) -> Optional[float]:
    """接口返回的百分数（如 2.62 表示 2.62%）原样转 float。"""
    return round(float(v), 4) if v is not None else None


def yi(v: Any) -> Optional[float]:
    """元 → 亿元，保留 4 位。"""
    return round(float(v) / 1e8, 4) if v is not None else None
