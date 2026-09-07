"""④ 分节生成：每条观点独立 LLM 会话（上下文隔离），产出结构化段落。

- 每节只喂：大纲中该节的 heading/guidance + 该节引用的事实切片 + 写作规范。
- 输出 JSON：{heading, body, cited_fact_ids}——cited 用于⑤对账。
- 盈利预测节：表格由代码从一致预期渲染（LLM 不产数字），LLM 只写说明文字。
- 风险提示节：小调用生成 3~5 条。
"""

from typing import Any

from pipeline.llm import chat_json
from pipeline.outline import load_spec

SYSTEM = """你是资深卖方分析师，撰写上市公司点评报告的一个段落。

写作规范：
{rules}

【范文】
{fewshot}
"""

VIEW_TMPL = """本段视角：{slot_id}
结论小标题（必须以此开头）：{heading}
论证要求：{guidance}
视角职责：{brief}

【允许引用的事实】（正文数字只能出自这里，逐个引用其编号）
{facts}

【背景素材】（可引用其中定性信息与专有名词，数字仍以上方事实为准）
{context}

写这一段（100~180字）。只输出 JSON：
{{"heading": "...", "body": "...", "cited_fact_ids": ["...", ...]}}
cited_fact_ids 列出 body 中数字实际用到的全部事实编号。
"""

FORECAST_TMPL = """以下是系统生成的盈利预测表（正文所有预测数字以它为准）：

{table}

结合最新业绩（{latest_summary}）写一段 80~150 字说明：点出预测期归母净利润
趋势与对应 PE，落脚到估值判断。允许引用的事实编号：{fact_ids}
只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}
"""

RISK_TMPL = """【已定稿的核心观点】（风险 = 每条观点依赖假设的反面，逐条审视）
{views}

【业务结构事实】（结构暴露，可引用其编号）
{structure}

【公告披露的风险线索】（公司特有风险）
{risk_clues}

写 3~5 条风险提示，每条 10~20 字、以"风险"二字结尾、用分号分隔成一行。
要求：核心观点的镜像风险必须覆盖（产能/价格/汇率方向），再补公告特有风险。
只输出 JSON：{{"body": "条目1；条目2；条目3", "cited_fact_ids": [...]}}"""


def _structure_facts(doc: dict[str, Any]) -> tuple[str, list[str]]:
    """业务结构暴露：境外收入占比、第一大业务占比与毛利率、募投项目。"""
    coll = doc["collections"]
    lines, ids = [], []
    regions = coll["mainop"].get("by_region") or []
    overseas = [it for it in regions if ("境外" in it["name"] or "海外" in it["name"])]
    if overseas:
        share = sum(it["income_ratio_pct"] or 0 for it in overseas)
        names = "、".join(it["name"] for it in overseas)
        lines.append(f"[mainop.境外收入合计] 境外收入（{names}）占比 {share:.2f}%")
    products = coll["mainop"].get("by_product") or []
    if products:
        top = products[0]
        lines.append(f"[mainop.{top['name']}] 第一大业务 {top['name']}："
                     f"占比 {top['income_ratio_pct']}%，毛利率 {top['gross_margin_pct']}%")
    for a in coll["announcements"]:
        if "catalyst" in a.get("tags", []) and ("定增" in a["title"] or "向特定对象" in a["title"]):
            lines.append(f"- 募投事件：{a['title']}")
    return "\n".join(lines), ids


