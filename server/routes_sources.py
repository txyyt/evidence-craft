"""数据源路由：adapter 注册表总览 / 外部连接配置 / 测试连接。

[deprecated] 经典模式（报告类型）已废弃：本路由仅剩 /types 页面仍在使用，
随经典 UI 下线一并摘除（V2 方案 §一 C6 / §三 §6）。不再新增功能。
连接凭据只进 settings.yaml（gitignored），API 响应不回显密钥型字段
（当前 database 为 SQLite 文件路径、rag 为 endpoint，均非密钥）。


连接凭据只进 settings.yaml（gitignored），API 响应不回显密钥型字段
（当前 database 为 SQLite 文件路径、rag 为 endpoint，均非密钥）。
"""

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from datalayer.registry import ADAPTERS
from datalayer.settings import settings

router = APIRouter(prefix="/api/sources")


def _update_sections(databases: dict | None, rag: dict | None) -> None:
    """写回 settings.yaml 的 databases / rag 段（ruamel 保注释）。"""
    from ruamel.yaml import YAML

    path = settings.resolve("config/settings.yaml")
    ry = YAML()
    ry.preserve_quotes = True
    with open(path, encoding="utf-8") as f:
        cfg = ry.load(f)
    if databases is not None:
        cfg["databases"] = databases
    if rag is not None:
        cfg["rag"] = rag
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        ry.dump(cfg, f)
    tmp.replace(path)


@router.get("/adapters")
def adapters() -> list[dict]:
    out = []
    for key, cls in sorted(ADAPTERS.items()):
        out.append({"key": key, "kind": getattr(cls, "kind", ""),
                    "reliability": getattr(cls, "default_reliability", ""),
                    "summary": getattr(cls, "summary", ""),
                    "param_schema": getattr(cls, "param_schema", None) or [],
                    "ctx_keys": getattr(cls, "ctx_keys", None) or []})
    return out


@router.get("/connections")
def connections() -> dict:
    return {"databases": settings.databases or {},
            "rag": settings.rag or {}}


class ConnectionsIn(BaseModel):
    databases: dict | None = None
    rag: dict | None = None


@router.put("/connections")
def put_connections(body: ConnectionsIn) -> dict:
    _update_sections(body.databases, body.rag)
    settings.reload()
    return connections()


class DbTestIn(BaseModel):
    db_ref: str


@router.post("/test/database")
def test_database(body: DbTestIn) -> dict:
    cfg = (settings.databases or {}).get(body.db_ref)
    if not cfg:
        raise HTTPException(404, f"未配置的数据库引用：{body.db_ref}")
    path = cfg.get("path") if isinstance(cfg, dict) else cfg
    p = settings.resolve(str(path))
    if not p.exists():
        return {"ok": False, "error": f"文件不存在：{p}"}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        con.close()
        return {"ok": True, "tables": tables[:20], "n_tables": len(tables)}
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e)}


@router.post("/test/rag")
def test_rag() -> dict:
    """RAG 连通测试：mock 模式下确认"测试即读本地片段"的路径约定（片段文件
    按绑定 params 提供，此处仅确认模式）；真实 endpoint 发一次探测。"""
    endpoint = (settings.rag or {}).get("endpoint", "mock")
    if endpoint == "mock":
        return {"ok": True, "mode": "mock",
                "note": "mock 模式：片段文件在各绑定 params.mock_fragments 声明，"
                        "用部门页的单绑定测试可实际验证读取"}
    import requests
    try:
        resp = requests.post(endpoint, json={"query": "连通测试", "top_k": 1}, timeout=10)
        return {"ok": resp.status_code < 500, "mode": "live",
                "status": resp.status_code,
                "note": "真实 endpoint 探测（返回体结构由对方服务决定）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mode": "live", "error": str(e)[:200]}
