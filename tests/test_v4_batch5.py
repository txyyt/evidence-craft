"""V4 批⑤单测（V4-06/07）：reuse_data_from 后端三重校验（合法/嵌套/../
缺 facts/缺 plan）、经典 /api/runs/start 契约不变。bus.start_run 拦截 mock，
无 LLM、无真实生成。

pytest 运行；也可 python tests/test_v4_batch5.py 直接执行。
"""

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datalayer.settings import settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from server.main import app  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_v4b5_tree"
RUN = "_t_v4b5_reuse_run"


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)
    d = settings.resolve("artifacts") / RUN
    if d.exists():
        shutil.rmtree(d)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _mk_fixture(monkeypatch):
    """树 + 已确认计划 + 直接子目录产物（含 facts.json）+ start_run 拦截。"""
    _cleanup()
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "批五测试树",
        "sections": [{"id": "a", "title": "A", "kind": "text",
                      "data_needs": ["x"]}]},
        {"name": "T", "subject": "S"}, actor="system")
    tree_store.save_plan(TID, {"mode": "plan", "rag": [], "web": [], "db": [],
                               "confirmed": True})
    d = settings.resolve("artifacts") / RUN
    d.mkdir(parents=True)
    (d / "facts.json").write_text(json.dumps({"facts": []}), encoding="utf-8")

    captured = {}

    def fake_start_run(argv, department, loop):
        captured["argv"] = list(argv)
        return SimpleNamespace(id="fake_run_id", events=[], status="running",
                               run_dir=None, error=None, error_detail=None,
                               cancel_event=None, department=department,
                               result=None, lock=None)

    from server import bus
    monkeypatch.setattr(bus, "start_run", fake_start_run)
    return captured


def test_reuse_data_from_validation(client, monkeypatch):
    """合法直接子目录 → 200 且 argv 带 --reuse-data；嵌套/.. → 403；
    缺 facts → 422；缺 plan → 422。"""
    captured = _mk_fixture(monkeypatch)
    try:
        body = {"tree_id": TID, "plan": "plans.json", "reuse_data_from": RUN}
        # 计划名：save_plan 返回带时间戳名——重新取
        plans = [p.name for p in
                 (tree_store.tree_dir(TID) / "plans").glob("*.json")]
        body["plan"] = plans[0]

        # 嵌套路径 → 403
        r_bad = client.post("/api/runs/from_tree", json={
            "tree_id": TID, "plan": plans[0],
            "reuse_data_from": f"{RUN}/nested"})
        assert r_bad.status_code == 403
        # .. 穿越 → 403
        r_dotdot = client.post("/api/runs/from_tree", json={
            "tree_id": TID, "plan": plans[0],
            "reuse_data_from": f"../artifacts/{RUN}"})
        assert r_dotdot.status_code == 403
        # 目录不存在（合法名字）→ 缺 facts → 422
        r_missing = client.post("/api/runs/from_tree", json={
            "tree_id": TID, "plan": plans[0],
            "reuse_data_from": "_t_v4b5_no_such_dir"})
        assert r_missing.status_code == 422
        # 缺 plan → 422
        r_noplan = client.post("/api/runs/from_tree", json={
            "tree_id": TID, "intent": "x", "reuse_data_from": RUN})
        assert r_noplan.status_code == 422
        # 合法 → 200，argv 带 --reuse-data 且值正确
        r_ok = client.post("/api/runs/from_tree", json=body)
        assert r_ok.status_code == 200, r_ok.text
        assert "--reuse-data" in captured["argv"]
        idx = captured["argv"].index("--reuse-data")
        assert captured["argv"][idx + 1] == RUN
    finally:
        _cleanup()


def test_classic_start_contract_unchanged(client, monkeypatch):
    """经典 /api/runs/start 契约不变：旧请求体 2xx，argv 不出现树模式参数。"""
    captured = {}

    def fake_start_run(argv, department, loop):
        captured["argv"] = list(argv)
        return SimpleNamespace(id="fake_classic", events=[], status="running",
                               run_dir=None, error=None, error_detail=None,
                               cancel_event=None, department=department,
                               result=None, lock=None)

    from server import bus
    monkeypatch.setattr(bus, "start_run", fake_start_run)
    r = client.post("/api/runs/start", json={
        "type_id": "company_review", "project": "测试"})
    assert r.status_code == 200
    assert r.json()["id"] == "fake_classic"
    assert "--tree" not in captured["argv"]
    assert "--reuse-data" not in captured["argv"]


if __name__ == "__main__":
    _c = TestClient(app)

    class _Mp:
        def setattr(self, m, n, v):
            setattr(m, n, v)

    test_reuse_data_from_validation(_c, _Mp())
    test_classic_start_contract_unchanged(_c, _Mp())
    print("test_v4_batch5 全部通过")
