"""⑦ HTML 渲染：按 Spec v2 的章节列表驱动版式（kind 分发渲染块）。

views → 标题 + 观点块；table → 标题 + 表格 + 说明文字；risk → 标题 + 风险行；
text → 标题 + 综述正文；figures → 标题 + 图件集（图N + 图注）。
评级由调用方传入（pipeline/rating.py），免责/说明段来自 spec.disclaimer，
溯源附注与头图为通用能力。
"""

import html
from datetime import datetime
from typing import Any

from pipeline.sections import render_table_sec
from render.common import md_table_rows
from template_factory.schema import SpecV2

TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         color: #222; background: #fff; color-scheme: light;
         max-width: 820px; margin: 0 auto; padding: 32px 24px;
         line-height: 1.75; }}
  .head {{ border-bottom: 3px solid #c0392b; padding-bottom: 14px; }}
  .meta {{ color: #777; font-size: 13px; margin-top: 8px; }}
  .rating {{ float: right; background: #fdf0ef; color: #c0392b; border: 1px solid #e6b0aa;
             padding: 4px 14px; border-radius: 4px; font-weight: 600; }}
  h1 {{ font-size: 24px; margin: 6px 0; }}
  h2 {{ font-size: 17px; color: #c0392b; border-left: 4px solid #c0392b;
        padding-left: 10px; margin: 28px 0 10px; }}
  h3 {{ font-size: 15px; color: #333; margin: 20px 0 8px; }}
  .view {{ margin: 14px 0; }}
  .view b {{ display: block; margin-bottom: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 14px; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: center; }}
  th {{ background: #f7f7f7; }}
  .table-note {{ font-size: 12px; color: #888; margin: 2px 0 10px; }}
  .sec-src {{ font-size: 11px; color: #999; margin: 2px 0 16px; }}
  .sec-src a {{ color: #a0743c; text-decoration: none; }}
  .risks p {{ margin: 4px 0; }}
  .appendix {{ font-size: 12px; color: #666; }}
  .appendix td {{ text-align: left; }}
  .disclaimer {{ margin-top: 36px; font-size: 12px; color: #999;
                 border-top: 1px solid #ddd; padding-top: 12px; }}
  @media print {{
    body {{ max-width: none; padding: 0 12px; }}
    img {{ max-height: 260px; }}
  }}
</style>
</head>
<body>
  <div class="head">
    <span class="rating">{rating}</span>
    <div class="meta">{code} {name} ｜ {industry} ｜ 报告日期 {date}</div>
    <h1>{title}</h1>
  </div>
{kline_img}
{body}
  <h2>附：数据溯源</h2>
  <table class="appendix">
    <tr><th>事实编号</th><th>指标</th><th>值</th><th>来源</th><th>截至</th></tr>
{appendix_rows}
  </table>

  <div class="disclaimer">
    {disclaimer}
    生成时间 {generated_at}。
  </div>
</body>
</html>
"""

APPENDIX_ROW = "    <tr id=\"src-{id}\"><td>{id}</td><td>{name}</td><td>{value}{unit}</td><td>{source}</td><td>{as_of}</td></tr>"


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=False)


def _md_table_html(md: str) -> tuple[str, str]:
    """渲染器 markdown 表 → HTML 表格 + 表注（解析共用 render/common）。"""
    rows, note = md_table_rows(md)
    if not rows:
        return "", ""
    head = "<tr>" + "".join(f"<th>{_esc(c)}</th>" for c in rows[0]) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>"
        for r in rows[1:])
    table = f'<table><thead>{head}</thead><tbody>{body}</tbody></table>'
    return table, (f'<p class="table-note">{_esc(note)}</p>' if note else "")


def _heading_html(sec: Any) -> str:
    """章节标题：heading 配置原样输出（范文式编号）；subheading 即本节标题时
    不再出自动章节标题（避免"资源保障"+「1.2　资源保障」双重标题）；inline 无标题。"""
    if sec.inline:
        return ""
    if sec.heading:
        return f"  <h2>{_esc(sec.heading)}</h2>\n"
    if sec.subheading:
        return ""
    return f"  <h2>{_esc(sec.title)}</h2>\n"


def _subheading_html(sec: Any) -> str:
    return f"  <h3>{_esc(sec.subheading)}</h3>\n" if sec.subheading else ""


def _charts_html(jobs: list[dict[str, str]] | None) -> str:
    return "\n".join(
        f'  <img src="{_esc(j.get("src") or j["png"])}" alt="{_esc(j["caption"])}" '
        f'style="width:100%; margin:12px 0 2px; border:1px solid #eee">\n'
        f'  <p class="table-note">{_esc(j["caption"])}</p>'
        for j in (jobs or []))


def _sources_html(fact_ids: list[str] | None,
                  facts_by_id: dict[str, dict[str, Any]]) -> str:
    """章节末尾的"本章数据来源"小字行（仅渲染层，不进正文/对账/字数）。
    编号即溯源附录锚点链接；无引用或引用不可解析的章节不加。"""
    ids = [fid for fid in (fact_ids or []) if fid in facts_by_id]
    if not ids:
        return ""
    items = []
    for fid in ids[:12]:            # 行宽控制：至多列 12 条
        f = facts_by_id[fid]
        src = str(f.get("source", ""))
        if len(src) > 46:
            src = src[:45] + "…"
        as_of = f"，截至 {f['as_of']}" if f.get("as_of") else ""
        items.append(
            f'<a href="#src-{_esc(fid)}">{_esc(fid)}</a>'
            f' {_esc(f["name"])}={f["value"]}{_esc(f.get("unit", ""))}'
            f'（{_esc(src)}{_esc(as_of)}）')
    return ('  <p class="sec-src">本章数据来源：' + "；".join(items)
            + "</p>\n")


def _section_html(sec: Any, doc: dict[str, Any], written: list[dict[str, Any]],
                  forecast: dict[str, Any], risks: dict[str, Any],
                  spec: SpecV2, texts: dict[str, dict[str, Any]] | None = None,
                  notes: dict[str, Any] | None = None,
                  charts: dict[str, list[dict[str, str]]] | None = None,
                  facts_by_id: dict[str, dict[str, Any]] | None = None) -> str:
    texts = texts or {}
    notes = notes or {}
    charts = charts or {}
    facts_by_id = facts_by_id or {}
    head = _heading_html(sec) + _subheading_html(sec)
    if sec.kind == "views":
        if sec.view_numbering:   # 范文式子节编号：3.1 / 3.2 …
            views_html = "\n".join(
                f'  <div class="view"><b>{_esc(sec.view_numbering)}.{i}　'
                f'{_esc(s["heading"])}</b>{_esc(s["body"])}</div>'
                for i, s in enumerate(written, 1))
        else:
            views_html = "\n".join(
                f'  <div class="view"><b>{_esc(s["heading"])}</b>{_esc(s["body"])}</div>'
                for s in written)
        src = _sources_html(
            [fid for s in written for fid in (s.get("cited_fact_ids") or [])],
            facts_by_id)
        return f"{head}{views_html}\n{src}{_charts_html(charts.get(sec.id))}"
    if sec.kind == "table":
        table_html, table_note = _md_table_html(render_table_sec(doc, sec, spec))
        n = notes.get(sec.id)
        note_body = ((n.get("body") if isinstance(n, dict) else n) or
                     forecast.get("body", ""))
        note_ids = n.get("cited_fact_ids") if isinstance(n, dict) else None
        src = _sources_html(note_ids, facts_by_id)
        return (f"{head}{table_html}\n{table_note}\n"
                f"  <p>{_esc(note_body)}</p>\n{src}"
                f"{_charts_html(charts.get(sec.id))}")
    if sec.kind == "risk":
        return (f'{head}'
                f'  <div class="risks"><p>{_esc(risks["body"])}</p></div>\n'
                f'{_sources_html((risks or {}).get("cited_fact_ids"), facts_by_id)}'
                f'{_charts_html(charts.get(sec.id))}')
    if sec.kind == "text":
        t = texts.get(sec.id)
        if not t:
            return ""
        paras = "\n".join(f"  <p>{_esc(p.strip())}</p>"
                          for p in t["body"].split("\n") if p.strip())
        src = _sources_html(t.get("cited_fact_ids"), facts_by_id)
        return f"{head}{paras}\n{src}{_charts_html(charts.get(sec.id))}"
    if sec.kind == "figures":
        jobs = charts.get(sec.id) or []
        if not jobs:
            return ""
        return f"{head}{_charts_html(jobs)}\n"
    return ""


def render(doc: dict[str, Any], outline: dict[str, Any],
           sections: list[dict[str, Any]], forecast: dict[str, Any],
           risks: dict[str, Any], spec: SpecV2, rating: str,
           kline_png: str | None = None,
           texts: list[dict[str, Any]] | None = None,
           notes: dict[str, Any] | None = None,
           charts: dict[str, list[dict[str, str]]] | None = None) -> str:
    meta = doc["meta"]
    texts_by_id = {t.get("section_id"): t for t in (texts or [])}
    facts_by_id = {f["id"]: f for f in doc["facts"]}
    kline_img = (f'\n  <img src="{kline_png}" alt="近期走势K线" '
                 'style="width:100%; margin:14px 0 4px; border:1px solid #eee">'
                 if kline_png else "")
    body = "\n".join(filter(None, (
        _section_html(sec, doc, sections, forecast, risks, spec,
                      texts_by_id, notes, charts, facts_by_id)
        for sec in spec.sections)))
    appendix = "\n".join(
        APPENDIX_ROW.format(id=_esc(f["id"]), name=_esc(f["name"]), value=f["value"],
                            unit=_esc(f["unit"]), source=_esc(f["source"]),
                            as_of=_esc(f["as_of"]))
        for f in doc["facts"])
    return TPL.format(
        title=_esc(outline["title"]),
        rating=_esc(rating),
        code=_esc(meta.get("stock", "")), name=_esc(meta.get("name", "")),
        industry=_esc(meta.get("industry", "")),
        date=datetime.now().strftime("%Y-%m-%d"),
        kline_img=kline_img,
        body=body,
        appendix_rows=appendix,
        disclaimer=_esc(spec.disclaimer or ""),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
