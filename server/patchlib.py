"""spec patch 引擎：路径语法 / 操作应用 / 深层 diff。

路径语法：`sections[0].view_slots[1].fewshot`（点号 + 下标）。对话修改与
手动修改都落到"整份 spec + pydantic 校验"，patch 只是 UI 层的呈现形式。
"""

import copy
import re
from typing import Any

_TOKEN_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def _tokens(path: str) -> list[tuple[str, Any]]:
    """'sections[0].title' → [('key','sections'), ('idx',0), ('key','title')]。"""
    if not path or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\[\d+]|(\.[A-Za-z_][A-Za-z0-9_]*))*", path):
        raise ValueError(f"非法路径：{path!r}")
    first = path.split("[", 1)[0].split(".", 1)[0]
    toks: list[tuple[str, Any]] = [("key", first)]
    for m in _TOKEN_RE.finditer(path):
        if m.group(1):
            toks.append(("key", m.group(1)))
        else:
            toks.append(("idx", int(m.group(2))))
    return toks


def _get(obj: Any, toks) -> Any:
    for kind, k in toks:
        obj = obj[k]
    return obj


def apply_ops(spec: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """在深拷贝上应用操作列表；任一失败抛 ValueError（调用方报给 LLM 重试）。

    set 语义下缺失的中间 dict 自动创建（如对尚无 check 的章节设
    sections[0].check.body_len）；del/越界一律报错不静默。
    """
    out = copy.deepcopy(spec)
    for op in ops:
        what, path = op.get("op"), str(op.get("path", ""))
        toks = _tokens(path)
        if what == "set":
            cur: Any = out
            for i, (kind, k) in enumerate(toks[:-1]):
                nxt_is_idx = toks[i + 1][0] == "idx"
                if isinstance(cur, dict):
                    if cur.get(k) is None:
                        cur[k] = [] if nxt_is_idx else {}
                    cur = cur[k]
                elif isinstance(cur, list):
                    if not isinstance(k, int) or k >= len(cur) or cur[k] is None:
                        raise ValueError(f"路径不存在：{path}")
                    cur = cur[k]
                else:
                    raise ValueError(f"父节点不是容器：{path}")
            parent, last = cur, toks[-1][1]
            if isinstance(parent, dict):
                parent[last] = op.get("value")
            elif isinstance(parent, list) and isinstance(last, int) \
                    and last < len(parent):
                parent[last] = op.get("value")
            else:
                raise ValueError(f"父节点不是容器：{path}")
        elif what == "del":
            if not toks:
                raise ValueError("del 需要具体路径")
            try:
                parent = _get(out, toks[:-1])
            except (KeyError, IndexError, TypeError) as e:
                raise ValueError(f"路径不存在：{path}") from e
            last = toks[-1][1]
            if not isinstance(parent, (dict, list)) or last not in parent:
                raise ValueError(f"路径不存在：{path}")
            del parent[last]
        elif what == "insert":
            parent = _get(out, toks) if toks else out
            if isinstance(parent, list):
                parent.append(op.get("value"))
            elif isinstance(parent, dict):
                parent[str(op.get("key"))] = op.get("value")
            else:
                raise ValueError(f"insert 目标不是容器：{path}")
        else:
            raise ValueError(f"未知操作：{what!r}（可用 set/del/insert）")
    return out


def _diff(a: Any, b: Any, path: str, out: list[dict[str, Any]]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() | b.keys():
            p = f"{path}.{k}" if path else k
            if k not in b:
                out.append({"op": "remove", "path": p, "before": a[k]})
            elif k not in a:
                out.append({"op": "add", "path": p, "after": b[k]})
            else:
                _diff(a[k], b[k], p, out)
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            p = f"{path}[{i}]"
            if i >= len(b):
                out.append({"op": "remove", "path": p, "before": a[i]})
            elif i >= len(a):
                out.append({"op": "add", "path": p, "after": b[i]})
            else:
                _diff(a[i], b[i], p, out)
    elif a != b:
        out.append({"op": "change", "path": path, "before": a, "after": b})


def diff(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """两份 spec 的字段级差异（前端渲染 before → after）。"""
    out: list[dict[str, Any]] = []
    _diff(a, b, "", out)
    return out
