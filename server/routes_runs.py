"""报告生成路由：启动（线程 + SSE 进度）/ 取消 / 数据预检 / 历史 / 产物 / 删除。"""

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from datalayer.settings import settings
from server import bus

router = APIRouter(prefix="/api/runs")


class RunIn(BaseModel):
    type_id: str = "company_review"
    stock: str | None = None
    project: str | None = None
    period: str | None = None
    spec: str | None = None
    intent: str | None = None      # 写作意图一段话（意图规划模式）
    folder: str | None = None      # 本地资料文件夹（现场建语料库）
    model_tier: str | None = None  # F3 档位覆盖（settings.model_tiers 档名）


class TreeRunIn(BaseModel):
    """树模式生成：结构从 config/trees/<id>/tree.yaml 加载（树对话工作台入口）。"""
    tree_id: str
    project: str | None = None
    period: str | None = None
    intent: str | None = None      # 写作意图（与树 data_needs 一起进规划器）
    folder: str | None = None      # 本地资料文件夹（现场建语料库）
    plan: str | None = None        # 沿用树目录 plans/ 下的采集计划
    model_tier: str | None = None


class PreviewIn(BaseModel):
    type_id: str
    params: dict[str, str] = {}
    intent: str | None = None
    folder: str | None = None
    model_tier: str | None = None


def _artifacts_root() -> Path:
    return settings.resolve(settings.artifacts_dir).resolve()


def _safe_dir(dir_name: str) -> Path:
    """只允许 artifacts/ 下的直接子目录，杜绝路径穿越。"""
    root = _artifacts_root()
    if not dir_name or "/" in dir_name or "\\" in dir_name or ".." in dir_name:
        raise HTTPException(403, "非法目录名")
    p = (root / dir_name).resolve()
    if p.parent != root:
        raise HTTPException(403, "非法目录名")
    return p


