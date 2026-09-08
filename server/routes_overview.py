"""首页 Dashboard 路由：部门/模板/最近运行汇总 + 健康检查。"""

from fastapi import APIRouter

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


@router.get("/overview")
def overview() -> dict:
    import os

    import yaml

    root = settings.resolve("config/departments")
    departments = []
    if root.exists():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            pf = d / "profile.yaml"
            if not pf.exists():
                continue
            with open(pf, encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
            departments.append({
                "id": d.name,
                "description": profile.get("description", ""),
                "bindings": len(profile.get("bindings") or []),
            })
    troot = settings.resolve("config/report_types")
    templates = len(list(troot.glob("*.yaml"))) if troot.exists() else 0

    from server import bus
    from server.routes_runs import _artifacts_root
    aroot = _artifacts_root()
    runs = []
    markers = ("meta.json", "outline.json", "judge_report.json", "final.html")
    if aroot.exists():
        dirs = [d for d in aroot.iterdir()
                if d.is_dir() and not d.name.startswith(".")
                and any((d / m).exists() for m in markers)]
        for d in sorted(dirs, key=lambda x: x.stat().st_mtime, reverse=True)[:6]:
            runs.append(bus.summarize(d))
    return {"departments": departments, "templates": templates,
            "recent_runs": runs, "model": health()["model"]}
