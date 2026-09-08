"""M6 验收：replay 样例回放——用样例自身数据验证提取出的模板。

流程：样例解析 → 数字+上下文抽成临时事实（reliability=manual，as_of=样例）→
LLM 迷你大纲（标题+各槽位小标题+选材）→ 复用流水线④分节生成与风险生成 →
复用⑤对账 + 字数校验。只验证模板的叙述模式与数字纪律，不用于真实交付。

用法：
  python -m template_factory.replay --spec config/report_types/xxx_draft.yaml \
      --sample 样例.docx
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import reconcile as reconcile_mod
from pipeline import sections
from template_factory import parsers
from template_factory.schema import load_spec

_UNIT_LIST = ["亿美元", "亿元", "万美元", "万元", "美元/吨", "克/吨", "pct",
              "个百分点", "%", "元/吨", "元", "亿吨", "万吨", "吨", "千米",
              "米", "倍", "台", "口"]


def _unit_after(text: str, end: int) -> str:
    tail = text[end:end + 6].lstrip()
    for u in _UNIT_LIST:
        if tail.startswith(u):
            return u
    return ""


def _facts_from_sample(parsed: dict[str, Any], max_facts: int = 60) -> list[dict[str, Any]]:
    """样例正文数字 + 所在句 → 临时事实（replay 命名空间）。"""
    as_of = "样例回放"
    facts: list[dict[str, Any]] = []
    seen: set[float] = set()
    for b in parsed["blocks"]:
        if b["type"] != "para":
            continue
        for sent in re.split(r"[。；！？\n]", b["text"]):
            matches = list(reconcile_mod._NUM_RE.finditer(sent))
            if not matches:
                continue
            clean = re.sub(r"\s", "", sent)
            for m in matches:
                raw = m.group(0).replace(",", "")
                if reconcile_mod._YEAR_RE.match(raw):
                    continue
                value = float(raw)
                if value in seen:
                    continue
                seen.add(value)
                unit = _unit_after(sent, m.end())
                name = clean[:44]
                facts.append({
                    "id": f"s.{len(facts) + 1:03d}", "name": name,
                    "value": value, "unit": unit,
                    "source": f"样例回放:{Path(parsed['source']).name}",
                    "as_of": as_of, "reliability": "manual",
                })
                if len(facts) >= max_facts:
                    return facts
    return facts


def _replay_doc(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "meta": {"stock": "REPLAY", "name": "", "industry": "",
                 "latest_period": None,
                 "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "warnings": ["replay：事实来自样例原文抽取，非权威数据"]},
        "facts": facts,
        "collections": {"periods": [], "mainop": {}, "statements": {},
                        "announcements": [], "consensus": {}, "peers": [],
                        "news": [], "industry_news": []},
    }


REPLAY_OUTLINE_SYSTEM = """你是{role}，为报告做写作规划。
报告类型：{description}

【行文规则】
{rules}

任务：拟结论式报告标题（title_style 要求）；为以下固定视角各拟一条结论式
小标题（判断句），从【可引用事实】中为它挑选支撑事实编号，并给一句论证
路径 guidance。只输出 JSON：
{{"title": "...", "views": [{{"slot_id": "...", "heading": "...",
"cited_fact_ids": ["..."], "guidance": "..."}}]}}
"""

REPLAY_OUTLINE_USER = """【title_style 要求】
{title_style}

【固定视角】
{slots}

【可引用事实】（正文数字只能出自这里）
{facts}

