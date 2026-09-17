"""通用图表渲染：绑定数据 → matplotlib PNG（图与正文同源同数）。

数据来源（ChartTemplate.source 语法，见 schema.ChartTemplate）：
- table:<tid>  → collections["tables"][tid]，x=类目列，y=数值列（可多列多序列）
- facts:<前缀> → 标量事实按 id 前缀过滤，label=事实名，value=数值（pie/bar 常用）
- scatter 仅 table: 源（x 为数值列）；hist 仅 table: 源（y 单数值列分布）

设计约束：
- 数字一律来自事实包/表格集合，本模块不做任何计算（占比/合计禁止）——
  图与正文引用同一份事实，天然图文一致；直方图频数由 matplotlib 分箱计数，
  不进入正文与对账，不属"计算数值"范畴
- 数值列允许"1,234.5"千分位与单位后缀，尽力解析；解析失败的行跳过
- 中文字体与 kline_chart 同方案（Microsoft YaHei / SimHei）
"""

import re
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from template_factory.schema import ChartTemplate, SpecV2  # noqa: E402

_NUM_RE = re.compile(r"^-?\d+(?:,\d{3})*(?:\.\d+)?")
_COLORS = ["#4C78A8", "#F58518", "#E45756", "#72B7B2", "#54A24B",
           "#EECA3B", "#B279A2", "#FF9DA6"]


def _font() -> None:
    # Windows 与 Linux 服务器字体都兜住（matplotlib 自动跳过缺失项）
    plt.rcParams["font.family"] = ["Microsoft YaHei", "SimHei",
                                   "Noto Sans CJK SC", "WenQuanYi Micro Hei"]
    plt.rcParams["axes.unicode_minus"] = False


