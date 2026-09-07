"""加载 config/settings.yaml，全模块共享。"""

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self) -> None:
        with open(_ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
            cfg: dict[str, Any] = yaml.safe_load(f)
        self.raw = cfg
        self.stock: str = cfg["stock"]
        self.http: dict = cfg["http"]
        self.cache: dict = cfg["cache"]
        self.artifacts_dir: str = cfg["artifacts_dir"]
        self.model: dict = cfg.get("model", {})

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else _ROOT / path


settings = Settings()
