"""文件缓存：artifacts/cache/<category>/<key>.json，带 TTL。

不同源按新鲜度给不同 TTL（settings.cache.ttl_minutes），payload 永远存
adapter 的最终返回值，读缓存的调用方无感知。
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from datalayer.settings import settings


def _key(category: str, raw: str) -> Path:
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    return settings.resolve(settings.cache["dir"]) / category / f"{h}.json"


def cached(category: str, key_raw: str, fetch: Callable[[], Any]) -> Any:
    path = _key(category, key_raw)
    ttl = settings.cache["ttl_minutes"].get(category, 60) * 60
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    payload = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return payload
