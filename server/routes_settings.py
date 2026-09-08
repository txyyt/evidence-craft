"""系统设置路由：模型配置读写（密钥只写不回显）+ 连通测试 + 关于。"""

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalayer.settings import mask_key, settings, update_model_section

router = APIRouter(prefix="/api/settings")


class ModelCfgIn(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""            # 空 = 保留现 key（页面掩码不回传明文）
    reasoning_effort: str = ""   # 空 = 不传该参数（服务端默认思考档）


def _masked() -> dict:
    m = settings.model
    return {"base_url": m.get("base_url", ""),
            "model": m.get("model", ""),
            "api_key_masked": mask_key(m.get("api_key", "")),
            "reasoning_effort": m.get("reasoning_effort", "")}


@router.get("/model")
def get_model() -> dict:
    return _masked()


@router.put("/model")
def put_model(cfg: ModelCfgIn) -> dict:
    if not cfg.base_url.strip() or not cfg.model.strip():
        raise HTTPException(422, "base_url 与 model 不能为空")
    update_model_section(cfg.base_url.strip(), cfg.model.strip(),
                         cfg.api_key.strip(), cfg.reasoning_effort.strip())
    settings.reload()
    from pipeline.llm import reset
    reset()
    return _masked()


@router.post("/model/test")
def test_model(cfg: ModelCfgIn) -> dict:
    """连通测试：优先用提交值（api_key 留空则用现配置），发一次小请求报时延。"""
    from openai import OpenAI

    m = settings.model
    base_url = (cfg.base_url or m.get("base_url", "")).strip()
    model = (cfg.model or m.get("model", "")).strip()
    api_key = cfg.api_key.strip() or m.get("api_key", "")
    effort = cfg.reasoning_effort.strip() or m.get("reasoning_effort", "")
    if not (base_url and model and api_key):
        raise HTTPException(422, "base_url / model / api_key 未配齐，无法测试")
    cli = OpenAI(base_url=base_url, api_key=api_key, timeout=30)
    kw = {"extra_body": {"reasoning_effort": effort}} if effort else {}
    t0 = time.time()
    try:
        r = cli.chat.completions.create(
            model=model, max_tokens=512,
            messages=[{"role": "user", "content": "回复两个字：可用"}], **kw)
        return {"ok": True, "latency_s": round(time.time() - t0, 1),
                "reply": (r.choices[0].message.content or "").strip()[:50]}
    except Exception as e:  # noqa: BLE001 —— 测试端点就是把错误报给页面
        return {"ok": False, "latency_s": round(time.time() - t0, 1),
                "error": str(e)[:300]}


@router.get("/about")
def about() -> dict:
    return {"name": "EvidenceCraft 多部门报告平台", "milestone": "M8",
            "pipeline_stages": ["data", "outline", "sections", "review", "render"]}
