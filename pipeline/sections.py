"""④ 分节生成：每条观点独立 LLM 会话（上下文隔离），产出结构化段落。

- 每节只喂：大纲中该节的 heading/guidance + 该节引用的事实切片 + spec 的
  行文规则与本槽位/章节的范文（SpecV2.fewshot_for：槽位级 → 章节级）。
- 输出 JSON：{heading, body, cited_fact_ids}——cited 用于⑤对账。
- 槽位兜底事实由 spec 的 fallback 声明驱动（语法 source:selector:limit），
  大纲漏勾时按声明自动注入素材，防止模型凭常识填空。
- 表格节：由 spec.tables[].renderer 分发代码渲染器（LLM 不产数字）。
- 风险提示节：按 spec 风险章节的 strategy/style 生成。
"""

from typing import Any

from pipeline.llm import chat_json
from pipeline.reconcile import norm_citation
from template_factory.schema import SpecV2

SYSTEM = """你是{role}，撰写报告的一个段落。

报告类型：{description}

写作规范：
{rules}

【范文】
{fewshot}
"""

VIEW_TMPL = """本段视角：{slot_id}
结论小标题（必须以此开头）：{heading}
论证要求：{guidance}
视角职责：{brief}
写作要求：{style}

【允许引用的事实】（正文数字只能出自这里，逐个引用其编号）
{facts}

【背景素材】（可引用其中定性信息与专有名词，数字仍以上方事实为准）
{context}

写这一段。只输出 JSON：
{{"heading": "...", "body": "...", "cited_fact_ids": ["...", ...]}}
cited_fact_ids 列出 body 中数字实际用到的全部事实编号。
"""

FORECAST_TMPL = """以下是系统生成的预测表（正文所有预测数字以它为准）：

{table}

【最新业绩摘要】
{latest_summary}

【允许引用的事实编号】{fact_ids}

按以下要求写说明文字：
{style}

只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}
"""

RISK_TMPL = """【已定稿的核心观点】
{views}

【业务结构事实】（结构暴露，可引用其编号）
{structure}

【公告披露的风险线索】（公司特有风险）
{risk_clues}

按以下要求写风险提示：
{style}

只输出 JSON：{{"body": "条目1；条目2；条目3", "cited_fact_ids": [...]}}"""


def _structure_facts(doc: dict[str, Any]) -> str:
    """业务结构暴露：境外收入占比、第一大业务占比与毛利率、募投项目。"""
    coll = doc["collections"]
    lines = []
    regions = coll.get("mainop", {}).get("by_region") or []
    overseas = [it for it in regions if ("境外" in it["name"] or "海外" in it["name"])]
    if overseas:
        share = sum(it["income_ratio_pct"] or 0 for it in overseas)
        names = "、".join(it["name"] for it in overseas)
        lines.append(f"[mainop.境外收入合计] 境外收入（{names}）占比 {share:.2f}%")
    products = coll.get("mainop", {}).get("by_product") or []
    if products:
        top = products[0]
        lines.append(f"[mainop.{top['name']}] 第一大业务 {top['name']}："
                     f"占比 {top['income_ratio_pct']}%，毛利率 {top['gross_margin_pct']}%")
    for a in coll.get("announcements") or []:
        if "catalyst" in a.get("tags", []) and ("定增" in a["title"] or "向特定对象" in a["title"]):
            lines.append(f"- 募投事件：{a['title']}")
    return "\n".join(lines)


def _citations_resolvable(doc: dict[str, Any], ids: list[Any]) -> list[str]:
    """过滤引用清单：只保留能解析到真实事实的 id（剥[]/补前缀后逐一验证）。
    模型偶发编造"mainop.X占比"这类不存在的业务名——风险段常无数字，
    与其让装饰性引用炸掉对账，不如在生成侧丢弃（数字仍由⑤逐一对账）。"""
    scalar = {f["id"] for f in doc["facts"]}
    coll = doc["collections"]
    mainop_names = {it["name"] for dim in ("by_product", "by_region", "by_industry")
                    for it in (coll.get("mainop", {}).get(dim) or [])}
    regions = coll.get("mainop", {}).get("by_region") or []
    if any("境外" in it["name"] or "海外" in it["name"] for it in regions):
        mainop_names.add("境外收入合计")     # _structure_facts 的合成事实
    ann_codes = {a["art_code"] for a in coll.get("announcements") or []}
    n_news = len(coll.get("industry_news") or [])
    out: list[str] = []
    for raw in ids:
        fid = norm_citation(raw)
        if not fid:
            continue
        if fid in scalar:
            out.append(fid)
        elif fid.startswith("ann.") and fid[4:] in ann_codes:
            out.append(fid)
        elif fid.startswith("newsind.") and fid[8:].isdigit() and int(fid[8:]) < n_news:
            out.append(fid)
        elif fid.startswith("mainop.") and fid[7:] in mainop_names:
            out.append(fid)
    return out


def _system(spec: SpecV2, fewshot: str) -> str:
    return SYSTEM.format(role=spec.writer_role, description=spec.description,
                         rules=spec.rules_text(), fewshot=fewshot)


