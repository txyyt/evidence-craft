"""部门与数据源管理路由。

M8-① 先提供部门列表（壳上全局部门切换器要用）；
绑定编辑 / adapter 总览 / 测试连接在 M8-④ 补全。
"""

import shutil
from datetime import datetime

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalayer.settings import settings

router = APIRouter(prefix="/api")


@router.get("/departments")
def list_departments() -> list[dict]:
    root = settings.resolve("config/departments")
    out: list[dict] = []
    if not root.exists():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        profile = d / "profile.yaml"
        if not profile.exists():
            continue
        with open(profile, encoding="utf-8") as f:
            p = yaml.safe_load(f) or {}
        out.append({"id": d.name,
                    "description": p.get("description", ""),
                    "bindings": len(p.get("bindings") or [])})
    return out


@router.get("/departments/{dept_id}")
def department_detail(dept_id: str) -> dict:
    """部门 profile 原文（params_schema 供 /run 表单动态渲染；M8-④ 编辑器复用）。"""
    if "/" in dept_id or ".." in dept_id:
        raise HTTPException(404, "非法部门名")
    p = settings.resolve(f"config/departments/{dept_id}/profile.yaml")
    if not p.exists():
        raise HTTPException(404, f"部门不存在：{dept_id}")
    with open(p, encoding="utf-8") as f:
        profile = yaml.safe_load(f) or {}
    return {"id": dept_id, "profile": profile}


class ProfileIn(BaseModel):
    profile: dict


@router.put("/departments/{dept_id}")
def save_department(dept_id: str, body: ProfileIn) -> dict:
    """保存 profile：写盘前快照留痕（ruamel 往返保注释）。"""
    from ruamel.yaml import YAML as _RY

    if "/" in dept_id or ".." in dept_id:
        raise HTTPException(403, "非法部门名")
    p = settings.resolve(f"config/departments/{dept_id}/profile.yaml")
    if not p.exists():
        raise HTTPException(404, f"部门不存在：{dept_id}")
    snaps = p.parent / "profile_snapshots"
    snaps.mkdir(exist_ok=True)
    shutil.copy2(p, snaps / f"{datetime.now():%Y%m%d_%H%M%S}.yaml")

    ry = _RY()
    ry.preserve_quotes = True
    with open(p, encoding="utf-8") as f:
        old = ry.load(f) or {}
    new = body.profile
    old.clear()
    old.update(new)
    tmp = p.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        ry.dump(old, f)
    tmp.replace(p)
    return {"ok": True,
            "snapshot": f"profile_snapshots/{datetime.now():%Y%m%d_%H%M%S}.yaml"}


class NewDeptIn(BaseModel):
    id: str
    description: str = ""


@router.post("/departments")
def create_department(body: NewDeptIn) -> dict:
    import re as _re
    dept = _re.sub(r"[^0-9a-zA-Z_]+", "_", body.id).strip("_").lower()
    if not dept:
        raise HTTPException(422, "部门名不合法")
    d = settings.resolve(f"config/departments/{dept}")
    if d.exists():
        raise HTTPException(409, f"部门已存在：{dept}")
    d.mkdir(parents=True)
    profile = {
        "description": body.description or f"{dept} 部门",
        "params_schema": {},
        "vocabulary": {},
        "features": {},
        "bindings": [],
    }
    with open(d / "profile.yaml", "w", encoding="utf-8") as f:
        yaml.dump(profile, f, allow_unicode=True, sort_keys=False)
    return {"ok": True, "id": dept}


class BindingTestIn(BaseModel):
    index: int
    params: dict[str, str] = {}


@router.post("/departments/{dept_id}/test_binding")
def test_binding(dept_id: str, body: BindingTestIn) -> dict:
    from datalayer.registry import test_binding as _tb
    try:
        return _tb(dept_id, body.index, body.params)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
