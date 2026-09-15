"""阶段二 F11 验收联跑（一次性脚本）：Excel 数据接入出表出图。

用法：python scripts/run_excel_demo.py
前置：服务器已启动；data/geology_demo/assay_ledger.xlsx 存在。
输出：artifacts/_baseline/excel_demo.json + 控制台摘要
"""

import json
import time
from pathlib import Path

import urllib.request

BASE = "http://127.0.0.1:8765"
TREE = "excel_demo"
FOLDER = str(Path("data/geology_demo").resolve())
OUT = Path("artifacts/_baseline")

SPEC = {
    "report_type": TREE, "description": "勘查化验台账数据速览（Excel 接入演示）",
    "writer_role": "地质勘查数据分析师",
    "title_style": "短语式标题，15~30 字",
    "sections": [
        {"id": "综述", "title": "化验成果综述", "kind": "text",
         "heading": "1　化验成果综述",
         "style": "综述本批样品的化验成果（品位区间、厚度特征），300~450 字，"
                  "数字必须引用事实切片，注明样品编号。",
         "data_needs": ["样品品位"],
         "charts": [{"id": "品位对比图", "title": "各样品 Au 品位对比",
                     "type": "bar", "source": "table:assay_ledger",
                     "x": "样品号", "y": ["Au品位"], "unit": "g/t"}]},
        {"id": "化验数据一览", "title": "化验数据一览", "kind": "table",
         "heading": "表1　化验数据一览", "table": "assay_ledger",
         "style": "用两三句话解读下表数据（数字以表格为准，不在正文另写表格）。",
         "data_needs": ["样品品位"]},
    ],
    "tables": [{"id": "assay_ledger", "renderer": "generic_rows"}],
}


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode("utf-8")
    try:
        with urllib.request.urlopen(req, data=data, timeout=600) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def sse_wait(url: str, timeout: int = 1800) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        last = {}
        for raw in r:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            try:
                last = json.loads(line[6:])
            except ValueError:
                continue
            if last.get("type") == "end":
                return last
    return last


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    record: dict = {"tree": TREE, "folder": FOLDER,
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    # ① 建树（幂等：存在则复用）
    code, body = api("POST", "/api/trees",
                     {"id": TREE, "name": "Excel 接入演示",
                      "subject": "GM-1 化验台账", "spec_dict": SPEC})
    print(f"[1] 建树：{code} {str(body)[:80]}")
    assert code in (200, 409)

    # ② 出计划（带资料文件夹：PDF 无 → corpus None；xlsx → 确定性绑定）
    code, body = api("POST", f"/api/trees/{TREE}/plan/start",
                     {"intent": "汇总 GM-1 化验台账成果", "folder": FOLDER})
    assert code == 200, body
    end = sse_wait(BASE + body["events_url"], timeout=600)
    assert end.get("status") == "done", end
    code, plans = api("GET", f"/api/trees/{TREE}/plans")
    plan_file = sorted(plans, key=lambda p: p["mtime"])[-1]["name"]
    code, plan = api("GET", f"/api/trees/{TREE}/plans/{plan_file}")
    fb = plan.get("file_bindings") or []
    print(f"[2] 计划 {plan_file}：xlsx 绑定 {len(fb)} 条，"
          f"corpus={'有' if plan.get('corpus') else '无（纯 Excel）'}，"
          f"coverage={[(c['need'], c['status']) for c in plan.get('needs_coverage') or []]}")
    assert fb, "xlsx 绑定未生成！"
    assert plan.get("needs_coverage") and \
        all(c["status"] != "gap" for c in plan["needs_coverage"]), \
        "需求应被 Excel 判定覆盖"
    record["plan_file"] = plan_file
    record["file_bindings"] = len(fb)
    record["coverage"] = plan.get("needs_coverage")

    # ③ 确认
    code, body = api("POST", f"/api/trees/{TREE}/plan/confirm",
                     {"plan_file": plan_file, "decisions": []})
    assert body.get("ok"), body
    print(f"[3] 确认：ok={body.get('ok')}")

    # ④ 预检（F9）——顺带取Excel 事实样本
    code, body = api("POST", f"/api/trees/{TREE}/plan/preview",
                     {"plan_file": plan_file})
    assert code == 200, body
    print(f"[4] 预检：facts={body.get('n_facts')} by_source={body.get('by_source')} "
          f"cached={body.get('cached')}")
    record["preview"] = {k: body.get(k) for k in
                         ("ok", "n_facts", "by_source", "cached", "fingerprint")}

    # ⑤ 生成
    code, body = api("POST", "/api/runs/from_tree",
                     {"tree_id": TREE, "plan": plan_file, "folder": FOLDER})
    assert code == 200, body
    end = sse_wait(BASE + body["events_url"], timeout=1800)
    run_dir = end.get("run_dir") or ""
    print(f"[5] 生成：{end.get('status')} run_dir={run_dir}")
    assert end.get("status") == "done", end
    record["run_dir"] = Path(run_dir).name

    # ⑥ 出表出图断言
    rd = Path(run_dir)
    facts = json.loads((rd / "facts.json").read_text(encoding="utf-8"))
    n_xlsx_facts = sum(1 for f in facts["facts"]
                       if f["id"].startswith("assay_ledger."))
    has_table = "assay_ledger" in (facts["collections"].get("tables") or {})
    html = (rd / "final.html").read_text(encoding="utf-8")
    record["asserts"] = {
        "n_xlsx_facts": n_xlsx_facts,
        "has_table": has_table,
        "html_has_table": "<table>" in html,
        "html_has_chart": any((rd / f.name).exists()
                              for f in rd.glob("chart_*.png")),
        "judge": json.loads((rd / "judge_report.json").read_text(encoding="utf-8")
                            ).get("total"),
    }
    print("[6] 断言：", json.dumps(record["asserts"], ensure_ascii=False))
    (OUT / "excel_demo.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入", OUT / "excel_demo.json")


if __name__ == "__main__":
    main()
