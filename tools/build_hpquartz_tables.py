"""从片段库提取结构化表格 → data/hp_quartz/tables.xlsx（表格/图件数据源）。

流程：定向检索片段 → LLM 按列schema提表 → 逐格数字回验片段原文（抽取即对账，
对不上的单元格整行丢弃）→ xlsx（附来源列）。构建报告落 tables_build_report.json。

用法：python -m tools.build_hpquartz_tables
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datalayer.adapters.rag import _number_in_text, _retrieve  # noqa: E402
from pipeline.llm import chat_json  # noqa: E402

CORPUS = Path("data/corpus/hp_quartz/fragments.json")
OUT_XLSX = Path("data/hp_quartz/tables.xlsx")
OUT_REPORT = Path("data/hp_quartz/tables_build_report.json")

TABLES = [
    {"sheet": "分省资源储量",
     "query": "脉石英 资源储量 分省 四川 湖南 新疆 万元",
     "columns": ["省区", "矿种", "资源储量(万t)", "来源文献", "页码"],
     "hint": "提取各省份的脉石英/石英资源储量数字，一行一个省区；矿种填片段中的矿种名称（如脉石英）"},
    {"sheet": "应用消费结构",
     "query": "高纯石英 应用 用量 占比 光伏 半导体 电光源 光纤",
     "columns": ["应用领域", "用量占比(%)", "来源文献", "页码"],
     "hint": "提取高纯石英在各应用领域的用量占比，一行一个领域"},
    {"sheet": "主要企业",
     "query": "高纯石英砂 主要企业 产能 石英股份 尤尼明 TQC 挪威",
     "columns": ["企业", "所属国家/地区", "产品或产能", "来源文献", "页码"],
     "hint": "提取全球主要高纯石英砂生产企业及其产能/产品等级；产能必须是片段原文数字，没有则填定性描述"},
]


def main() -> None:
    frags = json.loads(CORPUS.read_text(encoding="utf-8"))
    report = {"built_at": datetime.now().isoformat(timespec="seconds"),
              "sheets": []}
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for spec in TABLES:
        hits = _retrieve(frags, spec["query"], 8)
        src_block = "\n\n".join(
            f"【片段{i}｜来源：{f['doc']} 第{f['page']}页】\n{f['text']}"
            for i, f in enumerate(hits))
        system = ("你从资料片段中提取结构化表格。只使用片段中明确出现的数字，"
                  "禁止计算、换算、补全；来源文献与页码照抄片段标注。"
                  "没有把握的行不要输出。只输出 JSON："
                  '{"rows": [[...], ...]}')
        out = chat_json(system,
                        f"【任务】{spec['hint']}\n【表格列】{spec['columns']}\n"
                        f"【片段】\n{src_block}",
                        schema_hint="只输出一个合法 JSON 对象。")
        # 逐行回验：数字单元格必须能在某个片段原文中找到
        kept, dropped = [], []
        for row in out.get("rows") or []:
            row = [str(c).strip() for c in row][:len(spec["columns"])]
            nums_ok = True
            for cell in row:
                m = re.match(r"^-?\d+(?:\.\d+)?$", cell.replace(",", "")
                             .replace("万t", "").replace("%", "").strip())
                if not m:
                    continue
                v = float(m.group(0))
                if not any(_number_in_text(v, f["text"]) for f in hits):
                    nums_ok = False
                    break
            (kept if nums_ok else dropped).append(row)
        ws = wb.create_sheet(spec["sheet"])
        ws.append(spec["columns"])
        for row in kept:
            ws.append(row)
        report["sheets"].append({"sheet": spec["sheet"], "query": spec["query"],
                                 "n_fragments": len(hits),
                                 "n_rows_kept": len(kept),
                                 "n_rows_dropped": len(dropped),
                                 "dropped": dropped[:5]})
        print(f"{spec['sheet']}: 保留 {len(kept)} 行，丢弃 {len(dropped)} 行"
              + (f"（示例丢弃 {dropped[:2]}）" if dropped else ""))
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_XLSX)
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"完成 → {OUT_XLSX}")


if __name__ == "__main__":
    main()