def _num(v: Any) -> float | None:
    """尽力把表格单元格/展示值解析为数值（千分位、尾随单位容忍）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.match(str(v).strip())
    return float(m.group(0).replace(",", "")) if m else None


def _table_series(doc: dict[str, Any], tpl: ChartTemplate,
                  ) -> tuple[list[str], dict[str, list[float | None]]]:
    tid = tpl.source.split(":", 1)[1]
    data = (doc["collections"].get("tables") or {}).get(tid)
    if not data or not data.get("rows"):
        raise ValueError(f"图 {tpl.id} 的表格数据缺失：{tid}")
    cols = data["columns"]
    x_col = tpl.x or cols[0]
    y_cols = tpl.y or [c for c in cols[1:] if c != x_col][:1]
    if x_col not in cols:
        raise ValueError(f"图 {tpl.id} 类目列 {x_col!r} 不在表格 {tid} 列中")
    xi = cols.index(x_col)
    labels = [str(r[xi]) for r in data["rows"]]
    series: dict[str, list[float | None]] = {}
    for y in y_cols:
        if y not in cols:
            raise ValueError(f"图 {tpl.id} 数值列 {y!r} 不在表格 {tid} 列中")
        yi = cols.index(y)
        series[y] = [_num(r[yi]) for r in data["rows"]]
    return labels, series


def _facts_series(doc: dict[str, Any], tpl: ChartTemplate,
                  ) -> tuple[list[str], dict[str, list[float | None]]]:
    prefix = tpl.source.split(":", 1)[1]
    items = [f for f in doc["facts"] if f["id"].startswith(prefix)
             and isinstance(f.get("value"), (int, float))]
    if not items:
        raise ValueError(f"图 {tpl.id} 无匹配数据事实：{prefix}*")
    labels = [f["name"] for f in items]
    return labels, {tpl.y[0] if tpl.y else tpl.unit or "数值":
                    [float(f["value"]) for f in items]}


def _save(fig: Any, out_path: Path) -> Path:
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _draw_scatter(ax: Any, doc: dict[str, Any], tpl: ChartTemplate) -> None:
    """散点图：x 数值列 + y 多序列（按行对齐，x/y 解析失败的点剔除）。"""
    if not tpl.source.startswith("table:"):
        raise ValueError(f"散点图 {tpl.id} 仅支持 table: 数据源")
    tid = tpl.source.split(":", 1)[1]
    data = (doc["collections"].get("tables") or {}).get(tid)
    if not data or not data.get("rows"):
        raise ValueError(f"图 {tpl.id} 的表格数据缺失：{tid}")
    cols = data["columns"]
    x_col = tpl.x or cols[0]
    if x_col not in cols:
        raise ValueError(f"图 {tpl.id} 数值 x 列 {x_col!r} 不在表格 {tid} 列中")
    y_cols = tpl.y or [c for c in cols[1:] if c != x_col][:1]
    for y in y_cols:
        if y not in cols:
            raise ValueError(f"图 {tpl.id} 数值列 {y!r} 不在表格 {tid} 列中")
    xi, rows = cols.index(x_col), data["rows"]
    xnums = [_num(r[xi]) for r in rows]
    keep = [i for i, v in enumerate(xnums) if v is not None]
    if not keep:
        raise ValueError(f"图 {tpl.id}：x 列 {x_col!r} 无可解析数值")
    n_plotted = 0
    for si, y in enumerate(y_cols):
        yi = cols.index(y)
        pts = [(xnums[i], _num(rows[i][yi])) for i in keep]
        pts = [(x, v) for x, v in pts if v is not None]
        if not pts:
            continue
        ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=28, alpha=0.85,
                   label=y, color=_COLORS[si % len(_COLORS)])
        n_plotted += 1
    if not n_plotted:
        raise ValueError(f"图 {tpl.id}：y 列无可绘制数据点")
    ax.set_xlabel(x_col, fontsize=9)
    ax.set_ylabel(tpl.unit or "", fontsize=9)
    ax.set_title(tpl.title, fontsize=12, loc="left")
    if n_plotted > 1:
        ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


def _draw_hist(ax: Any, tpl: ChartTemplate,
               series: dict[str, list[float | None]], unit_suffix: str) -> None:
    """直方图：单数值列的频数分布（bins 可配，缺省 10；频数为分箱计数，
    不进入正文与对账）。"""
    if len(series) != 1:
        raise ValueError(f"直方图 {tpl.id} 只支持单个数值列（y 配一列）")
    name = next(iter(series))
    vals = [v for v in series[name] if v is not None]
    if len(vals) < 3:
        raise ValueError(f"直方图 {tpl.id} 有效数值不足（{len(vals)} 个）")
    ax.hist(vals, bins=max(int(tpl.bins or 10), 2), color=_COLORS[0],
            edgecolor="white")
    ax.set_xlabel(f"{name}{unit_suffix}", fontsize=9)
    ax.set_ylabel("频数", fontsize=9)
    ax.set_title(tpl.title, fontsize=12, loc="left")
    ax.grid(axis="y", alpha=0.25)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)


def make_chart(doc: dict[str, Any], tpl: ChartTemplate, out_path: Path) -> Path:
    """按模板渲染一张图 → PNG。数据缺失/无法解析抛 ValueError，由调用方兜底。"""
    _font()
    fig, ax = plt.subplots(figsize=(8.6, 4.4), dpi=150)
    if tpl.type == "scatter":        # 散点走原始行数据（x 数值，非类目）
        _draw_scatter(ax, doc, tpl)
        return _save(fig, out_path)
    if tpl.source.startswith("table:"):
        labels, series = _table_series(doc, tpl)
    elif tpl.source.startswith("facts:"):
        labels, series = _facts_series(doc, tpl)
    else:
        raise ValueError(f"图 {tpl.id} 的 source 语法非法：{tpl.source!r}"
                         "（应为 table:<tid> 或 facts:<前缀>）")
    labels = [lb if len(lb) <= 14 else lb[:13] + "…" for lb in labels]

    unit_suffix = f"（{tpl.unit}）" if tpl.unit else ""

    if tpl.type == "pie":
        vals = list(next(iter(series.values())))
        pairs = [(lb, v) for lb, v in zip(labels, vals) if v is not None and v > 0]
        if not pairs:
            raise ValueError(f"图 {tpl.id} 无可绘制的正值数据")
        ax.pie([v for _, v in pairs], labels=[lb for lb, _ in pairs],
               autopct="%1.1f%%", startangle=90,
               colors=[_COLORS[i % len(_COLORS)] for i in range(len(pairs))],
               textprops={"fontsize": 9}, wedgeprops={"edgecolor": "white"})
        ax.set_title(f"{tpl.title}{unit_suffix}", fontsize=12, loc="left")
    elif tpl.type == "hist":
        _draw_hist(ax, tpl, series, unit_suffix)
    else:
        n_series = len(series)
        width = 0.8 / max(n_series, 1)
        ys = list(range(len(labels)))
        for si, (name, vals) in enumerate(series.items()):
            xs = [y + si * width - 0.4 + width / 2 for y in ys]
            if tpl.type == "bar":
                ax.bar(xs, [v if v is not None else 0 for v in vals],
                       width=width, label=name, color=_COLORS[si % len(_COLORS)])
            else:  # line
                ax.plot(ys, [v if v is not None else float("nan") for v in vals],
                        marker="o", label=name, color=_COLORS[si % len(_COLORS)])
        ax.set_xticks(ys)
        ax.set_xticklabels(labels, fontsize=9,
                           rotation=18 if max(len(lb) for lb in labels) > 6 else 0)
        ax.set_ylabel(tpl.unit or "", fontsize=9)
        ax.set_title(tpl.title, fontsize=12, loc="left")
        ax.legend(fontsize=8, frameon=False) if n_series > 1 else None
        ax.grid(axis="y", alpha=0.25)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)

    return _save(fig, out_path)


def render_charts(doc: dict[str, Any], spec: SpecV2, run_dir: Path,
                  log) -> tuple[dict[str, list[dict[str, str]]],
                                list[dict[str, str]]]:
    """渲染 spec 中所有配置了 charts 的章节（figures 章节为图件集，
    text 章节可内嵌图）→ (jobs_by_section, failures)。
    F2：单图失败不再只在日志一闪而过——失败清单 [{id,title,section,reason}]
    随返回值上交，由调用方落 meta.json 供详情页治理体检展示。
    单图失败记警告跳过，不阻塞流水线。"""
    out: dict[str, list[dict[str, str]]] = {}
    failures: list[dict[str, str]] = []
    for sec in spec.sections:
        if not sec.charts:
            continue
        jobs = []
        for tpl in sec.charts:
            png = run_dir / f"chart_{tpl.id}.png"
            try:
                make_chart(doc, tpl, png)
            except Exception as e:  # noqa: BLE001 —— 单图失败不阻塞
                log(f"  [警告] 图 {tpl.title} 渲染失败：{e}")
                failures.append({"id": tpl.id, "title": tpl.title,
                                 "section": sec.id,
                                 "reason": f"{type(e).__name__}: {e}"})
                continue
            caption = tpl.title + (f"（{tpl.note}）" if tpl.note else "")
            # png：绝对路径（docx 嵌入用）；src：文件名（final.html 与图同目录，
            # http/file 访问都用相对名——Windows 反斜杠路径在浏览器里 404）
            jobs.append({"png": str(png.resolve()), "src": png.name,
                         "caption": caption})
        if jobs:
            out[sec.id] = jobs
    return out, failures
