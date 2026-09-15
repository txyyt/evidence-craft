"""V2 阶段一 A4/A5 单测：树模式生成门禁（from_tree 三态校验 + confirmed
持久化 + needs_folder 必须消费 folder）。

pytest 运行；也可 python tests/test_from_tree_gate.py 直接执行。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_gate_tree"


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


@pytest.fixture()
def client():
    _cleanup()
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "门禁测试树",
        "sections": [{"id": "a", "title": "A", "kind": "text",
                      "data_needs": ["x"]}]},
        {"name": "T"}, actor="system")
    with TestClient(app) as c:
        yield c
    _cleanup()


def _put_plan(plan: dict) -> str:
    name = tree_store.save_plan(TID, plan)
    return name


def test_gate_rejects_empty_sources(client):
    r = client.post("/api/runs/from_tree", json={"tree_id": TID})
    assert r.status_code == 422
    assert "取数来源" in r.json()["detail"]


def test_gate_rejects_unconfirmed_plan(client):
    name = _put_plan({"mode": "llm", "rag": [{"need": "corpus_a", "query": "q"}]})
    r = client.post("/api/runs/from_tree", 
                    json={"tree_id": TID, "plan": name})
    assert r.status_code == 422
    assert "尚未确认" in r.json()["detail"]


def test_gate_rejects_unresolved_gaps(client):
    name = _put_plan({"mode": "llm", "confirmed": True,
                      "needs_coverage": [{"need": "n1", "status": "gap"}]})
    r = client.post("/api/runs/from_tree",  json={"tree_id": TID, "plan": name})
    assert r.status_code == 422
    assert "未裁决缺口" in r.json()["detail"]


def test_gate_needs_folder_requires_folder(client):
    name = _put_plan({"mode": "llm", "confirmed": True,
                      "needs_coverage": [{"need": "n1", "status": "search"}],
                      "needs_folder": True})
    r = client.post("/api/runs/from_tree", 
                    json={"tree_id": TID, "plan": name, "folder": None})
    assert r.status_code == 422
    assert "我提供资料" in r.json()["detail"] and "文件夹" in r.json()["detail"]


def test_gate_pass_confirmed_plan(monkeypatch, client):
    name = _put_plan({"mode": "llm", "confirmed": True,
                      "needs_coverage": [{"need": "n1", "status": "covered"}]})
    captured = {}

    def fake_start_run(argv, department, loop):
        captured["argv"] = argv
        class _T:
            id = "fake123"
            events_url = "/api/runs/fake123/events"
        return _T()

    from server import routes_runs
    monkeypatch.setattr(routes_runs.bus, "start_run", fake_start_run)
    r = client.post("/api/runs/from_tree", 
                    json={"tree_id": TID, "plan": name, "folder": "data"})
    assert r.status_code == 200, r.text
    assert "--tree" in captured["argv"] and "--plan" in captured["argv"]
    assert "--folder" in captured["argv"]


def test_plan_confirm_persists_confirmed(client):
    name = _put_plan({"mode": "llm",
                      "needs_coverage": [{"need": "n1", "status": "gap"}]})
    # 第一步：裁决为 qualitative → 全定性需二次确认（ok=False + need_confirm）
    r = client.post(f"/api/trees/{TID}/plan/confirm",
                    json={"plan_file": name,
                          "decisions": [{"need": "n1", "decision": "qualitative"}]})
    body = r.json()
    assert body["ok"] is False and body.get("need_confirm")
    # 第二步：全定性二次确认 → ok=True，计划文件落 confirmed: true
    r2 = client.post(f"/api/trees/{TID}/plan/confirm",
                     json={"plan_file": name, "decisions": [],
                           "all_qualitative": True})
    assert r2.json()["ok"] is True
    plan = tree_store.load_plan(TID, name)
    assert plan.get("confirmed") is True
    assert plan.get("confirmed_at")


def test_needs_folder_decision_recorded(client):
    name = _put_plan({"mode": "llm",
                      "needs_coverage": [{"need": "n1", "status": "gap"}]})
    r = client.post(f"/api/trees/{TID}/plan/confirm",
                    json={"plan_file": name,
                          "decisions": [{"need": "n1", "decision": "provide_folder"}]})
    assert r.status_code == 200 and r.json()["ok"] is True
    plan = tree_store.load_plan(TID, name)
    assert plan.get("needs_folder") is True       # A5：标记供 from_tree/CLI 消费
    assert plan.get("confirmed") is True


if __name__ == "__main__":
    import contextlib
    with contextlib.suppress(Exception):
        pass
    print("请用 pytest 运行本文件（依赖 fixture）")
