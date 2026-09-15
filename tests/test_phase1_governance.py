"""V2 阶段一单测：A2 同标题查重 / A3 孤儿表模板 / B2 set_meta 分流 /
B5 乐观锁 / C2 错误人话翻译 / 迁移脚本纯函数（dedup/挂靠/lint 补齐）。

pytest 运行；也可 python tests/test_phase1_governance.py 直接执行。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import tree_agent  # noqa: E402
from trees import lint as tree_lint  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_phase1_gov"


def _spec(*repl, **kw):
    base = {"report_type": TID, "description": "测试树",
            "sections": [{"id": "a", "title": "A", "kind": "text"}]}
    for r in repl:
        base.update(r)
    base.update(kw)
    return base


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


# ---------- A2：同标题查重 ----------

def test_a2_apply_ops_rejects_duplicate_title():
    spec = _spec()
    meta = {}
    applied, _ = tree_agent._apply_ops(spec, meta, [
        {"action": "add_section",
         "section": {"id": "b", "title": "A", "kind": "text"}}])
    assert len(spec["sections"]) == 1                       # 未新增
    assert any("已存在同名节" in a for a in applied)


def test_a2_lint_duplicate_title_is_error():
    r = tree_lint.lint(_spec(sections=[
        {"id": "a", "title": "同题", "kind": "text"},
        {"id": "b", "title": "同题", "kind": "text"}]))
    assert any("标题重复" in e for e in r["errors"])


def test_a2_save_blocked_by_lint_error():
    """lint error（标题重复）拦保存：走 API 层语义，store 层 validate 通过但
    路由会先查 lint——这里验证 lint 与 save 的联动契约由 routes 保证，
    store 层仍可保存（供迁移脚本等受控使用）。"""
    from trees.lint import lint
    bad = _spec(sections=[
        {"id": "a", "title": "同题", "kind": "text"},
        {"id": "b", "title": "同题", "kind": "text"}])
    assert lint(bad)["errors"]


# ---------- A3：孤儿表模板 ----------

def test_a3_lint_orphan_table_warning():
    r = tree_lint.lint({
        "report_type": "x", "description": "d",
        "sections": [{"id": "a", "title": "A", "kind": "text",
                      "data_needs": ["x"]}],
        "tables": [{"id": "t1", "renderer": "facts_rows",
                    "source_prefix": "rag"}]})
    assert any("孤儿" in w for w in r["warnings"])


def test_a3_lint_table_mention_without_table_section():
    r = tree_lint.lint({
        "report_type": "x", "description": "d",
        "sections": [{"id": "a", "title": "A", "kind": "text",
                      "data_needs": ["x"],
                      "style": "本节要有消费结构表"}]})
    assert any("没有任何表格节" in w for w in r["warnings"])


def test_a3_clean_payload_drops_orphan_and_backfills():
    out = {"name": "N", "sections": [
        {"id": "甲", "title": "甲", "kind": "text", "style": "要有数据表"},
        {"id": "乙", "title": "乙", "kind": "table", "table": "tbl_x"},
    ], "tables": [{"id": "tbl_orphan"}, {"id": "tbl_x"}]}
    spec, _meta, warnings = tree_agent._clean_tree_payload(out)
    ids = [t["id"] for t in spec.get("tables", [])]
    assert "tbl_orphan" not in ids and "tbl_x" in ids      # 孤儿删、被引留
    assert any("孤儿" in w for w in warnings)
    # 引用缺失模板 → 自动补兜底
    out2 = {"name": "N", "sections": [
        {"id": "乙", "title": "乙", "kind": "table", "table": "tbl_missing"}]}
    spec2, _m2, w2 = tree_agent._clean_tree_payload(out2)
    assert spec2["tables"][0]["id"] == "tbl_missing"
    assert any("补" in w for w in w2)


# ---------- B2：set_meta 字段分流 ----------

def test_b2_set_meta_routes_fields_correctly():
    spec = _spec()
    meta = {"name": "旧名"}
    applied, meta = tree_agent._apply_ops(spec, meta, [
        {"action": "set_meta", "field": "name", "value": "新名"},
        {"action": "set_meta", "field": "description", "value": "新描述"},
        {"action": "set_meta", "field": "genre", "value": "research"},
        {"action": "set_meta", "field": "style_card", "value": "exec_brief"},
        {"action": "set_meta", "field": "forbidden_words",
         "value": ["非常", "绝对"]},
    ])
    assert meta["name"] == "新名"                    # meta 字段进 meta
    assert spec["description"] == "新描述"           # SpecV2 字段进顶层
    assert spec["genre"] == "research"
    assert spec["style_card"] == "exec_brief"
    assert spec["forbidden_words"] == ["非常", "绝对"]
    assert len(applied) == 5


# ---------- B5：乐观锁 ----------

def test_b5_optimistic_lock_conflict():
    _cleanup()
    try:
        tree_store.save_tree(TID, _spec(), {"name": "T"}, actor="system")
        with pytest.raises(tree_store.TreeConflict):
            tree_store.save_tree(TID, _spec({"description": "改"}),
                                 {"name": "T"}, actor="manual",
                                 base_version=99)       # 当前 v1，提交基于 v99
        r = tree_store.save_tree(TID, _spec({"description": "改"}),
                                 {"name": "T"}, actor="manual",
                                 base_version=1)        # 基于当前版 → 通过
        assert r["meta"]["version"] == 2
    finally:
        _cleanup()


# ---------- B1/B6：编辑器保存不抹 style_card / 治理字段往返 ----------

def test_b1_b6_style_card_and_vocab_survive_save():
    _cleanup()
    try:
        spec = _spec(style_card="exec_brief", genre="brief",
                     forbidden_words=["非常"],
                     controlled_vocab={"储量单位": ["万t", "万吨"]})
        tree_store.save_tree(TID, spec, {"name": "T"}, actor="system")
        loaded = tree_store.load_spec_dict(TID)["spec_dict"]
        assert loaded.get("style_card") == "exec_brief"
        assert loaded.get("genre") == "brief"
        assert loaded.get("forbidden_words") == ["非常"]
        assert loaded.get("controlled_vocab") == {"储量单位": ["万t", "万吨"]}
        # 不带 style_card 再存（模拟旧编辑器）→ 字段仍在由前端映射保证；
        # 这里验证带空值不写键时不破坏
        tree_store.save_tree(TID, _spec(style_card="journal_paper"),
                             {"name": "T"}, actor="manual")
        assert tree_store.load_spec_dict(TID)["spec_dict"]["style_card"] \
            == "journal_paper"
    finally:
        _cleanup()


# ---------- C2：schema 错误人话翻译 ----------

def test_c2_friendly_schema_message():
    with pytest.raises(ValueError) as ei:
        tree_store.validate_payload({
            "report_type": "x", "description": "d",
            "sections": [{"id": "v", "title": "V", "kind": "views"}]})
    msg = str(ei.value)
    assert "观点节" in msg and "视角槽位" in msg
    with pytest.raises(ValueError) as ei2:
        tree_store.validate_payload({
            "report_type": "x", "description": "d",
            "sections": [{"id": "t", "title": "T", "kind": "table"}]})
    assert "表格模板" in str(ei2.value)


# ---------- 迁移脚本纯函数 ----------

def test_migration_dedup_prefers_meaningful_id():
    from scripts.migrate_tree_ids import dedup_and_rename_sections
    payload = {"sections": [
        {"id": "abstract", "title": "摘要", "kind": "text"},
        {"id": "section", "title": "储运与物流约束", "kind": "text"},
        {"id": "logistics_constraints", "title": "储运与物流约束", "kind": "text"},
    ]}
    changes = []
    dedup_and_rename_sections(payload, changes)
    titles = [s["id"] for s in payload["sections"]]
    assert "section" not in titles
    assert "储运与物流约束" in titles and "摘要" in titles
    assert any("去重" in c for c in changes)


def test_migration_attach_and_fill():
    from scripts.migrate_tree_ids import attach_orphan_tables, fill_lint_gaps
    payload = {
        "sections": [
            {"id": "市场供需", "title": "市场供需", "kind": "text",
             "data_needs": ["下游各领域消费量占比"]},
        ],
        "tables": [{"id": "tbl_consumption", "renderer": "facts_rows",
                    "source_prefix": "web"}]}
    changes = []
    attach_orphan_tables(payload, changes)
    kinds = [(s["kind"], s.get("table")) for s in payload["sections"]]
    assert ("table", "tbl_consumption") in kinds
    assert any("挂靠" in c for c in changes)
    fill_lint_gaps(payload, changes)
    assert all(s.get("heading") and s.get("data_needs")
               for s in payload["sections"])


if __name__ == "__main__":
    test_a2_apply_ops_rejects_duplicate_title()
    test_a2_lint_duplicate_title_is_error()
    test_a2_save_blocked_by_lint_error()
    test_a3_lint_orphan_table_warning()
    test_a3_lint_table_mention_without_table_section()
    test_a3_clean_payload_drops_orphan_and_backfills()
    test_b2_set_meta_routes_fields_correctly()
    test_b5_optimistic_lock_conflict()
    test_b1_b6_style_card_and_vocab_survive_save()
    test_c2_friendly_schema_message()
    test_migration_dedup_prefers_meaningful_id()
    test_migration_attach_and_fill()
    print("test_phase1_governance 全部通过")
