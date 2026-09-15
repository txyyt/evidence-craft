"""渲染层共用工具：markdown 表格解析（html / docx 渲染器同源）。"""

import re
from typing import Any


def split_md_blocks(body: str) -> list[tuple[str, str]]:
    """正文分块：连续 ≥2 行以 | 开头的表格块 → ("table", 块文本)，
    其余连续行 → ("text", 块文本)（A1：text 节手写 markdown 表格兜底渲染，
    html/docx 渲染器共用此判定）。"""
    blocks: list[tuple[str, str]] = []
    cur: list[str] = []
    cur_kind = "text"

    def flush():
        if cur:
            blocks.append((cur_kind, "\n".join(cur)))
            cur.clear()

    for line in (body or "").splitlines():
        kind = "table" if line.lstrip().startswith("|") else "text"
        if kind != cur_kind:
            flush()
            cur_kind = kind
        cur.append(line)
    flush()
    # 孤行竖线（单行不成表）回退为普通文本，避免误渲染半张表
    out: list[tuple[str, str]] = []
    for kind, text in blocks:
        if kind == "table" and len(text.strip().splitlines()) < 2:
            out.append(("text", text))
        else:
            out.append((kind, text))
    return out


def md_table_rows(md: str) -> tuple[list[list[str]], str]:
    """markdown 表 → (行列表[含表头], 表注)。非表格行收进表注，分隔行丢弃。"""
    rows: list[list[str]] = []
    notes: list[str] = []
    for line in md.strip().splitlines():
        if not line.lstrip().startswith("|"):
            if line.strip():
                notes.append(line.strip())
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r"-{3,}", c) for c in cells if c):
            continue  # 分隔行
        rows.append(cells)
    return rows, " ".join(notes)
