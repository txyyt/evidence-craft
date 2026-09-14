"""M2 单测：树对话 agent（mock LLM）——生成/反问/编辑 ops 应用（D5 验证）。

pytest 运行；也可 python tests/test_tree_agent.py 直接执行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import tree_agent  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_tree_agent"

GOOD_TREE = {
    "kind": "tree", "name": "测试对话树", "subject": "测试主体",
    "description": "对话生成测试树", "genre": "brief",
    "writer_role": "研究员", "title_style": "短语式标题",
    "writing_rules": ["结论先行"], "style_card": "exec_brief",
    "sections": [
        {"id": "background", "title": "背景", "kind": "text",
         "heading": "1　背景", "style": "写背景 200 字",
         "data_needs": ["行业背景"], "origin": "user"},
        {"id": "conclusion", "title": "结论", "kind": "text",
         "style": "给结论 150 字", "data_needs": ["结论支撑"], "origin": "agent"},
    ],
    "tables": [{"id": "t1", "renderer": "facts_rows", "source_prefix": "rag"}],
}
QUESTIONS = {"kind": "questions", "questions": ["报告主题是什么？", "给谁看？"]}
# 编辑测试用的干净 spec（与 generate 产物同形态）
CLEAN_SPEC, CLEAN_META = tree_agent._clean_tree_payload(GOOD_TREE)


def _mock_chat(fixed):
    def fake(system, user, schema_hint, **kw):
        return fixed
    return fake


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)


def test_generate_tree_and_questions(monkeypatch=None):
    tree_agent.chat_json = _mock_chat(GOOD_TREE)
    r = tree_agent.generate([{"role": "user", "text": "我要一份测试报告"}])
    assert r["kind"] == "tree"
    spec = tree_store.validate_payload(r["spec_dict"])   # 必须过 SpecV2
    assert len(spec.sections) == 2
    assert spec.style_card == "exec_brief"
    assert r["meta"]["name"] == "测试对话树"

    tree_agent.chat_json = _mock_chat(QUESTIONS)
    r2 = tree_agent.generate([{"role": "user", "text": "帮我写个报告"}])
    assert r2["kind"] == "questions" and len(r2["questions"]) == 2

    from pipeline.llm import chat_json as real
    tree_agent.chat_json = real


def test_generate_rejects_empty_sections():
    tree_agent.chat_json = _mock_chat({"kind": "tree", "name": "空树",
                                       "sections": []})
    try:
        tree_agent.generate([{"role": "user", "text": "x"}])
        raise AssertionError("空章节树未被拒绝")
    except tree_agent.TreeAgentError:
        pass
    finally:
        from pipeline.llm import chat_json as real
        tree_agent.chat_json = real


def test_edit_reads_current_tree_and_applies():
    _cleanup()
    try:
        tree_store.save_tree(TID, CLEAN_SPEC, {"name": "测试对话树",
                                               "subject": "测试主体"},
                             actor="system", summary="创建")
        ops = [
            {"action": "add_section",
             "section": {"id": "policy", "title": "政策进展", "kind": "text",
                         "style": "写政策 200 字", "data_needs": ["政策动态"]},
             "after": "background"},
            {"action": "update_section", "section_id": "conclusion",
             "fields": {"style": "结论改写 300 字", "data_needs": ["新支撑"]}},
            {"action": "add_chart", "section_id": "conclusion",
             "chart": {"id": "c1", "title": "支撑数据", "type": "bar",
                       "source": "facts:rag"}},
            {"action": "move_section", "section_id": "policy", "direction": "down"},
            {"action": "remove_chart", "section_id": "conclusion", "chart_id": "c1"},
        ]
        tree_agent.chat_json = _mock_chat({"summary": "加了政策节", "ops": ops})
        r = tree_agent.edit(TID, "加一节讲政策，结论节加个图")
        # 加(policy after background) → move down：政策在最末；图加后又被删除
        spec = tree_store.load_tree(TID)["spec"]
        assert [s.id for s in spec.sections] == ["background", "conclusion", "policy"]
        assert spec.sections[1].style == "结论改写 300 字"
        assert spec.sections[1].data_needs == ["新支撑"]
        assert spec.sections[1].charts == []
        assert spec.sections[2].origin == "user"      # 用户意见加的节
        assert r["changed"] and r["version"] == 2
        # D5：actor=agent 写同一份日志
        assert tree_store.ops_log(TID)[-1]["actor"] == "agent"
    finally:
        _cleanup()
        from pipeline.llm import chat_json as real
        tree_agent.chat_json = real


def test_edit_noop_returns_message():
    _cleanup()
    try:
        tree_store.save_tree(TID, CLEAN_SPEC, {"name": "T"}, actor="system")
        tree_agent.chat_json = _mock_chat({"summary": "没听懂", "ops": []})
        r = tree_agent.edit(TID, "随便改改")
        assert r["changed"] is False and tree_store.load_tree(TID)["meta"]["version"] == 1
    finally:
        _cleanup()
        from pipeline.llm import chat_json as real
        tree_agent.chat_json = real


if __name__ == "__main__":
    test_generate_tree_and_questions()
    test_generate_rejects_empty_sections()
    test_edit_reads_current_tree_and_applies()
    test_edit_noop_returns_message()
    print("test_tree_agent 全部通过")
