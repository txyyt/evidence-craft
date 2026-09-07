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
# 排除紧贴字母的数字（"2026H1"的H1、"Q2"）与计数量词前的数字（"3家券商"）
_NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?(?![家条个月])")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _value_index(doc: dict[str, Any]) -> dict[str, float]:
    vals: dict[str, float] = {}
    for f in doc["facts"]:
        if isinstance(f.get("value"), (int, float)):
            vals[f["id"]] = float(f["value"])
    for dim in ("by_product", "by_region", "by_industry"):
        for it in doc["collections"]["mainop"].get(dim) or []:
            prefix = f"mainop.{it['name']}"
            vals[f"{prefix}.income_yi"] = it["income_yi"]
            vals[f"{prefix}.income_ratio_pct"] = it["income_ratio_pct"]
            vals[f"{prefix}.gross_margin_pct"] = it["gross_margin_pct"]
    regions = doc["collections"]["mainop"].get("by_region") or []
    overseas = [it for it in regions
                if "境外" in it["name"] or "海外" in it["name"]]
    if overseas:
        vals["mainop.境外收入合计"] = round(
            sum(it["income_ratio_pct"] or 0 for it in overseas), 2)
    mean = doc["collections"]["consensus"].get("mean_eps_forecast", {})
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
    for fid, v in index.items():
        if v and abs(v - n) / max(abs(v), 1e-9) <= TOLERANCE:
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
    for a in doc["collections"]["announcements"]:
        if a["art_code"] in ann_codes:
            out.update(m.replace(",", "") for m in _NUM_RE.findall(a["content"]["text"]))
    news = doc["collections"].get("industry_news") or []
    for i, n in enumerate(news):
        if f"newsind.{i}" in cited:
            out.update(m.replace(",", "") for m in _NUM_RE.findall(n["title"]))
    return out


def reconcile(doc: dict[str, Any], outline: dict[str, Any],
              sections: list[dict[str, Any]],
              forecast: dict[str, Any], risks: dict[str, Any]) -> dict[str, Any]:
    index = _value_index(doc)
    checks: list[dict[str, Any]] = []

    _KNOWN_PREFIXES = ("fin.", "quote.", "mainop.", "ann.", "newsind.",
                       "consensus.", "forecast.")

    def check(name: str, body: str, cited: list[str]) -> None:
        # 引用清洗：模型偶发把"[mainop.x]"连括号抄写、或把标题当 id——归一化，
        # 归一后仍不是任何已知前缀的记为 ignored（格式噪音），不算缺失
        norm = []
        for c in cited:
            if not isinstance(c, str):
                continue        # 模型偶发输出数字编号：直接丢弃
            c = c.strip().strip("[]").strip()
            if c.startswith(_KNOWN_PREFIXES):
                norm.append(c)
        bad_ids = [c for c in norm if not _cited_ok(c, index)]
        ignored = [c for c in cited
                   if isinstance(c, str)
                   and c.strip().strip("[]").strip() not in norm
                   and not c.strip().strip("[]").strip().startswith(_KNOWN_PREFIXES)]
        ann_nums = _ann_numbers(doc, norm)
        unknown, from_ann = [], 0
        for n in _numbers_in(body):
            if _match(n, index) is not None:
                continue
            if str(n).split(".")[0] in ann_nums or str(int(n)) in ann_nums:
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
        check(f"core_views.{s['slot_id']}", s["body"], s.get("cited_fact_ids") or [])
        if s.get("heading"):
            src = next((v["heading"] for v in outline["views"]
                        if v["slot_id"] == s["slot_id"]), None)
            if src is not None and src != s["heading"]:
                checks[-1]["heading_drift"] = {"outline": src, "written": s["heading"]}
    check("earnings_forecast", forecast["body"], forecast.get("cited_fact_ids") or [])
    check("risk_warning", risks["body"], risks.get("cited_fact_ids") or [])

    hard = any(c["cited_missing"] for c in checks)
    warn = any(c["unknown_numbers"] or c.get("heading_drift")
               or c.get("ignored_citations") for c in checks)
    return {
        "status": "fail" if hard else ("warn" if warn else "pass"),
        "tolerance_pct": TOLERANCE * 100,
        "index_size": len(index),
        "checks": checks,
    }