def gen_risks(doc: dict[str, Any], spec: SpecV2,
              views: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    risk_sec = spec.section("risk")
    if risk_sec is None:
        raise ValueError("spec 缺少 risk 章节")
    coll = doc["collections"]
    views_text = "\n".join(
        f"{i}. {v['heading']}：{v['body'][:120]}"
        for i, v in enumerate(views or [], 1)) or "（无）"
    structure = _structure_facts(doc)
    risk_anns = [a for a in coll.get("announcements") or [] if "risk_legal" in a.get("tags", [])]
    clues = "\n".join(f"- {a['date']} {a['title']}" for a in risk_anns[:5]) or "（无）"
    user = RISK_TMPL.format(views=views_text, structure=structure or "（无）",
                            risk_clues=clues, style=risk_sec.style or "")
    out = chat_json(_system(spec, spec.fewshot_for(risk_sec)), user,
                    schema_hint='只输出 JSON：body/cited_fact_ids。')
    out["cited_fact_ids"] = _citations_resolvable(doc, out.get("cited_fact_ids") or [])
    return out


def _fact_block(doc: dict[str, Any], fact_ids: list[str]) -> tuple[str, list[dict]]:
    """按 id 汇编事实切片，返回（展示文本, 事实对象列表）。收集标量事实与
    mainop 行、公告摘录、行业新闻四类。"""
    coll = doc["collections"]
    lines, objs = [], []
    scalar = {f["id"]: f for f in doc["facts"]}
    for raw_id in fact_ids:
        fid = norm_citation(raw_id)
        if not fid:
            continue
        if fid in scalar:
            f = scalar[fid]
            lines.append(f"[{fid}] {f['name']} = {f['value']}{f['unit']}")
            objs.append(f)
        elif fid.startswith("mainop."):
            biz = fid[len("mainop."):]
            for dim in ("by_product", "by_region", "by_industry"):
                for it in coll.get("mainop", {}).get(dim) or []:
                    if it["name"] == biz:
                        margin = it["gross_margin_pct"]
                        margin_text = f"毛利率 {margin}%" if margin is not None else "毛利率未披露"
                        line = (f"[{fid}] {it['name']}：收入 {it['income_yi']}亿 "
                                f"占比 {it['income_ratio_pct']}% {margin_text}")
                        lines.append(line)
                        extra = {"占比%": it["income_ratio_pct"]}
                        if margin is not None:
                            extra["毛利率%"] = margin
                        objs.append({"id": fid, "name": it["name"],
                                     "value": it["income_yi"], "unit": "亿元",
                                     "extra": extra})
        elif fid.startswith("ann."):
            code = fid[len("ann."):]
            for a in coll.get("announcements") or []:
                if a["art_code"] == code:
                    body = a.get("digest") or (a["content"]["text"] or "")[:300]
                    lines.append(f"[{fid}] {a['date']} {a['title']}\n    "
                                 + body.replace("\n", " "))
                    objs.append({"id": fid, "name": a["title"], "value": None, "unit": ""})
        elif fid.startswith("newsind."):
            idx = int(fid[len("newsind."):])
            items = coll.get("industry_news") or []
            if idx < len(items):
                it = items[idx]
                lines.append(f"[{fid}] {it['time'][:10]} 行业新闻：{it['title']}")
                objs.append({"id": fid, "name": it["title"], "value": None, "unit": ""})
    return "\n".join(lines) or "（无，请写定性叙述且不出现数字）", objs


def _resolve_fallback(doc: dict[str, Any], decl: str) -> list[str]:
    """解析 fallback 声明（"source:selector:limit"）为事实 id 列表。

    fin:latest:8        → 最新报告期的 fin.* 标量事实前 8 条
    mainop:by_product:3 → 分产品（或其他维度）前 3 行
    ann:catalyst:3      → tags 含 catalyst 的公告前 3 篇
    newsind:top:5       → 行业新闻前 5 条（selector 忽略）
    """
    parts = decl.split(":")
    if len(parts) != 3:
        raise ValueError(f"fallback 声明格式应为 source:selector:limit，得到 {decl!r}")
    source, selector, limit = parts
    n = int(limit)
    coll = doc["collections"]
    if source == "fin":
        latest = (coll.get("periods") or [""])[0].get("period", "") if coll.get("periods") else ""
        return [f["id"] for f in doc["facts"]
                if f["id"].startswith(f"fin.{latest}.")][:n]
    if source == "mainop":
        return [f"mainop.{it['name']}"
                for it in (coll.get("mainop", {}).get(selector) or [])[:n]]
    if source == "ann":
        return [f"ann.{a['art_code']}" for a in coll.get("announcements") or []
                if selector in a.get("tags", [])][:n]
    if source == "newsind":
        return [f"newsind.{i}" for i in range(min(n, len(coll.get("industry_news") or [])))]
    return []


def _default_facts(doc: dict[str, Any], spec: SpecV2, slot_id: str) -> list[str]:
    """槽位兜底事实：按 spec 的 fallback 声明解析；无声明则返回空。"""
    views_sec = spec.section("views")
    if views_sec is None:
        return []
    slot = next((v for v in views_sec.view_slots if v.id == slot_id), None)
    if slot is None:
        return []
    out: list[str] = []
    for decl in slot.fallback:
        out.extend(_resolve_fallback(doc, decl))
    return out


def gen_view(doc: dict[str, Any], view: dict[str, Any],
             spec: SpecV2) -> dict[str, Any]:
    views_sec = spec.section("views")
    if views_sec is None:
        raise ValueError("spec 缺少 views 章节")
    briefs = doc.get("slot_briefs") or {s.id: s.brief for s in views_sec.view_slots}
    brief = briefs.get(view["slot_id"], "") if isinstance(briefs, dict) else ""
    raw = view.get("cited_fact_ids") or []
    cited = [t for t in (norm_citation(x) for x in raw) if t] \
        or _default_facts(doc, spec, view["slot_id"])
    view["cited_fact_ids"] = cited
    facts_text, _ = _fact_block(doc, cited)
    context_lines = [f"{n['time'][:10]} {n['title']}"
                     for n in (doc["collections"].get("news") or [])[:8]]
    user = VIEW_TMPL.format(
        slot_id=view["slot_id"], heading=view["heading"],
        guidance=view.get("guidance", ""), brief=brief,
        style=views_sec.view_style or "",
        facts=facts_text, context="\n".join(context_lines) or "（无）",
    )
    out = chat_json(_system(spec, spec.fewshot_for(views_sec, view["slot_id"])),
                    user, schema_hint='只输出 JSON 对象：heading/body/cited_fact_ids 三个字段。')
    out["slot_id"] = view["slot_id"]
    return out


def forecast_table(doc: dict[str, Any], tpl: Any = None) -> str:
    """consensus_pe 渲染器：一致预期 EPS 均值 + 现价推算 PE（LLM 不碰数字）。"""
    q = {f["id"].split(".")[1]: f["value"] for f in doc["facts"]
         if f["id"].startswith("quote.")}
    price = q.get("price")
    mean = doc["collections"]["consensus"].get("mean_eps_forecast", {})
    rows = []
    for label, key in (("今年", "predictThisYearEps"),
                       ("明年", "predictNextYearEps"),
                       ("后年", "predictNextTwoYearEps")):
        eps = mean.get(key)
        pe = round(price / eps, 1) if (eps and price) else None
        rows.append((label, eps, pe))
    head = "| 指标 | " + " | ".join(r[0] for r in rows) + " |"
    sep = "|---" * (len(rows) + 1) + "|"
    eps_row = "| EPS(元) | " + " | ".join(str(r[1]) for r in rows) + " |"
    pe_row = "| PE(倍) | " + " | ".join(str(r[2]) for r in rows) + " |"
    note = (f"\n（{doc['collections']['consensus'].get('n_orgs', 0)} 家券商预测均值，"
            f"按现价 {price} 元计算）")
    return "\n".join([head, sep, eps_row, pe_row]) + note


def _generic_rows_table(doc: dict[str, Any], tpl: Any) -> str:
    """generic_rows 渲染器：adapter 产出的 collections["tables"][id] → markdown。"""
    data = (doc["collections"].get("tables") or {}).get(tpl.id)
    if not data or not data.get("rows"):
        return "（表格数据缺失）"
    columns = data.get("columns") or [f"列{i+1}" for i in range(len(data["rows"][0]))]
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "---|" * len(columns)
    body = "\n".join("| " + " | ".join(str(c) for c in row) + " |"
                     for row in data["rows"])
    return "\n".join([head, sep, body])


_RENDERERS = {"consensus_pe": forecast_table, "generic_rows": _generic_rows_table}


def render_table(doc: dict[str, Any], spec: SpecV2) -> str:
    """按 spec 的 table 章节 → tables[].renderer 分发代码渲染器。"""
    sec = spec.section("table")
    if sec is None:
        return ""
    tpl = spec.table(sec.table)
    if tpl is None or tpl.renderer not in _RENDERERS:
        raise ValueError(f"表格渲染器未注册：{sec.table} / {tpl and tpl.renderer}")
    return _RENDERERS[tpl.renderer](doc, tpl)


def gen_forecast_note(doc: dict[str, Any], spec: SpecV2) -> dict[str, Any]:
    table_sec = spec.section("table")
    if table_sec is None:
        raise ValueError("spec 缺少 table 章节")
    coll = doc["collections"]
    latest = (coll.get("periods") or [{}])[0]
    summary = (f"{latest.get('period_name', '')}营收 {latest.get('revenue_yi')}亿"
               f"（同比{latest.get('revenue_yoy_pct')}%）、归母净利 "
               f"{latest.get('netprofit_yi')}亿（同比{latest.get('netprofit_yoy_pct')}%）")
    ids = [f["id"] for f in doc["facts"] if f["id"].startswith(f"fin.{latest.get('period', 'X')}.")]
    user = FORECAST_TMPL.format(table=render_table(doc, spec), latest_summary=summary,
                                fact_ids=", ".join(ids[:8]), style=table_sec.style or "")
    return chat_json(_system(spec, spec.fewshot_for(table_sec)),
                     user, schema_hint="只输出 JSON：body/cited_fact_ids。")