只输出 JSON。"""


def run(spec_path: str, sample_path: str) -> dict[str, Any]:
    """单轮回放。多轮取优见 run_rounds（单轮受 LLM 方差影响）。"""
    return _run_once(spec_path, sample_path)


def run_rounds(spec_path: str, sample_path: str, rounds: int = 2) -> dict[str, Any]:
    """多轮回放取最好成绩：单轮偶然超差不冤枉模板，系统性问题（每轮都
    编数字/越界）任何一轮都逃不掉，依然 FAIL。"""
    attempts = [run(spec_path, sample_path) for _ in range(max(1, rounds))]
    order = {"PASS": 2, "PASS_WITH_WARN": 1, "FAIL": 0}
    best = max(attempts, key=lambda r: order.get(r["verdict"], -1))
    best["rounds"] = [{"verdict": a["verdict"],
                       "unknown_total": sum(len(v) for v in
                                            a["unknown_by_slot"].values())}
                      for a in attempts]
    return best


def _run_once(spec_path: str, sample_path: str) -> dict[str, Any]:
    spec = load_spec(spec_path)
    views_sec = spec.section("views")
    risk_sec = spec.section("risk")
    if views_sec is None:
        raise ValueError("spec 缺少 views 章节")

    parsed = parsers.parse(sample_path)
    facts = _facts_from_sample(parsed)
    if not facts:
        raise ValueError("样例中未抽取到数字事实，无法回放")
    doc = _replay_doc(facts)

    from pipeline.llm import chat_json

    slots_text = "\n".join(f"- `{s.id}`：{s.brief}" for s in views_sec.view_slots)
    facts_text = "\n".join(f"[{f['id']}] {f['name']} = {f['value']}{f['unit']}"
                           for f in facts)
    out = chat_json(
        REPLAY_OUTLINE_SYSTEM.format(
            role=spec.writer_role, description=spec.description,
            rules=spec.rules_text()),
        REPLAY_OUTLINE_USER.format(title_style=spec.title_style,
                                   slots=slots_text, facts=facts_text),
        schema_hint="只输出一个合法 JSON 对象。")
    outline = {"title": out["title"], "views": out["views"]}
    doc["slot_briefs"] = {s.id: s.brief for s in views_sec.view_slots}

    written = []
    for view in outline["views"]:
        written.append(sections.gen_view(doc, view, spec))

    risks = sections.gen_risks(doc, spec, views=written) if risk_sec else \
        {"body": "", "cited_fact_ids": []}

    report = reconcile_mod.reconcile(doc, outline, written,
                                     {"body": ""}, risks, spec)
    unknown_by_slot = {c["section"]: c["unknown_numbers"]
                       for c in report["checks"] if c["unknown_numbers"]}
    cited_missing = {c["section"]: c["cited_missing"]
                     for c in report["checks"] if c["cited_missing"]}

    # 字数校验（按 spec 声明的区间；区间是提取观测值，允许 ±15% 估计噪声）
    charlen = lambda t: len(re.sub(r"\s", "", t))
    lo, hi = views_sec.check.body_len or (80, 230)
    length_report = []
    for w in written:
        n = charlen(w["body"])
        if lo <= n <= hi:
            status = "ok"
        elif lo * 0.85 <= n <= hi * 1.15:
            status = "near"
        else:
            status = "out"
        length_report.append({"slot_id": w["slot_id"], "n_chars": n,
                              "status": status, "range": [lo, hi]})

    hard_len_fail = any(r["status"] == "out" for r in length_report)
    if unknown_by_slot or cited_missing or hard_len_fail:
        verdict = "FAIL"
    elif any(r["status"] == "near" for r in length_report):
        verdict = "PASS_WITH_WARN"
    else:
        verdict = "PASS"

    return {
        "spec": spec_path,
        "sample": sample_path,
        "n_facts": len(facts),
        "title": outline["title"],
        "views": [{"slot_id": w["slot_id"], "heading": w["heading"],
                   "n_chars": charlen(w["body"]),
                   "body": w["body"]} for w in written],
        "risks": risks["body"],
        "unknown_by_slot": unknown_by_slot,
        "cited_missing": cited_missing,
        "length": length_report,
        "verdict": verdict,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="replay：用样例自身数据回放验证模板草案")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--sample", required=True)
    ap.add_argument("--rounds", type=int, default=2,
                    help="回放轮数（取最好成绩，默认 2）")
    args = ap.parse_args()
    result = run_rounds(args.spec, args.sample, args.rounds)
    if result.get("rounds"):
        print("各轮判定：" + "；".join(
            f"第{i+1}轮 {r['verdict']}（未对账 {r['unknown_total']} 个）"
            for i, r in enumerate(result["rounds"])))
    print(f"\n== replay 回放报告 == 样例 {result['sample']}")
    print(f"标题：{result['title']}   事实 {result['n_facts']} 条（样例抽取）")
    for v in result["views"]:
        print(f"  [{v['slot_id']}] {v['heading']}（{v['n_chars']}字）")
    print(f"风险提示：{result['risks'][:80]}")
    for r in result["length"]:
        mark = {"ok": "✓", "near": "△", "out": "✗"}[r["status"]]
        print(f"  字数{mark} {r['slot_id']}: {r['n_chars']}（区间 {r['range']}）")
    for slot, nums in result["unknown_by_slot"].items():
        print(f"  ✗ 未对账数字 [{slot}]: {nums}")
    for slot, ids in result["cited_missing"].items():
        print(f"  ✗ 缺失引用 [{slot}]: {ids}")
    print(f"结论：{result['verdict']}")


if __name__ == "__main__":
    main()
