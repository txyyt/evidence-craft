"""③ 大纲生成：LLM 从事实包选材，产出标题 + 各观点小标题 + 引用事实清单。

LLM 在此阶段只做三件事：拟标题、给每条观点定结论式小标题、为每条观点
挑选要引用的事实 ID——不写正文。正文在④按节生成。
"""

import json
from pathlib import Path
from typing import Any

import yaml

from datalayer.settings import settings
from pipeline.llm import chat_json

SPEC_PATH = settings.resolve("config/report_types/company_review.yaml")


def load_spec() -> dict[str, Any]:
    with open(SPEC_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


SYSTEM = """你是资深卖方分析师，为上市公司点评报告做写作规划。

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


def _fmt_facts(facts: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"[{f['id']}] {f['name']} = {f['value']}{f['unit']}" for f in facts
    )


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


def build_outline(doc: dict[str, Any]) -> dict[str, Any]:
    spec = load_spec()
    meta = doc["meta"]
    facts = doc["facts"]
    coll = doc["collections"]

    latest = coll["periods"][0]["period"] if coll["periods"] else ""
    period_facts = _fmt_facts([f for f in facts if f["id"].startswith(f"fin.{latest}.")])
    quote = [f for f in facts if f["id"].startswith("quote.")]
    q = {f["id"].split(".")[1]: f["value"] for f in quote}
    cons = coll["consensus"]
    mean = cons.get("mean_eps_forecast", {})

    system = SYSTEM.format(rules=spec["rules"], fewshot=spec["fewshot"])
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
        n_views=spec["sections"][0]["n_views"],
    )
    schema = ('JSON 字段：title(str)；views(list)：slot_id 必须依次为 '
              + ",".join(s["id"] for s in spec["sections"][0]["view_slots"])
              + '；每项含 heading(str)/cited_fact_ids(list)/guidance(str)。')

    outline = chat_json(system, user, schema)
    outline["slot_briefs"] = {s["id"]: s["brief"] for s in spec["sections"][0]["view_slots"]}
    return outline
