"""文风卡（Style Card）：规则文字 + 百字级节选，替代"必须有范文"的文风锚点。

形态 = YAML（config/style_cards/<id>.yaml）：
  rules 注入写作 system prompt；excerpts 接在 fewshot 解析链的兜底位
  （章节/槽位无范文时顶上），让规则真正落地。
优先级（已拍板 D3）：用户反馈 > 文风卡 > 默认写作规范。
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from datalayer.settings import settings


@lru_cache(maxsize=32)
def _card_path(card_id: str) -> str:
    return str(settings.resolve(f"config/style_cards/{card_id}.yaml"))


@lru_cache(maxsize=32)
def load_card(card_id: str) -> dict[str, Any] | None:
    """读取文风卡；不存在返回 None（调用方静默降级）。"""
    try:
        with open(_card_path(card_id), encoding="utf-8") as f:
            card = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    return card if isinstance(card, dict) and card.get("id") else None


def list_cards() -> list[dict[str, Any]]:
    root = settings.resolve("config/style_cards")
    out = []
    if root.exists():
        for p in sorted(root.glob("*.yaml")):
            try:
                with open(p, encoding="utf-8") as f:
                    card = yaml.safe_load(f)
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(card, dict) and card.get("id"):
                out.append({"id": card["id"], "name": card.get("name", card["id"]),
                            "desc": card.get("desc", ""),
                            "source": card.get("source", "built-in"),
                            "n_excerpts": len(card.get("excerpts") or [])})
    return out


def apply(spec, fewshot: str) -> tuple[str, str]:
    """文风卡应用：返回 (rules_block, fewshot')。
    - rules_block 注入 system prompt（【文风】块）；未配置文风卡返回空串。
    - fewshot' = 原范文非空则原样保留；为空则用文风卡节选兜底（解析链末位）。"""
    card = load_card(getattr(spec, "style_card", None) or "")
    if card is None:
        return "", fewshot
    rules = str(card.get("rules") or "").strip()
    block = f"【文风】（{card.get('name', card['id'])}）\n{rules}" if rules else ""
    if not fewshot:
        lines = [str(e.get("text", "")).strip()
                 for e in (card.get("excerpts") or []) if e.get("text")]
        if lines:
            fewshot = "【文风节选】（学其句式与口吻，不引用其内容事实）\n" \
                      + "\n---\n".join(lines)
    return block, fewshot
