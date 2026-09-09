"""FastAPI 入口：静态前端 + API 路由挂载。

启动：python -m uvicorn server.main:app --host 127.0.0.1 --port 8765
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server import routes_overview, routes_runs, routes_settings, \
    routes_sources, routes_types

ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="EvidenceCraft 报告生成工具", version="1.0.0")
app.include_router(routes_overview.router)
app.include_router(routes_settings.router)
app.include_router(routes_runs.router)
app.include_router(routes_types.router)
app.include_router(routes_sources.router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/artifacts", StaticFiles(directory=ROOT / "artifacts"), name="artifacts")
