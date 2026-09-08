"""③ 大纲生成：LLM 从事实包选材，产出标题 + 各观点小标题 + 引用事实清单。

LLM 在此阶段只做三件事：拟标题、给每条观点定结论式小标题、为每条观点
挑选要引用的事实 ID——不写正文。正文在④按节生成。

角色、行文规则、范文全部来自 Spec v2（template_factory/schema.py），
本模块的 USER_TMPL 数据板块为股票场景组装逻辑（M7 数据源绑定后泛化）。
"""

from typing import Any

from pipeline.llm import chat_json
from template_factory.schema import SpecV2

SYSTEM = """你是{role}，为报告做写作规划。

报告类型：{description}

【标题要求】
{title_style}

【行文规则】
{rules}

【范文】
{fewshot}
"""

USER_TMPL = """为股票 {stock}({name}) 的点评报告做写作规划。

【标的与行情】
现价 {price} 元，总市值 {mktcap} 亿元，行业：{industry}

【最新报告期核心数据】
{period_facts}

【分业务构成】（收入/占比/毛利率）
{mainop}

【关键公告】（近期，含要点；带"行业章节摘要"者为募集说明书等长文档的行业段落）
{announcements}

【行业新闻】（按行业关键词扫描命中，可引用其编号）
{industry_news}

【个股资讯标题】（近期新闻线索）
{news}

【券商一致预期】（{n_orgs} 家覆盖，预测 EPS 均值：今年 {eps0} / 明年 {eps1} / 后年 {eps2} 元）

任务：
1. 拟结论式报告标题（title_style 要求）。
2. 为以下 {n_views} 个固定视角各拟一条结论式小标题（heading，8~15字判断句），
   并从素材中为它挑选支撑事实（cited_fact_ids 引用上面方括号里的事实编号；
   分业务数据引用 mainop.业务名 前缀，公告引用 ann.公告编号）。
3. 每条观点给一句 guidance：说明该段论证路径（先什么后什么、用什么数据对比）。

只输出 JSON：
{{"title": "...", "views": [{{"slot_id": "...", "heading": "...",
"cited_fact_ids": [...], "guidance": "..."}}]}}
"""


GENERIC_USER_TMPL = """为「{subject}」的报告做写作规划。

【可引用事实】（编号 名称 = 值 单位；标题与各视角的支撑数字只能出自这里）
{facts}

任务：
1. 按标题要求拟报告标题。
2. 为以下 {n_views} 个固定视角各拟一条结论式小标题（heading，判断句），
   并从【可引用事实】中为它挑选支撑编号（cited_fact_ids）。
3. 每条观点给一句 guidance：说明论证路径（先什么后什么、用什么数据对比）。

只输出 JSON：
{{"title": "...", "views": [{{"slot_id": "...", "heading": "...",
"cited_fact_ids": [...], "guidance": "..."}}]}}
"""


def _fmt_facts(facts: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[{f['id']}] {f['name']} = {f['value']}{f['unit']}" for f in facts
    )


def _fmt_facts_generic(facts: list[dict[str, Any]]) -> str:
    """通用事实清单：全部标量事实按 id 排序列出（非股票部门没有
    periods/mainop 等结构，无法分板块）。"""
    return "\n".join(
        f"[{f['id']}] {f['name']} = {f['value']}{f['unit']}"
        for f in sorted(facts, key=lambda x: x["id"])
    ) or "（无）"


def _fmt_mainop(mainop: dict[str, Any]) -> str:
    if not mainop:
        return "（无分业务数据）"
    lines = [f"报告期 {mainop.get('report_name', mainop.get('period'))}："]
    for dim, label in (("by_product", "分产品"), ("by_region", "分地区")):
        for it in mainop.get(dim) or []:
            margin = it['gross_margin_pct']
            margin_text = f"毛利率 {margin}%" if margin is not None else "毛利率未披露"
            lines.append(f"[mainop.{it['name']}] {label}·{it['name']}："
                         f"收入 {it['income_yi']}亿 占比 {it['income_ratio_pct']}% {margin_text}")
    return "\n".join(lines)


def _fmt_announcements(anns: list[dict[str, Any]]) -> str:
    lines = []
    for a in anns:
        body = a.get("digest") or (a["content"]["text"] or "")[:300]
        excerpt = body.replace("\n", " ")
        lines.append(f"[ann.{a['art_code']}] {a['date']} ({','.join(a['tags'])}) "
                     f"{a['title']}\n    要点摘录：{excerpt}")
    return "\n".join(lines) if anns else "（无）"


def _fmt_industry_news(items: list[dict[str, Any]]) -> str:
    return "\n".join(f"[newsind.{i}] {n['time'][:10]} {n['title']}"
                     for i, n in enumerate(items)) or "（无行业新闻命中）"


def _fmt_news(news: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {n['time'][:10]} {n['title']}" for n in news[:15]) or "（无）"


def build_outline(doc: dict[str, Any], spec: SpecV2) -> dict[str, Any]:
    views_sec = spec.section("views")
    if views_sec is None:
        raise ValueError("spec 缺少 views 章节")
    meta = doc["meta"]
    facts = doc["facts"]
    coll = doc["collections"]

    system = SYSTEM.format(role=spec.writer_role, description=spec.description,
                           title_style=spec.title_style,
                           rules=spec.rules_text(),
                           fewshot=spec.fewshot_for(views_sec))
    schema = ('JSON 字段：title(str)；views(list)：slot_id 必须依次为 '
              + ",".join(s.id for s in views_sec.view_slots)
              + '；每项含 heading(str)/cited_fact_ids(list)/guidance(str)。')

    # 股票场景：periods/consensus 等齐备时走分板块素材提示词（M2 验证过的形态）；
    # 其他部门：通用事实清单路径。两条路径共用 SYSTEM 与输出 Schema。
    if coll.get("periods") and coll.get("consensus"):
        latest = coll["periods"][0]["period"]
        period_facts = _fmt_facts(
            [f for f in facts if f["id"].startswith(f"fin.{latest}.")])
        quote = [f for f in facts if f["id"].startswith("quote.")]
        q = {f["id"].split(".")[1]: f["value"] for f in quote}
        cons = coll["consensus"]
        mean = cons.get("mean_eps_forecast", {})
        user = USER_TMPL.format(
            stock=meta["stock"], name=meta["name"], industry=meta["industry"],
            price=q.get("price"), mktcap=q.get("mktcap_total"),
            period_facts=period_facts,
            mainop=_fmt_mainop(coll["mainop"]),
            announcements=_fmt_announcements(coll["announcements"][:6]),
            industry_news=_fmt_industry_news(coll.get("industry_news") or []),
            news=_fmt_news(coll["news"]),
            n_orgs=cons.get("n_orgs", 0),
            eps0=mean.get("predictThisYearEps"), eps1=mean.get("predictNextYearEps"),
            eps2=mean.get("predictNextTwoYearEps"),
            n_views=views_sec.n_views,
        )
    else:
        user = GENERIC_USER_TMPL.format(
            subject=meta.get("name") or meta.get("stock"),
            facts=_fmt_facts_generic(facts), n_views=views_sec.n_views)

    outline = chat_json(system, user, schema)
    outline["slot_briefs"] = {s.id: s.brief for s in views_sec.view_slots}
    return outline
