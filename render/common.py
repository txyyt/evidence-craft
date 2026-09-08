"""渲染层共用工具：markdown 表格解析（html / docx 渲染器同源）。"""

import re
from typing import Any


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
