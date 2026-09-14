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
# 排除紧贴字母/数字的截断匹配（"4N8级"的4、"174号"回溯成"17"）与计数/
# 日期单位词前的数字（"3家券商"、"25件样品"、"4月10日"——不是量值）。
# 前瞻含 \d 是关键：多位数被计数词拦截后禁止回溯成更短的数（173种→17）。
# 小数分支（\.\d+）后仅禁跟数字、允许字母——单位直接贴数的量值（"0.97g/t"
# "4.3m"）若沿用整数分支的禁字母前瞻会回溯截断成 0/4，两分支各守各的。
_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:,\d{3})*"
    r"(?:\.\d+(?!\d)|(?![A-Za-z\d家条个月日号页件份个批次组种类项处]))")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
# 正文中的引用标注在数字提取前剥除——编号里的"01.02"会被读成数值 1.02，
# 纯属对账噪音。两种形态：方括号/【】（[rag.01.02]、【web.3】），以及模型
# 偏爱使用的全角括号+点分编号（（rag.05.15）（web.02.05、web.02.06）
# （grades.SiO2≥99.995%.purity、grades.SiO2≥99.998%.purity）——特征：
# 括号内无空白且含点号（总长放宽到 80，双引用连写也盖住）。
_CITE_RE = re.compile(
    r"[\[\【][^\]\】]{1,24}[\]\】]"
    r"|(?=[（(][^\s（）()]*\.)[（(][^\s（）()]{1,80}[）)]")
# 标准编号（DZ/T 0467、JC/T 1048—2018 等）：编号里的数字不是量值
_STDNO_RE = re.compile(r"[A-Z]{1,4}/T\s?\d{3,4}(?:[—-]\d{4})?")
# 通识数字出口：写作层用全角中括号〔...〕标记非事实清单支撑的通识性数值
# （如〔400～600℃〕，全篇限量由 validate 把关）——标记内数字不参与对账
_GENKNOW_RE = re.compile(r"〔[^〕]{1,40}〕")
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
    text = _GENKNOW_RE.sub("", text)    # 通识标记内数字不参与对账
    text = _CITE_RE.sub("", text)
    text = _STDNO_RE.sub("", text)
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


def _table_numbers(doc: dict[str, Any], tid: str) -> set[str]:
    """系统生成表格（collections.tables[tid]）单元格中的全部数字串。

    表格数字来自绑定数据源（xlsx 等，自带来源文献列），会原样渲染进报告——
    说明文字引用本表单元格数字即视为有出处（否则意图规划换了联网查询后，
    历史上恰好同值的联网事实不在了，表内数字反成对不上账的孤儿）。"""
    data = (doc["collections"].get("tables") or {}).get(tid)
    if not data:
        return set()
    out: set[str] = set()
    for row in data.get("rows") or []:
        for cell in row:
            out.update(m.replace(",", "") for m in _NUM_RE.findall(str(cell)))
    return out


def _reference_numbers(spec: Any) -> set[float]:
    """范文豁免集：对标范文（judge_reference 全文）与所有章节/槽位 fewshot
    中的数值集合。

    范文是用户提供的权威样本——写作提示词拿它教模型行文，模型复述范文中的
    数值（如"400～600℃成岩温度"）不应被"数字必有出处"拦下（范文数字的
    出处就是范文本身）。豁免集只参与对账放行，不进事实清单（不污染
    cited_fact_ids/溯源附录/index_size）。"""
    texts: list[str] = []
    if spec is not None:
        ref = getattr(spec, "judge_reference", None)
        if ref:
            try:
                from datalayer.settings import settings
                p = settings.resolve(str(ref))
                if p.is_file():
                    texts.append(p.read_text(encoding="utf-8"))
            except OSError:
                pass
        for sec in getattr(spec, "sections", []) or []:
            if getattr(sec, "fewshot", None):
                texts.append(sec.fewshot)
            for slot in getattr(sec, "view_slots", []) or []:
                if getattr(slot, "fewshot", None):
                    texts.append(slot.fewshot)
    out: set[float] = set()
    for t in texts:
        for m in _NUM_RE.finditer(_CITE_RE.sub("", _STDNO_RE.sub("", t))):
            raw = m.group(0).replace(",", "")
            if not _YEAR_RE.match(raw):
                out.add(float(raw))
    return out


def _match_ref(n: float, ref_vals: list[float]) -> bool:
    """范文豁免匹配（与事实索引同容差，按绝对值比对）。"""
    return any(abs(abs(v) - abs(n)) / max(abs(v), 1e-9) <= TOLERANCE
               for v in ref_vals)


