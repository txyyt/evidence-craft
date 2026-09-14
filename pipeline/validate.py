"""⑥ 规则校验器（纯代码，零 LLM）：结构/字数/术语/口径四类规则。

与对账层（reconcile）分工：对账管"数字对不对"，这里管"报告合不合格"。
全部参数来自 Spec v2：字数区间/结尾词/条数在 sections[].check 与 title_check，
禁用词、受控词表、派生规则开关在 spec 顶层字段——本模块不含任何业务专属常量。
"""

import re
from datetime import datetime
from typing import Any

from template_factory.schema import SpecV2

_now = datetime.now()

# 通识数字出口：全角中括号标记（〔400～600℃〕），全篇限量
_GENKNOW_RE = re.compile(r"〔[^〕]{1,40}〕")
_GENKNOW_CAP = 3
# 时效性：将来时措辞 + 四位年份（stale_forecast 规则）
_FUTURE_RE = re.compile(
    r"(预计到|预计于|将于|将达|将达到|有望于|预计(?:(?:到|于)?\s*20\d{2}\s*年?))"
    r"[^\d。；;]{0,12}(20\d{2})\s*年?")
# 历史引述前缀（句内出现即视为回顾旧预测，不判将来时失效）
_HISTORY_RE = re.compile(r"此前|曾经?|据[^。；;]{0,10}(预测|研究|统计)|历史上|当年")


def _stale_forecasts(text: str, current_year: int) -> list[str]:
    """正文里"将来时 + 目标年份已过"的片段（历史引述除外）。

    例："预计到2025年需求将达38.31万t"（今年 2026）→ 命中；
    "此前研究预测到2025年将达38.31万t"→ 历史引述，不命中。"""
    out: list[str] = []
    for m in _FUTURE_RE.finditer(text):
        target = int(m.group(2))
        if target >= current_year:
            continue
        # 只回看所在句（上一个句号/分号之后），句内有历史引述词则豁免
        start = max(text.rfind("。", 0, m.start()),
                    text.rfind("；", 0, m.start()),
                    text.rfind(";", 0, m.start())) + 1
        sentence = text[start:m.end()]
        if _HISTORY_RE.search(sentence):
            continue
        out.append(f"{m.group(0).strip()}（目标年份 {target} 已过）")
    return out


