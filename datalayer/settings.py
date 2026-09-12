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
        self.stock: str = cfg.get("stock", "")           # 旧版 CLI 缺省对象，可选
        self.http: dict = cfg["http"]
        self.cache: dict = cfg["cache"]
        self.artifacts_dir: str = cfg["artifacts_dir"]
        self.model: dict = cfg.get("model", {})
        # F3 模型分档（可选段）：model_tiers.{fast,quality} 只写覆盖字段
        # （base_url/api_key/model/reasoning_effort），缺省字段继承 model 段；
        # tier_roles 指定调用角色→档位（缺省 extract=fast，write=quality）。
        # 两段都不配置时一切行为与单模型完全一致。
        self.model_tiers: dict = cfg.get("model_tiers") or {}
        self.tier_roles: dict = cfg.get("tier_roles") or {}
        self.pipeline: dict = cfg.get("pipeline", {})     # 流水线参数（judge 门槛/修订轮数）
        self.databases: dict = cfg.get("databases", {})   # 全局连接：数据库（凭据只放本文件）
        self.rag: dict = cfg.get("rag", {})               # 全局连接：外部检索服务
        self.web_search: dict = cfg.get("web_search", {})  # 全局连接：付费搜索 API（可选）

    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else _ROOT / path


def update_sections(sections: dict) -> None:
    """写回 settings.yaml 的若干顶层段（ruamel 往返保注释）；写完由调用方 reload。"""
    from ruamel.yaml import YAML

    path = _ROOT / "config" / "settings.yaml"
    ry = YAML()
    ry.preserve_quotes = True
    with open(path, encoding="utf-8") as f:
        cfg = ry.load(f)
    for key, value in sections.items():
        cfg[key] = value
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        ry.dump(cfg, f)
    tmp.replace(path)


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
