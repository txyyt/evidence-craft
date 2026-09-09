"""web 类 adapter 首批实现：东财公开接口族（M1~M2 的 8 个源收编）。

sources/ 模块保持不动；本文件只做两件事——包装调用 + 把原 assemble 内联的
事实组装逻辑搬进各自 adapter（事实边界归数据源所有）。
"""

from datetime import datetime
from typing import Any

from datalayer.adapters.base import AdapterResult, SourceAdapter
from datalayer.sources import (announcement, consensus, financial, mainop,
                               news, peer, quote, statements)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class EMQuoteAdapter(SourceAdapter):
    key = "em_quote"
    kind = "web"
    summary = "行情快照：现价/市盈率TTM/市净率/总市值等（另带公司名称与行业）"
    param_schema = [{"k": "stock", "label": "股票代码",
                     "ph": "如 000803，一般填 $stock", "required": True}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        q = quote.fetch(params["stock"])
        now = _now()
        fields = [
            ("price", "现价", "元", q.get("price")),
            ("pe_ttm", "市盈率TTM", "倍", q.get("pe_ttm")),
            ("pe_dynamic", "动态市盈率", "倍", q.get("pe_dynamic")),
            ("pb", "市净率", "倍", q.get("pb")),
            ("mktcap_total", "总市值", "亿元", q.get("total_mktcap_yi")),
            ("mktcap_float", "流通市值", "亿元", q.get("float_mktcap_yi")),
        ]
        facts = [{"id": f"quote.{fid}", "name": name, "value": v, "unit": unit,
                  "source": "eastmoney/push2", "as_of": now}
                 for fid, name, unit, v in fields if v is not None]
        return AdapterResult(facts=facts, meta={"name": q.get("name"),
                                                "industry": q.get("industry")})


class EMFinancialAdapter(SourceAdapter):
    key = "em_financial"
    kind = "web"
    summary = "财务摘要：营收/净利/同比/毛利率/ROE/EPS（近几期，另带 periods 集合）"
    param_schema = [{"k": "stock", "label": "股票代码",
                     "ph": "如 000803，一般填 $stock", "required": True}]
    ctx_keys = ["board_code"]           # 所属板块代码，供 em_peer 引用

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        periods = financial.latest_periods(params["stock"])
        metrics = [
            ("revenue_yi", "营业收入", "亿元"),
            ("netprofit_yi", "归母净利润", "亿元"),
            ("revenue_yoy_pct", "营收同比", "%"),
            ("netprofit_yoy_pct", "归母净利同比", "%"),
            ("gross_margin_pct", "毛利率", "%"),
            ("roe_weighted_pct", "加权ROE", "%"),
            ("eps", "基本每股收益", "元"),
        ]
        facts = []
        for p in periods:
            for key, name, unit in metrics:
                v = p.get(key)
                if v is not None:
                    facts.append({
                        "id": f"fin.{p['period']}.{key}", "name": name,
                        "value": v, "unit": unit,
                        "source": "eastmoney/datacenter/RPT_LICO_FN_CPD",
                        "as_of": p["notice_date"]})
        return AdapterResult(
            facts=facts, collections={"periods": periods},
            meta={"latest_period": periods[0]["period"] if periods else None},
            ctx={"board_code": periods[0]["board_code"] if periods else None})


class EMPeerAdapter(SourceAdapter):
    key = "em_peer"
    kind = "web"
    summary = "同行业上市公司对比列表（估值/涨幅等）"
    param_schema = [{"k": "board_code", "label": "板块代码",
                     "ph": "$ctx.board_code",
                     "hint": "一般引用上游 em_financial 产出的 $ctx.board_code"}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        board = params.get("board_code")
        peers = peer.fetch(board) if board else []
        return AdapterResult(collections={"peers": peers})


class EMAnnouncementAdapter(SourceAdapter):
    key = "em_announcement"
    kind = "web"
    summary = "关键公告标题、原文与行业摘要段落"
    param_schema = [{"k": "stock", "label": "股票代码",
                     "ph": "如 000803，一般填 $stock", "required": True}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        anns = announcement.fetch_key_with_content(params["stock"])
        for a in anns:  # 长公告预生成行业摘要（募集说明书等行业章节段落）
            a["digest"] = announcement.industry_digest(a["content"]["text"])
        return AdapterResult(collections={"announcements": anns})


class EMIndustryNewsAdapter(SourceAdapter):
    key = "em_industry_news"
    kind = "web"
    summary = "行业新闻列表（按关键词抓取）"
    param_schema = [{"k": "keywords", "label": "关键词",
                     "ph": "如 $vocabulary.industry_keywords",
                     "hint": "一般引用词表里的关键词列表（解析后为数组）"}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        keywords = params.get("keywords") or []
        items = news.fetch_industry(keywords) if keywords else []
        return AdapterResult(collections={"industry_news": items})


class EMConsensusAdapter(SourceAdapter):
    key = "em_consensus"
    kind = "web"
    summary = "券商一致预期：今/明/后年 EPS 预测均值（盈利预测表的数据源）"
    param_schema = [{"k": "stock", "label": "股票代码",
                     "ph": "如 000803，一般填 $stock", "required": True}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        return AdapterResult(collections={"consensus": consensus.consensus(
            params["stock"])})


class EMMainopAdapter(SourceAdapter):
    key = "em_mainop"
    kind = "web"
    summary = "主营构成：各业务/产品的收入、成本与占比"
    param_schema = [{"k": "stock", "label": "股票代码",
                     "ph": "如 000803，一般填 $stock", "required": True}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        return AdapterResult(collections={"mainop": mainop.fetch(params["stock"])})


class EMStatementsAdapter(SourceAdapter):
    key = "em_statements"
    kind = "web"
    summary = "三大报表补充：经营现金流/财务费用/扣非净利（最新期）"
    param_schema = [{"k": "stock", "label": "股票代码",
                     "ph": "如 000803，一般填 $stock", "required": True}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        stmts = statements.fetch(params["stock"])
        facts: list[dict[str, Any]] = []
        # 三表补充：现金流质量与费用结构是研报常用论述点（最新期）
        # 注意期间格式差异：主指标接口为 "2026Q2"，三表接口为 "2026-06-30"
        if stmts.get("income"):
            period = stmts["income"][0]["period"]
            for stmt_key, fact_id, fact_name in [
                ("cashflow", "netcash_operate_yi", "经营现金流净额"),
                ("income", "finance_expense_yi", "财务费用"),
                ("income", "deduct_parent_netprofit_yi", "扣非归母净利润"),
            ]:
                for row in stmts[stmt_key]:
                    if row["period"] != period:
                        continue
                    for item in row["items"]:
                        if item["name"] == fact_name and item["value_yi"] is not None:
                            facts.append({
                                "id": f"fin.{period}.{fact_id}", "name": fact_name,
                                "value": item["value_yi"], "unit": "亿元",
                                "source": "eastmoney/datacenter/RPT_F10_FINANCE_G*",
                                "as_of": period})
        return AdapterResult(facts=facts, collections={"statements": stmts})


class EMNewsAdapter(SourceAdapter):
    key = "em_news"
    kind = "web"
    summary = "公司新闻列表（标题/来源/时间）"
    param_schema = [{"k": "stock", "label": "股票代码",
                     "ph": "如 000803，一般填 $stock", "required": True}]

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        return AdapterResult(collections={"news": news.fetch(params["stock"])})
