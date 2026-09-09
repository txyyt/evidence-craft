"""试跑一节（dry-run）：报告类型 spec × 真实数据 → 看一节的真实生成效果。

与 replay 的区别：replay 用样例自身数字（任何报告类型可跑），dry-run 走
该类型 sources.yaml 的数据绑定。只跑到④分节生成 + 对账，不做 judge。

用法：
  python -m template_factory.dryrun --spec x/report.yaml \
      --type geology_demo_review --project GM-1 --period 2026H1
"""

import argparse
import json
from typing import Any, Callable

from datalayer.registry import run_data_layer
from template_factory.schema import load_spec


def run(spec_path: str, type_id: str,
        run_params: dict[str, str] | None = None,
        progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    _p = progress or (lambda *_: None)
    spec = load_spec(spec_path)
    views_sec = spec.section("views")
    if views_sec is None:
        raise ValueError("报告结构缺少 views 章节，无法试跑")

    _p("按报告类型绑定拉取真实数据 ...")
    doc, _crosscheck = run_data_layer(type_id, run_params or {})
    _p(f"事实 {len(doc['facts'])} 条，生成大纲 ...")

    from pipeline.outline import build_outline
    outline = build_outline(doc, spec)

    from pipeline import reconcile as reconcile_mod
    from pipeline import sections

    _p("逐槽位试写 ...")
    written = [sections.gen_view(doc, view, spec) for view in outline["views"]]
    report = reconcile_mod.reconcile(doc, outline, written,
                                     {"body": ""}, {"body": ""}, spec)
    unknown = {c["section"]: c["unknown_numbers"]
               for c in report["checks"] if c["unknown_numbers"]}
    return {
        "title": outline["title"],
        "n_facts": len(doc["facts"]),
        "views": [{"slot_id": w["slot_id"], "heading": w["heading"],
                   "body": w["body"]} for w in written],
        "unknown_by_slot": unknown,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="试跑一节：报告结构 × 真实数据")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--type", required=True, help="报告类型 id（提供数据绑定）")
    ap.add_argument("--params", default="{}",
                    help='JSON，如 \'{"project": "GM-1", "period": "2026H1"}\'')
    args = ap.parse_args()

    result = run(args.spec, args.type, json.loads(args.params),
                 progress=lambda m: print(f"  {m}"))
    print(f"\n标题：{result['title']}（真实事实 {result['n_facts']} 条）")
    for v in result["views"]:
        print(f"  [{v['slot_id']}] {v['heading']}\n    {v['body'][:120]}...")
    for slot, nums in result["unknown_by_slot"].items():
        print(f"  ✗ 未对账数字 [{slot}]: {nums}")


if __name__ == "__main__":
    main()
