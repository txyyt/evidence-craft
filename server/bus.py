"""运行任务总线：工作线程跑流水线，progress 回调 → SSE 订阅队列。

事件协议（JSON，逐条推给浏览器）：
  {"type": "progress", "stage": "data|outline|sections|review|render",
   "message": "...", "data": {...}|null, "ts": "..."}
  {"type": "end", "status": "done|error", "run_dir": "...", "error": "..."|null}
"""

import asyncio
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class RunTask:
    def __init__(self, run_id: str, argv: list[str], department: str,
                 loop: asyncio.AbstractEventLoop) -> None:
        self.id = run_id
        self.argv = argv
        self.department = department
        self.status = "running"
        self.error: str | None = None
        self.run_dir: str | None = None
        self.result: Any = None
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.events: list[dict[str, Any]] = []
        self.queues: set[asyncio.Queue] = set()
        self.lock = threading.Lock()
        self._loop = loop

    def _emit(self, ev: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(ev)
            alive = list(self.queues)
        for q in alive:
            q.put_nowait(ev)

    def emit(self, ev: dict[str, Any]) -> None:
        """线程安全投递：工作线程经 call_soon_threadsafe 切回事件循环。"""
        self._loop.call_soon_threadsafe(self._emit, ev)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self.lock:
            for ev in self.events:
                q.put_nowait(ev)
            if self.status != "running":
                q.put_nowait(self._end_event())
            self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self.lock:
            self.queues.discard(q)

    def _end_event(self) -> dict[str, Any]:
        return {"type": "end", "status": self.status,
                "run_dir": self.run_dir, "error": self.error}


HUB: dict[str, RunTask] = {}


def start_run(argv: list[str], department: str,
              loop: asyncio.AbstractEventLoop) -> RunTask:
    run_id = uuid.uuid4().hex[:12]
    task = RunTask(run_id, argv, department, loop)
    HUB[run_id] = task

    def progress(stage: str, message: str, data: dict | None) -> None:
        if data and data.get("run_dir"):
            task.run_dir = data["run_dir"]
        task.emit({"type": "progress", "stage": stage, "message": message,
                   "data": data, "ts": datetime.now().isoformat(timespec="seconds")})

    def worker() -> None:
        try:
            from run_pipeline import main
            main(argv, progress=progress)
            task.status = "done"
        except SystemExit as e:  # run_pipeline 数据层 fail 时 SystemExit(1)
            task.status = "error"
            task.error = f"流水线中止（exit {e.code}）"
        except BaseException:  # noqa: BLE001 —— 把错误完整报给页面
            task.status = "error"
            task.error = traceback.format_exc(limit=6)
        finally:
            task.emit(task._end_event())

    threading.Thread(target=worker, daemon=True,
                     name=f"run-{run_id}").start()
    return task


def start_job(fn, label: str, loop: asyncio.AbstractEventLoop) -> RunTask:
    """通用后台任务（模板提取/回放/试跑）：fn(progress) 在工作线程执行，
    返回值存入 task.result。progress(stage, message, data|None)。"""
    run_id = uuid.uuid4().hex[:12]
    task = RunTask(run_id, [], label, loop)
    HUB[run_id] = task

    def progress(stage: str, message: str, data: dict | None = None) -> None:
        task.emit({"type": "progress", "stage": stage, "message": message,
                   "data": data, "ts": datetime.now().isoformat(timespec="seconds")})

    def worker() -> None:
        try:
            task.result = fn(progress)
            task.status = "done"
        except BaseException:  # noqa: BLE001
            task.status = "error"
            task.error = traceback.format_exc(limit=6)
        finally:
            task.emit(task._end_event())

    threading.Thread(target=worker, daemon=True,
                     name=f"job-{label}-{run_id}").start()
    return task


def summarize(run_dir: Path) -> dict[str, Any]:
    """产物目录 → 历史列表条目（judge 分/verdict/标题/文件存在性）。"""
    import json

    entry: dict[str, Any] = {
        "name": run_dir.name, "dir": run_dir.name,
        "mtime": datetime.fromtimestamp(run_dir.stat().st_mtime)
        .isoformat(timespec="seconds"),
        "has_html": (run_dir / "final.html").exists(),
        "has_docx": (run_dir / "final.docx").exists(),
    }
    for key, fname in (("judge_total", "judge_report.json"),
                       ("outline_title", "outline.json"),
                       ("meta", "meta.json")):
        p = run_dir / fname
        if not p.exists():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if key == "judge_total":
            entry["judge_total"] = j.get("total")
            entry["verdict"] = j.get("verdict")
        elif key == "outline_title":
            entry["title"] = j.get("title")
        else:
            entry["department"] = j.get("department")
    return entry
