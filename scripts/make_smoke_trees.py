# -*- coding: utf-8 -*-
"""M1 冒烟夹具：创建两棵最小树（smoke_text 纯 text / smoke_mix views+table+risk+图表）
与离线数据计划（rag 查询指向已建语料库缓存，不联网）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trees import store as tree_store  # noqa: E402

CORPUS_REL = "data/corpus/_intent/2f7d0cab7100/fragments.json"

TREE_TEXT = {
    "report_type": "smoke_text",
    "description": "冒烟树A：纯 text 结构（市场综述 + 展望建议）",
    "writer_role": "资深行业研究员",
    "title_style": "短语式标题，15~30 字，概括报告主题",
    "sections": [
        {"id": "background", "title": "背景与现状", "kind": "text",
         "data_needs": ["高纯石英 市场供需", "高纯石英 资源分布"],
         "origin": "user",
         "style": "综述高纯石英的用途背景与我国资源供需现状，2 个自然段，"
                  "数字全部引用事实切片。",
         "check": {"body_len": [200, 900]}},
        {"id": "outlook", "title": "展望与建议", "kind": "text",
         "data_needs": ["高纯石英 找矿进展"],
         "origin": "user",
         "style": "基于事实切片总结找矿与提纯进展，给出展望，2 个自然段。",
         "check": {"body_len": [200, 900]}},
    ],
}

TREE_MIX = {
    "report_type": "smoke_mix",
    "description": "冒烟树B：views+table+risk+图文混合结构",
    "writer_role": "资深行业研究员",
    "title_style": "短语式标题，15~30 字，概括报告主题",
    "sections": [
        {"id": "core_views", "title": "核心观点", "kind": "views", "n_views": 2,
         "view_numbering": "1",
         "view_style": "结论先行，用事实切片数字支撑，每段 100~200 字。",
         "view_slots": [
             {"id": "supply", "brief": "供需形势判断（对外依存与国产替代）",
              "data_needs": ["高纯石英 供需"]},
             {"id": "exploration", "brief": "找矿勘查与技术进展判断",
              "data_needs": ["高纯石英 勘查"]},
         ]},
        {"id": "demand_table", "title": "关键数据一览", "kind": "table",
         "table": "facts_key",
         "style": "用两三句话解读下表数据（数字以表格为准）。",
         "check": {"body_len": [40, 300]}},
        {"id": "market_text", "title": "市场要点", "kind": "text",
         "data_needs": ["高纯石英 市场"],
         "charts": [{"id": "chart_smoke", "title": "关键数值分布", "type": "bar",
                     "source": "facts:rag", "unit": ""}],
         "style": "综述市场要点，2 个自然段，数字全部引用事实切片。",
         "check": {"body_len": [150, 800]}},
        {"id": "risk_sec", "title": "风险提示", "kind": "risk",
         "strategy": "enumerate",
         "style": "3~4 条风险，用分号分隔成一行。",
         "check": {"count": [3, 5]}},
    ],
    "tables": [
        {"id": "facts_key", "renderer": "facts_rows", "source_prefix": "rag"},
    ],
}

PLAN = {
    "mode": "plan", "subject": "高纯石英", "focus": "高纯石英资源供需与找矿勘查进展综述",
    "intent": "高纯石英资源供需与找矿勘查进展综述",
    "corpus": {"fragments_rel": CORPUS_REL, "n_fragments": 809,
               "rebuilt": False, "fingerprint": "2f7d0cab7100"},
    "rag": [{"need": "corpus_smoke", "query": "高纯石英 市场 供需 勘查", "top_k": 6}],
    "web": [], "db": [], "tables_kept": [],
}


def main() -> None:
    for tid, spec in (("smoke_text", TREE_TEXT), ("smoke_mix", TREE_MIX)):
        if tree_store.exists(tid):
            tree_store.delete_tree(tid)
        r = tree_store.save_tree(tid, spec, {"name": spec["description"][:12],
                                             "subject": "高纯石英"},
                                 actor="system", summary="M1 冒烟夹具")
        plans = tree_store.tree_dir(tid) / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "smoke.json").write_text(
            json.dumps(PLAN, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{tid}: v{r['meta']['version']} fp={r['fingerprint']} plan=smoke.json")


if __name__ == "__main__":
    main()
