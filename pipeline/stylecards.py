"""文风卡（Style Card）：规则文字 + 百字级节选，替代"必须有范文"的文风锚点。

形态 = YAML（config/style_cards/<id>.yaml）：
  rules 注入写作 system prompt；excerpts 接在 fewshot 解析链的兜底位
  （章节/槽位无范文时顶上），让规则真正落地。
优先级（已拍板 D3）：用户反馈 > 文风卡 > 默认写作规范。
"""

import re
from pathlib import Path
from typing import Any

import yaml

from datalayer.settings import settings

# C：卡文件是几百字节小 YAML、调用频率低——缓存只有 staleness 害处
# （手改 YAML / 界面保存不重启不生效，P3 实锤），已移除 lru_cache。
CARD_ID_RE = re.compile(r"^[A-Za-z0-9_]{1,40}$")


def card_path(card_id: str) -> Path:
    """卡文件路径（id 合法性校验在此——id 直入文件名，防路径穿越）。"""
    if not CARD_ID_RE.fullmatch(card_id or ""):
        raise ValueError(f"非法文风卡 id：{card_id!r}"
                         "（只允许英数字与下划线，≤40 字符）")
    return settings.resolve(f"config/style_cards/{card_id}.yaml")


def load_card(card_id: str) -> dict[str, Any] | None:
    """读取文风卡；不存在返回 None（调用方静默降级）。"""
    try:
        with open(card_path(card_id), encoding="utf-8") as f:
            card = yaml.safe_load(f)
    except (OSError, yaml.YAMLError, ValueError):
        return None
    return card if isinstance(card, dict) and card.get("id") else None


def save_card(card: dict[str, Any]) -> dict[str, Any]:
    """新建/保存文风卡并落盘（UTF-8），写入即生效（无缓存）。
    内置卡（source=built-in）不可覆盖（复制内置卡请换 id）。"""
    card_id = str(card.get("id") or "")
    path = card_path(card_id)
    existing = load_card(card_id)
    if (existing or {}).get("source") == "built-in":
        raise PermissionError(f"内置文风卡「{card_id}」不可修改，"
                              "请改用「复制为自定义」后编辑副本")
    normalized = {
        "id": card_id,
        "name": str(card.get("name") or card_id),
        "desc": str(card.get("desc") or ""),
        "rules": str(card.get("rules") or ""),
        "excerpts": [{"text": str(e.get("text") or ""),
                      "source": str(e.get("source") or "")}
                     for e in (card.get("excerpts") or [])
                     if isinstance(e, dict) and (e.get("text") or "").strip()],
        "source": "custom",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(normalized, f, allow_unicode=True, sort_keys=False)
    return normalized


def delete_card(card_id: str) -> list[str]:
    """删除自定义文风卡。返回非空引用树清单 = 拒删（调用方回 409）；
    返回空 = 已删除。内置卡抛 PermissionError，不存在抛 FileNotFoundError。"""
    path = card_path(card_id)
    if not path.exists():
        raise FileNotFoundError(card_id)
    card = load_card(card_id) or {}
    if card.get("source") == "built-in":
        raise PermissionError(f"内置文风卡「{card_id}」不可删除")
    used = card_used_by(card_id)
    if used:
        return used
    path.unlink()
    return []


def card_used_by(card_id: str) -> list[str]:
    """引用该文风卡的树 id 清单（删除前置检查；style_card 在 spec 层）。"""
    root = settings.resolve("config/trees")
    out: list[str] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not (d / "tree.yaml").exists():
            continue
        try:
            with open(d / "tree.yaml", encoding="utf-8") as f:
                payload = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        sc = payload.get("style_card")
        meta_sc = (payload.get("tree") or {}).get("style_card")
        if sc == card_id or meta_sc == card_id:
            out.append(d.name)
    return out


def list_cards() -> list[dict[str, Any]]:
    """全部文风卡（含规则全文与节选——B1 编辑器预览需要完整内容）。"""
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
                            "rules": str(card.get("rules") or ""),
                            "excerpts": [{"text": str(e.get("text") or ""),
                                          "source": str(e.get("source") or "")}
                                         for e in (card.get("excerpts") or [])
                                         if isinstance(e, dict)],
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
