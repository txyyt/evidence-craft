"""M3 单测：反馈回路——路由清洗 / 三类执行 / 脏区 / 回滚 / 深度评审（mock LLM）。

pytest 运行；也可 python tests/test_feedback.py 直接执行。
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import feedback  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_fb_tree"
RUN = "_t_fb_run"

SPEC = {
    "report_type": TID, "description": "反馈测试树",
    "sections": [
        {"id": "intro", "title": "引言", "kind": "text", "style": "写引言",
         "data_needs": ["背景数据"]},
        {"id": "market", "title": "市场", "kind": "text", "style": "写市场",
         "data_needs": ["市场数据"]},
    ],
}

FACTS = [{"id": "rag.00.00", "name": "储量", "value": 100.0, "unit": "万t",
          "source": "s", "as_of": "2020年"},
         {"id": "rag.01.00", "name": "产量", "value": 50.0, "unit": "万t",
          "source": "s", "as_of": "2020年"}]


def _cleanup():
    for tid in (TID,):
        if tree_store.exists(tid):
            tree_store.delete_tree(tid)
    run_dir = feedback.Path  # noqa: F841 —— 占位说明
    import sys as _sys
    root = _sys.modules["datalayer.settings"].settings.resolve(
        "artifacts") / RUN
    if root.exists():
        shutil.rmtree(root)


def _mk_run():
    """mini 树 + mini run_dir（两节 text，正文各含一句）。"""
    _cleanup()
    tree_store.save_tree(TID, SPEC, {"name": "T", "subject": "S"}, actor="system")
    root = sys.modules["datalayer.settings"].settings.resolve("artifacts") / RUN
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


def _mk_router_ops(ops):
    def fake(system, user, schema_hint, **kw):
        return {"ops": ops, "ambiguities": []}
    return fake


def test_parse_clamps_ops():
    root = _mk_run()
    feedback.chat_json = _mk_router_ops([
        {"target": "sec:intro", "kind": "style", "action": "rewrite",
         "instruction": "更精炼"},
        {"target": "sec:market", "kind": "data", "action": "refetch",
         "instruction": "补最新产量", "web_queries": ["产量 2024"]},
        {"target": "sec:market", "kind": "bogus", "action": "x", "instruction": "y"},
    ])
    try:
        r = feedback.parse(root, "引言更精炼，市场补最新产量")
        assert len(r["ops"]) == 2 and r["ops"][0]["kind"] == "style"
    finally:
        from pipeline.llm import chat_json as real
        feedback.chat_json = real  # parse 内部 from llm import —— 见下注


def test_style_round_and_rollback(monkeypatch):
    root = _mk_run()
    # mock revise 的 chat_json：返回改写后正文（monkeypatch 保证恢复，防污染后续测试）
    import pipeline.revise as revise_mod
    monkeypatch.setattr(revise_mod, "chat_json",
                        lambda *a, **k: {"body": "引言改写后的新稿。",
                                         "cited_fact_ids": []})
    ops = [{"target": "intro", "kind": "style", "action": "rewrite",
            "instruction": "更精炼"}]
    entry = feedback.apply(root, ops, "引言更精炼")
    assert entry["round"] == 1 and any("文风" in a for a in entry["applied"])
    sec = json.loads((root / "sections.json").read_text(encoding="utf-8"))
    assert sec["texts"][0]["body"] == "引言改写后的新稿。"
    assert sec["texts"][1]["body"] == "市场原稿：储量100万t。"   # 未点名节逐字不变
    diff = json.loads((root / "rounds" / "1" / "diff.json").read_text(encoding="utf-8"))
    assert diff["sections"]["intro"]["new"] == "引言改写后的新稿。"
    # 回滚第 1 轮 → 恢复原稿
    feedback.rollback(root, 1)
    sec = json.loads((root / "sections.json").read_text(encoding="utf-8"))
    assert sec["texts"][0]["body"] == "引言原稿第一段。"
    ledger = feedback.read_ledger(root)
    assert ledger[-1]["rolled_back"] is True


def test_structure_round_dirty_region(monkeypatch):
    root = _mk_run()
    import pipeline.sections as sections_mod
    monkeypatch.setattr(sections_mod, "gen_text_section",
                        lambda doc, sec, plan, spec: {
                            "body": f"{sec.title}新生成正文。",
                            "section_id": sec.id, "cited_fact_ids": []})
    ops = [{"target": "global", "kind": "structure", "action": "add_section",
            "instruction": "加一节政策",
            "section": {"id": "policy", "title": "政策", "kind": "text",
                        "style": "写政策", "data_needs": ["政策"]},
            "after": "intro"}]
    entry = feedback.apply(root, ops, "加一节政策")
    sec = json.loads((root / "sections.json").read_text(encoding="utf-8"))
    ids = [t["section_id"] for t in sec["texts"]]
    assert ids == ["intro", "policy", "market"], ids      # after intro 插入
    assert sec["texts"][2]["body"] == "市场原稿：储量100万t。"  # 脏区：market 未动
    spec = tree_store.load_tree(TID)["spec"]
    assert spec.section_by_id("policy") is not None        # 树同步更新
    assert tree_store.ops_log(TID)[-1]["actor"] == "agent"


def test_judge_deep_writes_report(monkeypatch):
    root = _mk_run()
    import pipeline.judge as judge_mod
    monkeypatch.setattr(judge_mod, "chat_json", lambda *a, **k: {
        "scores": {k: {"score": 8, "comment": "ok"} for k in
                   ("structure", "professionalism", "data_support",
                    "compliance", "readability")},
        "total": 40, "verdict": "pass", "issues": []})
    out = feedback.judge_deep(root)
    assert out["verdict"] == "pass"
    assert json.loads((root / "judge_report.json").read_text(
        encoding="utf-8"))["total"] == 40
    _cleanup()


def test_structure_chinese_id_title_fallback(monkeypatch):
    """路由给中文 section.id 时（_apply_ops 会改写 id），按标题兜底重生成正文。"""
    root = _mk_run()
    import pipeline.sections as sections_mod
    monkeypatch.setattr(sections_mod, "gen_text_section",
                        lambda doc, sec, plan, spec: {
                            "body": f"{sec.title}生成内容。",
                            "section_id": sec.id, "cited_fact_ids": []})
    ops = [{"target": "global", "kind": "structure", "action": "add_section",
            "instruction": "加一节",
            "section": {"id": "储运与物流约束", "title": "储运约束", "kind": "text",
                        "style": "写储运", "data_needs": ["物流"]},
            "after": "market"}]
    entry = feedback.apply(root, ops, "加一节储运")
    sec = json.loads((root / "sections.json").read_text(encoding="utf-8"))
    match = [t for t in sec["texts"] if t["section_id"] == "储运与物流约束"
             or t["body"].startswith("储运约束")]
    assert match, [t["section_id"] for t in sec["texts"]]
    diff = json.loads((root / "rounds" / "1" / "diff.json").read_text(encoding="utf-8"))
    assert diff["sections"], "新增节应出现在 diff 中"
    _cleanup()


if __name__ == "__main__":
    test_parse_clamps_ops()
    test_style_round_and_rollback()
    test_structure_round_dirty_region()
    test_structure_chinese_id_title_fallback()
    test_judge_deep_writes_report()
    print("test_feedback 全部通过")
