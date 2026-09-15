"""V3 第一批（E1~E6、F1~F5）单测：全部 mock LLM，pytest 运行。

覆盖：E1 分类收敛+退回通道、E2 合并去重、E4 残句防线、F1 缺节补生成、
F2 图表失败清单、F3 按 target 聚合、F5 引用编号清洗。
（E3 见 test_phase2_loop.test_f4_thin_section_injection；
  E6 见 test_plan_preview.test_preview_fingerprint_includes_file_bindings）
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import judge, revise, sections  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_v3_b1"


def _spec(sections_list=None):
    return tree_store.validate_payload({
        "report_type": TID, "description": "V3 第一批测试树",
        "sections": sections_list or [
            {"id": "body", "title": "正文", "kind": "text",
             "check": {"body_len": [150, 1600]}}]})


def _doc():
    return {"meta": {}, "facts": [], "collections": {}}


# ---------- E1 分类器收敛（用 P6 实锤原文做用例） ----------

P6_TEXT = ("正文仅一句残句（26字），缺少暴涨与回落复盘、分档价格、"
           "驱动因素分析与折线图，未满足500~650字要求")


def test_e1_write_issue_not_structure():
    """P6 实锤原文含"缺少"二字——修复前误归 structure 遭冻结，修复后为 style。"""
    assert judge.classify_issue_kind(P6_TEXT) == "style"


def test_e1_structural_actions_are_structure():
    assert judge.classify_issue_kind("存在重复节「储运」") == "structure"
    assert judge.classify_issue_kind("第二节整节缺失，稿件中没有该节") == "structure"
    assert judge.classify_issue_kind("该节缺表，正文应附表") == "structure"
    assert judge.classify_issue_kind("章节顺序不对，背景在结论之后") == "structure"


def test_e1_data_reconcile_regression():
    """对账兜底合成 issue 仍归 data（回归）。"""
    assert judge.classify_issue_kind(
        "正文存在 3 个无法对账的数字（['5.2']），无事实出处") == "data"


def test_e1_unhandled_structure_routed_to_rewrite():
    """E1 退回通道：无确定性处理器的结构 issue（非重复节、无表模板可插）
    经 route_structure_issues 组合段转写作通道（kind→style），不再丢弃。"""
    from run_pipeline import route_structure_issues
    spec = _spec([{"id": "body", "title": "正文", "kind": "text"}])
    texts = [{"section_id": "body", "body": "原稿。", "cited_fact_ids": []}]
    issues = [{"target": "body", "kind": "structure",
               "problem": "节顺序不对", "instruction": "调整节顺序"}]
    to_rewrite, handled = route_structure_issues(
        _doc(), spec, texts, {}, issues)
    assert len(to_rewrite) == 1 and to_rewrite[0]["kind"] == "style"
    assert "已转写作通道修复" in to_rewrite[0]["instruction"]
    assert any("转写作通道" in h for h in handled)


# ---------- E2 judge 合成指令合并去重 ----------

def test_e2_merge_appends_to_same_target():
    """LLM 已占同 target 的坑时，系统合成指令追加进该条（前缀【系统校验】）
    而不是被去重吞掉——同 target 恰一条、指令完整。"""
    out = {"verdict": "pass", "issues": [
        {"target": "价格走势", "problem": "论述太浅", "instruction": "请深入分析"}]}
    judge._merge_synthetic_issue(out, "价格走势",
                                 "正文 26 字低于下限",
                                 "扩写至 150~1600 字区间内")
    assert out["verdict"] == "fail"
    assert len(out["issues"]) == 1                    # 恰一条
    ins = out["issues"][0]["instruction"]
    assert "请深入分析" in ins                        # LLM 原文保留
    assert "【系统校验】" in ins and "扩写至" in ins   # 系统指令追加
    # 幂等：重复合成不重复追加
    judge._merge_synthetic_issue(out, "价格走势",
                                 "正文 26 字低于下限",
                                 "扩写至 150~1600 字区间内")
    assert len(out["issues"]) == 1 and ins.count("【系统校验】") == 1


def test_e2_merge_new_target_when_no_conflict():
    """无冲突时行为与旧版一致：新建条目、verdict=fail。"""
    out = {"verdict": "pass", "issues": []}
    judge._merge_synthetic_issue(out, "风险", "p", "按格式要求重写风险提示。")
    assert out["verdict"] == "fail"
    assert len(out["issues"]) == 1
    assert out["issues"][0]["instruction"] == "按格式要求重写风险提示。"


# ---------- E4 残句防线 ----------

def test_e4_truncated_retry(monkeypatch):
    """第一次 26 字残句 → 立即重试（提示含"疑似未写完"）→ 第二次合格被采用。"""
    spec = _spec()
    sec = spec.sections[0]
    calls = []

    def fake_chat(system, user, **kw):
        calls.append(user)
        if len(calls) == 1:
            return {"body": "高纯石英砂价格在2022年经历了一轮完整的上涨。",
                    "cited_fact_ids": []}
        return {"body": "完整正文句子。" * 40, "cited_fact_ids": []}

    monkeypatch.setattr(sections, "chat_json", fake_chat)
    out = sections.gen_text_section(_doc(), sec, {}, spec)
    assert len(calls) == 2
    assert "疑似未写完" in calls[1]
    assert len(out["body"]) >= 75            # floor = max(60, 150*0.5)
    assert "thin" not in out


def test_e4_still_short_records_thin(monkeypatch):
    """两次都短 → 接受第二次产出并记 thin 警示（调用侧可观测）。"""
    spec = _spec()
    sec = spec.sections[0]
    calls = []

    def fake_chat(system, user, **kw):
        calls.append(user)
        return {"body": "短句。", "cited_fact_ids": []}

    monkeypatch.setattr(sections, "chat_json", fake_chat)
    out = sections.gen_text_section(_doc(), sec, {}, spec)
    assert len(calls) == 2
    assert "残句重试后仍短" in out["thin"]


def test_e4_normal_length_no_retry(monkeypatch):
    """正常长度正文零重试（chat_json 恰调用一次）。"""
    spec = _spec()
    sec = spec.sections[0]
    calls = []

    def fake_chat(system, user, **kw):
        calls.append(user)
        return {"body": "正常长度的正文段落。" * 10, "cited_fact_ids": []}

    monkeypatch.setattr(sections, "chat_json", fake_chat)
    sections.gen_text_section(_doc(), sec, {}, spec)
    assert len(calls) == 1


# ---------- F1 缺节确定性生成 ----------

def test_f1_missing_section_generated_inplace(monkeypatch):
    """树两节、稿件只有第一节，喂"第二节整节缺失"结构 issue → 一轮后
    texts 含第二节正文、树文件版本未变（只改运行内存）。"""
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "F1 测试",
        "sections": [{"id": "a", "title": "第一节", "kind": "text"},
                     {"id": "b", "title": "第二节", "kind": "text"}]},
        {"name": "F1树"}, actor="system")
    try:
        spec = tree_store.load_tree(TID)["spec"]
        assert tree_store.load_tree(TID)["meta"]["version"] == 1
        texts = [{"section_id": "a", "body": "第一节正文。", "cited_fact_ids": []}]

        def fake_gen(doc, sec, plan, spec_):
            return {"body": "补生成的第二节正文。", "section_id": sec.id,
                    "cited_fact_ids": []}

        monkeypatch.setattr(sections, "gen_text_section", fake_gen)
        handled, unhandled = revise.handle_structure_issues(
            _doc(), spec, texts, {},
            [{"target": "b", "kind": "structure",
              "problem": "第二节整节缺失", "instruction": "按树定义补写"}])
        assert [t["section_id"] for t in texts] == ["a", "b"]
        assert texts[1]["body"] == "补生成的第二节正文。"
        assert any("补生成" in h for h in handled) and not unhandled
        assert tree_store.load_tree(TID)["meta"]["version"] == 1   # 树未动
    finally:
        tree_store.delete_tree(TID)


# ---------- F2 图表渲染失败清单 ----------

def test_f2_render_charts_reports_failures(tmp_path):
    """数据缺失的图 → failures 条目（原因含"数据"），不阻塞其他图。"""
    from render.charts import render_charts
    spec = tree_store.validate_payload({
        "report_type": TID, "description": "F2 测试",
        "sections": [{"id": "figs", "title": "图件", "kind": "text",
                      "charts": [{"id": "c1", "title": "品位直方",
                                  "type": "hist", "source": "facts:nope",
                                  "y": ["数值"]}]}]})
    logs = []
    jobs, failures = render_charts(_doc(), spec, tmp_path, logs.append)
    assert jobs == {}
    assert len(failures) == 1
    assert failures[0]["id"] == "c1" and failures[0]["section"] == "figs"
    assert "数据" in failures[0]["reason"]


# ---------- F3 revise 按 target 聚合 ----------

def test_f3_merge_issues_by_target():
    merged = revise.merge_issues_by_target([
        {"target": "b", "problem": "压缩到 300 字", "instruction": "请压缩"},
        {"target": "b", "problem": "补充驱动因素", "instruction": "请补驱动"},
        {"target": "c", "problem": "太短", "instruction": "扩写"}])
    assert len(merged) == 2                       # 同 target 合并、异 target 独立
    mb = next(m for m in merged if m["target"] == "b")
    assert "压缩到 300 字" in mb["problem"] and "补充驱动因素" in mb["problem"]
    assert "请压缩" in mb["instruction"] and "请补驱动" in mb["instruction"]
    mc = next(m for m in merged if m["target"] == "c")
    assert mc["instruction"] == "扩写"


def test_f3_apply_same_target_single_rewrite(monkeypatch):
    """同 target 两条 issue → 模型恰被调一次，user 文本同时含两条指令。"""
    spec = _spec()
    calls = []

    def fake_revise_issue(spec_, fewshot, instruction, template, **kw):
        calls.append(template.format(**kw, **instruction))
        return {"body": "改写稿。", "cited_fact_ids": []}

    monkeypatch.setattr(revise, "_revise_issue", fake_revise_issue)
    texts = [{"section_id": "body", "body": "原稿。", "cited_fact_ids": []}]
    out = revise.apply(_doc(), {"title": "T"}, [], {}, {},
                       {"issues": [
                           {"target": "body", "problem": "压缩到 300 字",
                            "instruction": "请压缩"},
                           {"target": "body", "problem": "补充驱动因素",
                            "instruction": "请补充驱动因素分析"}]},
                       spec, texts=texts)
    assert len(calls) == 1
    assert "请压缩" in calls[0] and "请补充驱动因素分析" in calls[0]
    texts_out = out[4]                            # (written,f,risks,outline,texts,notes)
    assert texts_out[0]["body"] == "改写稿。"


def test_f3_apply_distinct_targets_two_calls(monkeypatch):
    """不同 target 互不影响：两个 target → 恰两次调用。"""
    spec = _spec()
    calls = []

    def fake_revise_issue(spec_, fewshot, instruction, template, **kw):
        calls.append(template.format(**kw, **instruction))
        return {"body": "改写稿。", "cited_fact_ids": []}

    monkeypatch.setattr(revise, "_revise_issue", fake_revise_issue)
    texts = [{"section_id": "b1", "body": "一。", "cited_fact_ids": []},
             {"section_id": "b2", "body": "二。", "cited_fact_ids": []}]
    spec2 = _spec([{"id": "b1", "title": "B1", "kind": "text"},
                   {"id": "b2", "title": "B2", "kind": "text"}])
    revise.apply(_doc(), {"title": "T"}, [], {}, {},
                 {"issues": [
                     {"target": "b1", "problem": "太短", "instruction": "扩写"},
                     {"target": "b2", "problem": "太干", "instruction": "充实"}]},
                 spec2, texts=texts)
    assert len(calls) == 2


# ---------- F5 引用编号清洗正则补全 ----------

def test_f5_strip_cites_all_forms():
    body = ("行业景气度上行（rag.02.00），价格走高(web.01.02)；"
            "库存回落［rag.03.00］。需求稳定 rag.02.01，供给收缩。")
    out = sections._strip_cites(body)
    for fid in ("rag.02.00", "web.01.02", "rag.03.00", "rag.02.01"):
        assert fid not in out
    assert "行业景气度上行，价格走高；" in out      # 全角括号剥除后句读完整
    assert "库存回落。" in out
    assert "需求稳定，供给收缩。" in out            # 裸编号与残留空格清理


def test_f5_clean_text_unchanged():
    s = "无编号正文，含逗号。数字 2022 年与 3.5 克，普通括号（说明）保留。"
    assert sections._strip_cites(s) == s


# ---------- F4 list_trees 附 lint 计数 ----------

def test_f4_list_trees_carries_lint():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)
    tree_store.save_tree(TID, {
        "report_type": TID, "description": "lint 测试",
        "sections": [{"id": "a", "title": "A", "kind": "text"}]},
        {"name": "lint树"}, actor="system")
    try:
        rows = tree_store.list_trees()
        row = next(r for r in rows if r["id"] == TID)
        assert set(row["lint"]) == {"errors", "warnings"}
        assert row["lint"]["errors"] == 0           # 合规树 0E
        assert row["lint"]["warnings"] >= 1         # 未声明 data_needs 至少 1W
    finally:
        tree_store.delete_tree(TID)
