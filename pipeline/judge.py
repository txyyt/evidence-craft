"""⑥ LLM-as-judge：以标杆报告为对标，五维评分 + 点名修改意见。

评分维度：structure 结构完整 / professionalism 专业性 / data_support 数据支撑 /
compliance 合规性 / readability 可读性，各 1~10 分。总分 ≥36 且无单维 ≤4 → pass；
否则输出 issues（点名到节），由 revise 循环重写。

对标范文路径来自 Spec v2 的 judge_reference（模板级，M7 部门可覆盖）；
评审对象章节名由 spec 章节 id 动态生成，本模块不含业务专属措辞。
views（核心观点）为可选章节：模板不设观点章时评审对象只剩标题/表格/综述/风险。
"""

from typing import Any

from datalayer.settings import settings
from pipeline.llm import chat_json, tier_for
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
{targets_line}"""

_SCORES_JSON = """
评审并只输出 JSON：
{{"scores": {{"structure": {{"score": 0, "comment": "..."}},
"professionalism": {{...}}, "data_support": {{...}}, "compliance": {{...}},
"readability": {{...}}}}, "total": 0,
"verdict": "pass|fail",
"issues": [{{"target": "...", "problem": "...", "instruction": "..."}}]}}
issues 仅在 fail 时给出，target 必须用上述取值。"""


def run(doc: dict[str, Any], outline: dict[str, Any],
        views: list[dict[str, Any]], forecast: dict[str, Any],
        risks: dict[str, Any], reconcile_report: dict[str, Any],
        validate_report: dict[str, Any], spec: SpecV2,
        rating: str = "",
        texts: list[dict[str, Any]] | None = None,
        notes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    views_sec = spec.section("views")
    table_secs = spec.sections_of("table")
    risk_sec = spec.section("risk")
    if not spec.judge_reference:
        raise ValueError("spec 缺少 judge_reference")
    blo, bhi = (80, 230)
    if views_sec is not None:
        _chk = views_sec.check
        blo, bhi = tuple(_chk.body_len) if _chk.body_len else (80, 230)
    reference = settings.resolve(spec.judge_reference).read_text(encoding="utf-8")
    unknown_n = sum(len(c["unknown_numbers"]) for c in reconcile_report["checks"])
    validate_items = "；".join(
        f"{i['rule']}({i['status']}): {i['detail']}" for i in validate_report["items"]) \
        or "无告警"

    # 评审对象按 spec 实际章节动态生成（views/table/risk 章节均可缺省）
    text_secs = {s.id: s for s in spec.sections_of("text")}
    texts_by_id = {t.get("section_id"): t for t in (texts or [])}
    targets: list[str] = []
    if views_sec is not None:
        targets.append(f"{views_sec.id}.<slot_id>")
    targets.extend(s.id for s in table_secs)
    if risk_sec:
        targets.append(risk_sec.id)
    targets.extend(text_secs.keys())
    targets.append("title")
    if views_sec is not None:
        slot_ids = ", ".join(v["slot_id"] for v in views)
        targets_line = (f"修订 target 取值：{views_sec.id}.+以下之一：{slot_ids}"
                        f"（禁止用序号代替），或 {'、'.join(targets)}。")
    else:
        targets_line = f"修订 target 取值只能是：{'、'.join(targets)}。"
    system = SYSTEM.format(targets_line=targets_line)

    blocks = [
        "【对标范文】（标杆报告节选）",
        reference,
        f"【待评报告】\n标题：{outline['title']}",
    ]
    if views_sec is not None:
        views_text = "\n".join(
            f"{i}. {v['heading']}\n   {v['body']}" for i, v in enumerate(views, 1))
        blocks.append(f"\n{views_sec.title}：\n{views_text}")
    for sid, sec in text_secs.items():
        t = texts_by_id.get(sid)
        if t:
            blocks.append(f"\n{sec.title}：\n{t['body']}")
    notes = notes or {}
    for sec in table_secs:
        from pipeline.sections import render_table_sec
        note_body = notes.get(sec.id, {}).get("body",
                                              forecast.get("body", "") if sec is table_secs[0] else "")
        blocks.append(f"\n{sec.title}（系统生成表格，说明文字见后）：\n"
                      f"{render_table_sec(doc, sec, spec)}\n说明文字：\n{note_body}")
    if risk_sec:
        blocks.append(f"\n{risk_sec.title}：\n{risks['body']}")

    # 模板格式要求：judge 按本模板的 spec 评审，不套用其他文体惯例
    spec_reqs = []
    if views_sec is not None:
        hlo, hhi = tuple(views_sec.check.heading_len) if \
            views_sec.check.heading_len else (6, 20)
        spec_reqs.append(f"{views_sec.title}：每条小标题 {hlo}~{hhi} 字、"
                         f"正文 {blo}~{bhi} 字")
    if table_secs:
        for sec in table_secs:
            flo, fhi = tuple(sec.check.body_len) if sec.check.body_len else (60, 200)
            spec_reqs.append(f"{sec.title}：说明文字 {flo}~{fhi} 字")
    if risk_sec:
        clo, chi = tuple(risk_sec.check.count) if risk_sec.check.count else (3, 5)
        req = f"{risk_sec.title}：{clo}~{chi} 条，用分号分隔成一行"
        if (risk_sec.check.min_shaped or 0) > 0 and risk_sec.check.item_suffix:
            req += f"，至少 {risk_sec.check.min_shaped} 条以" \
                   f"'{risk_sec.check.item_suffix}'结尾"
        else:
            req += "，对条目结尾词无模板要求"
        spec_reqs.append(req)
    for sid, sec in text_secs.items():
        tlo, thi = tuple(sec.check.body_len) if sec.check.body_len else (150, 1600)
        spec_reqs.append(f"{sec.title}：正文 {tlo}~{thi} 字，"
                         "数字必须能对账到事实切片")
    if spec_reqs:
        blocks.append("【模板格式要求】（本报告类型的模板约定，评审以此为准，"
                      "勿套用其他文体惯例）：\n" + "\n".join(f"- {r}" for r in spec_reqs))
    blocks.append(f"""

