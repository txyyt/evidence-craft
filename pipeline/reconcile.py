"""⑤ 数字对账：正文数字与事实包逐一对账。

方法：
1. 从 facts + mainop + 预测表构建"合法数字索引"（id → value）。
2. 正则提取每节正文中的数字（剔除年份），在索引中找容差匹配（≤0.5%）。
3. 命中不了的数字记 unknown——不硬阻断（可能是"3~5条"这类结构性数字），
   落盘供人工复核；被引用的 fact id 不存在则硬失败。
"""

import re
from typing import Any

TOLERANCE = 0.005
# 排除紧贴字母的数字（"2026H1"的H1、"Q2"）与计数量词前的数字（"3家券商"、
# "25件样品"——计数不是数据，数值对账只针对量值）
_NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?(?![家条个月件份个批次组])")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
# 模型偶发把公告号写成裸码（AN2026...，丢 ann. 前缀）——规范化而非丢弃
_BARE_ANN_RE = re.compile(r"^(AN|AP)\d+$", re.IGNORECASE)


def norm_citation(c: Any) -> str | None:
    """引用 id 规范化：剥 []、补 ann. 前缀；非字符串返回 None。"""
    if not isinstance(c, str):
        return None
    c = c.strip().strip("[]").strip()
    if c.lower().startswith("ann."):
        return "ann." + c[4:].strip()
    if _BARE_ANN_RE.fullmatch(c):
        return "ann." + c.upper()
    return c


def _value_index(doc: dict[str, Any]) -> dict[str, float]:
    vals: dict[str, float] = {}
    for f in doc["facts"]:
        if isinstance(f.get("value"), (int, float)):
            vals[f["id"]] = float(f["value"])
    mainop = doc["collections"].get("mainop") or {}
    for dim in ("by_product", "by_region", "by_industry"):
        for it in mainop.get(dim) or []:
            prefix = f"mainop.{it['name']}"
            vals[f"{prefix}.income_yi"] = it["income_yi"]
            vals[f"{prefix}.income_ratio_pct"] = it["income_ratio_pct"]
            vals[f"{prefix}.gross_margin_pct"] = it["gross_margin_pct"]
    regions = (doc["collections"].get("mainop") or {}).get("by_region") or []
    overseas = [it for it in regions
                if "境外" in it["name"] or "海外" in it["name"]]
    if overseas:
        vals["mainop.境外收入合计"] = round(
            sum(it["income_ratio_pct"] or 0 for it in overseas), 2)
    mean = (doc["collections"].get("consensus") or {}).get("mean_eps_forecast", {})
    for k, v in mean.items():
        if v is not None:
            vals[f"consensus.{k}"] = float(v)
    q = {f["id"].split(".")[1]: f["value"] for f in doc["facts"]
         if f["id"].startswith("quote.")}
    price = q.get("price")
    if price:
        for k, eps in mean.items():
            if eps:
                vals[f"forecast.pe.{k}"] = round(price / eps, 1)
    return vals


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).replace(",", "")
        if _YEAR_RE.match(raw):
            continue
        out.append(float(raw))
    return out


def _match(n: float, index: dict[str, float]) -> str | None:
    # 按绝对值比对：中文正文对负值事实常写作"下滑1.95%"（无负号），
    # 数值提取亦不含符号；幅度对账是本层职责，方向表述由 judge 把关
    for fid, v in index.items():
        if v and abs(abs(v) - abs(n)) / max(abs(v), 1e-9) <= TOLERANCE:
            return fid
    return None


def _cited_ok(fid: str, index: dict[str, float]) -> bool:
    """mainop.X 前缀引用：索引中有 mainop.X.* 任一键即视为存在。"""
    if fid in index or fid.startswith("ann."):
        return True
    return fid.startswith("mainop.") and any(
        k.startswith(fid + ".") for k in index)


