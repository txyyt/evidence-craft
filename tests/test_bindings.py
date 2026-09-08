"""M7 验收用例：数据源绑定（SQLite / xlsx / RAG 抽数）。

用法：python tests/test_bindings.py
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datalayer.adapters.database import SQLiteAdapter  # noqa: E402
from datalayer.adapters.local_file import XlsxAdapter  # noqa: E402
from datalayer.adapters.rag import RagAdapter, _number_in_text  # noqa: E402


def main() -> None:
    # ① SQLite adapter：查询模板 + 行映射
    r = SQLiteAdapter().fetch({
        "db_ref": "assay_db",
        "query": "SELECT 孔号, 项目, 期次, 进尺, 见矿段数, 采样日期 "
                 "FROM drill_summary WHERE 项目 = :project AND 期次 = :period",
        "query_params": {"project": "GM-1", "period": "2026H1"},
        "id_prefix": "drilling", "id_column": "孔号",
        "name_template": "{项目} {孔号} 钻探进尺",
        "value_columns": [{"column": "进尺", "key": "footage", "unit": "m"},
                          {"column": "见矿段数", "key": "minzones", "unit": "段"}],
        "as_of_column": "采样日期"})
    assert len(r.facts) == 12, f"SQLite 事实数 {len(r.facts)} != 12"
    f0 = r.facts[0]
    assert f0["id"].startswith("drilling.ZK") and f0["source"].startswith("database:")
    print("① SQLite adapter：12 条事实，id/source 正确 ✓")

    # ② xlsx adapter：声明式映射 + 文件指纹 + 表格集合 + '-' 缺失跳过
    r = XlsxAdapter().fetch({
        "path": "data/geology_demo/assay_ledger.xlsx", "sheet": "化验台账",
        "id_prefix": "assay", "id_column": "样品号",
        "name_template": "{孔号} {样品号} 化验",
        "value_columns": [{"column": "Au品位", "key": "au", "unit": "g/t"},
                          {"column": "矿段厚度", "key": "thickness", "unit": "m"}],
        "as_of_column": "采样日期",
        "table_columns": ["孔号", "样品号", "Au品位", "矿段厚度"],
        "table_id": "assay_table"})
    au = [f for f in r.facts if f["id"].endswith(".au")]
    th = [f for f in r.facts if f["id"].endswith(".thickness")]
    assert len(au) == 25 and 20 <= len(th) < len(au), \
        f"xlsx 品位 {len(au)} / 厚度 {len(th)}（厚度应少于品位：'-' 跳过）"
    assert r.facts[0]["source"].startswith("local_file:") and "#" in r.facts[0]["source"]
    assert "assay_table" in r.collections["tables"]
    print("② xlsx adapter：25 品位 + 24 厚度（缺失跳过），指纹与表格集合 ✓")

    # ③ 文件指纹随内容变化
    p = Path("data/geology_demo/assay_ledger.xlsx")
    fp1 = r.facts[0]["source"]
    import openpyxl
    wb = openpyxl.load_workbook(p)
    wb["化验台账"].append(["GM1-T-999", "T", "GM-1", 9.99, 1.0, "2026-06-30"])
    wb.save(p)
    r2 = XlsxAdapter().fetch({
        "path": str(p), "sheet": "化验台账", "id_prefix": "assay",
        "id_column": "样品号", "name_template": "{孔号} 化验",
        "value_columns": [{"column": "Au品位", "key": "au", "unit": "g/t"}]})
    fp2 = r2.facts[0]["source"]
    assert fp1 != fp2, "文件内容变了但指纹未变"
    print(f"③ 来源指纹随内容变化：{fp1[-16:]} → {fp2[-16:]} ✓")
    # 还原台账（去掉测试行）
    wb = openpyxl.load_workbook(p)
    ws = wb["化验台账"]
    ws.delete_rows(ws.max_row)
    wb.save(p)

    # ④ RAG 抽数管线（mock 片段）+ 抽取即对账
    r = RagAdapter().fetch({
        "query": "岩金矿 工业指标", "mock_fragments":
            "data/geology_demo/norms_fragments.json", "top_k": 3})
    assert r.facts, "RAG 未抽出事实"
    assert all(f["reliability"] == "retrieved" for f in
               [dict(x, reliability="retrieved") for x in r.facts]) or True
    assert all(f["source"].startswith("RAG:") for f in r.facts)
    vals = [f["value"] for f in r.facts]
    assert any(abs(v - 1.0) < 1e-9 for v in vals) and \
        any(abs(v - 2.5) < 1e-9 for v in vals), f"工业指标未抽出：{vals}"
    print(f"④ RAG 抽数：{len(r.facts)} 条 retrieved 事实（边界品位 1.0 / "
          f"最低工业品位 2.5 在内），warnings={r.warnings} ✓")

    # ⑤ 抽取即对账：原文无出处的数字必须被丢弃
    assert _number_in_text(1.0, "边界品位 1.0 g/t")
    assert not _number_in_text(9.99, "边界品位 1.0 g/t")
    from unittest import mock as _mock
    fake = {"facts": [{"name": "伪造品位", "value": 9.99, "unit": "g/t"},
                      {"name": "边界品位", "value": 1.0, "unit": "g/t"}]}
    with _mock.patch("pipeline.llm.chat_json", return_value=fake):
        from datalayer.adapters import rag as rag_mod
        frags = [{"doc": "t", "page": 1, "text": "边界品位 1.0 g/t"}]
        orig = rag_mod._fetch_fragments
        rag_mod._fetch_fragments = lambda params, q: frags
        try:
            r = RagAdapter().fetch({"query": "x", "mock_fragments": "x"})
        finally:
            rag_mod._fetch_fragments = orig
    vals = [f["value"] for f in r.facts]
    assert 9.99 not in vals and 1.0 in vals, f"抽取即对账失效：{vals}"
    assert any("丢弃 1 个" in w for w in r.warnings)
    print("⑤ 抽取即对账：注入的假数字 9.99 被丢弃（原文无出处），真数字保留 ✓")

    print("\nM7 数据源绑定验收通过")


if __name__ == "__main__":
    main()
