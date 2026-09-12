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

import hashlib
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

_EXTRACT_BATCH_SYSTEM = """你从同一批检索片段中抽取定量事实。只抽取各片段原文中明确出现的
数字，禁止计算、换算或补全。每个事实给出：frag（片段序号，从 0 开始）、
name（事实名，含上下文主体）、value（数值，保留原文精度）、unit（单位，
原文没有则空串）。整批没有定量事实时输出空列表。只输出 JSON：
{"facts": [{"frag": 0, "name": "...", "value": 0, "unit": "..."}]}"""

_NUM_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?")


def _query_ngrams(query: str) -> list[str]:
    """查询词 → 匹配词元：ASCII 词原样 + 中文按 2-gram（无分词依赖）。"""
    terms = {t.lower() for t in re.findall(r"[A-Za-z0-9]+", query)}
    for part in re.sub(r"[^\u4e00-\u9fff]", " ", query).split():
        terms.update({part} if len(part) == 1
                     else {part[i:i + 2] for i in range(len(part) - 1)})
    return [t for t in terms if t]


def _retrieve(fragments: list[dict[str, Any]], query: str,
              top_k: int) -> list[dict[str, Any]]:
    """mock 模式检索：查询词元命中数排序，零命中回退原顺序（保底）。"""
    terms = _query_ngrams(query)
    if not terms:
        return fragments[:top_k]

    def score(f: dict[str, Any]) -> int:
        t = f.get("text", "").lower()
        return sum(t.count(term) for term in terms)

    ranked = sorted(fragments, key=lambda f: -score(f))
    ranked = [f for f in ranked if score(f) > 0]
    return (ranked or fragments)[:top_k]


def _fetch_fragments(params: dict[str, Any], query: str) -> list[dict[str, Any]]:
    top_k = int(params.get("top_k", 5))
    endpoint = (settings.rag or {}).get("endpoint")
    if endpoint and endpoint != "mock":
        from datalayer.sources.base import post_json
        data = post_json(endpoint, {"query": query, "top_k": top_k})
        return data.get("results", [])[:top_k]
    # mock：本地片段文件（建库用 datalayer.corpus），关键词检索取 top_k
    mock_file = params.get("mock_fragments")
    if not mock_file:
        raise ValueError("rag adapter 需要 settings.rag.endpoint 或 mock_fragments")
    frags = json.loads(settings.resolve(mock_file).read_text(encoding="utf-8"))
    return _retrieve(frags, query, top_k)


def _number_in_text(value: float, text: str) -> bool:
    """抽取即对账：数值串（含千分位/整数形态）必须出现在片段原文中。"""
    candidates = {str(value), str(int(value)) if value == int(value) else "",
                  f"{value:,}"}
    text_norm = text.replace(",", "")
    for c in candidates:
        if c and c.replace(",", "") in text_norm:
            return True
    return False


def _extract_cache_path(fragments_ref: str, query: str, top_k: int) -> Path:
    """批量抽取结果缓存：片段库指向+查询+档位（+模型）决定，TTL 永久
    （片段库内容指纹在文件名里，变了自然换 key）。"""
    key = f"{fragments_ref}|{query}|{top_k}|{settings.model.get('model', '')}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return settings.resolve(f"data/cache/rag_extract/{h}.json")


class RagAdapter(SourceAdapter):
    key = "rag_client"
    kind = "rag"
    default_reliability = "retrieved"
    summary = "检索片段抽数：LLM 抽取 + 原文对账（无出处的数字直接丢弃）"

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        from pipeline.llm import chat_json, tier_for

        query = params["query"]
        fragments = _fetch_fragments(params, query)
        if not fragments:
            raise ValueError("RAG 检索无结果")

        # 抽取结果缓存：同一片段库+同一查询直接复用（迭代写作零 LLM 调用）
        cache_file = _extract_cache_path(
            params.get("mock_fragments") or params.get("endpoint") or query,
            query, int(params.get("top_k", 5)))
        if cache_file.exists():
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            return AdapterResult(facts=payload.get("facts") or [],
                                 warnings=payload.get("warnings") or [])

        # 批量抽取：整绑定一次调用（片段逐个标注序号），"抽取即对账"不变——
        # 每条事实仍回验其所属片段原文；批量调用失败退回逐片段老路径
        facts: list[dict[str, Any]] = []
        warnings: list[str] = []
        n_dropped = 0
        batch_text = "\n\n".join(
            f"【片段 {i}】来源：{f.get('doc', '?')} 第{f.get('page', '?')}页\n"
            f"{f.get('text', '')[:2000]}" for i, f in enumerate(fragments))
        extracted: list[tuple[int, dict]] = []   # (片段序号, 事实)
        try:
            out = chat_json(_EXTRACT_BATCH_SYSTEM, batch_text,
                            schema_hint="只输出一个合法 JSON 对象。",
                            tier=tier_for("extract"))
            for f in out.get("facts") or []:
                if not isinstance(f, dict):
                    continue
                try:
                    # 缺 frag 时按 0 处理：单片段批次兼容旧形状；多片段由
                    # 逐条对账兜底（归错片段的数字验不过原文会被丢弃）
                    fi = int(f.get("frag", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if not (0 <= fi < len(fragments)):
                    continue
                extracted.append((fi, f))
        except Exception:  # noqa: BLE001 —— 批量失败退回逐片段（老路径）
            for fi, frag in enumerate(fragments):
                try:
                    out = chat_json(_EXTRACT_SYSTEM,
                                    f"【检索片段】来源：{frag.get('doc', '?')} "
                                    f"第{frag.get('page', '?')}页\n"
                                    f"{frag.get('text', '')[:2000]}",
                                    schema_hint="只输出一个合法 JSON 对象。",
                                    tier=tier_for("extract"))
                except Exception:  # noqa: BLE001 —— 单片段失败不拖垮绑定
                    warnings.append(f"rag 片段 {fi} 抽取失败（跳过）")
                    continue
                for f in out.get("facts") or []:
                    extracted.append((fi, f))

        counter: dict[int, int] = {}
        for fi, f in extracted:
            frag = fragments[fi]
            text = frag.get("text", "")
            try:
                value = float(f["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if not _number_in_text(value, text):
                n_dropped += 1      # 原文找不到该数字 → 抽取幻觉，丢弃
                continue
            j = counter.get(fi, 0)
            counter[fi] = j + 1
            facts.append({
                "id": f"rag.{fi:02d}.{j:02d}",
                "name": str(f.get("name", ""))[:60],
                "value": value, "unit": str(f.get("unit", "")),
                "source": f"RAG:{frag.get('doc', '?')} p.{frag.get('page', '?')}",
                "as_of": (f"{frag['year']}年" if frag.get("year")
                          else "检索时点")})
        if n_dropped:
            warnings.append(f"rag 抽取丢弃 {n_dropped} 个原文无出处的数字")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"facts": facts, "warnings": warnings},
                       ensure_ascii=False), encoding="utf-8")
        return AdapterResult(facts=facts, warnings=warnings)
