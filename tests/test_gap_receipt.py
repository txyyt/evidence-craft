"""M2 单测：缺口回执三态 + 四选一裁决各路径 + 确认门槛（mock LLM）。

pytest 运行；也可 python tests/test_gap_receipt.py 直接执行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datalayer.planner import _clamp_coverage  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_gap_tree"

SPEC = {
    "report_type": TID, "description": "缺口裁决测试树",
    "sections": [
        {"id": "market", "title": "市场", "kind": "text",
         "style": "写市场供需", "data_needs": ["消费结构占比", "价格走势"]},
        {"id": "policy", "title": "政策", "kind": "text",
         "style": "写政策", "data_needs": ["矿业政策"]},
    ],
}


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


def _mk_plan(coverage):
    return {"mode": "llm", "subject": "s", "focus": "f",
            "rag": [{"need": "corpus_a", "query": "q", "top_k": 5}],
            "web": [], "db": [], "tables_kept": [],
            "needs_coverage": coverage}


def test_clamp_coverage():
    raw = [{"need": "消费结构占比", "status": "covered"},
           {"need": "价格走势", "status": "bogus"},
           {"need": "多余项", "status": "covered"}]
    out = _clamp_coverage(raw, ["消费结构占比", "价格走势"])
    assert out[0] == {"need": "消费结构占比", "status": "covered"}
    assert out[1]["status"] == "gap"                       # 非法 status → gap
    assert len(out) == 2                                   # 清单外的多余项被剔除


def test_decisions_all_paths():
    _cleanup()
    try:
        tree_store.save_tree(TID, SPEC, {"name": "T", "subject": "S"},
                             actor="system")
        plan_name = tree_store.save_plan(TID, _mk_plan([
            {"need": "消费结构占比", "status": "covered"},
            {"need": "价格走势", "status": "gap"},
            {"need": "矿业政策", "status": "gap"}]))
        r = tree_store.apply_decisions(TID, plan_name, [
            {"need": "价格走势", "decision": "search"},
            {"need": "矿业政策", "decision": "qualitative"}])
        cov = {c["need"]: c["status"] for c in r["coverage"]}
        assert cov == {"消费结构占比": "covered", "价格走势": "search",
                       "矿业政策": "qualitative"}
        # search 落效：web 查询追加
        plan = tree_store.load_plan(TID, plan_name)
        assert any(q["need"] == "web_价格走势" for q in plan["web"])
        # qualitative 落效：政策节摘需求 + brief 追加定性声明（树版本 +1）
        spec = tree_store.load_tree(TID)["spec"]
        assert spec.sections[1].data_needs == []
        assert "定性" in spec.sections[1].style
        assert spec.sections[0].data_needs == ["消费结构占比", "价格走势"]  # search 不动树
        assert tree_store.ops_log(TID)[-1]["actor"] == "system"

        # drop 路径
        tree_store.apply_decisions(TID, plan_name,
                                   [{"need": "消费结构占比", "decision": "drop"}])
        spec = tree_store.load_tree(TID)["spec"]
        assert spec.sections[0].data_needs == ["价格走势"]
        # provide_folder 路径
        tree_store.apply_decisions(TID, plan_name,
                                   [{"need": "消费结构占比", "decision": "provide_folder"}])
        plan = tree_store.load_plan(TID, plan_name)
        assert plan.get("needs_folder") is True
    finally:
        _cleanup()


def test_gate():
    gate = tree_store.coverage_gate(_mk_plan([
        {"need": "a", "status": "covered"}, {"need": "b", "status": "gap"}]))
    assert not gate["ok"] and gate["unresolved"] == ["b"]
    gate2 = tree_store.coverage_gate(_mk_plan([
        {"need": "a", "status": "covered"}, {"need": "b", "status": "dropped"}]))
    assert gate2["ok"] and not gate2["all_qualitative"]
    gate3 = tree_store.coverage_gate(_mk_plan([
        {"need": "a", "status": "qualitative"}, {"need": "b", "status": "dropped"}]))
    assert gate3["ok"] and gate3["all_qualitative"] and gate3["n_usable"] == 0


if __name__ == "__main__":
    test_clamp_coverage()
    test_decisions_all_paths()
    test_gate()
    print("test_gap_receipt 全部通过")
