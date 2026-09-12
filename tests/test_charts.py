"""F4 验收用例：图表扩展（scatter / hist）——合成表格数据直接渲染 PNG。

用法：python tests/test_charts.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render.charts import make_chart  # noqa: E402
from template_factory.schema import ChartTemplate  # noqa: E402


def _doc() -> dict:
    """合成表格集合（rows_to_table 形状：行数组 + 字符串单元格）：
    品位-深度散点（数值 x）+ 一批品位值（直方分箱）。"""
    rows = []
    au = [0.8, 1.2, 1.5, 1.8, 2.1, 2.4, 2.6, 2.9, 3.3, 3.6,
          3.9, 4.2, 4.6, 5.1, 5.5, 6.0, 6.2, 4.9, 3.7, 2.8,
          2.2, 1.9, 1.4, 1.1, 0.9]
    for i, v in enumerate(au, 1):
        rows.append([f"ZK{1000 + i}", str(float(50 + i * 7)), str(v),
                     str(round(1.0 + (i % 5) * 0.8, 1))])
    return {"collections": {"tables": {
        "demo_table": {"columns": ["孔号", "深度", "Au品位", "矿段厚度"],
                       "rows": rows}}}}


def main() -> None:
    doc = _doc()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)

        hist = ChartTemplate(id="t_hist", title="Au 品位分布直方图",
                             type="hist", source="table:demo_table",
                             y=["Au品位"], unit="g/t", bins=8)
        p1 = make_chart(doc, hist, out / "hist.png")
        assert p1.is_file() and p1.stat().st_size > 10_000, "hist PNG 异常"
        print(f"① 直方图：{p1.name} 渲染成功（{p1.stat().st_size // 1024} KB，"
              f"25 个品位值 / bins=8）✓")

        scatter = ChartTemplate(id="t_scatter", title="品位-深度散点",
                                type="scatter", source="table:demo_table",
                                x="深度", y=["Au品位", "矿段厚度"], unit="g/t")
        p2 = make_chart(doc, scatter, out / "scatter.png")
        assert p2.is_file() and p2.stat().st_size > 10_000, "scatter PNG 异常"
        print(f"② 散点图：{p2.name} 渲染成功（{p2.stat().st_size // 1024} KB，"
              f"x=深度数值列，y 双序列）✓")

        # 边界：多列直方图拒绝；散点 x 全非数值拒绝
        try:
            make_chart(doc, ChartTemplate(id="bad1", title="x", type="hist",
                                          source="table:demo_table",
                                          y=["Au品位", "矿段厚度"]),
                       out / "bad1.png")
            raise AssertionError("多列 hist 未被拒绝")
        except ValueError:
            print("③ 边界：hist 多数值列被拒绝（只支持单列）✓")
        bad_doc = {"collections": {"tables": {"demo_table": {
            "columns": ["孔号", "Au品位"],
            "rows": [[f"ZK{i}", "1.0"] for i in range(5)]}}}}
        try:
            make_chart(bad_doc, ChartTemplate(id="bad2", title="x", type="scatter",
                                              source="table:demo_table", x="孔号",
                                              y=["Au品位"]),
                       out / "bad2.png")
            raise AssertionError("非数值 x 散点未被拒绝")
        except ValueError:
            print("④ 边界：scatter 的 x 列无数值被拒绝 ✓")

    print("\nF4 图表扩展验收通过")


if __name__ == "__main__":
    main()
