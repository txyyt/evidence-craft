"""V4 批④单测：V4-03 计划列表增量字段/计划快照树指纹；V4-05 模型档位
能力端点。LLM 全 mock（硬约束 8）。

pytest 运行；也可 python tests/test_v4_batch4.py 直接执行。
"""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from server.routes_trees import plan_fingerprint  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_v4b4_tree"


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _mk_tree():
    _cleanup()
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "批四测试树",
        "sections": [{"id": "a", "title": "A", "kind": "text",
                      "data_needs": ["x"]}]},
        {"name": "T", "subject": "S"}, actor="system")


def test_tree_plans_enriched_fields(client):
    """计划列表增量字段：confirmed/gap_count/qualitative_count/tree_version/
    tree_fingerprint/folder/preview_cached；旧计划缺字段 → null 不报错。"""
    _mk_tree()
    try:
        d = tree_store.tree_dir(TID) / "plans"
        d.mkdir(parents=True, exist_ok=True)
        # 新式计划（含全部字段）
        plan_full = {"mode": "plan", "rag": [], "web": [], "db": [],
                     "confirmed": True,
                     "tree_version": 3,
                     "tree_fingerprint": "abcdef0123456789",
                     "focus": "测试意图",
                     "folder": "D:/data",
                     "needs_coverage": [
                         {"need": "a", "status": "covered"},
                         {"need": "b", "status": "covered",
                          "decision": "qualitative"}]}
        (d / "20260916_010101.json").write_text(
            json.dumps(plan_full, ensure_ascii=False), encoding="utf-8")
        # 旧式计划（缺增量字段；取数字段不同以免指纹撞车）
        (d / "20260916_010001.json").write_text(
            json.dumps({"mode": "plan", "rag": [{"q": "旧查询"}],
                        "web": [], "db": []}),
            encoding="utf-8")
        # 预检缓存文件（针对 full 计划指纹）
        fp = plan_fingerprint(plan_full)
        (d / f"_preview_{fp}.json").write_text("{}", encoding="utf-8")

        rows = client.get(f"/api/trees/{TID}/plans").json()
        by_name = {r["name"]: r for r in rows}
        full = by_name["20260916_010101.json"]
        assert full["confirmed"] is True
        assert full["gap_count"] == 0
        assert full["qualitative_count"] == 1
        assert full["tree_version"] == 3
        assert full["tree_fingerprint"] == "abcdef0123456789"
        assert full["focus"] == "测试意图"
        assert full["folder"] == "D:/data"
        assert full["preview_cached"] is True
        old = by_name["20260916_010001.json"]
        assert old["confirmed"] is None
        assert old["tree_fingerprint"] is None
        assert old["preview_cached"] is False
    finally:
        _cleanup()


def test_plan_start_snapshots_tree_fingerprint(client, monkeypatch):
    """plan/start 生成计划时把当时树版本与指纹写入计划 JSON。"""
    _mk_tree()
    try:
        from datalayer import planner
        monkeypatch.setattr(planner, "make_plan",
                            lambda *a, **k: {"mode": "plan", "rag": [],
                                             "web": [], "db": [],
                                             "focus": "意图X"})
        with client:
            r = client.post(f"/api/trees/{TID}/plan/start", json={"intent": "X"})
            assert r.status_code == 200, r.text
            jid = r.json()["id"]
            status = None
            for _ in range(60):
                status = client.get(f"/api/jobs/{jid}").json()["status"]
                if status in ("done", "error"):
                    break
                time.sleep(0.1)
            assert status == "done", status
        loaded = tree_store.load_tree(TID)
        plans = client.get(f"/api/trees/{TID}/plans").json()
        assert plans, "计划未生成"
        plan = tree_store.load_plan(TID, plans[0]["name"])
        assert plan["tree_version"] == loaded["meta"]["version"]
        assert plan["tree_fingerprint"] == loaded["fingerprint"]
        assert plan["focus"] == "X"   # focus 记录出计划时的写作意图
    finally:
        _cleanup()


def test_pipeline_capability_model_tiers(client):
    """GET /api/settings/pipeline 增量 model_tiers：默认档恰一个；
    value 均为非空字符串（可被 TreeRunIn.model_tier 接受）；不泄露配置值。"""
    r = client.get("/api/settings/pipeline")
    assert r.status_code == 200
    body = r.json()
    tiers = body["model_tiers"]
    assert isinstance(tiers, list) and len(tiers) >= 1
    defaults = [t for t in tiers if t.get("is_default")]
    assert len(defaults) == 1
    assert defaults[0]["value"] == ""
    for t in tiers:
        assert isinstance(t["value"], str)
        assert t["label"]
        # 密钥/配置值绝不出现在档位描述里
        blob = json.dumps(t, ensure_ascii=False)
        assert "sk-" not in blob and "api_key" not in blob


if __name__ == "__main__":
    _c = TestClient(app)

    class _Mp:
        def setattr(self, m, n, v):
            setattr(m, n, v)

    test_tree_plans_enriched_fields(_c)
    test_plan_start_snapshots_tree_fingerprint(_c, _Mp())
    test_pipeline_capability_model_tiers(_c)
    print("test_v4_batch4 全部通过")