def gen_risks(doc: dict[str, Any], views: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    coll = doc["collections"]
    views_text = "\n".join(
        f"{i}. {v['heading']}：{v['body'][:120]}"
        for i, v in enumerate(views or [], 1)) or "（无）"
    structure, _ = _structure_facts(doc)
    risk_anns = [a for a in coll["announcements"] if "risk_legal" in a.get("tags", [])]
    clues = "\n".join(f"- {a['date']} {a['title']}" for a in risk_anns[:5]) or "（无）"
    user = RISK_TMPL.format(views=views_text, structure=structure or "（无）",
                            risk_clues=clues)
    spec = load_spec()
    return chat_json(SYSTEM.format(rules=spec["rules"], fewshot=spec["fewshot"]),
                     user, schema_hint='只输出 JSON：body/cited_fact_ids。')


def _fact_block(doc: dict[str, Any], fact_ids: list[str]) -> tuple[str, list[dict]]:
    """按 id 汇编事实切片，返回（展示文本, 事实对象列表）。收集标量事实与
    mainop 行、公告摘录三类。"""
    coll = doc["collections"]
    lines, objs = [], []
    scalar = {f["id"]: f for f in doc["facts"]}
    for fid in fact_ids:
        if fid in scalar:
            f = scalar[fid]
            lines.append(f"[{fid}] {f['name']} = {f['value']}{f['unit']}")
            objs.append(f)
        elif fid.startswith("mainop."):
            biz = fid[len("mainop."):]
            for dim in ("by_product", "by_region", "by_industry"):
                for it in coll["mainop"].get(dim) or []:
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
            for a in coll["announcements"]:
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


def _default_facts(doc: dict[str, Any], slot_id: str) -> list[str]:
    """槽位兜底事实：大纲漏勾时按槽位语义自动注入素材，防止模型凭常识填空。"""
    coll = doc["collections"]
    if slot_id == "catalyst":
        return [f"ann.{a['art_code']}" for a in coll["announcements"]
                if "catalyst" in a.get("tags", [])][:3]
    if slot_id == "overall_performance":
        latest = coll["periods"][0]["period"] if coll["periods"] else ""
        return [f["id"] for f in doc["facts"]
                if f["id"].startswith(f"fin.{latest}.")][:8]
    if slot_id == "business_breakdown":
        return [f"mainop.{it['name']}"
                for it in (coll["mainop"].get("by_product") or [])[:3]]
    if slot_id == "industry_outlook":
        return [f"newsind.{i}"
                for i in range(min(5, len(coll.get("industry_news") or [])))]
    return []


def gen_view(doc: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    spec = load_spec()
    briefs = doc.get("slot_briefs") or spec["sections"][0]["view_slots"]
    brief = briefs.get(view["slot_id"], "") if isinstance(briefs, dict) else ""
    cited = view.get("cited_fact_ids") or _default_facts(doc, view["slot_id"])
    view["cited_fact_ids"] = cited
    facts_text, _ = _fact_block(doc, cited)
    context_lines = [f"{n['time'][:10]} {n['title']}" for n in doc["collections"]["news"][:8]]
    user = VIEW_TMPL.format(
        slot_id=view["slot_id"], heading=view["heading"],
        guidance=view.get("guidance", ""), brief=brief,
        facts=facts_text, context="\n".join(context_lines) or "（无）",
    )
    out = chat_json(SYSTEM.format(rules=load_spec()["rules"], fewshot=load_spec()["fewshot"]),
                    user, schema_hint='只输出 JSON 对象：heading/body/cited_fact_ids 三个字段。')
    out["slot_id"] = view["slot_id"]
    return out


def forecast_table(doc: dict[str, Any]) -> str:
    """代码生成预测表：一致预期 EPS 均值 + 现价推算 PE（LLM 不碰数字）。"""
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


def gen_forecast_note(doc: dict[str, Any]) -> dict[str, Any]:
    coll = doc["collections"]
    latest = coll["periods"][0] if coll["periods"] else {}
    summary = (f"{latest.get('period_name', '')}营收 {latest.get('revenue_yi')}亿"
               f"（同比{latest.get('revenue_yoy_pct')}%）、归母净利 "
               f"{latest.get('netprofit_yi')}亿（同比{latest.get('netprofit_yoy_pct')}%）")
    ids = [f["id"] for f in doc["facts"] if f["id"].startswith(f"fin.{latest.get('period', 'X')}.")]
    user = FORECAST_TMPL.format(table=forecast_table(doc), latest_summary=summary,
                                fact_ids=", ".join(ids[:8]))
    spec = load_spec()
    return chat_json(
        SYSTEM.format(rules=spec["rules"], fewshot=spec["fewshot"]),
        user, schema_hint="只输出 JSON：body/cited_fact_ids。")