def reconcile(doc: dict[str, Any], outline: dict[str, Any],
              sections: list[dict[str, Any]],
              forecast: dict[str, Any], risks: dict[str, Any],
              spec: Any = None,
              texts: list[dict[str, Any]] | None = None,
              notes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
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
    ref_vals = sorted(_reference_numbers(spec), reverse=True)
    checks: list[dict[str, Any]] = []
    n_ref_exempt = 0

    _KNOWN_PREFIXES = ("fin.", "quote.", "mainop.", "ann.", "newsind.",
                       "consensus.", "forecast.", "rag.", "web.")

    def check(name: str, body: str, cited: list[str],
              extra_ok: set[str] | None = None) -> None:
        # 引用清洗：剥 []、裸公告号补 ann. 前缀（norm_citation）；
        # 已知前缀或能命中数值索引的 id 均算可解析（xlsx 等泛化 adapter 的
        # 三段式 id 如 consum.光伏.share 不在硬编码前缀里，但在索引中）；
        # 归一后仍不认识的记为 ignored（格式噪音），不算缺失
        norm, ignored = [], []
        for c in cited:
            t = norm_citation(c)
            if t is None:
                continue        # 模型偶发输出数字编号：直接丢弃
            if t.startswith(_KNOWN_PREFIXES) or t in index:
                norm.append(t)
            else:
                ignored.append(t)
        bad_ids = [c for c in norm if not _cited_ok(c, index)]
        ann_nums = _ann_numbers(doc, norm) | (extra_ok or set())
        unknown, from_ann, ref_exempt = [], 0, 0
        for n in _numbers_in(body):
            if _match(n, index) is not None:
                continue
            # 白名单比对：完整数值串或整数串（公告文号/股数常为整数；
            # 不能只取整数部分比对——那会把 43.96 截成 43 而漏配）
            if str(n) in ann_nums or str(int(n)) in ann_nums:
                from_ann += 1
                continue
            # 范文豁免：数字出自己在对标范文/槽位 fewshot 中（同容差匹配），
            # 属"复述范文"而非编造——放行并计数，不进 unknown
            if _match_ref(n, ref_vals):
                ref_exempt += 1
                continue
            unknown.append(n)
        nonlocal n_ref_exempt
        n_ref_exempt += ref_exempt
        checks.append({
            "section": name,
            "cited_ids": norm,
            "cited_missing": bad_ids,          # 引用了不存在的事实 → 硬问题
            "ignored_citations": ignored,      # 标题/散文式引用 → 格式噪音
            "numbers_in_text": len(_numbers_in(body)),
            "numbers_from_announcement": from_ann,
            "numbers_ref_exempt": ref_exempt,  # 范文豁免命中（复述范文数字）
            "unknown_numbers": unknown,        # 对不上账的数字 → 人工复核
        })

    for s in sections:
        check(f"{views_id}.{s['slot_id']}", s["body"], s.get("cited_fact_ids") or [])
        if s.get("heading"):
            src = next((v["heading"] for v in outline["views"]
                        if v["slot_id"] == s["slot_id"]), None)
            if src is not None and src != s["heading"]:
                checks[-1]["heading_drift"] = {"outline": src, "written": s["heading"]}
    # 多表格章节：逐节对账说明文字（该节表格的单元格数字视为有出处）；
    # 未传 notes 时沿用单表格（forecast）路径
    if notes:
        for tid, note in notes.items():
            sec = spec.section_by_id(tid) if spec is not None else None
            tbl = sec.table if sec is not None else None
            check(tid, note.get("body", ""), note.get("cited_fact_ids") or [],
                  extra_ok=_table_numbers(doc, tbl) if tbl else None)
    else:
        sec = spec.section("table") if spec is not None else None
        tbl = sec.table if sec is not None else None
        check(table_id, forecast["body"], forecast.get("cited_fact_ids") or [],
              extra_ok=_table_numbers(doc, tbl) if tbl else None)
    check(risk_id, risks["body"], risks.get("cited_fact_ids") or [])
    for t in texts or []:
        check(t["section_id"], t["body"], t.get("cited_fact_ids") or [])

    hard = any(c["cited_missing"] for c in checks)
    warn = any(c["unknown_numbers"] or c.get("heading_drift")
               or c.get("ignored_citations") for c in checks)
    return {
        "status": "fail" if hard else ("warn" if warn else "pass"),
        "tolerance_pct": TOLERANCE * 100,
        "index_size": len(index),
        "n_ref_exempt": n_ref_exempt,   # 范文豁免命中总数（审计用）
        "checks": checks,
    }
