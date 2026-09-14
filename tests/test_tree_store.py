"""M1 单测：树存储（版本/指纹/操作日志/回滚）、lint、计划加载、schema 兼容。

pytest 运行；也可 python tests/test_tree_store.py 直接执行。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trees import lint as tree_lint  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_tree_store"


def _spec(extra_sections=None) -> dict:
    return {"report_type": TID, "description": "测试树",
            "sections": [dict({"id": "a", "title": "A", "kind": "text"},
                              **(extra_sections or {}))]}


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


def test_store_lifecycle():
    _cleanup()
    try:
        r1 = tree_store.save_tree(TID, _spec(), {"name": "测试树", "subject": "主体"},
                                  actor="system", summary="创建")
        assert r1["meta"]["version"] == 1
        fp1 = r1["fingerprint"]

        sd = _spec({"data_needs": ["需求数据"]})
        r2 = tree_store.save_tree(TID, sd, None, actor="manual", summary="手动改")
        assert r2["meta"]["version"] == 2 and r2["fingerprint"] != fp1

        r3 = tree_store.save_tree(TID, _spec({"data_needs": ["另一数据"]}), None,
                                  actor="agent", summary="对话改")
        assert r3["meta"]["version"] == 3

        # D5：对话改与手动改写同一份日志
        ops = tree_store.ops_log(TID)
        actors = [o["actor"] for o in ops]
        assert actors == ["system", "manual", "agent"], actors

        # 版本链
        vs = tree_store.versions(TID)
        assert [v["version"] for v in vs] == [1, 2, 3]

        # 回滚到 v1：内容指纹精确恢复，版本继续前进
        rb = tree_store.rollback(TID, 1)
        assert rb["meta"]["version"] == 4 and rb["fingerprint"] == fp1
        loaded = tree_store.load_tree(TID)
        assert loaded["spec"].sections[0].data_needs == []

        # 回滚也进日志
        assert tree_store.ops_log(TID)[-1]["actor"] == "rollback"
    finally:
        _cleanup()


def test_store_validation_rejects_bad_spec():
    with pytest.raises(ValueError):
        tree_store.save_tree("_t_bad", {"report_type": "x", "sections": [
            {"id": "v", "title": "V", "kind": "views"}]},   # views 缺槽位
            actor="manual")


def test_load_plan_suffix():
    _cleanup()
    try:
        tree_store.save_tree(TID, _spec(), {"name": "T"}, actor="system")
        import json
        plans = tree_store.tree_dir(TID) / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "p1.json").write_text('{"rag": []}', encoding="utf-8")
        assert tree_store.load_plan(TID, "p1") == {"rag": []}      # 省后缀
        assert tree_store.load_plan(TID, "p1.json") == {"rag": []}
        with pytest.raises(ValueError):
            tree_store.load_plan(TID, "nope")
    finally:
        _cleanup()


def test_lint():
    # 错误：views 节缺槽位（schema 级）
    r = tree_lint.lint({"report_type": "x", "description": "d",
                        "sections": [{"id": "v", "title": "V", "kind": "views"}]})
    assert r["errors"]
    # 警告：缺 data_needs / 图引用缺表 / facts_rows 缺前缀
    r = tree_lint.lint({
        "report_type": "x", "description": "d",
        "sections": [
            {"id": "a", "title": "A", "kind": "text",
             "charts": [{"id": "c1", "title": "C", "type": "bar",
                         "source": "table:missing_tbl"}]},
            {"id": "tbl", "title": "表", "kind": "table", "table": "t1"},
        ],
        "tables": [{"id": "t1", "renderer": "facts_rows"}]})
    warnings = " ".join(r["warnings"])
    assert "未声明数据需求" in warnings
    assert "missing_tbl" in warnings
    assert "source_prefix" in warnings
    assert not r["errors"]


def test_schema_backcompat():
    """旧 report.yaml 加载不受新字段影响；新字段缺省值正确。"""
    from template_factory.schema import load_spec
    spec = load_spec("config/report_types/hp_quartz_review/report.yaml")
    assert spec.style_card is None and spec.genre is None
    assert all(s.data_needs == [] for s in spec.sections)
    assert all(s.origin == "agent" for s in spec.sections)


if __name__ == "__main__":
    test_store_lifecycle()
    test_store_validation_rejects_bad_spec()
    test_load_plan_suffix()
    test_lint()
    test_schema_backcompat()
    print("test_tree_store 全部通过")
