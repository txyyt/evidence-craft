"""V4 批③单测（V4-02 全局任务中心）：通用 job 状态/取消端点、协作取消、
preview/start 后台化、同步 preview 契约不变。全部 mock，无 LLM/联网。

pytest 运行；也可 python tests/test_v4_tasks.py 直接执行。
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server import bus  # noqa: E402
from server.main import app  # noqa: E402

TID = "_t_v4tasks_tree"


def _cleanup():
    from trees import store as tree_store
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _wait_status(task, statuses, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.status in statuses:
            return task.status
        time.sleep(0.05)
    return task.status


def test_job_status_and_cancel_lifecycle(client):
    """可控慢 job：start → running → cancel → cancelled；重复 cancel 不抛 500；
    状态接口字段稳定。"""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    def fn(progress):
        # 协作取消检查点（真实 fn 同款写法）
        ev = getattr(progress, "cancel_event", None)
        deadline = time.time() + 6
        while time.time() < deadline:
            if ev is not None and ev.is_set():
                from run_pipeline import PipelineCancelled
                raise PipelineCancelled()
            time.sleep(0.02)
        return {"ok": True}

    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(2)
    task = bus.start_job(fn, "v4tasks-slow", loop)
    try:
        # running
        r = client.get(f"/api/jobs/{task.id}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == task.id and body["status"] == "running"
        assert "stage" in body and "message" in body and "error" in body
        # cancel
        r2 = client.post(f"/api/jobs/{task.id}/cancel")
        assert r2.status_code == 200 and r2.json()["ok"] is True
        final = _wait_status(task, {"cancelled", "done", "error"})
        assert final == "cancelled", final
        # 重复 cancel → 200 {ok:false}，不抛 500
        r3 = client.post(f"/api/jobs/{task.id}/cancel")
        assert r3.status_code == 200
        assert r3.json() == {"ok": False, "status": "cancelled"}
        # 终态查询稳定
        r4 = client.get(f"/api/jobs/{task.id}")
        assert r4.json()["status"] == "cancelled"
    finally:
        bus.HUB.pop(task.id, None)
        loop.call_soon_threadsafe(loop.stop)


def test_job_unknown_404(client):
    assert client.get("/api/jobs/no_such_job").status_code == 404
    assert client.post("/api/jobs/no_such_job/cancel").status_code == 404


def test_preview_start_background(client, monkeypatch):
    """preview/start 后台化：返回 {id, events_url}；任务到 done；
    同步 /plan/preview 保留且行为不变（缓存命中）。"""
    from trees import store as tree_store
    _cleanup()
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "任务中心测试树",
        "sections": [{"id": "a", "title": "A", "kind": "text",
                      "data_needs": ["x"]}]},
        {"name": "T", "subject": "S"}, actor="system")
    plan_file = tree_store.save_plan(TID, {"mode": "plan", "rag": [],
                                           "web": [], "db": []})
    try:
        # mock 数据层：轻量可控
        import datalayer.registry as registry
        monkeypatch.setattr(registry, "run_data_layer",
                            lambda *a, **k: (
                                {"meta": {"warnings": []},
                                 "facts": [{"id": "f1", "source": "web"}]},
                                {"status": "pass"}))
        with client:
            r = client.post(f"/api/trees/{TID}/plan/preview/start",
                            json={"plan_file": plan_file})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["id"] and body["events_url"].endswith("/events")
            # 轮询任务到 done（mock 数据层瞬时完成）
            status = None
            for _ in range(60):
                s = client.get(f"/api/jobs/{body['id']}").json()
                status = s["status"]
                if status in ("done", "error", "cancelled"):
                    break
                time.sleep(0.1)
            assert status == "done", status
            # 同步端点（缓存命中：后台预检已写缓存）
            r2 = client.post(f"/api/trees/{TID}/plan/preview",
                             json={"plan_file": plan_file})
            assert r2.status_code == 200
            sync = r2.json()
            assert sync["cached"] is True and sync["n_facts"] == 1
    finally:
        _cleanup()


def test_plan_start_contract_unchanged(client):
    """plan/start 旧响应契约不变（树不存在 → 404）。"""
    r = client.post("/api/trees/no_such_tree/plan/start", json={"intent": ""})
    assert r.status_code == 404


if __name__ == "__main__":
    _c = TestClient(app)
    test_job_status_and_cancel_lifecycle(_c)
    test_job_unknown_404(_c)

    class _Mp:
        def setattr(self, m, n, v):
            setattr(m, n, v)

    test_preview_start_background(_c, _Mp())
    test_plan_start_contract_unchanged(_c)
    print("test_v4_tasks 全部通过")
