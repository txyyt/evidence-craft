"""模板库路由：清单 / 版本时间线 / 两版 diff / 回滚 / 原文读取。"""

import difflib
import shutil
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalayer.settings import settings

router = APIRouter(prefix="/api/templates")


def _root() -> Path:
    return settings.resolve("config/report_types")


def _versions_dir(name: str) -> Path:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(403, "非法模板名")
    return _root() / "versions" / name


def _main_file(name: str) -> Path:
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(403, "非法模板名")
    p = _root() / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(404, f"模板不存在：{name}")
    return p


@router.get("")
def list_templates() -> list[dict]:
    root = _root()
    out = []
    if not root.exists():
        return out
    for f in sorted(root.glob("*.yaml")):
        name = f.stem
        vdir = root / "versions" / name
        out.append({
            "name": name,
            "file": f"config/report_types/{f.name}",
            "draft": name.endswith("_draft"),
            "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds"),
            "versions": len(list(vdir.glob("*.yaml"))) if vdir.exists() else 0,
        })
    return out


@router.get("/{name}/raw")
def raw(name: str) -> dict:
    return {"name": name, "text": _main_file(name).read_text(encoding="utf-8")}


@router.get("/{name}/versions")
def versions(name: str) -> dict:
    vdir = _versions_dir(name)
    files = sorted(vdir.glob("*.yaml"), reverse=True) if vdir.exists() else []
    return {
        "current": _main_file(name).name,
        "versions": [{"file": v.name,
                      "ts": datetime.fromtimestamp(v.stat().st_mtime)
                      .isoformat(timespec="seconds")} for v in files],
    }


def _resolve_side(name: str, side: str) -> str:
    """side = 'current' 或 versions 目录下的文件名。"""
    if side == "current":
        return _main_file(name).read_text(encoding="utf-8")
    if "/" in side or "\\" in side or ".." in side:
        raise HTTPException(403, "非法版本文件名")
    p = _versions_dir(name) / side
    if not p.exists():
        raise HTTPException(404, f"版本不存在：{side}")
    return p.read_text(encoding="utf-8")


@router.get("/{name}/diff")
def diff_versions(name: str, a: str = "current", b: str = "current") -> dict:
    la, lb = _resolve_side(name, a).splitlines(), _resolve_side(name, b).splitlines()
    dl = list(difflib.unified_diff(la, lb, fromfile=a, tofile=b, lineterm=""))
    return {"diff": "\n".join(dl) or "（两版完全一致）",
            "changed": bool([x for x in dl if x[:1] in "+-" and x[:3] not in ("+++", "---")])}


class RollbackIn(BaseModel):
    name: str
    version: str


@router.post("/rollback")
def rollback(body: RollbackIn) -> dict:
    target = _main_file(body.name)
    vdir = _versions_dir(body.name)
    src = vdir / body.version
    if not src.exists():
        raise HTTPException(404, f"版本不存在：{body.version}")
    # 当前版先留痕，再回滚
    shutil.copy2(target, vdir / f"{datetime.now():%Y%m%d_%H%M%S}_pre_rollback.yaml")
    shutil.copy2(src, target)
    return {"ok": True, "restored_from": body.version}
