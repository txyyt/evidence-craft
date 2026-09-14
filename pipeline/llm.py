"""LLM 客户端封装（OpenAI 兼容，DeepSeek）。

约定：
- system prompt（写作规范+范文）与用户消息（事实切片+任务）分离——固定前缀
  便于供应商侧 context cache 命中，批量分节生成时输入成本大幅下降。
- 推理模型的思维链在 reasoning_content，M4 前端可展示；此处返回完整消息。
- 输出一律 JSON mode，解析失败重试。
- F3 模型分档：chat_json(tier=...) 选档——档位配置在 settings.model_tiers
  （每档只写覆盖字段，缺省继承 model 段），调用分工经 settings.tier_roles
  由 tier_for(role) 映射（extract=快档抽取/规划，write=质量档写作/评审）；
  --model-tier 可全局覆盖。未配置分档时一切调用走 model 段（默认档）。
"""

import json
import threading
from typing import Any

from openai import (APIConnectionError, APIStatusError, APITimeoutError,
                    OpenAI, RateLimitError)

from datalayer.settings import settings

_clients: dict[str, OpenAI] = {}
_tier_override: str | None = None          # --model-tier 全局覆盖（单机单跑场景）
_stats: dict[str, Any] = {"calls": 0, "seconds": 0.0, "tiers": {}}
_stats_lock = threading.Lock()
# 偶发超时/限流按可重试处理（单次上限由 client() 的 timeout=180s 控制）；
# 5xx 服务端过载（如硅基流动 503 "System is too busy"）同样指数退避重试
def _is_5xx(e: Exception) -> bool:
    return isinstance(e, APIStatusError) and getattr(e, "status_code", 0) >= 500


_RETRYABLE = (APITimeoutError, RateLimitError, APIConnectionError)

_TIER_FIELDS = ("base_url", "api_key", "model", "reasoning_effort")


def reset() -> None:
    """丢弃缓存的客户端——页面上改了模型配置后由 server 层调用。"""
    global _tier_override
    _clients.clear()
    _tier_override = None


def set_tier_override(tier: str | None) -> None:
    """固定本次全部调用走某档（--model-tier）；None = 按 tier_roles 分工。"""
    global _tier_override
    _tier_override = tier or None


def tier_for(role: str) -> str | None:
    """调用角色 → 档位名：--model-tier 覆盖优先，否则 settings.tier_roles；
    未配置返回 None（默认档）。角色约定：extract（rag/web 抽取、规划器）、
    write（大纲/分节/judge/修订）。"""
    if _tier_override:
        return _tier_override
    return (settings.tier_roles or {}).get(role) or None


def tier_config(tier: str | None) -> dict[str, Any]:
    """档位 → 合并后的模型配置（model 段为基础，档位段只覆盖已配字段）。"""
    m = dict(settings.model)
    if tier:
        over = (settings.model_tiers or {}).get(tier) or {}
        m.update({k: over[k] for k in _TIER_FIELDS if over.get(k)})
    return m


def reset_stats() -> None:
    with _stats_lock:
        _stats.update(calls=0, seconds=0.0, tiers={})


def stats() -> dict:
    with _stats_lock:
        out = dict(_stats)
        out["tiers"] = {k: dict(v) for k, v in _stats["tiers"].items()}
        return out


def client(tier: str | None = None) -> OpenAI:
    key = tier or ""
    c = _clients.get(key)
    if c is None:
        m = tier_config(tier)
        api_key = (m.get("api_key") or "").strip()
        if not api_key or not api_key.isascii():
            # api_key 会进 HTTP 头，非 ASCII 字符（如占位中文）编码直接失败——
            # 这里给出可读的配置错误，而不是底层 UnicodeEncodeError
            raise RuntimeError(
                f"模型配置 api_key 未配置或含非法字符（tier={tier or '默认'}；"
                "请填入供应商提供的 API Key）")
        c = OpenAI(base_url=m["base_url"], api_key=api_key, timeout=180)
        _clients[key] = c
    return c


def chat_json(system: str, user: str, schema_hint: str,
              max_tokens: int = 8000, tier: str | None = None) -> dict[str, Any]:
    """一次 JSON mode 调用（tier=None 默认档；档位名见 settings.model_tiers）。

    推理模型先输出思维链（reasoning_content）再输出正文；若 max_tokens
    被思维链耗尽，content 为空且 finish_reason=length——此时翻倍预算重试。
    解析失败再给一次带纠错提示的机会。
    """
    m = tier_config(tier)
    cl = client(tier)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user + "\n\n" + schema_hint},
    ]
    # GLM 等始终思考的模型：reasoning_effort 控制思维链档位（low 减少思考
    # token，约提速 2 倍）；未配置时不传该参数（其他供应商不接受，保持干净）
    extra = {"reasoning_effort": m["reasoning_effort"]} \
        if m.get("reasoning_effort") else None
    budget = max_tokens
    import time
    for attempt in range(3):
        t0 = time.perf_counter()
        try:
            resp = cl.chat.completions.create(
                model=m["model"], messages=messages,
                response_format={"type": "json_object"},
                max_tokens=budget, **({"extra_body": extra} if extra else {}))
        except _RETRYABLE:
            # 部分主机偶发超时/限流（实测阿里百炼专属主机 >120s 比例不低）：
            # 指数退避后重试，最后一次把超时原样抛出
            if attempt >= 2:
                raise
            time.sleep(2 ** attempt)
            continue
        except Exception as e:
            if not _is_5xx(e) or attempt >= 2:
                raise     # 5xx 服务端过载：退避重试两次，仍失败原样抛出
            time.sleep(2 ** attempt * 2)
            continue
        with _stats_lock:
            _stats["calls"] += 1
            _stats["seconds"] += time.perf_counter() - t0
            tstat = _stats["tiers"].setdefault(
                tier or "default",
                {"calls": 0, "seconds": 0.0, "model": m["model"]})
            tstat["calls"] += 1
            tstat["seconds"] = round(tstat["seconds"]
                                     + time.perf_counter() - t0, 1)
        msg = resp.choices[0].message
        reasoning = getattr(msg, "reasoning_content", None) or ""
        text = (msg.content or "").strip()
        if not text:
            if resp.choices[0].finish_reason == "length" and budget < 24000:
                budget *= 2          # 思维链吃光预算：加大重来
                continue
            if attempt < 2:
                continue             # 偶发空回复：原样重试
            raise RuntimeError("LLM 返回空内容（finish_reason="
                               f"{resp.choices[0].finish_reason}，"
                               f"思维链 {len(reasoning)} 字）")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        # 模型偶发返回数组或标量（JSON mode 下的形态抖动）：统一按"不是对象"
        # 处理，走纠错重试；调用方都依赖 dict 形态
        if not isinstance(data, dict):
            if attempt < 2:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content":
                     "上面的输出不是合法 JSON 或不是 JSON 对象。"
                     "只输出一个合法 JSON 对象，不要任何其他文字。"},
                ]
                continue
            raise RuntimeError(f"LLM 输出无法解析为 JSON 对象: {text[:200]}")
        return data
    raise RuntimeError("unreachable")
