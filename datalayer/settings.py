"""加载 config/settings.yaml，全模块共享。"""

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        """（重）读 settings.yaml。保持单例身份不变，各处引用自动看到新值——
        server 层写回配置后调用，实现页面上改模型即生效。"""
        with open(_ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
            cfg: dict[str, Any] = yaml.safe_load(f)
        self.raw = cfg
        self.stock: str = cfg["stock"]
        self.http: dict = cfg["http"]
        self.cache: dict = cfg["cache"]
        self.artifacts_dir: str = cfg["artifacts_dir"]
        self.model: dict = cfg.get("model", {})
        self.databases: dict = cfg.get("databases", {})   # db_ref → 连接配置（M7）
        self.rag: dict = cfg.get("rag", {})               # 外部检索服务配置（M7）

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else _ROOT / path


def mask_key(key: str) -> str:
    """密钥掩码：API 一律不回显完整 key。"""
    if not key:
        return ""
    return f"{key[:6]}****{key[-4:]}" if len(key) > 12 else "****"


def update_model_section(base_url: str, model: str, api_key: str,
                         reasoning_effort: str) -> None:
    """写回 settings.yaml 的 model 段（ruamel 往返保注释）；api_key 空 = 保留现值，
    reasoning_effort 空 = 删除该键（用服务端默认档）。原子替换，写完由调用方 reload。"""
    from ruamel.yaml import YAML

    path = _ROOT / "config" / "settings.yaml"
    ry = YAML()
    ry.preserve_quotes = True
    with open(path, encoding="utf-8") as f:
        cfg = ry.load(f)
    m = cfg.get("model")
    if m is None:
        m = {}
        cfg["model"] = m
    m["base_url"] = base_url
    m["model"] = model
    if api_key:
        m["api_key"] = api_key
    if reasoning_effort:
        m["reasoning_effort"] = reasoning_effort
    elif "reasoning_effort" in m:
        del m["reasoning_effort"]
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        ry.dump(cfg, f)
    tmp.replace(path)


settings = Settings()
