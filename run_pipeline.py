"""CLI 入口。

用法：
  python run_pipeline.py --data-only --type company_review --stock 000803
  python run_pipeline.py --full --type company_review --stock 000803
  python run_pipeline.py --full --type geology_demo_review --project GM-1 --period 2026H1

--department 为 --type 的兼容别名。--spec 可显式覆盖报告结构文件（调试用）。

progress 回调：每个阶段/每节完成时调用 progress(stage, message, data|None)，
data 恒含 llm_calls / llm_seconds（本次运行累计）；server 层 SSE 直接转发。
cancel_event 置位后在阶段边界干净退出（PipelineCancelled）。
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from datalayer.settings import settings


class PipelineCancelled(RuntimeError):
    """用户取消（server 层捕获后标记运行状态）。"""


def save(path: Path, payload) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"    已写入 {path.name}")


def main(argv: list[str] | None = None,
         progress: Callable[[str, str, dict | None], None] | None = None,
         cancel_event=None) -> None:
    parser = argparse.ArgumentParser(description="EvidenceCraft 报告生成流水线")
    parser.add_argument("--type", "--department", dest="type_id",
                        default="company_review",
                        help="报告类型 id（config/report_types/<id>/，默认 company_review）")
    parser.add_argument("--stock", default=None, help="运行参数：股票代码")
    parser.add_argument("--project", default=None, help="运行参数：项目编号（地学等场景）")
    parser.add_argument("--period", default=None, help="运行参数：报告期次")
    parser.add_argument("--spec", default=None, help="报告结构 yaml 路径（覆盖报告类型缺省）")
    parser.add_argument("--data-only", action="store_true", help="只跑数据层")
    parser.add_argument("--full", action="store_true", help="全流程生成报告")
    args = parser.parse_args(argv)

    _progress = progress or (lambda *_: None)
    from pipeline.llm import reset_stats, stats
    reset_stats()
    _stage_t0 = datetime.now().timestamp()

    def emit(stage: str, msg: str, data: dict | None = None) -> None:
        nonlocal _stage_t0
        now = datetime.now().timestamp()
        payload = {"llm_calls": stats()["calls"],
                   "llm_seconds": round(stats()["seconds"], 1),
                   "stage_seconds": round(now - _stage_t0, 1)}
        _stage_t0 = now
        if data:
            payload.update(data)
        print(msg)
        _progress(stage, msg, payload)

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise PipelineCancelled("用户取消")

    from template_factory.schema import load_spec
    from datalayer import registry

    type_dir = registry.type_dir(args.type_id)
    if not registry.type_exists(args.type_id):
        raise SystemExit(f"报告类型不存在：{args.type_id}")
    sources = registry.load_sources(args.type_id)
    spec_file = args.spec or str(registry.spec_path(args.type_id))
    spec = load_spec(spec_file)

    # judge 对标范文解析链：模板级 → 报告类型级
    if not spec.judge_reference and sources.get("judge_reference"):
        spec.judge_reference = sources["judge_reference"]

    # stock 仅股票类报告取 settings 缺省；其余以 --project 为主参数
    stock = args.stock or (settings.stock if args.type_id == "company_review" else None)
    run_params = {k: v for k, v in
                  {"stock": stock, "project": args.project,
                   "period": args.period}.items() if v is not None}

    subject = run_params.get("project") or run_params.get("stock") or args.type_id
    run_dir = settings.resolve(settings.artifacts_dir) / \
        f"{subject}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    emit("data", f"[1/6] 数据层：按报告类型「{sources.get('name', args.type_id)}」绑定拉取 {subject} 数据 ...")
    check_cancel()
    doc, crosscheck = registry.run_data_layer(args.type_id, run_params)
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
    check_cancel()
    spec_outline = outline.build_outline(doc, spec)
    save(run_dir / "outline.json", spec_outline)
    emit("outline", f"  标题: {spec_outline['title']}",
         {"title": spec_outline["title"]})
    save(run_dir / "meta.json", {
        "type_id": args.type_id,
        "type_name": sources.get("name", args.type_id),
        "template_fingerprint": registry.template_fingerprint(args.type_id),
        "params": run_params,
        "spec": "report.yaml" if not args.spec else str(args.spec),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": spec_outline["title"]})

    emit("sections", "[3/6] 分节生成（每条观点独立会话）...")
    written = []
    for i, view in enumerate(spec_outline["views"], 1):
        check_cancel()
        s = sections.gen_view(doc, view, spec)
        written.append(s)
        emit("sections", f"  ({i}/{len(spec_outline['views'])}) {s['heading']}",
             {"slot_id": s["slot_id"], "heading": s["heading"]})

    emit("sections", "[4/6] 预测说明 + 风险提示（镜像核心观点）...")
    check_cancel()
    forecast = sections.gen_forecast_note(doc, spec) if spec.section("table") \
        else {"body": "", "cited_fact_ids": []}
    risks = sections.gen_risks(doc, spec, views=written) if spec.section("risk") \
        else {"body": "", "cited_fact_ids": []}
    save(run_dir / "sections.json", {"views": written,
                                     "forecast": forecast, "risks": risks})

    emit("review", "[5/6] 对账 + 规则校验 + judge 评审（不合格退回重写，至多"
                   f"{int((settings.pipeline or {}).get('revise_rounds', 2))} 轮）...")
    check_cancel()
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
    max_rounds = int((settings.pipeline or {}).get("revise_rounds", 2))
    round_no = 1
    while judge_report["verdict"] != "pass" and round_no <= max_rounds:
        check_cancel()
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
    check_cancel()
    chart = None
    if (sources.get("features") or {}).get("kline_chart"):
        from render.kline_chart import make_chart
        chart = make_chart("1.000001", run_dir / "index_kline.png")
        print(f"    头图已生成 {chart.name}")
    html = html_report.render(
        doc, spec_outline, written, forecast, risks, spec,
        rating=rating, kline_png=chart.name if chart else None)
    html_path = run_dir / "final.html"
    html_path.write_text(html, encoding="utf-8")

    # docx 终稿（规范简洁版）；渲染失败不影响流水线结果
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
