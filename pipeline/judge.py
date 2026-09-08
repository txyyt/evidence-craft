"""⑥ LLM-as-judge：以标杆报告为对标，五维评分 + 点名修改意见。

评分维度：structure 结构完整 / professionalism 专业性 / data_support 数据支撑 /
compliance 合规性 / readability 可读性，各 1~10 分。总分 ≥36 且无单维 ≤4 → pass；
否则输出 issues（点名到节），由 revise 循环重写。

对标范文路径来自 Spec v2 的 judge_reference（模板级，M7 部门可覆盖）；
评审对象章节名由 spec 章节 id 动态生成，本模块不含业务专属措辞。
"""

from typing import Any

from datalayer.settings import settings
from pipeline.llm import chat_json
from template_factory.schema import SpecV2

SYSTEM = """你是报告质量评审官。以同类型标杆报告为对标，对自动生成的报告
严格评分。评分要给证据（引用原文短语），问题要定位到具体节。

评分维度（1~10）：
- structure 结构完整：各章节是否齐备且形态正确（对标标杆报告的章节形态）
- professionalism 专业性：论证方式、术语使用是否接近范文（对比/归因/口径）
- data_support 数据支撑：数据密度与精度，引用是否恰当（结合系统对账结果）
- compliance 合规性：无第一人称、无夸大、合规段落到位、表达克制
- readability 可读性：语句通顺、逻辑连贯、无模板腔

判定：总分≥36 且无单维≤4 → pass；否则 fail 并给出 issues。
target 取值只能是：title、{table_target}、{risk_target}、{views_target}.
+以下之一：{slot_ids}（禁止用序号代替）。"""


