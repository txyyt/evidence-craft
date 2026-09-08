"""模板工厂①：样例文档解析 → 统一块结构。

docx / pdf(文字版) / md / txt 统一解析为扁平块列表：
  {"type": "heading", "level": N, "text": ...}
  {"type": "para", "text": ..., "n_chars": N}
  {"type": "table", "caption": ..., "header": [...], "n_rows": N, "sample_rows": [...]}
  {"type": "figure", "caption": ...}
纯代码、零 LLM；产物落盘可断点（extract.py 负责）。
"""

import re
from pathlib import Path
from typing import Any

# 公文式编号开头：第一章/一、/（一）/1.1/1、
# 阿拉伯数字分支必须带分隔符（、.．或多级+空格），避免"2026年7月…"这类
# 数字开头的正文句被误判为标题
_NUM_HEAD_RE = re.compile(
    r"^(第[一二三四五六七八九十百\d]+[章节部分]|[(（][一二三四五六七八九十]+[)）]"
    r"|[0-9]+(?:\.[0-9]+)*[、.．]|[0-9]+(?:\.[0-9]+)+\s)\s*\S*")
_FIG_RE = re.compile(r"^(图|Figure|Fig\.?)\s*[0-9]+")


def _looks_heading(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 50:
        return False
    if t.endswith(("。", "；", "，", "；")):
        return False
    return bool(_NUM_HEAD_RE.match(t))


def _heading_level(text: str) -> int:
    t = text.strip()
    if re.match(r"^第[一二三四五六七八九十百\d]+[章篇]", t):
        return 1
    if re.match(r"^第[一二三四五六七八九十百\d]+节", t) or _NUM_HEAD_RE.match(t):
        # "一、" 视为一级，"（一）"二级，"1.1"按点数
        if re.match(r"^[（(]", t):
            return 3
        m = re.match(r"^([0-9]+(?:\.[0-9]+)*)", t)
        if m:
            return min(2 + m.group(1).count("."), 5)
        return 2
    return 2


def parse(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".docx":
        return _parse_docx(p)
    if suffix == ".pdf":
        return _parse_pdf(p)
    if suffix in (".md", ".markdown", ".txt"):
        return _parse_text(p)
    raise ValueError(f"不支持的样例格式: {p}（支持 docx/pdf/md/txt，扫描版 PDF 不支持）")


def _parse_docx(p: Path) -> dict[str, Any]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(p))
    blocks: list[dict[str, Any]] = []

    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "") if para.style is not None else ""
            m = re.match(r"^(?:Heading|标题)\s*(\d)", style)
            level = int(m.group(1)) if m else None
            if _FIG_RE.match(text) and len(text) <= 60:
                blocks.append({"type": "figure", "caption": text})
            elif level or _looks_heading(text):
                blocks.append({"type": "heading",
                               "level": level or _heading_level(text), "text": text})
            else:
                blocks.append({"type": "para", "text": text,
                               "n_chars": len(re.sub(r"\s", "", text))})
        elif child.tag == qn("w:tbl"):
            tbl = Table(child, doc)
            rows = [[c.text.strip() for c in row.cells] for row in tbl.rows]
            blocks.append(_table_block(rows))

    return {"source": str(p), "kind": "docx", "blocks": blocks}


def _table_block(rows: list[list[str]]) -> dict[str, Any]:
    rows = [r for r in rows if any(c for c in r)]
    header = rows[0] if rows else []
    return {"type": "table", "caption": "", "header": header,
            "n_rows": len(rows), "n_cols": len(header),
            "sample_rows": rows[1:3]}


def _lines_to_blocks(lines: list[str]) -> list[dict[str, Any]]:
    """行列表 → 块列表；硬换行的连续行并入同一段落（句末标点或空行结算）。"""
    blocks: list[dict[str, Any]] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            text = "".join(buf).strip()
            buf.clear()
            if text:
                blocks.append({"type": "para", "text": text,
                               "n_chars": len(re.sub(r"\s", "", text))})

    for line in lines:
        line = line.strip()
        if not line:
            flush()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        is_fig = bool(_FIG_RE.match(line)) and len(line) <= 60
        is_heading = bool(m) or _looks_heading(line)
        if m or is_fig or is_heading:
            flush()
        if m:
            blocks.append({"type": "heading", "level": len(m.group(1)),
                           "text": m.group(2).strip()})
        elif is_fig:
            blocks.append({"type": "figure", "caption": line})
        elif is_heading:
            blocks.append({"type": "heading", "level": _heading_level(line),
                           "text": line})
        else:
            buf.append(line)
            if line.endswith(("。", "！", "？", "；", "：")):
                flush()
    flush()
    return blocks


def _parse_pdf(p: Path) -> dict[str, Any]:
    from pypdf import PdfReader
    reader = PdfReader(str(p))
    lines: list[str] = []
    for page in reader.pages:
        lines.extend((page.extract_text() or "").splitlines())
        lines.append("")  # 页边界视为段落边界
    return {"source": str(p), "kind": "pdf",
            "blocks": _lines_to_blocks(lines),
            "limits": ["PDF 解析不含表格结构（表格识别为普通段落），表格模板需人工补充"]}


def _parse_text(p: Path) -> dict[str, Any]:
    return {"source": str(p), "kind": p.suffix.lstrip("."),
            "blocks": _lines_to_blocks(
                p.read_text(encoding="utf-8").splitlines())}


def skeleton(parsed: dict[str, Any]) -> str:
    """块结构 → 供结构提取的紧凑骨架文本（含段落统计，不含正文全文）。"""
    lines = [f"样例：{parsed['source']}"]
    for b in parsed["blocks"]:
        if b["type"] == "heading":
            lines.append(f"[H{b['level']}] {b['text']}")
        elif b["type"] == "para":
            lines.append(f"    （段落 {b['n_chars']} 字）")
        elif b["type"] == "table":
            lines.append(f"    [表格 {b['n_rows']}行×{b['n_cols']}列 "
                         f"表头:{'|'.join(b['header'][:6])}]")
        elif b["type"] == "figure":
            lines.append(f"    [图件] {b['caption']}")
    return "\n".join(lines)


def _find_heading(blocks: list[dict[str, Any]], heading_text: str) -> int | None:
    """按标题找起点：精确 → 互为子串（LLM 归一化标题与原文常有出入）。"""
    target = heading_text.strip()
    for i, b in enumerate(blocks):
        if b["type"] != "heading":
            continue
        t = b["text"].strip()
        if t == target or t in target or target in t:
            return i
    return None


def section_text(parsed: dict[str, Any], heading_text: str,
                 max_chars: int = 6000) -> str:
    """取某标题之下、同级或更高级标题之前的全部正文块（供槽位细化）。"""
    blocks = parsed["blocks"]
    start = _find_heading(blocks, heading_text)
    if start is None:
        return ""
    level = blocks[start]["level"]
    out, total = [], 0
    for b in blocks[start + 1:]:
        if b["type"] == "heading" and b["level"] <= level:
            break
        if b["type"] == "para":
            out.append(b["text"])
            total += b["n_chars"]
        elif b["type"] == "table":
            out.append(f"[表格] 表头:{'|'.join(b['header'][:8])}；"
                       f"示例行:{b['sample_rows']}")
        elif b["type"] == "figure":
            out.append(f"[图件] {b['caption']}")
        if total > max_chars:
            out.append("（后文截断）")
            break
    return "\n".join(out)
