"""CLI 入口。

用法：
  python run_pipeline.py --data-only --stock 000803   # 只跑数据层
  python run_pipeline.py --full --stock 000803        # 全流程：取数→大纲→分节→对账→渲染
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from datalayer import assemble
from datalayer.settings import settings


def save(path: Path, payload) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"    已写入 {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="EvidenceCraft 报告生成流水线")
    parser.add_argument("--stock", default=settings.stock)
    parser.add_argument("--data-only", action="store_true", help="只跑数据层")
    parser.add_argument("--full", action="store_true", help="全流程生成报告")
    args = parser.parse_args()

    run_dir = settings.resolve(settings.artifacts_dir) / \
        f"{args.stock}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] 数据层：拉取 {args.stock} 全量数据 ...")
    from pipeline.outline import load_spec
    spec = load_spec()
    doc, crosscheck = assemble.build(args.stock,
                                     industry_keywords=spec.get("industry_keywords"))
    save(run_dir / "facts.json", doc)
    save(run_dir / "crosscheck_report.json", crosscheck)
    meta = doc["meta"]
    print(f"  {meta['stock']} {meta['name']}  事实 {len(doc['facts'])} 条  "
          f"交叉校验 {crosscheck['status'].upper()}")
    for w in meta["warnings"]:
        print(f"  [警告] {w}")
    if crosscheck["status"] == "fail":
        raise SystemExit(1)
    if args.data_only:
        print(f"\n完成（仅数据层）。产物目录: {run_dir}")
        return

    from pipeline import outline, reconcile, sections
    from render import html_report

    print("[2/6] 大纲生成（标题 + 观点规划）...")
    spec_outline = outline.build_outline(doc)
    save(run_dir / "outline.json", spec_outline)
    print(f"  标题: {spec_outline['title']}")

    print("[3/6] 分节生成（每条观点独立会话）...")
    written = []
    for i, view in enumerate(spec_outline["views"], 1):
        s = sections.gen_view(doc, view)
        written.append(s)
        print(f"  ({i}/{len(spec_outline['views'])}) {s['heading']}")

    print("[4/6] 盈利预测说明 + 风险提示（镜像核心观点）...")
    forecast = sections.gen_forecast_note(doc)
    risks = sections.gen_risks(doc, views=written)
    save(run_dir / "sections.json", {"views": written,
                                     "forecast": forecast, "risks": risks})

    print("[5/6] 对账 + 规则校验 + judge 评审（不合格退回重写，至多2轮）...")
    from pipeline import judge, revise, validate
    from render.html_report import _rule_rating

    report = reconcile.reconcile(doc, spec_outline, written, forecast, risks)
    validate_report = validate.run(doc, spec_outline, written, forecast, risks,
                                   _rule_rating(doc), crosscheck)
    judge_report = judge.run(doc, spec_outline, written, forecast, risks,
                             report, validate_report)

    def _print_status() -> None:
        print(f"  对账 {report['status'].upper()}（索引 {report['index_size']}）"
              f" | 规则 {validate_report['status'].upper()}"
              f"（{len(validate_report['items'])} 项告警）"
              f" | judge {judge_report['total']} 分 {judge_report['verdict'].upper()}")
        for c in report["checks"]:
            if c["unknown_numbers"] or c["cited_missing"]:
                print(f"  [{c['section']}] 未匹配数字 {c['unknown_numbers']}"
                      f"  缺失引用 {c['cited_missing']}")
        for i in validate_report["items"]:
            print(f"  [规则 {i['status']}] {i['rule']}: {i['detail']}")

    _print_status()
    round_no = 1
    while judge_report["verdict"] != "pass" and round_no <= 2:
        issues = judge_report.get("issues") or []
        print(f"  —— 第 {round_no} 轮修订（{len(issues)} 个问题）——")
        for it in issues:
            print(f"    [{it.get('target')}] {it.get('problem')}")
        written, forecast, risks, spec_outline = revise.apply(
            doc, spec_outline, written, forecast, risks, judge_report)
        report = reconcile.reconcile(doc, spec_outline, written, forecast, risks)
        validate_report = validate.run(doc, spec_outline, written, forecast,
                                       risks, _rule_rating(doc), crosscheck)
        judge_report = judge.run(doc, spec_outline, written, forecast, risks,
                                 report, validate_report)
        _print_status()
        save(run_dir / f"revision_round{round_no}.json", {
            "reconcile": report["status"], "validate": validate_report,
            "judge": {"total": judge_report["total"],
                      "verdict": judge_report["verdict"],
                      "issues": judge_report.get("issues")}})
        round_no += 1

    save(run_dir / "reconcile_report.json", report)
    save(run_dir / "validate_report.json", validate_report)
    save(run_dir / "judge_report.json", judge_report)

    print("[6/6] 渲染 final.html ...")
    from render.kline_chart import make_chart
    chart = make_chart("1.000001", run_dir / "index_kline.png")
    print(f"    头图已生成 {chart.name}")
    html = html_report.render(
        doc, spec_outline, written, forecast["body"], risks["body"],
        kline_png=chart.name)
    html_path = run_dir / "final.html"
    html_path.write_text(html, encoding="utf-8")

    print(f"""
完成。产物目录: {run_dir}
  报告标题: {spec_outline['title']}
  对账: {report['status'].upper()}    预览: {html_path}
""")


if __name__ == "__main__":
    main()