@router.post("/start")
async def start(body: RunIn) -> dict:
    argv = ["--type", body.type_id, "--full"]
    if body.spec:
        argv += ["--spec", body.spec]
    for flag, v in (("--stock", body.stock), ("--project", body.project),
                    ("--period", body.period), ("--intent", body.intent),
                    ("--folder", body.folder)):
        if v:
            argv += [flag, v]
    if body.model_tier:
        argv += ["--model-tier", body.model_tier]
    task = bus.start_run(argv, body.type_id, asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/runs/{task.id}/events"}


@router.post("/from_tree")
async def from_tree(body: TreeRunIn) -> dict:
    """树模式生成入口（A4 生成门禁）：
    - intent/folder/plan 三者全空 → 422 拒绝（0 事实空谈稿不得出厂）
    - plan 给出 → 校验 confirmed 持久化标记 + 无未裁决 gap（coverage_gate）
    - 计划含 needs_folder（缺口裁决「我提供资料」）→ 必须给 folder（A5）"""
    from trees import store as tree_store
    if not tree_store.exists(body.tree_id):
        raise HTTPException(404, f"结构树不存在：{body.tree_id}")
    if not (body.intent or body.folder or body.plan):
        raise HTTPException(
            422, "缺少取数来源：请先「出数据计划」完成缺口裁决并确认，"
                 "或填写写作意图/本地资料文件夹（三者全空将生成无数据支撑的报告，已被门禁拦截）")
    if body.plan:
        plan = tree_store.load_plan(body.tree_id, body.plan)
        if not plan.get("confirmed"):
            raise HTTPException(422, "该数据计划尚未确认：请先完成缺口裁决并点「确认裁决」")
        gate = tree_store.coverage_gate(plan)
        if not gate["ok"]:
            raise HTTPException(422, "计划仍有未裁决缺口："
                                + "、".join(gate["unresolved"][:5]))
        if plan.get("needs_folder") and not body.folder:
            raise HTTPException(422, "该计划有缺口裁决为『我提供资料』，请填写本地资料文件夹")
    argv = ["--tree", body.tree_id, "--full"]
    for flag, v in (("--project", body.project), ("--period", body.period),
                    ("--intent", body.intent), ("--folder", body.folder),
                    ("--plan", body.plan)):
        if v:
            argv += [flag, v]
    if body.model_tier:
        argv += ["--model-tier", body.model_tier]
    # 须为 async def：start_run 需要 running loop（同步 def 跑在线程池里没有）
    task = bus.start_run(argv, body.tree_id, asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/runs/{task.id}/events"}


@router.post("/preview")
def preview(body: PreviewIn) -> dict:
    """数据预检：只跑数据层（秒级），提前暴露缺参数/数据为空。
    意图模式（intent/folder 给出时）先用规划器生成采集计划，按计划取数，
    并把计划摘要一并返回供页面展示。model_tier 只作用于本次预检调用。"""
    from datalayer import registry
    from pipeline import llm
    if body.model_tier:
        llm.set_tier_override(body.model_tier)
    try:
        return _preview_impl(body, registry)
    finally:
        llm.set_tier_override(None)


def _preview_impl(body: PreviewIn, registry) -> dict:
    override = None
    plan_summary = None
    if body.intent or body.folder:
        try:
            from datalayer.planner import make_plan, plan_to_bindings
            from template_factory.schema import load_spec
            spec = load_spec(registry.spec_path(body.type_id))
            plan = make_plan(spec, registry.load_sources(body.type_id),
                             body.intent or "", body.folder or None)
            override = plan_to_bindings(plan, registry.load_sources(body.type_id))
            plan_summary = {
                "mode": plan["mode"],
                "focus": plan.get("focus", ""),
                "n_rag": len(plan.get("rag") or []),
                "n_web": len(plan.get("web") or []),
                "n_db": len(plan.get("db") or []),
                "n_tables": len(plan.get("tables_kept") or []),
                "corpus": ({"n_fragments": plan["corpus"]["n_fragments"],
                            "rebuilt": plan["corpus"]["rebuilt"],
                            "n_files": plan["corpus"]["n_files"]}
                           if plan.get("corpus") else None),
            }
            if plan.get("warnings"):
                plan_summary["warnings"] = plan["warnings"][:5]
            if plan.get("error"):
                plan_summary["note"] = f"规划降级（{plan['error'][:80]}）"
        except FileNotFoundError as e:
            return {"ok": False, "error": f"数据文件不存在：{e}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"规划失败：{type(e).__name__}: {e}"}
    try:
        doc, crosscheck = (registry.run_data_layer(body.type_id, body.params,
                                                   bindings_override=override)
                           if override is not None else
                           registry.run_data_layer(body.type_id, body.params))
    except FileNotFoundError as e:
        return {"ok": False, "error": f"数据文件不存在：{e}"}
    except Exception as e:  # noqa: BLE001 —— 预检就是把问题提前报出来
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    warnings = doc["meta"].get("warnings") or []
    return {"ok": len(doc["facts"]) > 0,
            "n_facts": len(doc["facts"]),
            "warnings": warnings,
            "plan": plan_summary,
            "crosscheck": (crosscheck or {}).get("status") if crosscheck else None,
            "sample": doc["facts"][:5],
            "error": None if doc["facts"] else "所有绑定均未取到数据（检查参数与绑定）"}


@router.post("/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    t = bus.HUB.get(run_id)
    if not t:
        raise HTTPException(404, "运行不存在或服务已重启")
    if t.status != "running":
        return {"ok": False, "status": t.status}
    t.cancel_event.set()
    return {"ok": True}


# ---------- 反馈回路（M3）：总意见 → 路由 → 三类执行 → 轮次/回滚/深度评审 ----------

class FeedbackParseIn(BaseModel):
    text: str


class FeedbackApplyIn(BaseModel):
    text: str
    ops: list[dict]


class FeedbackRollbackIn(BaseModel):
    round: int


def _run_dir(dir_name: str) -> Path:
    d = _safe_dir(dir_name)
    if not (d / "sections.json").exists():
        raise HTTPException(404, "该目录没有报告稿件（反馈回路仅支持树模式生成的运行）")
    return d


@router.post("/{dir_name}/feedback/parse")
def feedback_parse(dir_name: str, body: FeedbackParseIn) -> dict:
    from pipeline import feedback
    return feedback.parse(_run_dir(dir_name), body.text)


@router.post("/{dir_name}/feedback/apply")
async def feedback_apply(dir_name: str, body: FeedbackApplyIn) -> dict:
    run_dir = _run_dir(dir_name)

    def fn(progress) -> dict:
        from pipeline import feedback
        return feedback.apply(run_dir, body.ops, body.text, progress=progress)

    task = bus.start_job(fn, f"feedback-{dir_name}", asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/runs/feedback/{task.id}/events"}


@router.get("/feedback/{job_id}/events")
async def feedback_events(job_id: str):
    t = bus.HUB.get(job_id)
    if not t:
        raise HTTPException(404, "服务已重启，该任务状态不可查")
    return bus.sse_response(t)


@router.get("/{dir_name}/feedback/rounds")
def feedback_rounds(dir_name: str) -> list[dict]:
    from pipeline import feedback
    return feedback.rounds(_run_dir(dir_name))


@router.get("/{dir_name}/feedback/diff")
def feedback_diff(dir_name: str, round: int) -> dict:
    import json as _json
    p = _run_dir(dir_name) / "rounds" / str(round) / "diff.json"
    if not p.exists():
        raise HTTPException(404, f"第 {round} 轮没有 diff")
    return _json.loads(p.read_text(encoding="utf-8"))


@router.post("/{dir_name}/feedback/rollback")
async def feedback_rollback(dir_name: str, body: FeedbackRollbackIn) -> dict:
    run_dir = _run_dir(dir_name)

    def fn(progress) -> dict:
        from pipeline import feedback
        return feedback.rollback(run_dir, body.round, progress=progress)

    task = bus.start_job(fn, f"rollback-{dir_name}", asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/runs/feedback/{task.id}/events"}


@router.post("/{dir_name}/judge")
async def judge_deep_endpoint(dir_name: str) -> dict:
    run_dir = _run_dir(dir_name)

    def fn(progress) -> dict:
        from pipeline import feedback
        return feedback.judge_deep(run_dir, progress=progress)

    task = bus.start_job(fn, f"judge-{dir_name}", asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/runs/feedback/{task.id}/events"}


@router.get("")
def history(type_id: str | None = None, tree: str | None = None) -> list[dict]:
    """V2 §三 §2：报告库统一列表（树模式 + 历史经典报告）。
    tree 参数：按树 id 过滤（模板页「报告计数」跳转用）。"""
    root = _artifacts_root()
    markers = ("meta.json", "outline.json", "facts.json",
               "judge_report.json", "final.html")
    tree_names: dict[str, str] = {}
    try:
        from trees import store as tree_store
        tree_names = {t["id"]: t["name"] for t in tree_store.list_trees()}
    except Exception:  # noqa: BLE001 —— 树列表不可用时不阻塞报告库
        tree_names = {}
    out = []
    if root.exists():
        for d in root.iterdir():
            if d.is_dir() and not d.name.startswith(".") \
                    and not d.name.startswith("_") \
                    and any((d / m).exists() for m in markers):
                entry = bus.summarize(d)
                if type_id and entry.get("type_id") != type_id:
                    continue
                if tree and entry.get("tree_id") != tree:
                    continue
                if entry.get("tree_id"):
                    entry["source_name"] = tree_names.get(entry["tree_id"],
                                                          entry["tree_id"])
                else:
                    entry["source_name"] = entry.get("type_name")
                out.append(entry)
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:80]


@router.delete("/{dir_name}")
def delete_run(dir_name: str, delete_files: bool = True) -> dict:
    d = _safe_dir(dir_name)
    if delete_files:
        import shutil
        shutil.rmtree(d)
    return {"ok": True}


@router.get("/{dir_name}/files")
def list_files(dir_name: str) -> list[dict]:
    """V2 §三 §3 详情页「文件」页签：产物目录清单（根 + rounds/<n>/ 一层）。
    只读；后缀白名单，防路径穿越。"""
    import time
    d = _safe_dir(dir_name)
    allowed = (".json", ".jsonl", ".html", ".png", ".docx", ".txt")
    out = []
    for p in sorted(d.iterdir(), key=lambda x: x.name):
        if p.is_file() and p.suffix.lower() in allowed:
            out.append({"name": p.name, "size": p.stat().st_size,
                        "mtime": time.strftime(
                            "%Y-%m-%d %H:%M",
                            time.localtime(p.stat().st_mtime))})
        elif p.is_dir() and p.name == "rounds":
            for sub in sorted(p.iterdir(), key=lambda x: x.name):
                if not sub.is_dir() or not sub.name.isdigit():
                    continue
                for f in sorted(sub.iterdir(), key=lambda x: x.name):
                    if f.is_file() and f.suffix.lower() in allowed:
                        out.append({"name": f"rounds/{sub.name}/{f.name}",
                                    "size": f.stat().st_size,
                                    "mtime": time.strftime(
                                        "%Y-%m-%d %H:%M",
                                        time.localtime(f.stat().st_mtime))})
    return out


@router.get("/artifact")
def artifact(dir: str, file: str) -> FileResponse:
    """产物文件读取。V2 §三 §3：白名单放行 rounds/<n>/<name> 子目录
    （反馈轮次的 diff/ops/reconcile/validate 快照），其余拒绝路径穿越。"""
    if ".." in file or file.startswith("/"):
        raise HTTPException(403, "非法文件名")
    p = _safe_dir(dir)
    if "/" in file:
        parts = file.split("/")
        if len(parts) != 3 or parts[0] != "rounds" \
                or not parts[1].isdigit() or not parts[2] \
                or "/" in parts[2] or "\\" in parts[2] or ".." in parts[2]:
            raise HTTPException(403, "非法文件名")
        p = p / "rounds" / parts[1] / parts[2]
    else:
        if "\\" in file:
            raise HTTPException(403, "非法文件名")
        p = p / file
    if not p.is_file():
        raise HTTPException(404, f"产物尚未生成：{file}")
    return FileResponse(p, media_type="application/json; charset=utf-8")


@router.get("/report")
def report(dir: str, format: str = "html") -> FileResponse:
    d = _safe_dir(dir)
    if format == "docx":
        p = d / "final.docx"
        if not p.is_file():
            raise HTTPException(404, "docx 尚未生成")
        return FileResponse(p, filename="final.docx",
                            media_type="application/vnd.openxmlformats-officedocument"
                                       ".wordprocessingml.document")
    p = d / "final.html"
    if not p.is_file():
        raise HTTPException(404, "final.html 尚未生成")
    return FileResponse(p, media_type="text/html; charset=utf-8")


@router.get("/{run_id}")
def run_status(run_id: str) -> dict:
    t = bus.HUB.get(run_id)
    if not t:
        raise HTTPException(404, "服务已重启，该次运行状态不可查（历史产物不受影响）")
    return {"id": t.id, "status": t.status, "type_id": t.department,
            "run_dir": t.run_dir, "error": t.error,
            "error_detail": t.error_detail}


@router.get("/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    t = bus.HUB.get(run_id)
    if not t:
        raise HTTPException(404, "服务已重启，该次运行状态不可查（历史产物不受影响）")
    return bus.sse_response(t)
