"""生成 geology_demo 合成部门的数据（SQLite 钻探库 + xlsx 化验台账 + RAG mock 片段）。

运行：python data/geology_demo/build_demo_data.py
全部为虚构数据，仅供多部门机制验证。
"""

import json
import random
import sqlite3
from pathlib import Path

import openpyxl

HERE = Path(__file__).parent
random.seed(42)  # 固定随机种子：可复现

HOLES = [
    ("ZK1801", 385.2, 5), ("ZK1802", 412.6, 3), ("ZK1803", 356.4, 6),
    ("ZK1804", 468.9, 4), ("ZK1805", 301.7, 2), ("ZK1806", 429.3, 5),
]


def build_db() -> None:
    con = sqlite3.connect(HERE / "assay.db")
    con.execute("DROP TABLE IF EXISTS drill_summary")
    con.execute("""CREATE TABLE drill_summary (
        孔号 TEXT PRIMARY KEY, 项目 TEXT, 期次 TEXT,
        进尺 REAL, 见矿段数 INTEGER, 采样日期 TEXT)""")
    for hole, footage, zones in HOLES:
        con.execute("INSERT INTO drill_summary VALUES (?,?,?,?,?,?)",
                    (hole, "GM-1", "2026H1", footage, zones,
                     f"2026-0{random.randint(3,6)}-{random.randint(10,28)}"))
    con.commit()
    con.close()
    print("assay.db 写入", len(HOLES), "条钻探汇总")


def build_xlsx() -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "化验台账"
    ws.append(["样品号", "孔号", "项目", "Au品位", "矿段厚度", "采样日期"])
    n = 0
    for hole, _, zones in HOLES:
        for z in range(zones):
            n += 1
            grade = round(random.uniform(0.8, 6.5), 2)
            thickness = round(random.uniform(0.8, 6.0), 1)
            ws.append([f"GM1-{hole}-{z+1:03d}", hole, "GM-1", grade,
                       thickness if random.random() > 0.1 else "-",
                       f"2026-0{random.randint(4,6)}-{random.randint(1,28)}"])
    wb.save(HERE / "assay_ledger.xlsx")
    print("assay_ledger.xlsx 写入", n, "条样品（含 1 条厚度缺失，验证 '-' 跳过）")


def build_rag_mock() -> None:
    fragments = [
        {"doc": "《岩金矿地质勘查规范》选编", "page": 12,
         "text": "岩金矿床工业指标一般要求：边界品位 1.0 g/t，最低工业品位 2.5 g/t，"
                 "矿段最小可采厚度 1.0 m，夹石剔除厚度 2.0 m。当矿床品位分布不均匀时，"
                 "应结合矿体特征论证专用指标。"},
        {"doc": "《岩金矿地质勘查规范》选编", "page": 31,
         "text": "资源量估算采用地质块段法时，各块段矿体厚度应不小于最小可采厚度 1.0 m；"
                 "推断资源量（334）对应工程控制程度为稀疏控制，可信度较低，"
                 "不得直接用于可行性研究。"},
        {"doc": "矿区勘查设计评审意见（内部）", "page": 2,
         "text": "评审认为 GM-1 矿区圈连矿体时厚度较小，建议本期补充样品内检，"
                 "内检比例不低于 10%，并重点核查品位 5.0 g/t 以上特高样品的代表性。"},
    ]
    (HERE / "norms_fragments.json").write_text(
        json.dumps(fragments, ensure_ascii=False, indent=1), encoding="utf-8")
    print("norms_fragments.json 写入", len(fragments), "个 mock 片段")


if __name__ == "__main__":
    build_db()
    build_xlsx()
    build_rag_mock()
