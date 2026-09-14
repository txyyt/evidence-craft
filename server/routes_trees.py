"""结构树路由：模板工作台后端（列表/创建/详情/保存/版本/回滚/日志/删除）。

树 = Spec v2 + tree: 元信息块（trees/store.py）。保存一律先过 SpecV2 校验
（错误阻止）与 lint（软警告随响应返回前端展示）。
"""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server import bus
from trees import lint as tree_lint
from trees import store as tree_store

router = APIRouter(prefix="/api/trees")


class TreeNewIn(BaseModel):
    id: str
    name: str
    subject: str = ""
    description: str = ""
    genre: str | None = None          # journal | research | brief
    style_card: str | None = None
    spec_dict: dict | None = None     # 缺省给最小可跑骨架（纯 text 一节）


class TreeSaveIn(BaseModel):
    spec_dict: dict
    meta: dict = Field(default_factory=dict)   # name/subject/status/genre/style_card
    summary: str = ""


class RollbackIn(BaseModel):
    version: int


_DEFAULT_SPEC = {
    "report_type": "{id}",
    "description": "{description}",
    "writer_role": "资深行业研究员",
    "title_style": "短语式标题，15~30 字，概括报告主题与视角",
    "sections": [
        {"id": "overview", "title": "综述", "kind": "text",
         "style": "围绕报告主题写一段综述（2~3 个自然段）。"},
    ],
}


@router.get("/style-cards/all")
def style_cards() -> list[dict]:
    from pipeline.stylecards import list_cards
    return list_cards()


@router.get("")
def list_trees() -> list[dict]:
    return tree_store.list_trees()


@router.post("")
def create_tree(body: TreeNewIn) -> dict:
    if tree_store.exists(body.id):
        raise HTTPException(409, f"结构树 id 已存在：{body.id}")
    spec_dict = body.spec_dict or {}
    if not spec_dict:
        spec_dict = {**_DEFAULT_SPEC,
                     "report_type": _DEFAULT_SPEC["report_type"].format(id=body.id),
                     "description": body.description
                     or f"{body.name}（对话式生成）"}
    meta = {"name": body.name, "subject": body.subject or body.name,
            "provenance": {"kind": "manual"}}
    for k in ("genre", "style_card"):
        if getattr(body, k):
            spec_dict[k] = getattr(body, k)
    try:
        result = tree_store.save_tree(body.id, spec_dict, meta,
                                      actor="system", summary="创建结构树")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "id": body.id, "version": result["meta"]["version"],
            "fingerprint": result["fingerprint"]}


@router.get("/{tree_id}")
def tree_detail(tree_id: str) -> dict:
    try:
        loaded = tree_store.load_spec_dict(tree_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    lint_result = tree_lint.lint(loaded["spec_dict"])
    return {"meta": loaded["meta"], "spec_dict": loaded["spec_dict"],
            "fingerprint": loaded["fingerprint"], "lint": lint_result,
            "versions": tree_store.versions(tree_id),
            "ops": tree_store.ops_log(tree_id, limit=30)}


@router.put("/{tree_id}")
def save_tree(tree_id: str, body: TreeSaveIn) -> dict:
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    try:
        result = tree_store.save_tree(tree_id, body.spec_dict, body.meta,
                                      actor="manual",
                                      summary=body.summary or "手动保存")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "version": result["meta"]["version"],
            "fingerprint": result["fingerprint"],
            "lint": tree_lint.lint(body.spec_dict)}


@router.get("/{tree_id}/versions")
def tree_versions(tree_id: str) -> list[dict]:
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    return tree_store.versions(tree_id)


@router.get("/{tree_id}/ops")
def tree_ops(tree_id: str) -> list[dict]:
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    return tree_store.ops_log(tree_id)


@router.post("/{tree_id}/rollback")
def tree_rollback(tree_id: str, body: RollbackIn) -> dict:
    try:
        result = tree_store.rollback(tree_id, body.version)
    except ValueError as e:
        raise HTTPException(404, str(e))
    loaded = tree_store.load_spec_dict(tree_id)
    return {"ok": True, "version": result["meta"]["version"],
            "fingerprint": result["fingerprint"],
            "lint": tree_lint.lint(loaded["spec_dict"])}


