"""阶段二验收联跑（一次性脚本）：数据裸奔门禁复现 → 计划+裁决 → 生成 → 收敛基线。

用法：python scripts/run_baseline_manual.py
输出：artifacts/_baseline/manual_demo_baseline.json + 控制台摘要
"""

import json
import sys
import time
from pathlib import Path

import urllib.request

BASE = "http://127.0.0.1:8765"
TREE = "manual_demo"
OUT = Path("artifacts/_baseline")


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


def sse_wait(url: str, timeout: int = 900) -> dict:
    """订阅 SSE 直到 end 事件，返回最后一个 data。"""
    t0 = time.time()
    last = {}
    with urllib.request.urlopen(url, timeout=timeout) as r:
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
            if time.time() - t0 > timeout:
                break
    return last


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    record: dict = {"tree": TREE, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    # ① 门禁复现：三来源全空 → 422
    code, body = api("POST", "/api/runs/from_tree", {"tree_id": TREE})
    record["gate"] = {"status": code, "detail": body.get("detail", "")}
    print(f"[1] 门禁拦截：HTTP {code} — {body.get('detail', '')[:80]}")
    assert code == 422, "门禁未拦截！"

    # ② 出数据计划
    code, body = api("POST", f"/api/trees/{TREE}/plan/start", {"intent": ""})
    assert code == 200, body
    end = sse_wait(BASE + body["events_url"])
    assert end.get("status") == "done", end
    plan_file = None
    for ev in []:
        pass
    code, plan = api("GET", f"/api/trees/{TREE}/plans")
    plan_file = sorted(plan, key=lambda p: p["mtime"])[-1]["name"]
    code, plan_body = api("GET", f"/api/trees/{TREE}/plans/{plan_file}")
    coverage = plan_body.get("needs_coverage") or []
    print(f"[2] 计划 {plan_file}：needs {len(coverage)} 条")
    record["plan_file"] = plan_file
    record["coverage_before"] = coverage

    # ③ 缺口裁决：全部定性（快速通道，不做联网取数）
    decisions = [{"need": c["need"], "decision": "qualitative"}
                 for c in coverage if c["status"] == "gap"]
    code, body = api("POST", f"/api/trees/{TREE}/plan/confirm",
                     {"plan_file": plan_file, "decisions": decisions})
    if not body.get("ok") and body.get("need_confirm"):
        code, body = api("POST", f"/api/trees/{TREE}/plan/confirm",
                         {"plan_file": plan_file, "decisions": [],
                          "all_qualitative": True})
    print(f"[3] 裁决+确认：ok={body.get('ok')}（gap {len(decisions)} 个定性）")
    assert body.get("ok"), body
    record["decisions"] = decisions

    # ④ 生成（沿用已确认计划）
    code, body = api("POST", "/api/runs/from_tree",
                     {"tree_id": TREE, "plan": plan_file})
    assert code == 200, body
    end = sse_wait(BASE + body["events_url"], timeout=1800)
    run_dir = end.get("run_dir") or ""
    print(f"[4] 生成：{end.get('status')} run_dir={run_dir}")
    record["run_status"] = end.get("status")
    record["run_dir"] = Path(run_dir).name if run_dir else None
    assert end.get("status") == "done", end

    # ⑤ 收敛基线：修订轮次 / judge 分数轨迹 / 遗留 issue 数
    rd = Path(run_dir)
    judge = json.loads((rd / "judge_report.json").read_text(encoding="utf-8"))
    rev = json.loads((rd / "revision_history.json").read_text(encoding="utf-8"))
    record["baseline"] = {
        "judge_total": judge.get("total"),
        "judge_verdict": judge.get("verdict"),
        "n_leftover_issues": len(judge.get("issues") or []),
        "revise_rounds_recorded": len(rev.get("rounds") or []),
        "rounds_judge": [r.get("round") for r in rev.get("rounds") or []],
        "structure_notes": rev.get("structure_notes"),
        "thin_warnings": rev.get("thin_warnings"),
        "method": "同一次运行内的修订轮次与 judge 轨迹（不做跨树比较）",
    }
    meta = json.loads((rd / "meta.json").read_text(encoding="utf-8"))
    record["model_tier"] = meta.get("model_tier")
    (OUT / "manual_demo_baseline.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[5] 收敛基线：", json.dumps(record["baseline"], ensure_ascii=False)[:400])
    print("已写入", OUT / "manual_demo_baseline.json")


if __name__ == "__main__":
    main()
