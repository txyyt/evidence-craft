"""公告（东财聚合源，列表+全文；权威源巨潮的交叉校验在 assemble）。

M1 只对"关键公告"（标题命中关键词）拉全文，其余存标题索引。
"""

import re
from typing import Any

from datalayer.cache import cached
from datalayer.sources.base import get_json

LIST_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"

# 分类标签 → 关键词。用于筛选"关键公告"（拉全文）并在 facts.json 里标注性质，
# 供 M2 大纲阶段按类别取材（催化剂/治理/风险）。
KEYWORD_GROUPS = {
    "catalyst": ["定增", "非公开发行", "向特定对象发行", "业绩预告", "业绩快报",
                 "利润分配", "收购", "重大合同", "中标", "增发", "募集说明书",
                 "注册稿", "问询"],
    "risk_legal": ["诉讼", "仲裁", "立案", "处罚", "监管函", "关注函", "问询",
                   "警示", "纠纷", "担保"],
    "governance": ["辞职", "聘任", "换届", "高管变动", "股东变动", "增持",
                   "减持", "回购"],
}
KEYWORDS = [k for kws in KEYWORD_GROUPS.values() for k in kws]


def tag(title: str) -> list[str]:
    return [g for g, kws in KEYWORD_GROUPS.items() if any(k in title for k in kws)]


def fetch_list(stock: str, page_size: int = 50) -> list[dict[str, Any]]:
    def _fetch() -> list[dict[str, Any]]:
        data = get_json(LIST_URL, params={
            "sr": -1, "page_size": page_size, "page_index": 1,
            "ann_type": "A", "stock_list": stock,
        })
        return [{
            "art_code": a["art_code"],
            "title": a["title_ch"],
            "date": a["notice_date"][:10],
            "columns": [c["column_name"] for c in a.get("columns", [])],
        } for a in data["data"]["list"]]

    return cached("announcement", f"list_{stock}_{page_size}", _fetch)


def fetch_content(art_code: str) -> dict[str, Any]:
    def _fetch() -> dict[str, Any]:
        d = get_json(CONTENT_URL, params={
            "art_code": art_code, "client_source": "web", "page_index": 1,
        })["data"]
        return {
            "title": d.get("title"),
            "text": (d.get("notice_content") or "").strip(),
            "pdf_url": d.get("attach_url"),
        }

    return cached("announcement", f"content_{art_code}", _fetch)


def fetch_key_with_content(stock: str, limit: int = 12) -> list[dict[str, Any]]:
    """列表 + 命中关键词的公告全文（按时间倒序取前 limit 篇）。"""
    out = []
    for a in fetch_list(stock, page_size=100):
        tags = tag(a["title"])
        if tags:
            a = {**a, "tags": tags, "content": fetch_content(a["art_code"])}
            out.append(a)
            if len(out) >= limit:
                break
    return out


# 行业摘要：从长公告（募集说明书/问询回复等）中抽取行业相关段落。
# 关键词按标的主营业务领域配置，命中段按原文顺序拼接到预算内。
INDUSTRY_TERMS = ["UCO", "工业级混合油", "生物柴油", "SAF", "可持续航空",
                  "废弃油脂", "餐厨", "行业的", "市场", "产能", "政策",
                  "碳", "价格"]


def industry_digest(text: str, max_chars: int = 1800) -> str:
    paras = [p.strip() for p in re.split(r"[\n\r]+", text)
             if len(p.strip()) >= 40]
    hits = [p for p in paras
            if sum(1 for t in INDUSTRY_TERMS if t in p) >= 2]
    if not hits:
        return ""
    out, used = [], 0
    for p in hits:
        if used + len(p) > max_chars:
            break
        out.append(p)
        used += len(p)
    return "\n".join(out)
