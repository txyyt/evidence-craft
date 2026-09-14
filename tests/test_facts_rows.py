"""M1 单测：facts_rows 表格渲染器（树场景：事实前缀 → 行列表格）。

pytest 运行；也可 python tests/test_facts_rows.py 直接执行。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.sections import render_table_sec  # noqa: E402
from template_factory.schema import Section, SpecV2, TableTemplate  # noqa: E402


def _doc() -> dict:
    return {"meta": {}, "collections": {},
            "facts": [
                {"id": "rag.00.00", "name": "2025年需求", "value": 38.31,
                 "unit": "万t", "source": "s", "as_of": "2025年"},
                {"id": "rag.01.00", "name": "2019年产量", "value": 123.62,
                 "unit": "万t", "source": "s", "as_of": "2019年"},
                {"id": "web.00.00", "name": "不匹配前缀", "value": 1.0,
                 "unit": "", "source": "s", "as_of": ""},
            ]}


def _spec(prefix="rag", columns=None) -> SpecV2:
    return SpecV2(
        report_type="mini", description="d",
        sections=[Section(id="tbl", title="关键数据", kind="table", table="facts_key")],
        tables=[TableTemplate(id="facts_key", renderer="facts_rows",
                              source_prefix=prefix, columns=columns or [])])


def test_facts_rows_renders_prefix_matches():
    md = render_table_sec(_doc(), _spec().sections[0], _spec())
    assert "| 指标 | 数值 | 截至 |" in md
    assert "38.31万t" in md and "123.62万t" in md
    assert "不匹配前缀" not in md          # 前缀过滤生效
    assert len(md.splitlines()) == 4       # 表头/分隔/2 行


def test_facts_rows_custom_columns():
    sp = _spec(columns=["指标", "数值"])
    md = render_table_sec(_doc(), sp.sections[0], sp)
    assert "| 指标 | 数值 |" in md
    assert "38.31万t | 2025年 |" not in md  # 截至列被裁掉（行尾无 as_of 单元格）


def test_facts_rows_empty_prefix_no_crash():
    sp = _spec(prefix="nothing")
    md = render_table_sec(_doc(), sp.sections[0], sp)
    assert md == "（表格数据缺失）"


if __name__ == "__main__":
    test_facts_rows_renders_prefix_matches()
    test_facts_rows_custom_columns()
    test_facts_rows_empty_prefix_no_crash()
    print("test_facts_rows 全部通过")
