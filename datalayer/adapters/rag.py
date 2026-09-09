"""rag 类 adapter：外部检索服务客户端 + 片段抽数管线。

边界（已拍板）：对方服务负责存与检索（向量库/embedding 都是对方的）；
本 adapter 负责把检索片段变成可信 facts——这是本类源的核心价值。

- 真实模式：settings.rag.endpoint（HTTP JSON：POST {query, top_k} →
  {results: [{doc, page, text}]}）；协议未明确前不启用
- mock 模式：endpoint 缺省或为 "mock" 时读本地片段文件（联调前跑通我方管线）
- 抽取即对账：LLM 从片段抽出的每个数字，必须能在该片段原文中找到
  （把 reconcile 的理念前移到取数环节），找不到即丢弃——防"抽取幻觉"
- reliability=retrieved：judge 对此类来源的关键数字从严，渲染溯源更醒目
"""

import json
import re
from pathlib import Path
from typing import Any

from datalayer.adapters.base import AdapterResult, SourceAdapter
from datalayer.settings import settings

_EXTRACT_SYSTEM = """你从检索片段中抽取定量事实。只抽取片段原文中明确出现的数字，
禁止计算、换算或补全。每个事实给出：name（事实名，含上下文主体）、
value（数值，保留原文精度）、unit（单位，原文没有则空串）。
片段中没有定量事实时输出空列表。只输出 JSON：{"facts": [{"name": "...",
"value": 0, "unit": "..."}]}"""

_NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?")


def _fetch_fragments(params: dict[str, Any], query: str) -> list[dict[str, Any]]:
    top_k = int(params.get("top_k", 5))
    endpoint = (settings.rag or {}).get("endpoint")
    if endpoint and endpoint != "mock":
        from datalayer.sources.base import post_json
        data = post_json(endpoint, {"query": query, "top_k": top_k})
        return data.get("results", [])[:top_k]
    # mock：本地片段文件
    mock_file = params.get("mock_fragments")
    if not mock_file:
        raise ValueError("rag adapter 需要 settings.rag.endpoint 或 mock_fragments")
    frags = json.loads(settings.resolve(mock_file).read_text(encoding="utf-8"))
    return frags[:top_k]


def _number_in_text(value: float, text: str) -> bool:
    """抽取即对账：数值串（含千分位/整数形态）必须出现在片段原文中。"""
    candidates = {str(value), str(int(value)) if value == int(value) else "",
                  f"{value:,}"}
    text_norm = text.replace(",", "")
    for c in candidates:
        if c and c.replace(",", "") in text_norm:
            return True
    return False


class RagAdapter(SourceAdapter):
    key = "rag_client"
    kind = "rag"
    default_reliability = "retrieved"
    summary = "检索片段抽数：LLM 抽取 + 原文对账（无出处的数字直接丢弃）"

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        from pipeline.llm import chat_json

        query = params["query"]
        fragments = _fetch_fragments(params, query)
        if not fragments:
            raise ValueError("RAG 检索无结果")

        facts: list[dict[str, Any]] = []
        warnings: list[str] = []
        n_dropped = 0
        for fi, frag in enumerate(fragments):
            text = frag.get("text", "")
            out = chat_json(_EXTRACT_SYSTEM,
                            f"【检索片段】来源：{frag.get('doc', '?')} "
                            f"第{frag.get('page', '?')}页\n{text[:2000]}",
                            schema_hint="只输出一个合法 JSON 对象。")
            for j, f in enumerate(out.get("facts") or []):
                try:
                    value = float(f["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not _number_in_text(value, text):
                    n_dropped += 1      # 原文找不到该数字 → 抽取幻觉，丢弃
                    continue
                facts.append({
                    "id": f"rag.{fi:02d}.{j:02d}",
                    "name": str(f.get("name", ""))[:60],
                    "value": value, "unit": str(f.get("unit", "")),
                    "source": f"RAG:{frag.get('doc', '?')} p.{frag.get('page', '?')}",
                    "as_of": "检索时点"})
        if n_dropped:
            warnings.append(f"rag 抽取丢弃 {n_dropped} 个原文无出处的数字")
        return AdapterResult(facts=facts, warnings=warnings)