【系统标注】
报告评级：{rating or '（未推算）'}（系统规则推算，展示于报告头部，不属于
正文内容，勿因正文未出现评级而扣分）

【系统校验摘要】
数字对账：{reconcile_report['status'].upper()}（未匹配数字 {unknown_n} 个）
规则校验：{validate_report['status'].upper()}（{validate_items}）
""" + _SCORES_JSON)
    out = chat_json(system, "\n".join(blocks),
                    schema_hint="只输出一个合法 JSON 对象。issues 的 target "
                                "只能用：" + "、".join(targets),
                    max_tokens=4000, tier=tier_for("write"))
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
    # 通过门槛可在 settings.yaml 的 pipeline.judge_threshold 配置（默认 36）
    threshold = int((settings.pipeline or {}).get("judge_threshold", 36))
    out["verdict"] = "pass" if (out["total"] >= threshold and min_score > 4) else "fail"
    out["threshold"] = threshold

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
        if rid.startswith("structure.") and rid.endswith((".body_len", ".heading_len")) \
                and views_sec is not None:
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
        elif rid.startswith("length.") and table_secs:
            sid = rid.split(".", 1)[1]
            if sid in text_secs:
                target = sid
                n = i.get("metric")
                tlo, thi = tuple(text_secs[sid].check.body_len) \
                    if text_secs[sid].check.body_len else (150, 1600)
                direction = "压缩" if (n or 0) > thi else "扩写"
                instruction = (f"正文 {n} 字，{'超过上限' if (n or 0) > thi else '低于下限'}"
                               f" {tlo}~{thi}：{direction}至区间内。"
                               "只使用事实切片中已有的数字，不得新增任何数字或计算值。")
            else:
                tsec = next((s for s in table_secs if s.id == sid), None)
                if tsec is None:
                    continue
                target = sid
                n = i.get("metric")
                tlo, thi = tuple(tsec.check.body_len) if tsec.check.body_len \
                    else (60, 200)
                direction = "压缩" if (n or 0) > thi else "扩写"
                instruction = (f"说明文字 {n} 字，{'超过上限' if (n or 0) > thi else '低于下限'}"
                               f" {tlo}~{thi}：{direction}至区间内，"
                               "表格数字以系统生成的表格为准，不新增数字。")
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