@router.delete("/{tree_id}")
def delete_tree(tree_id: str) -> dict:
    try:
        tree_store.delete_tree(tree_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ---------- 树对话（M2）：需求生成树 / 自然语言编辑树 ----------

class GenerateIn(BaseModel):
    messages: list[dict] = Field(default_factory=list)   # [{role: user|assistant, text}]
    tree_id: str | None = None          # 给出且不冲突时用它，否则自动起 id


class ChatEditIn(BaseModel):
    message: str


def _slug(name: str) -> str:
    import re
    import time
    s = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    return s[:40] or f"tree_{int(time.time())}"


@router.post("/generate")
def generate_tree(body: GenerateIn) -> dict:
    """需求对话 → 树（创建并返回）或反问。同步单次 LLM 调用。"""
    from pipeline import tree_agent
    try:
        result = tree_agent.generate(body.messages)
    except tree_agent.TreeAgentError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"树生成失败：{type(e).__name__}: {e}")
    if result["kind"] == "questions":
        return result
    spec_dict, meta = result["spec_dict"], result["meta"]
    tid = body.tree_id or _slug(meta.get("name") or "tree")
    base = tid
    n = 2
    while tree_store.exists(tid):
        tid = f"{base}_{n}"
        n += 1
    saved = tree_store.save_tree(tid, spec_dict,
                                 {"name": meta.get("name") or tid,
                                  "subject": meta.get("subject") or tid,
                                  "provenance": {"kind": "dialogue"}},
                                 actor="agent", summary="对话生成整棵树")
    return {"kind": "tree", "tree_id": tid, "name": meta.get("name"),
            "spec_dict": spec_dict, "version": saved["meta"]["version"],
            "fingerprint": saved["fingerprint"],
            "lint": tree_lint.lint(spec_dict)}


@router.post("/{tree_id}/chat")
def chat_edit(tree_id: str, body: ChatEditIn) -> dict:
    """编辑对话：NL → 树操作 → 自动应用（actor=agent，版本 +1）。"""
    from pipeline import tree_agent
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    try:
        return tree_agent.edit(tree_id, body.message)
    except tree_agent.TreeAgentError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"树编辑失败：{type(e).__name__}: {e}")


# ---------- 数据计划（M2 扩展 needs_coverage / 裁决；M1 先提供启动与沿用） ----------

class PlanStartIn(BaseModel):
    intent: str = ""                 # 写作意图（与 data_needs 汇总一起给规划器）
    folder: str | None = None        # 本地资料文件夹（现场建语料库）


@router.post("/{tree_id}/plan/start")
async def plan_start(tree_id: str, body: PlanStartIn) -> dict:
    """出数据采集计划（后台任务 + SSE）：树 data_needs + 意图 → planner。
    须为 async def：start_job 需要 running loop。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")

    def fn(progress) -> dict:
        from datalayer.planner import make_plan
        loaded = tree_store.load_tree(tree_id)
        spec, meta = loaded["spec"], loaded["meta"]
        sources = tree_store.synthetic_sources(meta, spec)
        needs = tree_store.tree_needs(tree_id)
        # 意图缺省用树描述（树场景 data_needs 本身就是规划输入）
        intent = body.intent or f"{spec.description}（主体：{meta.get('subject') or ''}）"
        progress("plan", "按树的数据需求生成采集计划 ...", None)
        plan = make_plan(spec, sources, intent,
                         body.folder or None, needs=needs or None)
        if body.folder and not (plan.get("corpus") or {}).get("fragments_rel"):
            progress("plan", "警告：资料文件夹未产出语料", None)
        name = tree_store.save_plan(tree_id, plan)
        progress("plan", f"计划已保存 {name}", {"plan_file": name, "plan": plan})
        return {"plan_file": name, "plan": plan}

    task = bus.start_job(fn, f"plan-{tree_id}", asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/trees/{tree_id}/jobs/{task.id}/events"}


class DecisionIn(BaseModel):
    need: str
    decision: str          # search | provide_folder | qualitative | drop


class PlanConfirmIn(BaseModel):
    plan_file: str
    decisions: list[DecisionIn] = Field(default_factory=list)
    all_qualitative: bool = False      # 全定性二次确认


@router.post("/{tree_id}/plan/confirm")
def plan_confirm(tree_id: str, body: PlanConfirmIn) -> dict:
    """缺口四选一裁决 → 落效（计划 + 树）→ 过确认门槛。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    result = tree_store.apply_decisions(tree_id, body.plan_file,
                                        [d.model_dump() for d in body.decisions])
    gate = tree_store.coverage_gate(result["plan"])
    if not gate["ok"]:
        return {"ok": False, **gate, "coverage": result["coverage"]}
    if gate["all_qualitative"] and not body.all_qualitative:
        return {"ok": False, **gate, "coverage": result["coverage"],
                "need_confirm": "全树无数据来源（全定性生成），需二次确认"}
    return {"ok": True, **gate, "coverage": result["coverage"]}


@router.get("/{tree_id}/plans/{plan_name}")
def tree_plan_content(tree_id: str, plan_name: str) -> dict:
    """读取计划内容（含 needs_coverage 回执）——前端载入展示。"""
    try:
        return tree_store.load_plan(tree_id, plan_name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{tree_id}/plans")
def tree_plans(tree_id: str) -> list[dict]:
    """树目录 plans/ 下的数据计划文件列表（生成入口选择沿用）。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    d = tree_store.tree_dir(tree_id) / "plans"
    out = []
    if d.exists():
        for f in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime,
                        reverse=True):
            out.append({"name": f.name, "size": f.stat().st_size,
                        "mtime": f.stat().st_mtime})
    return out


@router.get("/{tree_id}/jobs/{job_id}/events")
async def job_events(tree_id: str, job_id: str):
    t = bus.HUB.get(job_id)
    if not t:
        raise HTTPException(404, "服务已重启，该任务状态不可查")
    return bus.sse_response(t)
