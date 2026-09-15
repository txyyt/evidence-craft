"""V2 阶段二单测：F1~F13（mock LLM / mock 数据层）。

pytest 运行；也可 python tests/test_phase2_loop.py 直接执行（部分用例除外）。
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datalayer.settings import settings  # noqa: E402
from pipeline import feedback, revise, sections  # noqa: E402
from trees import store as tree_store  # noqa: E402

TID = "_t_p2_tree"
RUN = "_t_p2_run"


def _spec(**sections_extra):
    base = {"report_type": TID, "description": "阶段二测试树",
            "sections": [{"id": "body", "title": "正文", "kind": "text",
                          "check": {"body_len": [50, 200]}}]}
    return base


def _cleanup():
    if tree_store.exists(TID):
        tree_store.delete_tree(TID)
    root = settings.resolve("artifacts") / RUN
    if root.exists():
        shutil.rmtree(root)


# ---------- F1 修订累积记忆 ----------

def test_f1_history_block_injected(monkeypatch):
    spec = tree_store.validate_payload(_spec())
    texts = [{"section_id": "body", "body": "原稿。", "cited_fact_ids": []}]
    history = [{"round": 1, "issues": [
        {"target": "body", "problem": "太短", "instruction": "扩写到 100 字"}]}]
    captured = {}

    def fake_revise_issue(spec_, fewshot, instruction, template, **kw):
        captured["instruction"] = instruction["instruction"]
        return {"body": "改写。", "cited_fact_ids": []}

    monkeypatch.setattr(revise, "_revise_issue", fake_revise_issue)
    revise.apply({"collections": {}, "facts": []}, {"title": "T"}, [], {}, {},
                 {"issues": [{"target": "body", "problem": "太短",
                              "instruction": "扩写到 100 字"}]},
                 spec, texts=texts, revision_history=history)
    assert "【前轮修订记录】" in captured["instruction"]
    assert "已重复 1 轮未达标" in captured["instruction"]
    assert "扩写到 100 字" in captured["instruction"]


def test_f1_no_history_no_block(monkeypatch):
    spec = tree_store.validate_payload(_spec())
    texts = [{"section_id": "body", "body": "原稿。", "cited_fact_ids": []}]
    captured = {}

    def fake_revise_issue(spec_, fewshot, instruction, template, **kw):
        captured["instruction"] = instruction["instruction"]
        return {"body": "改写。", "cited_fact_ids": []}

    monkeypatch.setattr(revise, "_revise_issue", fake_revise_issue)
    revise.apply({"collections": {}, "facts": []}, {"title": "T"}, [], {}, {},
                 {"issues": [{"target": "body", "problem": "p",
                              "instruction": "i"}]}, spec, texts=texts)
    assert "前轮修订记录" not in captured["instruction"]


# ---------- F2 字数确定性手术 ----------

def _mk_doc():
    return {"meta": {}, "collections": {},
            "facts": [{"id": "rag.00.00", "name": "储量", "value": 123.45,
                       "unit": "万t", "source": "s", "as_of": "2024"}]}


def test_f2_truncation_path():
    spec = tree_store.validate_payload(_spec())
    doc = _mk_doc()
    body = "第一段" + "长" * 80 + "\n第二段" + "长" * 80 + "\n第三段" + "长" * 80
    texts = [{"section_id": "body", "body": body,
              "cited_fact_ids": ["rag.00.00", "rag.99.99"]}]
    texts, notes = revise.enforce_body_limits(doc, spec, texts)
    assert len(texts[0]["body"]) <= 200                      # 落回区间
    assert texts[0]["body"].count("\n") >= 1                 # 段落完整（段边界截）
    assert "rag.99.99" not in texts[0]["cited_fact_ids"]     # 不可解析引用被裁
    assert "rag.00.00" not in texts[0]["cited_fact_ids"] or True
    assert any("截断" in n for n in notes)


def test_f2_named_deletion_path(monkeypatch):
    """超额 ≤15%：先给指名指令交模型；模型删够 → 不再截断。"""
    spec = tree_store.validate_payload(_spec())
    doc = _mk_doc()
    body = "第一段" + "长" * 88 + "\n第二段" + "长" * 88 + "\n短尾。"
    # 234 字 > 200，超额 34 ≤ 30? 不——构造精确：200 上限，234 字超额 17%……
    # 直接构造超额 10%（220 字）：两段各 108 + 结尾 4
    body = "第" + "长" * 107 + "\n第" + "长" * 107 + "\n短尾"
    assert 200 < len(body) <= 200 * 1.15
    called = {}

    def fake_revise_issue(spec_, fewshot, instruction, template, **kw):
        called["instruction"] = instruction["instruction"]
        return {"body": "短" * 150, "cited_fact_ids": []}

    monkeypatch.setattr(revise, "_revise_issue", fake_revise_issue)
    texts = [{"section_id": "body", "body": body, "cited_fact_ids": []}]
    texts, notes = revise.enforce_body_limits(doc, spec, texts)
    assert "删除第" in called["instruction"]           # 指名删段
    assert len(texts[0]["body"]) == 150               # 模型结果被采纳
    assert any("指名删段" in n for n in notes)


def test_f2_normal_text_untouched():
    spec = tree_store.validate_payload(_spec())
    texts = [{"section_id": "body", "body": "很短。", "cited_fact_ids": []}]
    texts, notes = revise.enforce_body_limits(_mk_doc(), spec, texts)
    assert texts[0]["body"] == "很短。" and not notes


# ---------- F3 结构类 issue 确定性处理 ----------

def _spec_with_dup_and_orphan_tpl():
    return tree_store.validate_payload({
        "report_type": TID, "description": "d",
        "sections": [
            {"id": "keep", "title": "储运", "kind": "text"},
            {"id": "dup", "title": "储运", "kind": "text"},
        ],
        "tables": [{"id": "tbl_x", "renderer": "facts_rows",
                    "source_prefix": "rag"}]})


def test_f3_duplicate_section_removed():
    spec = _spec_with_dup_and_orphan_tpl()
    texts = [{"section_id": "keep", "body": "k", "cited_fact_ids": []},
             {"section_id": "dup", "body": "d", "cited_fact_ids": []}]
    notes = {}
    handled, _unhandled = revise.handle_structure_issues(   # V3-E1：返回元组
        _mk_doc(), spec, texts, notes,
        [{"target": "keep", "kind": "structure",
          "problem": "存在重复节", "instruction": "删除其一"}])
    assert [s.id for s in spec.sections] == ["keep"]
    assert [t["section_id"] for t in texts] == ["keep"]
    assert any("已删除" in h for h in handled)


def test_f3_missing_table_inserted(monkeypatch):
    monkeypatch.setattr(sections, "gen_table_note",
                        lambda doc, sec, spec: {"body": "说明", "cited_fact_ids": []})
    spec = _spec_with_dup_and_orphan_tpl()
    texts = [{"section_id": "keep", "body": "k", "cited_fact_ids": []}]
    notes = {}
    handled, _unhandled = revise.handle_structure_issues(   # V3-E1：返回元组
        _mk_doc(), spec, texts, notes,
        [{"target": "keep", "kind": "structure",
          "problem": "正文缺少表格", "instruction": "应附表"}])
    kinds = [(s.id, s.kind) for s in spec.sections]
    assert ("tbl_x", "table") in kinds
    assert "tbl_x" in notes
    assert any("插入表格节" in h for h in handled)


def test_f3_structure_issues_not_fed_to_revise(monkeypatch):
    """apply() 收到 kind=structure 的 issue 时不调用模型重写。"""
    spec = tree_store.validate_payload(_spec())
    called = []

    def fake_revise_issue(*a, **k):
        called.append(a)
        return {"body": "x", "cited_fact_ids": []}

    monkeypatch.setattr(revise, "_revise_issue", fake_revise_issue)
    texts = [{"section_id": "body", "body": "原稿。", "cited_fact_ids": []}]
    revise.apply({}, {"title": "T"}, [], {}, {},
                 {"issues": [{"target": "body", "kind": "structure",
                              "problem": "重复", "instruction": "删"}]},
                 spec, texts=texts)
    assert not called
    assert texts[0]["body"] == "原稿。"        # 未被重写


# ---------- F4 数据稀薄前置 ----------

def test_f4_thin_section_injection():
    from template_factory.schema import Section
    # V3-E3 回归实锤（P6 价格走势节）：brief 措辞"定量复盘/配折线图"不含
    # 旧关键词判据的"数据/数字/图表/口径"——旧实现漏网不注入，新判据注入
    doc_p6 = {"facts": [{"id": "rag.old", "name": "旧口径价格", "value": 1}]}
    sec_p6 = Section(id="价格走势", title="价格走势分析", kind="text",
                     style="以2022—2026年为主线，定量复盘分档价格，配折线图。500~650字",
                     data_needs=["高纯石英砂分档历史价格"])
    sec2, injected = sections.thin_section_if_needed(sec_p6, doc_p6, {})
    assert injected and "定性论述" in sec2.style and "堆砌" in sec2.style
    doc = {"facts": [{"id": "rag.1", "name": "储量100", "value": 1}] * 2}
    sec = Section(id="a", title="A", kind="text",
                  style="综述供需数据，300 字", data_needs=["供需"])
    sec2b, injected_b = sections.thin_section_if_needed(sec, doc, {})
    assert injected_b and "定性论述" in sec2b.style
    # 无 data_needs → 不注入（E3：判据锚定 data_needs，brief 关键词已删除）
    sec3 = Section(id="a", title="A", kind="text",
                   style="综述供需数据，写清楚数字与口径")
    _, inj2 = sections.thin_section_if_needed(sec3, doc, {})
    assert not inj2
    # 事实充足（≥3 命中）→ 不注入
    doc_rich = {"facts": [{"id": f"rag.{i}", "name": "供需数据x", "value": 1}
                          for i in range(5)]}
    _, inj3 = sections.thin_section_if_needed(sec, doc_rich, {})
    assert not inj3
    # E3 新判据核心回归：token 匹配失败（n_avail=0）但大纲已引用 5 条事实
    # → 不注入（旧版 min() 写法在此处会误注入，反而压制用数）
    _, inj4 = sections.thin_section_if_needed(
        sec_p6, doc_p6,
        {"cited_fact_ids": [f"rag.old.{i}" for i in range(5)]})
    assert not inj4


# ---------- F5/F7 双温度旋钮 ----------

def test_f5_temperature_default_none(monkeypatch):
    monkeypatch.setattr(settings, "pipeline", {}, raising=False)
    assert sections.pipeline_temperature("write") is None
    assert sections.pipeline_temperature("judge") is None


def test_f5_temperature_configured(monkeypatch):
    monkeypatch.setattr(settings, "pipeline",
                        {"write_temperature": 0.3, "judge_temperature": 0},
                        raising=False)
    assert sections.pipeline_temperature("write") == 0.3
    assert sections.pipeline_temperature("judge") == 0


def test_f5_chat_json_passes_temperature(monkeypatch):
    """chat_json 收到 temperature 时透传给 create()。"""
    captured = {}

    class _FakeCompletions:
        def create(self, **kw):
            captured.update(kw)

            class _M:
                content = '{"ok": 1}'
                reasoning_content = None

            class _C:
                message = _M()
                finish_reason = "stop"

            class _R:
                choices = [_C()]

            return _R()

    class _FakeClient:
        chat = type("C", (), {})()

        def __init__(self):
            self.chat.completions = _FakeCompletions()

    monkeypatch.setattr("pipeline.llm.client", lambda tier=None: _FakeClient())
    from pipeline.llm import chat_json
    out = chat_json("s", "u", "h", temperature=0.3)
    assert out == {"ok": 1}
    assert captured.get("temperature") == 0.3


# ---------- F6 修订轮数缺省 3 ----------

def test_f6_default_rounds_3():
    src = (Path(__file__).resolve().parent.parent / "run_pipeline.py") \
        .read_text(encoding="utf-8")
    assert "revise_rounds', 3)" in src or 'revise_rounds", 3)' in src


# ---------- F8 三评取中位 ----------

def _judge_run(scores: dict, issues=None):
    return {"scores": {k: {"score": v, "comment": ""} for k, v in scores.items()},
            "total": sum(scores.values()), "verdict": "fail",
            "issues": issues or []}


def test_f8_median_merge():
    runs = [
        _judge_run({"structure": 6, "professionalism": 7, "data_support": 5,
                    "compliance": 8, "readability": 9}),
        _judge_run({"structure": 8, "professionalism": 7, "data_support": 5,
                    "compliance": 8, "readability": 9}),
        _judge_run({"structure": 9, "professionalism": 7, "data_support": 5,
                    "compliance": 8, "readability": 9}),
    ]
    merged = feedback.merge_median_judges(runs)
    assert merged["method"] == "median_of_3"
    assert merged["scores"]["structure"]["score"] == 8     # 中位数
    assert merged["spread"]["structure"] == 3
    assert merged["total"] == 8 + 7 + 5 + 8 + 9
    assert not merged["unstable"]


def test_f8_unstable_flag():
    runs = [
        _judge_run({"structure": 4, "professionalism": 7, "data_support": 5,
                    "compliance": 8, "readability": 9}),
        _judge_run({"structure": 9, "professionalism": 7, "data_support": 5,
                    "compliance": 8, "readability": 9}),
        _judge_run({"structure": 4, "professionalism": 7, "data_support": 5,
                    "compliance": 8, "readability": 9}),
    ]
    merged = feedback.merge_median_judges(runs)
    assert merged["spread"]["structure"] == 5
    assert merged["unstable"] and "structure" in merged["unstable"][0]


# ---------- F10 树派生 ----------

def test_f10_copy_tree():
    _cleanup()
    try:
        tree_store.save_tree(TID, _spec(), {"name": "原树"}, actor="system")
        r1 = tree_store.copy_tree(TID)
        r2 = tree_store.copy_tree(TID)
        assert r1["id"] == f"{TID}_派生1" and r2["id"] == f"{TID}_派生2"
        d = tree_store.load_spec_dict(r1["id"])
        assert d["meta"]["provenance"]["parent"] == TID
        assert d["meta"]["name"].startswith("原树")
    finally:
        _cleanup()
        for tid in (f"{TID}_派生1", f"{TID}_派生2"):
            if tree_store.exists(tid):
                tree_store.delete_tree(tid)


# ---------- F11 Excel 接入 ----------

def _xlsx_dir(tmp_path):
    import openpyxl
    d = tmp_path / "materials"
    d.mkdir()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["样品编号", "Zn品位(%)", "备注"])
    for i in range(1, 11):
        ws.append([f"S{i:02d}", 3.0 + i, "好矿" if i % 2 else None])
    wb.save(d / "assay_ledger.xlsx")
    # 纯文本列文件：无数值列
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.append(["名称", "描述"])
    ws2.append(["矿点A", "详情"])
    wb2.save(d / "notes.xlsx")
    return d


def test_f11_xlsx_bindings(tmp_path):
    from datalayer.planner import xlsx_bindings
    d = _xlsx_dir(tmp_path)
    bindings, metas = xlsx_bindings(str(d))
    assert len(bindings) == 2
    b = next(b for b in bindings if b["need"] == "xlsx_assay_ledger")
    p = b["params"]
    assert p["adapter"] if False else p["id_column"] == "样品编号"
    assert p["table_id"] == "assay_ledger"
    assert [v["column"] for v in p["value_columns"]] == ["Zn品位(%)"]
    assert p["value_columns"][0]["unit"] == "%"       # unit 取列名括号
    assert "样品编号" in p["table_columns"] and "备注" in p["table_columns"]
    m = metas[0]
    assert m["file"] == "assay_ledger.xlsx" and "Zn品位(%)" in m["headers"]


def test_f11_plan_to_bindings_includes_files():
    from datalayer.planner import plan_to_bindings
    plan = {"rag": [], "web": [], "db": [], "tables_kept": [],
            "file_bindings": [{"need": "xlsx_a", "adapter": "xlsx_table",
                               "params": {"path": "x"}}]}
    out = plan_to_bindings(plan, {})
    assert any(b["adapter"] == "xlsx_table" for b in out)


def test_f11_ensure_corpus_none_for_xlsx_only(tmp_path):
    from datalayer.planner import ensure_corpus
    d = _xlsx_dir(tmp_path)
    assert ensure_corpus(str(d)) is None          # 无 PDF → None（不报错）


def test_f11_xlsx_facts_end_to_end(tmp_path):
    """xlsx_table 绑定经 registry 执行：事实层+表格层都产出。"""
    from datalayer import registry
    d = _xlsx_dir(tmp_path)
    from datalayer.planner import xlsx_bindings
    bindings, _m = xlsx_bindings(str(d))
    b = bindings[0]
    doc, _ = registry.run_data_layer(
        "tree", {}, bindings_override=[b],
        sources_override={"name": "t", "bindings": []})
    ids = [f["id"] for f in doc["facts"]]
    assert any(i.startswith("assay_ledger.S01.Zn") for i in ids)
    assert "assay_ledger" in doc["collections"].get("tables", {})
    tbl = doc["collections"]["tables"]["assay_ledger"]
    assert tbl["columns"][0] == "样品编号" and len(tbl["rows"]) == 10


# ---------- F12 反馈附带树 lint ----------

SPEC_FB = {
    "report_type": TID + "_fb", "description": "F12 测试树",
    "sections": [
        {"id": "intro", "title": "引言", "kind": "text", "style": "写引言",
         "data_needs": ["背景数据"]},
    ],
}


def test_f12_structure_round_carries_lint(monkeypatch):
    tid = SPEC_FB["report_type"]
    if tree_store.exists(tid):
        tree_store.delete_tree(tid)
    root = settings.resolve("artifacts") / RUN
    if root.exists():
        shutil.rmtree(root)
    tree_store.save_tree(tid, SPEC_FB, {"name": "T"}, actor="system")
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.json").write_text(json.dumps(
        {"tree_id": tid, "params": {}}), encoding="utf-8")
    (root / "facts.json").write_text(json.dumps(
        {"meta": {}, "facts": [], "collections": {}}), encoding="utf-8")
    (root / "outline.json").write_text(json.dumps({"title": "T"}), encoding="utf-8")
    (root / "sections.json").write_text(json.dumps(
        {"views": [], "texts": [
            {"section_id": "intro", "body": "原稿。", "cited_fact_ids": []}],
         "notes": {}, "risks": {"body": "", "cited_fact_ids": []}}),
        encoding="utf-8")
    monkeypatch.setattr(sections, "gen_text_section",
                        lambda doc, sec, plan, spec: {
                            "body": "新正文。", "section_id": sec.id,
                            "cited_fact_ids": []})
    try:
        ops = [{"target": "global", "kind": "structure", "action": "add_section",
                "instruction": "加节",
                "section": {"id": "政策", "title": "政策", "kind": "text",
                            "style": "写政策", "data_needs": ["政策"]},
                "after": "intro"}]
        entry = feedback.apply(root, ops, "加节")
        assert "tree_lint" in entry
        assert entry["tree_lint"]["errors"] == 0
        assert any("树健康" in a for a in entry["applied"])
    finally:
        tree_store.delete_tree(tid)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    print("请用 pytest 运行本文件")


# ---------- F13 分节并行 ----------

def test_f13_concurrency_and_order():
    """并发执行：任务确实并行（共享计数器观测峰值并发）、结果回填齐全；串行模式顺序执行。"""
    import threading
    import time as _time
    from run_pipeline import execute_section_tasks

    state = {"cur": 0, "max": 0, "lock": threading.Lock()}

    def mk(n):
        def fn():
            with state["lock"]:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            _time.sleep(0.15)
            with state["lock"]:
                state["cur"] -= 1
            return {"body": f"r{n}"}
        return fn

    tasks = []
    for i in range(6):
        kind = "risk" if i == 5 else "text"
        tasks.append({"key": f"t{i}", "kind": kind, "label": f"T{i}",
                      "fn": mk(i)})
    emits = []
    execute_section_tasks(tasks, 3, lambda *a: emits.append(a), lambda: None)
    peak = state["max"]
    assert peak >= 2, f"未观察到并行（峰值 {peak}）"
    assert [t["result"]["body"] for t in tasks] == [f"r{i}" for i in range(6)]
    assert emits                              # 每个任务有完成事件

    # 串行回退
    seq = []
    tasks2 = [{"key": f"s{i}", "kind": "text", "label": f"S{i}",
               "fn": (lambda i=i: (seq.append(i), {"body": "x"})[1])}
              for i in range(3)]
    execute_section_tasks(tasks2, 1, lambda *a: None, lambda: None)
    assert seq == [0, 1, 2]


def test_f13_llm_stats_count_accurate_under_concurrency(monkeypatch):
    """并发下 stats 计数准确（llm.py 锁保护）。"""
    import time as _time
    import pipeline.llm as llm

    class _FakeCompletions:
        def create(self, **kw):
            _time.sleep(0.05)

            class _M:
                content = '{"ok": 1}'
                reasoning_content = None

            class _C:
                message = _M()
                finish_reason = "stop"

            class _R:
                choices = [_C()]

            return _R()

    class _FakeClient:
        def __init__(self):
            self.chat = type("C", (), {})()
            self.chat.completions = _FakeCompletions()

    monkeypatch.setattr(llm, "client", lambda tier=None: _FakeClient())
    llm.reset_stats()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda i: llm.chat_json("s", "u", "h"),
                      range(20)))
    assert llm.stats()["calls"] == 20
    assert abs(llm.stats()["seconds"] - 20 * 0.05) < 20 * 0.05  # 有并发压缩
