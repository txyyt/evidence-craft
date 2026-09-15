"""V2 阶段二 F9 单测：树模式数据预检（只跑数据层 + 内容指纹缓存复用）。

pytest 运行。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_preview_tree"


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


@pytest.fixture()
def client():
    _cleanup()
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "预检测试树",
        "sections": [{"id": "a", "title": "A", "kind": "text",
                      "data_needs": ["x"]}]},
        {"name": "T"}, actor="system")
    with TestClient(app) as c:
        yield c
    _cleanup()


def test_preview_runs_and_caches(client, monkeypatch):
    name = tree_store.save_plan(TID, {
        "mode": "llm", "confirmed": True,
        "rag": [{"need": "corpus_a", "query": "萤石 产量", "top_k": 5}],
        "web": [], "db": []})
    calls = {"n": 0}

    def fake_run_data_layer(type_id, params, **kw):
        calls["n"] += 1
        assert kw.get("bindings_override"), "预检必须带计划绑定"
        doc = {"meta": {"warnings": []},
               "facts": [{"id": "rag.00.00", "name": "产量", "value": 1.0,
                          "unit": "万t", "source": "s", "as_of": "2024"}],
               "collections": {}}
        return doc, None

    from datalayer import registry
    monkeypatch.setattr(registry, "run_data_layer", fake_run_data_layer)
    r1 = client.post(f"/api/trees/{TID}/plan/preview",
                     json={"plan_file": name})
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["ok"] is True and body1["n_facts"] == 1
    assert body1["cached"] is False
    assert calls["n"] == 1
    # 同一计划再预检 → 指纹一致 → 复用缓存，不重跑数据层
    r2 = client.post(f"/api/trees/{TID}/plan/preview",
                     json={"plan_file": name})
    body2 = r2.json()
    assert body2["cached"] is True
    assert body2["fingerprint"] == body1["fingerprint"]
    assert calls["n"] == 1                    # 未重跑


def test_preview_missing_tree(client):
    r = client.post("/api/trees/不存在的树/plan/preview",
                    json={"plan_file": "x.json"})
    assert r.status_code == 404


def test_preview_fingerprint_includes_file_bindings(client, monkeypatch):
    """V3-E6：file_bindings 必须入预检指纹——换 Excel（同名不同 path）→
    指纹不同 → 真跑数据层（run_data_layer 被调第二次）；完全相同 → 缓存命中。"""
    plan = {"mode": "llm", "confirmed": True, "rag": [], "web": [], "db": [],
            "file_bindings": [{"need": "xlsx_a", "adapter": "xlsx_table",
                               "params": {"path": "old.xlsx"}}]}
    name = tree_store.save_plan(TID, plan)
    calls = {"n": 0}

    def fake_run(type_id, params, **kw):
        calls["n"] += 1
        return {"meta": {"warnings": []}, "facts": [], "collections": {}}, None

    from datalayer import registry
    monkeypatch.setattr(registry, "run_data_layer", fake_run)
    r1 = client.post(f"/api/trees/{TID}/plan/preview", json={"plan_file": name})
    assert r1.json()["cached"] is False and calls["n"] == 1
    # 完全相同的计划 → 缓存命中（只调一次）
    r2 = client.post(f"/api/trees/{TID}/plan/preview", json={"plan_file": name})
    assert r2.json()["cached"] is True
    assert r2.json()["fingerprint"] == r1.json()["fingerprint"]
    assert calls["n"] == 1
    # 换 Excel（同名不同 path）→ 指纹变 → 第二次预检真跑数据层
    tree_store.save_plan_file(TID, name, {
        "mode": "llm", "confirmed": True, "rag": [], "web": [], "db": [],
        "file_bindings": [{"need": "xlsx_a", "adapter": "xlsx_table",
                           "params": {"path": "new.xlsx"}}]})
    r3 = client.post(f"/api/trees/{TID}/plan/preview", json={"plan_file": name})
    assert r3.json()["cached"] is False
    assert r3.json()["fingerprint"] != r1.json()["fingerprint"]
    assert calls["n"] == 2


def test_copy_endpoint(client):
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "d",
        "sections": [{"id": "a", "title": "A", "kind": "text"}]},
        {"name": "T"}, actor="system")
    try:
        r = client.post(f"/api/trees/{TID}/copy")
        assert r.status_code == 200
        new_id = r.json()["id"]
        assert new_id.startswith(TID + "_派生")
        assert tree_store.exists(new_id)
        tree_store.delete_tree(new_id)
    finally:
        _cleanup()
