"""本地资料库构建：PDF → 切片 → fragments.json（rag 适配器 mock 模式消费）。

定位：只负责"带出处地切准"——来源文件名、页码、片段文本、年份线索；
检索与"抽取即对账"由 rag 适配器负责（mock 模式内置关键词检索）。

切片规则：逐页解析（PyMuPDF 优先，pypdf 兜底），页内按句子边界拼装成
≤max_chars 的片段；<min_chars 的尾片段并入前一片段，不丢数字；
图片页（无可提取文本）跳过并记录到 index.json。

年份线索：片段内出现频次最高的 20xx 年，供 as_of 溯源与新旧数据合并；
仅是启发值，允许为空。

用法：
  python -m datalayer.corpus --dir "高纯石英矿报告资料" \
      --out data/corpus/hp_quartz/fragments.json
"""

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

_SENT_SPLIT = re.compile(r"(?<=[。；;！!？?])")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _page_texts(path: Path) -> tuple[list[tuple[int, str]], list[int]]:
    """返回 [(页码, 文本)], 空文本页码列表。PyMuPDF 优先，失败换 pypdf。"""
    try:
        import fitz  # PyMuPDF
        pages, empty = [], []
        with fitz.open(path) as pdf:
            for i, page in enumerate(pdf, 1):
                t = (page.get_text() or "").strip()
                if len(t) < 30:
                    empty.append(i)
                    continue
                pages.append((i, t))
        return pages, empty
    except Exception:  # noqa: BLE001 —— 损毁/加密 PDF 走 pypdf 兜底
        from pypdf import PdfReader
        pages, empty = [], []
        reader = PdfReader(path)
        for i, page in enumerate(reader.pages, 1):
            try:
                t = (page.extract_text() or "").strip()
            except Exception:  # noqa: BLE001
                t = ""
            if len(t) < 30:
                empty.append(i)
                continue
            pages.append((i, t))
        return pages, empty


def _split_year(text: str) -> str:
    years = _YEAR_RE.findall(text)
    if not years:
        return ""
    return Counter(years).most_common(1)[0][0]


def _chunks(text: str, max_chars: int, min_chars: int) -> list[str]:
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    out: list[str] = []
    buf = ""
    for s in sents:
        if len(s) > max_chars:          # 超长句硬切
            if buf:
                out.append(buf)
                buf = ""
            out.extend(s[k:k + max_chars] for k in range(0, len(s), max_chars))
            continue
        if buf and len(buf) + len(s) > max_chars:
            out.append(buf)
            buf = s
        else:
            buf = f"{buf}{s}"
    if buf:
        out.append(buf)
    # 尾片段过短并入前一片段（防止关键数字落在碎片段里被检索遗漏）
    merged: list[str] = []
    for c in out:
        if merged and len(c) < min_chars:
            merged[-1] += c
        else:
            merged.append(c)
    return merged


def build(src_dir: Path, out_path: Path, max_chars: int = 700,
          min_chars: int = 60) -> dict:
    files = sorted(p for p in src_dir.rglob("*.pdf") if p.stat().st_size > 0)
    fragments: list[dict] = []
    report: list[dict] = []
    for p in files:
        try:
            pages, empty = _page_texts(p)
        except Exception as e:  # noqa: BLE001 —— 单文件失败不阻塞建库
            report.append({"file": p.name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            continue
        n0 = len(fragments)
        for page_no, text in pages:
            for chunk in _chunks(text, max_chars, min_chars):
                fragments.append({
                    "doc": p.stem, "page": page_no, "text": chunk,
                    "year": _split_year(chunk)})
        report.append({"file": p.name, "ok": True, "pages": len(pages),
                       "empty_pages": len(empty), "fragments": len(fragments) - n0})
        print(f"  {p.name}: {len(pages)} 页 → {len(fragments) - n0} 片段"
              + (f"（{len(empty)} 个空文本页）" if empty else ""))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fragments, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    index = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(src_dir),
        "out": str(out_path),
        "n_files_ok": sum(1 for r in report if r.get("ok")),
        "n_files_failed": sum(1 for r in report if not r.get("ok")),
        "n_fragments": len(fragments),
        "files": report,
    }
    (out_path.parent / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"建库完成：{len(fragments)} 个片段 → {out_path}")
    return index


def main() -> None:
    ap = argparse.ArgumentParser(description="本地资料 PDF → 检索片段库")
    ap.add_argument("--dir", required=True, help="资料目录（递归收集 PDF）")
    ap.add_argument("--out", required=True, help="输出 fragments.json 路径")
    ap.add_argument("--max-chars", type=int, default=700)
    ap.add_argument("--min-chars", type=int, default=60)
    a = ap.parse_args()
    build(Path(a.dir), Path(a.out), a.max_chars, a.min_chars)


if __name__ == "__main__":
    main()