def _charlen(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _range(check: Any, key: str, default: tuple[int, int]) -> tuple[int, int]:
    v = getattr(check, key, None)
    return tuple(v) if v else default


def run(doc: dict[str, Any], outline: dict[str, Any],
        views: list[dict[str, Any]], forecast: dict[str, Any],
        risks: dict[str, Any], rating: str, spec: SpecV2,
        crosscheck: dict[str, Any] | None = None,
        texts: list[dict[str, Any]] | None = None,
        notes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    views_sec = spec.section("views")
    table_sec = spec.section("table")
    risk_sec = spec.section("risk")

    def rule(rid: str, ok: bool | None, detail: str,
             level: str = "fail", metric: int | None = None) -> None:
        if ok is not None and not ok:
            item = {"rule": rid, "status": level, "detail": detail}
            if metric is not None:
                item["metric"] = metric
            items.append(item)

    # —— 标题（参数：spec.title_check）——
    lo, hi = _range(spec.title_check, "title_len", (10, 40))
    rule("structure.title_len", lo <= _charlen(outline["title"]) <= hi,
         f"标题 {len(outline['title'])} 字")
    if spec.title_check.require_comma:
        rule("structure.title_comma", "，" in outline["title"],
             "结论式标题应以逗号分隔两个判断")

    # —— 核心观点（参数：views 章节）——
    if views_sec is not None:
        rule("structure.n_views", len(views) == views_sec.n_views,
             f"{views_sec.title} {len(views)} 条（应为 {views_sec.n_views}）")
    hlo, hhi = _range(views_sec.check if views_sec else None, "heading_len", (6, 20))
    blo, bhi = _range(views_sec.check if views_sec else None, "body_len", (80, 230))
    all_text = outline["title"] + "".join(v["body"] for v in views) + \
        forecast["body"] + risks["body"]
    for v in views:
        rule(f"structure.{v['slot_id']}.heading_len",
             hlo <= len(v["heading"]) <= hhi, f"小标题{len(v['heading'])}字")
        n = _charlen(v["body"])
        rule(f"structure.{v['slot_id']}.body_len", blo <= n <= bhi,
             f"正文 {n} 字（要求 {blo}~{bhi}）", metric=n)

    # —— 表格节说明文字（多表格逐节校验；未传 notes 沿用单表格路径）——
    if notes:
        table_secs = {s.id: s for s in spec.sections_of("table")}
        for tid, note in notes.items():
            sec = table_secs.get(tid)
            if sec is None:
                continue
            flo, fhi = _range(sec.check, "body_len", (60, 200))
            rule(f"length.{tid}", flo <= _charlen(note.get("body", "")) <= fhi,
                 f"{sec.title} 说明 {len(note.get('body', ''))} 字")
    elif table_sec is not None:
        flo, fhi = _range(table_sec.check, "body_len", (60, 200))
        rule("length.forecast", flo <= _charlen(forecast["body"]) <= fhi,
             f"预测说明 {len(forecast['body'])} 字")

    # —— 综述/图件章节说明文字（text 章节，参数在各章节 check.body_len）——
    text_secs = {s.id: s for s in spec.sections_of("text")}
    for t in texts or []:
        sec = text_secs.get(t.get("section_id"))
        if sec is None:
            continue
        tlo, thi = _range(sec.check, "body_len", (150, 1600))
        n = _charlen(t["body"])
        rule(f"length.{sec.id}", tlo <= n <= thi,
             f"{sec.title} 正文 {n} 字（要求 {tlo}~{thi}）", metric=n)

    # —— 时效性（text 章节 freshness_days：被引事实 as_of 距今不超 N 天，warn 级）——
    facts_by_id = {f["id"]: f for f in doc["facts"]}

    def _as_of_date(v: Any):
        v = str(v or "")
        if "检索时点" in v:
            return _now
        m = re.match(r"(\d{4})[-/年](\d{1,2})?", v)
        if m:
            return datetime(int(m.group(1)), min(int(m.group(2) or 12), 12), 1)
        return None

    for t in texts or []:
        sec = text_secs.get(t.get("section_id"))
        if sec is None or not sec.freshness_days:
            continue
        stale = []
        for fid in t.get("cited_fact_ids") or []:
            f = facts_by_id.get(fid)
            d = _as_of_date(f.get("as_of")) if f else None
            if d and (_now - d).days > sec.freshness_days:
                stale.append(f"{fid}({f.get('as_of')})")
        if stale:
            items.append({"rule": f"freshness.{sec.id}", "status": "warn",
                          "detail": f"{sec.title} 引用超期事实 "
                                    f"{'、'.join(stale[:5])}（要求 ≤{sec.freshness_days} 天）"})

    # —— 风险提示（参数：risk 章节 check；章节缺省则整组跳过）——
    if risk_sec is not None:
        clo, chi = _range(risk_sec.check, "count", (3, 5))
        suffix = risk_sec.check.item_suffix or "风险"
        # 注意 is not None：min_shaped=0 是合法配置（不要求结尾词），不能 or 回退
        min_shaped = risk_sec.check.min_shaped \
            if risk_sec.check.min_shaped is not None else 3
        risk_items = [r for r in re.split(r"[;；]", risks["body"]) if r.strip()]
        rule("risk.count", clo <= len(risk_items) <= chi, f"{len(risk_items)} 条")
        risk_shaped = sum(1 for r in risk_items if r.strip().endswith(suffix))
        rule("risk.format", risk_shaped >= min_shaped,
             f"{risk_shaped}/{len(risk_items)} 条以'{suffix}'结尾")

    # —— 术语：禁用词（spec.forbidden_words）——
    hits = [w for w in spec.forbidden_words if w in all_text]
    rule("style.forbidden", not hits, f"禁用词 {hits}", level="warn")

    # —— 通识数字出口限量：全篇〔...〕标记至多 3 处（缺省启用，
    #     spec.check_rules 写 "genknow_cap_off" 关闭）——
    if "genknow_cap_off" not in spec.check_rules:
        full_text = all_text + "".join(t.get("body", "") for t in texts or [])
        marks = _GENKNOW_RE.findall(full_text)
        rule("style.genknow_cap", len(marks) <= _GENKNOW_CAP,
             f"通识数值标记 {len(marks)} 处（上限 {_GENKNOW_CAP}）："
             + "、".join(m[:20] for m in marks[:_GENKNOW_CAP + 1]),
             metric=len(marks))

    # —— 时效性：将来时 + 过期年份（stale_forecast，spec.check_rules 开关，
    #     缺省启用；写 "stale_forecast_off" 关闭）——
    if "stale_forecast_off" not in spec.check_rules:
        year = _now.year
        bodies = [outline["title"], forecast["body"], risks["body"]] \
            + [v.get("body", "") for v in views] \
            + [t.get("body", "") for t in texts or []] \
            + [n.get("body", "") for n in (notes or {}).values()
               if isinstance(n, dict)]
        stale_all: list[str] = []
        for b in bodies:
            stale_all.extend(_stale_forecasts(b, year))
        rule("stale_forecast", not stale_all,
             "把目标年份已过的预测写成将来时（应改为历史口径，如'此前研究预测到"
             f"2025年…'）：{'；'.join(stale_all[:4])}", metric=len(stale_all))

    # —— 受控词表（spec.controlled_vocab）——
    rating_vocab = spec.controlled_vocab.get("rating")
    if rating_vocab:
        rule("rating.valid", rating in rating_vocab, f"评级 '{rating}'")

    # —— 派生口径规则（spec.check_rules 开关，股票场景专属）——
    if "pe_ttm_suspect" in spec.check_rules or "pe_spread" in spec.check_rules:
        q = {f["id"].split(".")[1]: f["value"] for f in doc["facts"]
             if f["id"].startswith("quote.")}
        pe_ttm, pe_static, pe_dyn = (q.get("pe_ttm"), q.get("pe_static"),
                                     q.get("pe_dynamic"))
        latest = (doc["collections"].get("periods") or [{}])[0]
        growing = (latest.get("netprofit_yoy_pct") or 0) > 10
        if "pe_ttm_suspect" in spec.check_rules and pe_ttm and pe_static and pe_dyn:
            # 增长期正常排序 dynamic ≤ ttm ≤ static（利润越新越高）；ttm 超 static
            # 说明接口 TTM 口径可疑（本例按 2025 全年利润计）
            if growing and pe_ttm > pe_static * 1.05:
                items.append({"rule": "derived.pe_ttm_suspect", "status": "warn",
                              "detail": f"PE-TTM({pe_ttm}) 高于静态PE({pe_static})，"
                                        f"接口TTM口径疑点，正文避免引用该字段"})
        if "pe_spread" in spec.check_rules and pe_ttm and pe_dyn:
            if abs(pe_ttm - pe_dyn) / max(pe_dyn, 1e-9) > 3:
                items.append({"rule": "derived.pe_spread", "status": "warn",
                              "detail": f"PE-TTM({pe_ttm}) 与动态PE({pe_dyn}) 相差超3倍，"
                                        f"利润基数低所致，正文引用估值需注明口径"})
    if "pe_vs_consensus" in spec.check_rules:
        q = {f["id"].split(".")[1]: f["value"] for f in doc["facts"]
             if f["id"].startswith("quote.")}
        mean_pe = doc["collections"]["consensus"].get("mean_eps_forecast", {})
        if q.get("price") and mean_pe.get("predictThisYearEps"):
            implied = q["price"] / mean_pe["predictThisYearEps"]
            report_pe = ((doc["collections"].get("consensus") or {})
                         .get("reports", [{}])[0]
                         .get("pe_forecast", {}).get("predictThisYearPe"))
            if report_pe and abs(implied - report_pe) / report_pe > 0.5:
                items.append({"rule": "derived.pe_vs_consensus", "status": "warn",
                              "detail": f"推算PE({implied:.1f}) 与券商预测PE({report_pe})"
                                        f"偏差>50%，多为EPS口径差异（摊薄/全面摊薄）"})

    if crosscheck and crosscheck.get("status") == "fail":
        items.append({"rule": "data.crosscheck", "status": "fail",
                      "detail": "数据层交叉校验失败"})

    hard = [i for i in items if i["status"] == "fail"]
    return {
        "status": "fail" if hard else ("warn" if items else "pass"),
        "items": items,
    }
