"""CLI 入口。

用法：
  python run_pipeline.py --data-only --stock 000803   # 只跑数据层
  python run_pipeline.py --full --stock 000803        # 全流程：取数→大纲→分节→对账→渲染
  python run_pipeline.py --full --stock 600519 \
      --spec config/report_types/company_review.yaml  # 指定报告规格（v2）

progress 回调：每个阶段/每节完成时调用 progress(stage, message, data|None)，
M8 前端的 SSE 层直接转发（本模块的 print 仅为 CLI 观察）。
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from datalayer.settings import settings


def save(path: Path, payload) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"    已写入 {path.name}")


def main(argv: list[str] | None = None,
         progress: Callable[[str, str, dict | None], None] | None = None) -> None:
    parser = argparse.ArgumentParser(description="EvidenceCraft 报告生成流水线")
    parser.add_argument("--department", default="stock_demo",
                        help="部门 profile 名（数据源绑定/词表/特性，默认 stock_demo）")
    parser.add_argument("--stock", default=None, help="运行参数：股票代码")
    parser.add_argument("--project", default=None, help="运行参数：项目编号（地学等场景）")
    parser.add_argument("--period", default=None, help="运行参数：报告期次")
    parser.add_argument("--spec", default=None, help="报告规格 v2 yaml 路径（缺省 company_review）")
    parser.add_argument("--data-only", action="store_true", help="只跑数据层")
    parser.add_argument("--full", action="store_true", help="全流程生成报告")
    args = parser.parse_args(argv)

    _progress = progress or (lambda *_: None)

    def emit(stage: str, msg: str, data: dict | None = None) -> None:
        print(msg)
        _progress(stage, msg, data)

    from template_factory.schema import load_spec
    spec = load_spec(args.spec)
    from datalayer.registry import load_profile, run_department
    profile = load_profile(args.department)

    # judge 对标范文解析链：模板级 → 部门级
    if not spec.judge_reference and profile.get("judge_reference"):
        spec.judge_reference = profile["judge_reference"]

    # stock 仅股票部门取 settings 缺省；地学等部门以 --project 为主参数
    stock = args.stock or (settings.stock if args.department == "stock_demo" else None)
    run_params = {k: v for k, v in
                  {"stock": stock, "project": args.project,
                   "period": args.period}.items() if v is not None}

    subject = run_params.get("project") or run_params.get("stock") or args.department
    run_dir = settings.resolve(settings.artifacts_dir) / \
        f"{subject}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    emit("data", f"[1/6] 数据层：按部门 {args.department} 绑定拉取 {subject} 数据 ...")
    doc, crosscheck = run_department(args.department, run_params)
    save(run_dir / "facts.json", doc)
    if crosscheck:
        save(run_dir / "crosscheck_report.json", crosscheck)
    meta = doc["meta"]
    emit("data", f"  {meta['stock']} {meta.get('name', '')}  "
                 f"事实 {len(doc['facts'])} 条  "
                 f"交叉校验 {crosscheck['status'].upper() if crosscheck else 'N/A'}",
         {"facts": len(doc["facts"]),
          "crosscheck": crosscheck["status"] if crosscheck else "none"})
    for w in meta["warnings"]:
        print(f"  [警告] {w}")
    if crosscheck and crosscheck["status"] == "fail":
        raise SystemExit(1)
    if args.data_only:
        print(f"\n完成（仅数据层）。产物目录: {run_dir}")
        return

    from pipeline import judge, outline, reconcile, revise, sections, validate
    from pipeline.rating import rule_rating
    from render import html_report

    emit("outline", "[2/6] 大纲生成（标题 + 观点规划）...")
    spec_outline = outline.build_outline(doc, spec)
    save(run_dir / "outline.json", spec_outline)
    emit("outline", f"  标题: {spec_outline['title']}",
         {"title": spec_outline["title"]})
    save(run_dir / "meta.json", {
        "department": args.department, "params": run_params,
        "spec": str(args.spec) if args.spec else "default:company_review",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": spec_outline["title"]})

    emit("sections", "[3/6] 分节生成（每条观点独立会话）...")
    written = []
    for i, view in enumerate(spec_outline["views"], 1):
        s = sections.gen_view(doc, view, spec)
        written.append(s)
        emit("sections", f"  ({i}/{len(spec_outline['views'])}) {s['heading']}",
             {"slot_id": s["slot_id"], "heading": s["heading"]})

    emit("sections", "[4/6] 预测说明 + 风险提示（镜像核心观点）...")
    forecast = sections.gen_forecast_note(doc, spec) if spec.section("table") \
        else {"body": "", "cited_fact_ids": []}
    risks = sections.gen_risks(doc, spec, views=written) if spec.section("risk") \
        else {"body": "", "cited_fact_ids": []}
    save(run_dir / "sections.json", {"views": written,
                                     "forecast": forecast, "risks": risks})

    emit("review", "[5/6] 对账 + 规则校验 + judge 评审（不合格退回重写，至多2轮）...")
    rating = rule_rating(doc)
    report = reconcile.reconcile(doc, spec_outline, written, forecast, risks, spec)
    validate_report = validate.run(doc, spec_outline, written, forecast, risks,
                                   rating, spec, crosscheck)
    judge_report = judge.run(doc, spec_outline, written, forecast, risks,
                             report, validate_report, spec, rating)

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
        emit("review", f"  —— 第 {round_no} 轮修订（{len(issues)} 个问题）——")
        for it in issues:
            print(f"    [{it.get('target')}] {it.get('problem')}")
        written, forecast, risks, spec_outline = revise.apply(
            doc, spec_outline, written, forecast, risks, judge_report, spec)
        report = reconcile.reconcile(doc, spec_outline, written, forecast, risks, spec)
        validate_report = validate.run(doc, spec_outline, written, forecast,
                                       risks, rating, spec, crosscheck)
        judge_report = judge.run(doc, spec_outline, written, forecast, risks,
                                 report, validate_report, spec, rating)
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
    emit("review", f"  评审结束：judge {judge_report['total']} 分 "
                   f"{judge_report['verdict'].upper()}",
         {"judge_total": judge_report["total"],
          "judge_verdict": judge_report["verdict"],
          "reconcile": report["status"]})

    emit("render", "[6/6] 渲染 final.html ...")
    chart = None
    if (profile.get("features") or {}).get("kline_chart"):
        from render.kline_chart import make_chart
        chart = make_chart("1.000001", run_dir / "index_kline.png")
        print(f"    头图已生成 {chart.name}")
    html = html_report.render(
        doc, spec_outline, written, forecast, risks, spec,
        rating=rating, kline_png=chart.name if chart else None)
    html_path = run_dir / "final.html"
    html_path.write_text(html, encoding="utf-8")

    # docx 终稿（M8 规范简洁版）；渲染失败不影响流水线结果
    try:
        from render.docx_report import render_docx
        render_docx(doc, spec_outline, written, forecast, risks, spec,
                    rating=rating, kline_png=str(chart) if chart else None,
                    out_path=run_dir / "final.docx")
        print("    docx 已生成 final.docx")
    except Exception as e:  # noqa: BLE001
        print(f"    [警告] docx 渲染失败：{e}")

    print(f"""
完成。产物目录: {run_dir}
  报告标题: {spec_outline['title']}
  对账: {report['status'].upper()}    预览: {html_path}
""")
    emit("render", f"完成。产物目录: {run_dir}", {"run_dir": str(run_dir)})


if __name__ == "__main__":
    main()
