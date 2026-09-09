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


class PreviewIn(BaseModel):
    type_id: str
    params: dict[str, str] = {}


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
                    ("--period", body.period)):
        if v:
            argv += [flag, v]
    task = bus.start_run(argv, body.type_id, asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/runs/{task.id}/events"}


@router.post("/preview")
def preview(body: PreviewIn) -> dict:
    """数据预检：只跑数据层（秒级），提前暴露缺参数/数据为空。"""
    from datalayer import registry
    try:
        doc, crosscheck = registry.run_data_layer(body.type_id, body.params)
    except FileNotFoundError as e:
        return {"ok": False, "error": f"数据文件不存在：{e}"}
    except Exception as e:  # noqa: BLE001 —— 预检就是把问题提前报出来
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    warnings = doc["meta"].get("warnings") or []
    return {"ok": len(doc["facts"]) > 0,
            "n_facts": len(doc["facts"]),
            "warnings": warnings,
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


@router.get("")
def history(type_id: str | None = None) -> list[dict]:
    root = _artifacts_root()
    markers = ("meta.json", "outline.json", "facts.json",
               "judge_report.json", "final.html")
    out = []
    if root.exists():
        for d in root.iterdir():
            if d.is_dir() and not d.name.startswith(".") \
                    and not d.name.startswith("_") \
                    and any((d / m).exists() for m in markers):
                entry = bus.summarize(d)
                if type_id and entry.get("type_id") != type_id:
                    continue
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


@router.get("/artifact")
def artifact(dir: str, file: str) -> FileResponse:
    if "/" in file or "\\" in file or ".." in file:
        raise HTTPException(403, "非法文件名")
    p = _safe_dir(dir) / file
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
    q = t.subscribe()

    async def gen():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=600)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # 空心跳，维持连接
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "end":
                    break
        finally:
            t.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
