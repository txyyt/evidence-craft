"""V4 批①单测：V4-01 产物目录名安全 + 详情错误出路；V4-08 回滚防重 +
轮次状态字段。LLM/治理全 mock（硬约束 8）。

pytest 运行；也可 python tests/test_v4_batch1.py 直接执行。
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from datalayer.settings import settings  # noqa: E402
from pipeline import feedback  # noqa: E402
from server import bus  # noqa: E402

TID = "_t_v4b1_tree"
RUN = "_t_v4b1_run"

SPEC = {
    "report_type": TID, "description": "V4批1测试树",
    "sections": [
        {"id": "intro", "title": "引言", "kind": "text", "style": "写引言",
         "data_needs": ["背景数据"]},
        {"id": "market", "title": "市场", "kind": "text", "style": "写市场",
         "data_needs": ["市场数据"]},
    ],
}

FACTS = [{"id": "rag.00.00", "name": "储量", "value": 100.0, "unit": "万t",
          "source": "s", "as_of": "2020年"}]


def _cleanup():
    from trees import store as tree_store
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)
    root = settings.resolve("artifacts") / RUN
    if root.exists():
        shutil.rmtree(root)


def _mk_run():
    """mini 树 + mini run_dir（同 test_feedback._mk_run 的最小形态）。"""
    _cleanup()
    from trees import store as tree_store
    tree_store.save_tree(TID, SPEC, {"name": "T", "subject": "S"}, actor="system")
    root = settings.resolve("artifacts") / RUN
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.json").write_text(json.dumps(
        {"tree_id": TID, "params": {}}), encoding="utf-8")
    (root / "facts.json").write_text(json.dumps(
        {"meta": {"params": {}}, "facts": FACTS, "collections": {}}),
        encoding="utf-8")
    (root / "outline.json").write_text(json.dumps(
        {"title": "测试标题", "section_plans": {}}), encoding="utf-8")
    (root / "sections.json").write_text(json.dumps({
        "views": [],
        "texts": [
            {"section_id": "intro", "body": "引言原稿第一段。", "cited_fact_ids": []},
            {"section_id": "market", "body": "市场原稿：储量100万t。",
             "cited_fact_ids": ["rag.00.00"]}],
        "notes": {}, "risks": {"body": "", "cited_fact_ids": []}},
        ensure_ascii=False), encoding="utf-8")
    return root


# ---------------- V4-01：产物目录名安全 ----------------

def test_safe_artifact_name_strips_forbidden():
    from run_pipeline import _safe_artifact_name
    # E07 复现主题：含 / 与 Windows 禁止字符
    out = _safe_artifact_name("中国高纯石英行业（砂/材料）:市场?")
    assert "/" not in out and "\\" not in out
    for ch in '<>:"|?*':
        assert ch not in out, (ch, out)
    assert out  # 非空
    # 控制字符与路径分隔符一并清洗；连续替换符合并
    out2 = _safe_artifact_name("a/b\\c:d*e?f<g>h|i")
    assert "/" not in out2 and "\\" not in out2
    for ch in '<>:"|?*':
        assert ch not in out2
    assert out2 == "a-b-c-d-e-f-g-h-i"
    # 空结果回退 report
    assert _safe_artifact_name("") == "report"
    assert _safe_artifact_name("///") == "report"
    assert _safe_artifact_name("  ..  ") == "report"
    # 超长主题：UTF-8 字节数受限，且不以半个字符截断（末尾仍是合法 UTF-8）
    long_sub = "石" * 500 + "英"
    out = _safe_artifact_name(long_sub)
    assert len(out.encode("utf-8")) <= 160
    out.encode("utf-8")  # 不抛即合法
    # 正常主题不受影响
    assert _safe_artifact_name("国内高纯石英产业研究") == "国内高纯石英产业研究"


def test_end_event_carries_artifact_dir_and_run_dir():
    """结束事件同时含 artifact_dir（basename）与旧 run_dir（兼容字段保留）。"""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        task = bus.RunTask("x1", [], "t", loop)
        task.run_dir = str(settings.resolve("artifacts") / "某主题_20260915_120000")
        task.status = "done"
        ev = task._end_event()
        assert ev["artifact_dir"] == "某主题_20260915_120000"
        assert ev["run_dir"].endswith("某主题_20260915_120000")
        # run_dir 为空时不炸
        task2 = bus.RunTask("x2", [], "t", loop)
        assert task2._end_event()["artifact_dir"] is None
    finally:
        loop.close()


def test_summarize_carries_artifact_dir():
    root = settings.resolve("artifacts") / "_t_v4b1_sum"
    if root.exists():
        shutil.rmtree(root)
    try:
        root.mkdir(parents=True)
        (root / "outline.json").write_text(json.dumps({"title": "T"}),
                                           encoding="utf-8")
        entry = bus.summarize(root)
        assert entry["artifact_dir"] == root.name
        assert entry["dir"] == root.name
    finally:
        shutil.rmtree(root)


def test_history_finds_sanitized_dir_and_api_404(client):
    """清洗名目录（直接子目录）→ /api/runs 可见；不存在的 run id → 404。"""
    root = settings.resolve("artifacts") / "清洗主题_20260915_120000"
    if root.exists():
        shutil.rmtree(root)
    try:
        root.mkdir(parents=True)
        (root / "meta.json").write_text(json.dumps(
            {"type_id": "t_demo", "tree_id": TID}), encoding="utf-8")
        rows = client.get("/api/runs").json()
        match = [r for r in rows if r["dir"] == "清洗主题_20260915_120000"]
        assert match, [r["dir"] for r in rows[:5]]
        assert match[0]["artifact_dir"] == "清洗主题_20260915_120000"
    finally:
        shutil.rmtree(root)
    r = client.get("/api/runs/no_such_run_xyz")
    assert r.status_code == 404


# ---------------- V4-08：回滚防重 + 轮次状态 ----------------

def test_rollback_twice_second_conflicts(monkeypatch):
    """同一轮连续回滚：第一次成功只追加一条；第二次 409（冲突），账本不变；
    回滚记录本身不可回滚。"""
    root = _mk_run()
    import pipeline.revise as revise_mod
    monkeypatch.setattr(revise_mod, "chat_json",
                        lambda *a, **k: {"body": "引言改写后的新稿。",
                                         "cited_fact_ids": []})
    ops = [{"target": "intro", "kind": "style", "action": "rewrite",
            "instruction": "更精炼"}]
    feedback.apply(root, ops, "引言更精炼")
    n1 = len(feedback.read_ledger(root))
    assert n1 == 1

    entry = feedback.rollback(root, 1)
    assert entry["rollback_of"] == 1
    n2 = len(feedback.read_ledger(root))
    assert n2 == n1 + 1                     # 第一次只新增一条

    with pytest.raises(feedback.RollbackConflict):
        feedback.rollback(root, 1)          # 第二次：已被回滚
    assert len(feedback.read_ledger(root)) == n2   # 账本行数不变

    # 回滚记录（round=2, rollback_of=1）本身不可回滚
    with pytest.raises(feedback.RollbackConflict):
        feedback.rollback(root, entry["round"])
    # 不存在的轮不可回滚
    with pytest.raises(feedback.RollbackConflict):
        feedback.rollback(root, 99)
    assert len(feedback.read_ledger(root)) == n2
    _cleanup()


def test_rollback_api_returns_409(monkeypatch):
    """端点层预检：重复回滚 → HTTP 409，且不产生新任务/账本行。"""
    root = _mk_run()
    import pipeline.revise as revise_mod
    monkeypatch.setattr(revise_mod, "chat_json",
                        lambda *a, **k: {"body": "改写。", "cited_fact_ids": []})
    feedback.apply(root, [{"target": "intro", "kind": "style",
                           "action": "rewrite", "instruction": "更精炼"}],
                   "引言更精炼")
    feedback.rollback(root, 1)
    n = len(feedback.read_ledger(root))
    with TestClient(__import__("server.main", fromlist=["app"]).app) as c:
        r = c.post(f"/api/runs/{RUN}/feedback/rollback", json={"round": 1})
        assert r.status_code == 409
        assert "已被回滚" in r.json()["detail"]
        r2 = c.post(f"/api/runs/{RUN}/feedback/rollback", json={"round": 77})
        assert r2.status_code == 409
    assert len(feedback.read_ledger(root)) == n
    _cleanup()


def test_rounds_fields_and_legacy_merge(monkeypatch):
    """rounds() 增量字段 kind/target_round/rolled_back；旧账本重复回滚
    确定性归并展示（不改写历史文件）。"""
    root = _mk_run()
    import pipeline.revise as revise_mod
    monkeypatch.setattr(revise_mod, "chat_json",
                        lambda *a, **k: {"body": "改写。", "cited_fact_ids": []})
    feedback.apply(root, [{"target": "intro", "kind": "style",
                           "action": "rewrite", "instruction": "更精炼"}],
                   "引言更精炼")
    rs = feedback.rounds(root)
    assert len(rs) == 1
    assert rs[0]["kind"] == "apply" and rs[0]["rolled_back"] is False
    assert rs[0]["target_round"] is None

    feedback.rollback(root, 1)
    rs = feedback.rounds(root)
    by_kind = {r["round"]: r for r in rs}
    apply_row = [r for r in rs if r["kind"] == "apply"][0]
    rb_row = [r for r in rs if r["kind"] == "rollback"][0]
    assert apply_row["round"] == 1
    assert apply_row["rolled_back"] is True          # 从 rollback 记录推导
    assert rb_row["target_round"] == 1

    # 模拟旧账本的重复回滚（历史脏数据）：再手写一条 rollback_of=1，
    # rounds() 照实展示两条 rollback，第 1 轮仍标已回滚，不改写文件
    with open(root / "feedback_ledger.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"round": 3, "ts": "x", "text": "[回滚] 撤销第 1 轮",
                            "ops": [], "applied": ["已回滚第 1 轮并重渲染"],
                            "rolled_back": True, "rollback_of": 1},
                           ensure_ascii=False) + "\n")
    raw = (root / "feedback_ledger.jsonl").read_text(encoding="utf-8")
    rs = feedback.rounds(root)
    assert len([r for r in rs if r["kind"] == "rollback"]) == 2
    assert [r for r in rs if r["round"] == 1][0]["rolled_back"] is True
    assert (root / "feedback_ledger.jsonl").read_text(encoding="utf-8") == raw
    _cleanup()


def test_rounds_empty_dir_ok():
    """无台账/无 rounds 目录的旧产物：rounds() 返回空列表不炸。"""
    root = _mk_run()
    try:
        assert feedback.rounds(root) == []
    finally:
        _cleanup()


@pytest.fixture()
def client():
    with TestClient(__import__("server.main", fromlist=["app"]).app) as c:
        yield c


class _FakeMp:
    """python 直跑时的 monkeypatch 替身（仅兜底用；CI 走 pytest）。"""

    def setattr(self, mod, name, val):
        setattr(mod, name, val)


if __name__ == "__main__":
    test_safe_artifact_name_strips_forbidden()
    test_end_event_carries_artifact_dir_and_run_dir()
    test_summarize_carries_artifact_dir()
    test_history_finds_sanitized_dir_and_api_404(TestClient(
        __import__("server.main", fromlist=["app"]).app))
    _mp = _FakeMp()
    test_rollback_twice_second_conflicts(_mp)
    test_rollback_api_returns_409(_mp)
    test_rounds_fields_and_legacy_merge(_mp)
    test_rounds_empty_dir_ok()
    print("test_v4_batch1 全部通过")
