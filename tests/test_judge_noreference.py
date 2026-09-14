"""M1/M3 单测：judge 无范文分支（树运行 judge_reference=None 可跑，rubric 走树依据）。

mock chat_json，不依赖真实模型。pytest 运行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import judge  # noqa: E402
from template_factory.schema import Section, SpecV2  # noqa: E402


def _spec(with_reference: bool) -> SpecV2:
    return SpecV2(
        report_type="mini", description="d",
        judge_reference="config/reference/hp_quartz_reference.md" if with_reference else None,
        sections=[Section(id="intro", title="引言", kind="text",
                          style="写背景与意义，两段。")])


def _doc() -> dict:
    return {"meta": {}, "collections": {},
            "facts": [{"id": "rag.00.00", "name": "x", "value": 1.0,
                       "unit": "", "source": "s", "as_of": ""}]}


def _mock_llm(calls: list):
    def fake_chat_json(system, user, schema_hint, **kw):
        calls.append(system + user)
        return {"scores": {k: {"score": 8, "comment": "ok"} for k in
                           ("structure", "professionalism", "data_support",
                            "compliance", "readability")},
                "total": 40, "verdict": "pass", "issues": []}
    return fake_chat_json


def test_judge_runs_without_reference(monkeypatch=None):
    calls = []
    judge.chat_json = _mock_llm(calls)     # 覆盖模块内引用
    try:
        out = judge.run(_doc(), {"title": "T"}, [], {"body": ""},
                        {"body": "", "cited_fact_ids": []},
                        {"checks": [{"section": "intro", "unknown_numbers": []}],
                         "status": "pass", "index_size": 1},
                        {"items": [], "status": "pass"}, _spec(with_reference=False), rating="")
    finally:
        from pipeline.llm import chat_json as real
        judge.chat_json = real
    assert out["verdict"] == "pass" and out["total"] == 40
    prompt = calls[0]
    assert "无标杆范文" in prompt and "行文规则" in prompt      # 树依据 rubric
    assert "写作要求：写背景与意义" in prompt                   # brief 进入评审基准


def test_judge_with_reference_unchanged():
    """有范文时走原路径：提示词含【对标范文】，rubric 文案不出现。"""
    calls = []
    judge.chat_json = _mock_llm(calls)
    try:
        out = judge.run(_doc(), {"title": "T"}, [], {"body": ""},
                        {"body": "", "cited_fact_ids": []},
                        {"checks": [{"section": "intro", "unknown_numbers": []}],
                         "status": "pass", "index_size": 1},
                        {"items": [], "status": "pass"}, _spec(with_reference=True), rating="")
    finally:
        from pipeline.llm import chat_json as real
        judge.chat_json = real
    assert out["verdict"] == "pass"
    assert "【对标范文】" in calls[0] and "无标杆范文" not in calls[0]


if __name__ == "__main__":
    test_judge_runs_without_reference()
    test_judge_with_reference_unchanged()
    print("test_judge_noreference 全部通过")