def _ann_numbers(doc: dict[str, Any], cited: list[str]) -> set[str]:
    """被引出处（公告原文/行业新闻标题）中的全部数字串，视为有出处。"""
    ann_codes = {a[len("ann."):] for a in cited if a.startswith("ann.")}
    out: set[str] = set()
    for a in doc["collections"].get("announcements") or []:
        if a["art_code"] in ann_codes:
            out.update(m.replace(",", "") for m in _NUM_RE.findall(a["content"]["text"]))
    news = doc["collections"].get("industry_news") or []
    for i, n in enumerate(news):
        if f"newsind.{i}" in cited:
            out.update(m.replace(",", "") for m in _NUM_RE.findall(n["title"]))
    return out


def reconcile(doc: dict[str, Any], outline: dict[str, Any],
              sections: list[dict[str, Any]],
              forecast: dict[str, Any], risks: dict[str, Any],
              spec: Any = None) -> dict[str, Any]:
    # 章节名来自 Spec v2 的章节 id（judge issues 的 target 与其一一对应），
    # 各章节独立解析（table/risk 可缺省）；spec 缺省时沿用股票模板命名
    views_id = "core_views"
    table_id, risk_id = "earnings_forecast", "risk_warning"
    if spec is not None:
        v, t, r = spec.section("views"), spec.section("table"), spec.section("risk")
        if v:
            views_id = v.id
        if t:
            table_id = t.id
        if r:
            risk_id = r.id
    index = _value_index(doc)
    checks: list[dict[str, Any]] = []

    _KNOWN_PREFIXES = ("fin.", "quote.", "mainop.", "ann.", "newsind.",
                       "consensus.", "forecast.")

    def check(name: str, body: str, cited: list[str]) -> None:
        # 引用清洗：剥 []、裸公告号补 ann. 前缀（norm_citation）；
        # 归一后仍不是任何已知前缀的记为 ignored（格式噪音），不算缺失
        norm, ignored = [], []
        for c in cited:
            t = norm_citation(c)
            if t is None:
                continue        # 模型偶发输出数字编号：直接丢弃
            if t.startswith(_KNOWN_PREFIXES):
                norm.append(t)
            else:
                ignored.append(t)
        bad_ids = [c for c in norm if not _cited_ok(c, index)]
        ann_nums = _ann_numbers(doc, norm)
        unknown, from_ann = [], 0
        for n in _numbers_in(body):
            if _match(n, index) is not None:
                continue
            # 白名单比对：完整数值串或整数串（公告文号/股数常为整数；
            # 不能只取整数部分比对——那会把 43.96 截成 43 而漏配）
            if str(n) in ann_nums or str(int(n)) in ann_nums:
                from_ann += 1
                continue
            unknown.append(n)
        checks.append({
            "section": name,
            "cited_ids": norm,
            "cited_missing": bad_ids,          # 引用了不存在的事实 → 硬问题
            "ignored_citations": ignored,      # 标题/散文式引用 → 格式噪音
            "numbers_in_text": len(_numbers_in(body)),
            "numbers_from_announcement": from_ann,
            "unknown_numbers": unknown,        # 对不上账的数字 → 人工复核
        })

    for s in sections:
        check(f"{views_id}.{s['slot_id']}", s["body"], s.get("cited_fact_ids") or [])
        if s.get("heading"):
            src = next((v["heading"] for v in outline["views"]
                        if v["slot_id"] == s["slot_id"]), None)
            if src is not None and src != s["heading"]:
                checks[-1]["heading_drift"] = {"outline": src, "written": s["heading"]}
    check(table_id, forecast["body"], forecast.get("cited_fact_ids") or [])
    check(risk_id, risks["body"], risks.get("cited_fact_ids") or [])

    hard = any(c["cited_missing"] for c in checks)
    warn = any(c["unknown_numbers"] or c.get("heading_drift")
               or c.get("ignored_citations") for c in checks)
    return {
        "status": "fail" if hard else ("warn" if warn else "pass"),
        "tolerance_pct": TOLERANCE * 100,
        "index_size": len(index),
        "checks": checks,
    }
