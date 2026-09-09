"""LLM 客户端封装（OpenAI 兼容，DeepSeek）。

约定：
- system prompt（写作规范+范文）与用户消息（事实切片+任务）分离——固定前缀
  便于供应商侧 context cache 命中，批量分节生成时输入成本大幅下降。
- 推理模型的思维链在 reasoning_content，M4 前端可展示；此处返回完整消息。
- 输出一律 JSON mode，解析失败重试。
"""

import json
from typing import Any

from openai import OpenAI

from datalayer.settings import settings

_client: OpenAI | None = None
_stats = {"calls": 0, "seconds": 0.0}


def reset() -> None:
    """丢弃缓存的客户端——页面上改了模型配置后由 server 层调用。"""
    global _client
    _client = None


def reset_stats() -> None:
    _stats.update(calls=0, seconds=0.0)


def stats() -> dict:
    return dict(_stats)


def client() -> OpenAI:
    global _client
    if _client is None:
        m = settings.model
        if not m.get("api_key"):
            raise RuntimeError("settings.model.api_key 未配置")
        _client = OpenAI(base_url=m["base_url"], api_key=m["api_key"],
                         timeout=120)
    return _client


def chat_json(system: str, user: str, schema_hint: str,
              max_tokens: int = 8000) -> dict[str, Any]:
    """一次 JSON mode 调用。

    推理模型先输出思维链（reasoning_content）再输出正文；若 max_tokens
    被思维链耗尽，content 为空且 finish_reason=length——此时翻倍预算重试。
    解析失败再给一次带纠错提示的机会。
    """
    m = settings.model
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user + "\n\n" + schema_hint},
    ]
    # GLM 等始终思考的模型：reasoning_effort 控制思维链档位（low 减少思考
    # token，约提速 2 倍）；未配置时不传该参数（DeepSeek 不接受也无害，但保持干净）
    extra = {"reasoning_effort": m["reasoning_effort"]} \
        if m.get("reasoning_effort") else None
    budget = max_tokens
    import time
    for attempt in range(3):
        t0 = time.perf_counter()
        resp = client().chat.completions.create(
            model=m["model"], messages=messages,
            response_format={"type": "json_object"},
            max_tokens=budget, **({"extra_body": extra} if extra else {}))
        _stats["calls"] += 1
        _stats["seconds"] += time.perf_counter() - t0
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
            return json.loads(text)
        except json.JSONDecodeError:
            if attempt < 2:
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content":
                     "上面的输出不是合法 JSON。只输出一个合法 JSON 对象，不要任何其他文字。"},
                ]
                continue
            raise RuntimeError(f"LLM 输出无法解析为 JSON: {text[:200]}")
    raise RuntimeError("unreachable")
