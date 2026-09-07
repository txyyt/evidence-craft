"""⑦ HTML 渲染：研报版式（标题区/核心观点/盈利预测/风险提示/溯源附注）。"""

import html
import re
from datetime import datetime
from typing import Any

from pipeline.sections import forecast_table

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
  <h2>核心观点</h2>
{views_html}

  <h2>盈利预测与估值</h2>
{table_html}
{table_note}
  <p>{forecast_note}</p>

  <h2>风险提示</h2>
  <div class="risks"><p>{risks}</p></div>

  <h2>附：数据溯源</h2>
  <table class="appendix">
    <tr><th>事实编号</th><th>指标</th><th>值</th><th>来源</th><th>截至</th></tr>
{appendix_rows}
  </table>

  <div class="disclaimer">
    本报告由 EvidenceCraft 系统自动生成，评级为规则推算结果，所有数字溯源自公开数据
    （东方财富数据接口），仅供技术研究使用，不构成任何投资建议。
    生成时间 {generated_at}。
  </div>
</body>
</html>
"""

APPENDIX_ROW = "    <tr><td>{id}</td><td>{name}</td><td>{value}{unit}</td><td>{source}</td><td>{as_of}</td></tr>"


def _rule_rating(doc: dict[str, Any]) -> str:
    """规则评级 v1：今年预测 PE 相对板块中位数折价 15% 以上 → 增持，
    溢价 15% 以上 → 中性，其余 → 增持（业绩同比 >30% 时）或中性。"""
    q = {f["id"].split(".")[1]: f["value"] for f in doc["facts"]
         if f["id"].startswith("quote.")}
    mean = doc["collections"]["consensus"].get("mean_eps_forecast", {})
    eps = mean.get("predictThisYearEps")
    peers_pe = sorted(p["pe_ttm"] for p in doc["collections"]["peers"] if p.get("pe_ttm"))
    if not (eps and q.get("price") and peers_pe):
        return "未评级"
    pe = q["price"] / eps
    median = peers_pe[len(peers_pe) // 2]
    if pe < median * 0.85:
        return "增持"
    if pe > median * 1.15:
        return "中性"
    latest = doc["collections"]["periods"][0] if doc["collections"]["periods"] else {}
    return "增持" if (latest.get("netprofit_yoy_pct") or 0) > 30 else "中性"


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=False)


def _md_table_html(md: str) -> tuple[str, str]:
    """forecast_table 的 markdown 转 HTML 表格；非表格行（表注）单独返回。"""
    rows, notes = [], []
    for line in md.strip().splitlines():
        if not line.lstrip().startswith("|"):
            notes.append(line.strip())
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r"-{3,}", c) for c in cells if c):
            continue  # 分隔行
        rows.append(cells)
    if not rows:
        return "", ""
    head = "<tr>" + "".join(f"<th>{_esc(c)}</th>" for c in rows[0]) + "</tr>"
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>"
        for r in rows[1:])
    table = f'<table><thead>{head}</thead><tbody>{body}</tbody></table>'
    note = f'<p class="table-note">{_esc(" ".join(notes))}</p>' if notes else ""
    return table, note


def render(doc: dict[str, Any], outline: dict[str, Any],
           sections: list[dict[str, Any]], forecast_note: str,
           risks_text: str, kline_png: str | None = None) -> str:
    meta = doc["meta"]
    kline_img = (f'\n  <img src="{kline_png}" alt="上证指数K线" '
                 'style="width:100%; margin:14px 0 4px; border:1px solid #eee">'
                 if kline_png else "")
    views_html = "\n".join(
        f'  <div class="view"><b>{_esc(s["heading"])}</b>{_esc(s["body"])}</div>'
        for s in sections)
    appendix = "\n".join(
        APPENDIX_ROW.format(id=_esc(f["id"]), name=_esc(f["name"]), value=f["value"],
                            unit=_esc(f["unit"]), source=_esc(f["source"]),
                            as_of=_esc(f["as_of"]))
        for f in doc["facts"] if f["id"].startswith(("fin.", "quote.")))

    table_html, table_note = _md_table_html(forecast_table(doc))
    return TPL.format(
        title=_esc(outline["title"]),
        rating=_rule_rating(doc),
        code=_esc(meta["stock"]), name=_esc(meta["name"]),
        industry=_esc(meta["industry"]),
        date=datetime.now().strftime("%Y-%m-%d"),
        kline_img=kline_img,
        views_html=views_html,
        table_html=table_html,
        table_note=table_note,
        forecast_note=_esc(forecast_note),
        risks=_esc(risks_text),
        appendix_rows=appendix,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
