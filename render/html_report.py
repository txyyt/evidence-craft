"""⑦ HTML 渲染：按 Spec v2 的章节列表驱动版式（kind 分发渲染块）。

views → 标题 + 观点块；table → 标题 + 表格 + 说明文字；risk → 标题 + 风险行；
text/figures → M7/M8 实现。评级由调用方传入（pipeline/rating.py），
免责/说明段来自 spec.disclaimer，溯源附注与头图为通用能力。
"""

import html
from datetime import datetime
from typing import Any

from pipeline.sections import render_table
from render.common import md_table_rows
from template_factory.schema import SpecV2

TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         color: #222; max-width: 820px; margin: 0 auto; padding: 32px 24px;
         line-height: 1.75; }}
  .head {{ border-bottom: 3px solid #c0392b; padding-bottom: 14px; }}
  .meta {{ color: #777; font-size: 13px; margin-top: 8px; }}
  .rating {{ float: right; background: #fdf0ef; color: #c0392b; border: 1px solid #e6b0aa;
             padding: 4px 14px; border-radius: 4px; font-weight: 600; }}
  h1 {{ font-size: 24px; margin: 6px 0; }}
  h2 {{ font-size: 17px; color: #c0392b; border-left: 4px solid #c0392b;
        padding-left: 10px; margin: 28px 0 10px; }}
  .view {{ margin: 14px 0; }}
  .view b {{ display: block; margin-bottom: 4px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 14px; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: center; }}
  th {{ background: #f7f7f7; }}
  .table-note {{ font-size: 12px; color: #888; margin: 2px 0 10px; }}
  .risks p {{ margin: 4px 0; }}
  .appendix {{ font-size: 12px; color: #666; }}
  .appendix td {{ text-align: left; }}
  .disclaimer {{ margin-top: 36px; font-size: 12px; color: #999;
                 border-top: 1px solid #ddd; padding-top: 12px; }}
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

APPENDIX_ROW = "    <tr><td>{id}</td><td>{name}</td><td>{value}{unit}</td><td>{source}</td><td>{as_of}</td></tr>"


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


def _section_html(sec: Any, doc: dict[str, Any], written: list[dict[str, Any]],
                  forecast: dict[str, Any], risks: dict[str, Any],
                  spec: SpecV2) -> str:
    if sec.kind == "views":
        views_html = "\n".join(
            f'  <div class="view"><b>{_esc(s["heading"])}</b>{_esc(s["body"])}</div>'
            for s in written)
        return f"  <h2>{_esc(sec.title)}</h2>\n{views_html}\n"
    if sec.kind == "table":
        table_html, table_note = _md_table_html(render_table(doc, spec))
        return (f"  <h2>{_esc(sec.title)}</h2>\n{table_html}\n{table_note}\n"
                f"  <p>{_esc(forecast['body'])}</p>\n")
    if sec.kind == "risk":
        return (f'  <h2>{_esc(sec.title)}</h2>\n'
                f'  <div class="risks"><p>{_esc(risks["body"])}</p></div>\n')
    return ""  # text / figures：M7/M8 实现


def render(doc: dict[str, Any], outline: dict[str, Any],
           sections: list[dict[str, Any]], forecast: dict[str, Any],
           risks: dict[str, Any], spec: SpecV2, rating: str,
           kline_png: str | None = None) -> str:
    meta = doc["meta"]
    kline_img = (f'\n  <img src="{kline_png}" alt="近期走势K线" '
                 'style="width:100%; margin:14px 0 4px; border:1px solid #eee">'
                 if kline_png else "")
    body = "\n".join(filter(None, (
        _section_html(sec, doc, sections, forecast, risks, spec)
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
