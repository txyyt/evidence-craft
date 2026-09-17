"""大盘/个股 K 线图：报告头图（蜡烛+均线+成交量）。

数据来自东财 push2his 日 K 接口（公开），图由 matplotlib 本地绘制——
图内每个数字与 K 线接口同源，延续"可溯源"原则。样式对齐券商研报头图习惯：
红涨绿跌、5/10/20/30 日均线、成交额副图（亿元）。
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from datalayer.cache import cached  # noqa: E402
from datalayer.sources.base import get_json  # noqa: E402

HOSTS = ["https://push2his.eastmoney.com", "https://push2delay.eastmoney.com"]
PATH = "/api/qt/stock/kline/get"

MA_SPECS = [(5, "#7f7f7f"), (10, "#e6a23c"), (20, "#e377c2"), (30, "#2e8b57")]
UP, DOWN = "#d92e2e", "#1a9e4b"


def _fetch_klines(secid: str, lmt: int) -> dict[str, Any]:
    def _fetch_em() -> dict[str, Any]:
        from datalayer.sources.base import SourceError
        params = {"secid": secid, "klt": 101, "fqt": 1, "end": "20500101",
                  "lmt": lmt, "fields1": "f1,f2,f3,f4,f5,f6",
                  "fields2": "f51,f52,f53,f54,f55,f56,f57"}
        last: Exception | None = None
        for host in HOSTS:
            try:
                data = (get_json(host + PATH, params=params,
                                 referer="https://quote.eastmoney.com/")
                        .get("data") or {})
                if data.get("klines"):
                    return {"name": data["name"], "code": data["code"],
                            "klines": data["klines"]}
            except SourceError as e:
                last = e
        raise SourceError(f"K线源不可用: {last}")

    def _fetch_tx() -> dict[str, Any]:
        # 跨厂商兜底（腾讯）：无成交额字段，amount 置 0，图表自动改画成交量
        mkt, code = ("sh", secid.split(".")[1]) if secid.startswith("1.") \
            else ("sz", secid.split(".")[1])
        data = get_json("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                        params={"param": f"{mkt}{code},day,,,{lmt},qfq"},
                        referer="https://gu.qq.com/")
        rows = data["data"][f"{mkt}{code}"]["day"]
        return {"name": "上证指数" if code == "000001" else code, "code": code,
                "klines": [",".join(r[:6] + ["0.0"]) for r in rows]}

    def _fetch() -> dict[str, Any]:
        from datalayer.sources.base import SourceError
        try:
            return _fetch_em()
        except SourceError:
            return _fetch_tx()

    return cached("quote", f"kline_{secid}_{lmt}", _fetch)


def _parse(klines: list[str]) -> list[dict[str, float]]:
    out = []
    for line in klines:
        p = line.split(",")
        out.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                    "high": float(p[3]), "low": float(p[4]),
                    "vol_yi": float(p[5]) / 1e8,
                    "amount_yi": float(p[6]) / 1e8})
    return out


def _ma(closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(closes)):
        out.append(sum(closes[i - n + 1:i + 1]) / n if i >= n - 1 else None)
    return out


def make_chart(secid: str, out_path: Path, bars: int = 60) -> Path:
    raw = _parse(_fetch_klines(secid, bars + 40)["klines"])[-bars:]
    dates = [r["date"][5:] for r in raw]
    closes = [r["close"] for r in raw]
    x = list(range(len(raw)))

    # Windows 与 Linux 服务器字体都兜住（matplotlib 自动跳过缺失项）
    plt.rcParams["font.family"] = ["Microsoft YaHei", "SimHei",
                                   "Noto Sans CJK SC", "WenQuanYi Micro Hei"]
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 5.2), dpi=150, sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1], "hspace": 0.06})
    latest = raw[-1]["date"]
    ax1.set_title(f"{raw and ''}上证指数[{secid.split('.')[1]}] {latest} "
                  f"生成于 {datetime.now():%H:%M}", loc="left", fontsize=11)

    for i, r in enumerate(raw):
        color = UP if r["close"] >= r["open"] else DOWN
        ax1.vlines(i, r["low"], r["high"], color=color, linewidth=0.9)
        body_bottom = min(r["open"], r["close"])
        body_height = abs(r["close"] - r["open"]) or 0.5
        ax1.bar(i, body_height, bottom=body_bottom, width=0.65,
                color=color, edgecolor=color, linewidth=0.8, zorder=3)
    for n, color in MA_SPECS:
        ax1.plot(x, _ma(closes, n), color=color, linewidth=1.0,
                 label=f"{n}PMA")
    ax1.legend(loc="upper right", fontsize=8, ncol=4, frameon=False)
    ax1.grid(True, linestyle="--", linewidth=0.4, alpha=0.5)
    ax1.set_ylabel("点位", fontsize=9)

    use_amount = any(r["amount_yi"] for r in raw)
    bars = [r["amount_yi"] if use_amount else r["vol_yi"] for r in raw]
    ax2.bar(x, bars,
            color=[UP if r["close"] >= r["open"] else DOWN for r in raw],
            width=0.65)
    ax2.grid(True, linestyle="--", linewidth=0.4, alpha=0.5)
    ax2.set_ylabel("成交额(亿)" if use_amount else "成交量(亿手)", fontsize=9)
    step = max(len(x) // 8, 1)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels(dates[::step], fontsize=8)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