def run(doc: dict[str, Any], outline: dict[str, Any],
        views: list[dict[str, Any]], forecast: dict[str, Any],
        risks: dict[str, Any], reconcile_report: dict[str, Any],
        validate_report: dict[str, Any], spec: SpecV2,
        rating: str = "") -> dict[str, Any]:
    views_sec = spec.section("views")
    table_sec = spec.section("table")
    risk_sec = spec.section("risk")
    if views_sec is None or not spec.judge_reference:
        raise ValueError("spec 缺少 views 章节或 judge_reference")
    _chk = views_sec.check
    blo, bhi = tuple(_chk.body_len) if _chk.body_len else (80, 230)
    reference = settings.resolve(spec.judge_reference).read_text(encoding="utf-8")
    unknown_n = sum(len(c["unknown_numbers"]) for c in reconcile_report["checks"])
    validate_items = "；".join(
        f"{i['rule']}({i['status']}): {i['detail']}" for i in validate_report["items"]) \
        or "无告警"
    views_text = "\n".join(
        f"{i}. {v['heading']}\n   {v['body']}" for i, v in enumerate(views, 1))

    # target 取值与评审内容按 spec 实际章节动态生成（table/risk 章节可缺省）
    targets = [f"{views_sec.id}.<slot_id>"]
    if table_sec:
        targets.append(table_sec.id)
    if risk_sec:
        targets.append(risk_sec.id)
    targets.append("title")
    system = SYSTEM.format(
        table_target=table_sec.id if table_sec else "（本模板无此章节）",
        risk_target=risk_sec.id if risk_sec else "（本模板无此章节）",
        views_target=views_sec.id,
        slot_ids=", ".join(v["slot_id"] for v in views))

    blocks = [
        "【对标范文】（标杆报告节选）",
        reference,
        f"【待评报告】\n标题：{outline['title']}",
        f"\n{views_sec.title}：\n{views_text}",
    ]
    if table_sec:
        from pipeline.sections import render_table
        blocks.append(f"\n{table_sec.title}（系统生成表格，说明文字见后）：\n"
                      f"{render_table(doc, spec)}\n说明文字：\n{forecast['body']}")
    if risk_sec:
        blocks.append(f"\n{risk_sec.title}：\n{risks['body']}")

    # 模板格式要求：judge 按本模板的 spec 评审，不套用其他文体惯例
    hlo, hhi = tuple(views_sec.check.heading_len) if views_sec.check.heading_len \
        else (6, 20)
    spec_reqs = [f"{views_sec.title}：每条小标题 {hlo}~{hhi} 字、"
                 f"正文 {blo}~{bhi} 字"]
    if table_sec:
        flo, fhi = tuple(table_sec.check.body_len) if table_sec.check.body_len \
            else (60, 200)
        spec_reqs.append(f"{table_sec.title}：说明文字 {flo}~{fhi} 字")
    if risk_sec:
        clo, chi = tuple(risk_sec.check.count) if risk_sec.check.count else (3, 5)
        req = f"{risk_sec.title}：{clo}~{chi} 条，用分号分隔成一行"
        if (risk_sec.check.min_shaped or 0) > 0 and risk_sec.check.item_suffix:
            req += f"，至少 {risk_sec.check.min_shaped} 条以" \
                   f"'{risk_sec.check.item_suffix}'结尾"
        else:
            req += "，对条目结尾词无模板要求"
        spec_reqs.append(req)
    blocks.append("【模板格式要求】（本报告类型的模板约定，评审以此为准，"
                  "勿套用其他文体惯例）：\n" + "\n".join(f"- {r}" for r in spec_reqs))
    blocks.append(f"""

【系统标注】
报告评级：{rating or '（未推算）'}（系统规则推算，展示于报告头部，不属于
正文内容，勿因正文未出现评级而扣分）

【系统校验摘要】
数字对账：{reconcile_report['status'].upper()}（未匹配数字 {unknown_n} 个）
规则校验：{validate_report['status'].upper()}（{validate_items}）

评审并只输出 JSON：
{{"scores": {{"structure": {{"score": 0, "comment": "..."}},
"professionalism": {{...}}, "data_support": {{...}}, "compliance": {{...}},
"readability": {{...}}}}, "total": 0,
"verdict": "pass|fail",
"issues": [{{"target": "...", "problem": "...", "instruction": "..."}}]}}
issues 仅在 fail 时给出，target 必须用上述取值。""")
    out = chat_json(system, "\n".join(blocks),
                    schema_hint="只输出一个合法 JSON 对象。issues 的 target "
                                "只能用：" + "、".join(targets),
                    max_tokens=4000)
    # 兼容多种输出：{"structure": 8} / {"structure": {"score": 8, ...}} /
    # 偶发把非评分内容（如 "fail"）塞进 scores——跳过该维度
    scores: dict[str, dict[str, Any]] = {}
    for k, v in out.get("scores", {}).items():
        try:
            if isinstance(v, dict) and "score" in v:
                scores[k] = {"score": int(v["score"]),
                             "comment": str(v.get("comment", ""))}
            else:
                scores[k] = {"score": int(v), "comment": ""}
        except (TypeError, ValueError):
            continue
    out["scores"] = scores
    total = sum(d["score"] for d in scores.values())
    out["total"] = total if total else out.get("total", 0)
    min_score = min((d["score"] for d in scores.values()), default=0)
    out["verdict"] = "pass" if (out["total"] >= 36 and min_score > 4) else "fail"

    # 对账兜底（代码级强制）：存在未匹配数字的节必须 FAIL——评审不得主观放过
    dirty = [c for c in reconcile_report["checks"] if c["unknown_numbers"]]
    if dirty:
        out["verdict"] = "fail"
        have = {i.get("target") for i in out.get("issues") or []}
        for c in dirty:
            if c["section"] in have:
                continue
            out.setdefault("issues", []).append({
                "target": c["section"],
                "problem": f"正文存在 {len(c['unknown_numbers'])} 个无法对账的数字"
                           f"（{c['unknown_numbers']}），无事实出处",
                "instruction": "只允许引用事实编号中的数字，禁止对事实数字做任何"
                               "计算（合计/倍数/占比推导）；无出处的数字删除，"
                               "相关内容改为定性表述。"})
    # 规则硬伤兜底：validate FAIL 项合成修订指令（字数类硬伤 judge 评语可能遗漏
    # 或与其他意见相互抵消，导致修订越改越长）
    for i in validate_report.get("items", []):
        if i.get("status") != "fail":
            continue
        rid, detail = i["rule"], i["detail"]
        if rid.startswith("structure.") and rid.endswith((".body_len", ".heading_len")):
            target = f"{views_sec.id}.{rid.split('.')[1]}"
            n = i.get("metric")
            if rid.endswith(".body_len") and n is not None:
                lo, hi = blo, bhi
                direction = "压缩" if n > hi else "扩写"
                instruction = (f"正文 {n} 字，{'超过上限' if n > hi else '低于下限'}"
                               f" {lo}~{hi}：{direction}至区间内（宜 100~180 字）。"
                               "只使用事实切片中已有的数字，不得新增任何数字或计算值，"
                               "不得引入与核心观点重复的内容。")
            else:
                instruction = "小标题长度超出区间：改为 8~15 字判断句。"
        elif rid == "length.forecast" and table_sec:
            target = table_sec.id
            instruction = "压缩说明文字至要求区间，保留估值判断，不新增数字。"
        elif rid.startswith("risk.") and risk_sec:
            target = risk_sec.id
            instruction = "按格式要求重写风险提示。"
        else:
            continue
        out["verdict"] = "fail"
        have = {x.get("target") for x in out.get("issues") or []}
        if target not in have:
            out.setdefault("issues", []).append(
                {"target": target, "problem": detail, "instruction": instruction})
    if out["verdict"] == "pass":
        out["issues"] = []
    return out
