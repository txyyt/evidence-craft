"""首页 Dashboard 路由：报告类型/最近生成汇总 + 健康检查 + 任务状态。"""

from fastapi import APIRouter, HTTPException

from datalayer import registry
from datalayer.settings import mask_key, settings

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    m = settings.model
    return {"ok": True,
            "model": {"base_url": m.get("base_url", ""),
                      "model": m.get("model", ""),
                      "reasoning_effort": m.get("reasoning_effort", ""),
                      "api_key_masked": mask_key(m.get("api_key", ""))}}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    """任意后台任务（run/job 通用）的状态与错误——页面排查用。
    V4-02 增量字段：stage/message/run_dir（旧字段全部保留）。"""
    from server import bus
    t = bus.HUB.get(job_id)
    if not t:
        raise HTTPException(404, "任务不存在或服务已重启")
    last = None
    for ev in reversed(t.events):
        if ev.get("type") == "progress":
            last = ev
            break
    return {"id": t.id, "status": t.status, "error": t.error,
            "error_detail": (t.error_detail or "")[-800:] if t.error_detail else None,
            "result": t.result if t.status == "done" else None,
            "kind": t.department,
            "stage": (last or {}).get("stage"),
            "message": (last or {}).get("message"),
            "run_dir": t.run_dir}


@router.get("/overview")
def overview() -> dict:
    types = registry.list_types()
    from server import bus
    from server.routes_runs import _artifacts_root
    aroot = _artifacts_root()
    runs = []
    markers = ("meta.json", "outline.json", "judge_report.json", "final.html")
    if aroot.exists():
        dirs = [d for d in aroot.iterdir()
                if d.is_dir() and not d.name.startswith((".", "_"))
                and any((d / m).exists() for m in markers)]
        for d in sorted(dirs, key=lambda x: x.stat().st_mtime, reverse=True)[:6]:
            runs.append(bus.summarize(d))
    return {"types": types, "n_types": len(types),
            "recent_runs": runs, "model": health()["model"]}
