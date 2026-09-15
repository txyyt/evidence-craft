"""⑥ 退回重写：judge 点名的节，带原稿与评审意见重新生成。

仅重写被点名的节（其余保留），数字约束不变；标题可被整体修订。
修订目标按 Spec v2 的章节 id 匹配；范文走 fewshot_for 解析链
（槽位级 → 章节级），重写要求取自对应章节的 style。
"""

from typing import Any

import re

from pipeline.llm import chat_json, tier_for
from pipeline.sections import _citations_resolvable, _default_facts, _fact_block
from template_factory.schema import SpecV2

REVISE_TMPL = """【本节原稿】
小标题：{heading}
正文：{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

【允许引用的事实】（正文数字只能出自这里）
{facts}

在保持事实准确的前提下重写此节（小标题可微调）。只输出 JSON：
{{"heading": "...", "body": "...", "cited_fact_ids": [...]}}
"""

TITLE_TMPL = """当前标题：{title}
评审意见：{problem}
修改要求：{instruction}
标题要求：{title_style}
只输出 JSON：{{"title": "..."}}"""

FORECAST_REVISE_TMPL = """【预测说明原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

预测表（数字以此为准）：
{table}

按以下要求重写说明文字：
{style}

只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}"""

RISK_REVISE_TMPL = """【风险提示原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

按以下要求重写风险提示：
{style}

只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}"""

TEXT_REVISE_TMPL = """【本节原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

【允许引用的事实】（正文数字只能出自这里）
{facts}

在保持事实准确的前提下重写此节。只输出 JSON：
{{"body": "...", "cited_fact_ids": [...]}}
"""

TABLE_NOTE_REVISE_TMPL = """【表格说明原稿】
{body}

【评审意见】
问题：{problem}
修改要求：{instruction}

表格（数字以此为准）：
{table}

按以下要求重写说明文字：
{style}

只输出 JSON：{{"body": "...", "cited_fact_ids": [...]}}"""


def _system(spec: SpecV2, fewshot: str,
            doc: dict[str, Any] | None = None) -> str:
    from pipeline.sections import _TIMELINESS_TMPL, _era_line
    from pipeline.stylecards import apply as style_apply
    timeliness = ""
    if doc is not None:
        from datetime import datetime
        timeliness = _TIMELINESS_TMPL.format(
            today=datetime.now().strftime("%Y-%m-%d"),
            era_line=_era_line(doc))
        timeliness += "\n"
    style_block, fewshot = style_apply(spec, fewshot)
    if style_block:
        style_block = style_block + "\n"
    return (f"你是{spec.writer_role}，修订报告的一个部分。\n\n"
            f"报告类型：{spec.description}\n{timeliness}{style_block}"
            f"\n写作规范：\n{spec.rules_text()}\n\n"
            f"【范文】\n{fewshot}\n")


def _revise_issue(spec: SpecV2, fewshot: str, instruction: dict[str, str],
                  template: str, doc: dict[str, Any] | None = None,
                  **kwargs) -> dict[str, Any]:
    from pipeline.llm import pipeline_temperature
    return chat_json(_system(spec, fewshot, doc),
                     template.format(**kwargs, **instruction),
                     schema_hint="只输出一个合法 JSON 对象。",
                     tier=tier_for("write"),
                     temperature=pipeline_temperature("write"))


def _history_block(revision_history: list[dict[str, Any]] | None,
                   target: str) -> str:
    """F1 累积记忆：该节在前几轮被点名过的意见摘要 + 重复轮次警示。
    revision_history 形如 [{"round": 1, "issues": [{target, problem, instruction}]}]。"""
    if not revision_history:
        return ""
    prior = [e for e in revision_history
             if any(i.get("target") == target for i in (e.get("issues") or []))]
    if not prior:
        return ""
    lines = []
    for e in prior[-2:]:
        for i in (e.get("issues") or []):
            if i.get("target") == target:
                lines.append(f"- 第{e['round']}轮意见：{str(i.get('problem', ''))[:60]}"
                             f"（要求：{str(i.get('instruction', ''))[:60]}）")
    warn = (f"\n【警告】该问题已重复 {len(prior)} 轮未达标，本轮必须彻底执行，"
            "不得再敷衍。") if len(prior) >= 1 else ""
    return "\n【前轮修订记录】\n" + "\n".join(lines) + warn


# ---------- F2 字数确定性手术（仅超上限方向） ----------

def _trim_cited_to_text(doc: dict[str, Any], cited_ids: list[str],
                        text: str) -> list[str]:
    """截断后引用裁剪：只保留正文中实际出现其数字的引用（防"缺失引用"告警）。"""
    import re
    by_id = {f["id"]: f for f in doc.get("facts") or []}
    out = []
    for fid in cited_ids or []:
        f = by_id.get(fid)
        if f is None:
            continue
        nums = [n for n in re.findall(r"\d+(?:\.\d+)?",
                                      f"{f.get('value', '')}{f.get('name', '')}")
                if len(n) >= 2]
        if any(n in text for n in nums):
            out.append(fid)
    return out


def enforce_body_limits(doc: dict[str, Any], spec: SpecV2,
                        texts: list[dict[str, Any]],
                        revision_history: list[dict[str, Any]] | None = None
                        ) -> tuple[list[dict[str, Any]], list[str]]:
    """F2：超上限的 text 节先做确定性预算——
    1) 超额 ≤15%：给模型指名指令（"删除第 X 段或将第 X/Y 段合并"，按段落长度选候选）；
    2) 模型改后复检仍超限：代码在段落边界截断，cited_fact_ids 同步裁剪。
    返回 (texts, 手术说明列表)。低于下限仍走模型扩写指令（不在本函数）。"""
    notes: list[str] = []
    for t in texts:
        sec = spec.section_by_id(t.get("section_id") or "")
        if sec is None or sec.kind != "text" or not sec.check.body_len:
            continue
        hi = tuple(sec.check.body_len)[1]
        body = t.get("body") or ""
        if len(body) <= hi:
            continue
        paras = [p for p in body.split("\n") if p.strip()]
        excess = len(body) - hi
        # 第一步：超额 ≤15% 且有多段 → 指名指令交模型执行
        if 0 < excess <= hi * 0.15 and len(paras) >= 2:
            ranked = sorted(range(len(paras)), key=lambda i: -len(paras[i]))
            x, y = ranked[0] + 1, ranked[1] + 1
            named = (f"当前正文 {len(body)} 字，超出上限 {hi} 字。"
                     f"请删除第 {x} 段，或将第 {x}/{y} 段合并压缩，"
                     "其余段落逐字保留，不得新增数字。")
            out = _revise_issue(spec, spec.fewshot_for(sec),
                                {"problem": f"正文超限（{len(body)}>{hi}）",
                                 "instruction": named},
                                TEXT_REVISE_TMPL, doc=doc, body=body,
                                facts="（本手术只删不增，事实切片略）")
            new_body = (out or {}).get("body") or ""
            if new_body and len(new_body) <= hi:
                t["body"] = new_body
                t["cited_fact_ids"] = _trim_cited_to_text(
                    doc, t.get("cited_fact_ids") or [], new_body)
                notes.append(f"节「{sec.title}」指名删段后 {len(new_body)} 字"
                             f"（原 {len(body)}）")
                continue
        # 第二步：复检仍超限（或模型改失败）→ 段落边界截断
        kept: list[str] = []
        acc = 0
        for p in paras:
            if kept and acc + len(p) > hi:
                break
            kept.append(p)
            acc += len(p)
        new_body = "\n".join(kept)
        t["body"] = new_body
        t["cited_fact_ids"] = _trim_cited_to_text(
            doc, t.get("cited_fact_ids") or [], new_body)
        notes.append(f"节「{sec.title}」字数超限（{len(body)}>{hi}），"
                     f"已按段落边界截断至 {len(new_body)} 字并同步裁剪引用")
    return texts, notes


# ---------- F3 结构类 issue 确定性处理（只改本运行内存，不动树） ----------

_MISS_SEC_RE = re.compile(r"章节缺失|整节缺失|缺少.{0,8}节|缺失.{0,4}节|缺.{0,2}一节")


def handle_structure_issues(doc: dict[str, Any], spec: SpecV2,
                            texts: list[dict[str, Any]],
                            notes: dict[str, Any],
                            issues: list[dict[str, Any]],
                            ) -> tuple[list[str], list[dict[str, Any]]]:
    """重复节 → 保留 judge 点名节、删除其余同名节；缺表且有模板 → 该节后插入
    kind=table 节（说明文字用既有 table_note 生成）；章节缺失（F1）→ 树上
    有该节定义、稿件无 → 按树定义就地 gen_text_section 补生成。
    处理结果只影响本稿，不动树。
    E1 返回 (handled 摘要, unhandled_issues)：没能确定性处理的结构 issue
    原样退回，由调用方转写作通道——结构类只有确定性处理或重写两种出口，
    不再丢弃。"""
    from template_factory.schema import Section
    from pipeline.sections import gen_table_note
    handled: list[str] = []
    unhandled: list[dict[str, Any]] = []
    for it in issues:
        if it.get("kind") != "structure":
            continue
        target = str(it.get("target") or "")
        text = (it.get("problem") or "") + (it.get("instruction") or "")
        sec = spec.section_by_id(target.split(".")[0])
        # F1 缺节：树上定义了该节、稿件里整节没有 → 按树定义就地补生成
        if _MISS_SEC_RE.search(text) and sec is not None:
            if any(t.get("section_id") == sec.id for t in texts):
                # 稿件其实有该节：是写作问题被说成"缺失"，退回写作通道
                unhandled.append(it)
                handled.append(f"「缺失」实为该节质量问题，转写作通道：{target}")
                continue
            from pipeline.sections import gen_text_section
            new_text = gen_text_section(doc, sec,
                                        {"guidance": it.get("instruction", "")},
                                        spec)
            order = {s.id: i for i, s in enumerate(spec.sections)}
            idx = len(texts)
            for i, t in enumerate(texts):
                if order.get(t.get("section_id") or "",
                             10 ** 9) > order.get(sec.id, 0):
                    idx = i
                    break
            texts.insert(idx, new_text)
            handled.append(f"缺失节〈{sec.title}〉已按树定义补生成"
                           f"（{len(new_text.get('body') or '')} 字）")
            continue
        # 重复节：同名节保留点名节，删除其余（含其稿件）
        if "重复" in text and sec is not None:
            dups = [s for s in spec.sections
                    if s.title == sec.title and s.id != sec.id]
            for d in dups:
                spec.sections.remove(d)
                texts[:] = [t for t in texts if t.get("section_id") != d.id]
                notes.pop(d.id, None)
                handled.append(f"重复节「{d.title}」（{d.id}）已删除，"
                               f"保留「{sec.id}」")
            if dups:
                continue
        # 缺表：候选=未被任何表格节引用的模板，插到点名节之后
        if ("缺表" in text or "未附表" in text
                or ("表" in text and any(k in text for k in
                                         ("缺", "插入", "增加", "补充", "添加")))) \
                and sec is not None and sec.kind != "table":
            referenced = {s.table for s in spec.sections_of("table") if s.table}
            cand = next((t2 for t2 in spec.tables if t2.id not in referenced),
                        None)
            if cand is None:
                handled.append(f"缺表问题未能确定性修复（无可用模板）：{target}")
                unhandled.append(it)
                continue
            new_sec = Section(id=cand.id, title=cand.id, kind="table",
                              table=cand.id,
                              style="用两三句话解读下表数据（数字以表格为准）。")
            spec.sections.insert(spec.sections.index(sec) + 1, new_sec)
            notes[cand.id] = gen_table_note(doc, new_sec, spec)
            handled.append(f"缺表：已在节「{sec.title}」之后插入表格节"
                           f"「{cand.id}」（模板既有，说明文字已生成），"
                           "建议在模板工作台同步该结构")
            continue
        # 其余结构动作（节顺序/多余节/未附图等）无确定性处理器 → 退回写作通道
        unhandled.append(it)
        handled.append(f"结构类问题未能确定性处理，已转写作通道修复：{target}")
    return handled, unhandled


def merge_issues_by_target(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """F3：同 target 多条意见合并为一条——problem 去重拼接（分号连接）、
    instruction 按序换行拼接（1. 2. 编号），每节每轮只重写一次、指令完整，
    防逐条串行重写互相覆盖。不同 target 互不影响。"""
    merged: list[dict[str, Any]] = []
    by: dict[str, dict[str, Any]] = {}
    for it in issues:
        t = str(it.get("target") or "")
        m = by.get(t)
        if m is None:
            m = dict(it)
            m["_probs"] = [str(it.get("problem") or "")]
            m["_instrs"] = [str(it.get("instruction") or "")]
            by[t] = m
            merged.append(m)
            continue
        p = str(it.get("problem") or "")
        if p and p not in m["_probs"]:
            m["_probs"].append(p)
        m["_instrs"].append(str(it.get("instruction") or ""))
    for m in merged:
        m["problem"] = "；".join(x for x in m["_probs"] if x)
        instrs = [x for x in m["_instrs"] if x]
        m["instruction"] = instrs[0] if len(instrs) <= 1 \
            else "\n".join(f"{i}. {x}" for i, x in enumerate(instrs, 1))
        m.pop("_probs", None)
        m.pop("_instrs", None)
    return merged


def apply(doc: dict[str, Any], outline: dict[str, Any],
          written: list[dict[str, Any]], forecast: dict[str, Any],
          risks: dict[str, Any],
          judge_report: dict[str, Any], spec: SpecV2,
          texts: list[dict[str, Any]] | None = None,
          notes: dict[str, dict[str, Any]] | None = None,
          revision_history: list[dict[str, Any]] | None = None,
          issues_override: list[dict[str, Any]] | None = None) -> tuple:
    """按 issues 修订，返回 (written, forecast, risks, outline, texts, notes)。

    views/table/risk 章节均可缺省（地学等报告类型无观点章/风险章）。
    F1：每个修订提示词注入【前轮修订记录】；F3：kind=structure 的 issue 不喂
    重写（由 handle_structure_issues 确定性处理，调用方负责）。
    E1：调用方可经 issues_override 显式传入本轮待重写 issues（含结构类退回），
    缺省仍从 judge_report 过滤（旧语义）。
    F3：重写前按 target 聚合，同 target 多条合并为一条。"""
    views_sec = spec.section("views")
    table_sec = spec.section("table")
    risk_sec = spec.section("risk")
    texts = list(texts or [])
    notes = dict(notes or {})

    issues = list(issues_override) if issues_override is not None \
        else [i for i in judge_report.get("issues", [])
              if i.get("kind") != "structure"]      # F3：结构类不走文风通道
    issues = merge_issues_by_target(issues)         # F3：同 target 聚合防覆盖
    for issue in issues:
        target = issue.get("target", "")
        fb = {"problem": issue.get("problem", ""),
              "instruction": issue.get("instruction", "")
              + _history_block(revision_history, target)}
        if target == "title":
            outline["title"] = _revise_issue(
                spec, spec.fewshot_for(views_sec) if views_sec else "",
                fb, TITLE_TMPL, title=outline["title"],
                title_style=spec.title_style)["title"]
            continue
        if views_sec is not None and target.startswith(views_sec.id + "."):
            views_prefix = views_sec.id + "."
            key = target[len(views_prefix):]
            # judge 偶发用序号（<views>.1）代替 slot_id：按位置兜底映射
            if key not in {v["slot_id"] for v in written} and key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(written):
                    target = views_prefix + written[idx]["slot_id"]
            if target.startswith(views_prefix):
                slot = target[len(views_prefix):]
                for i, v in enumerate(written):
                    if v["slot_id"] != slot:
                        continue
                    cited = v.get("cited_fact_ids") or _default_facts(doc, spec, slot)
                    facts_text, _ = _fact_block(doc, cited)
                    out = _revise_issue(spec, spec.fewshot_for(views_sec, slot),
                                        fb, REVISE_TMPL, doc=doc,
                                        heading=v["heading"],
                                        body=v["body"], facts=facts_text)
                    written[i] = {**out, "slot_id": slot, "cited_fact_ids": cited}
            continue
        if table_sec is not None and target == table_sec.id and target not in notes:
            from pipeline.sections import render_table
            out = _revise_issue(spec, spec.fewshot_for(table_sec),
                                fb, FORECAST_REVISE_TMPL, doc=doc, body=forecast["body"],
                                table=render_table(doc, spec),
                                style=table_sec.style or "")
            forecast = {**forecast, **out}
            continue
        if target in notes:
            tsec = spec.section_by_id(target)
            from pipeline.sections import render_table_sec
            note = notes[target]
            out = _revise_issue(spec, spec.fewshot_for(tsec) if tsec else "",
                                fb, TABLE_NOTE_REVISE_TMPL, doc=doc, body=note.get("body", ""),
                                table=render_table_sec(doc, tsec, spec) if tsec else "",
                                style=(tsec.style if tsec else "") or "")
            out["section_id"] = target
            out["cited_fact_ids"] = _citations_resolvable(
                doc, out.get("cited_fact_ids") or [])
            notes[target] = out
            continue
        if risk_sec is not None and target == risk_sec.id:
            out = _revise_issue(spec, spec.fewshot_for(risk_sec),
                                fb, RISK_REVISE_TMPL, doc=doc, body=risks["body"],
                                style=risk_sec.style or "")
            out["cited_fact_ids"] = _citations_resolvable(
                doc, out.get("cited_fact_ids") or [])
            risks = {**risks, **out}
            continue
        # text 章节：按 section_id 匹配重写
        for ti, t in enumerate(texts):
            if t.get("section_id") != target:
                continue
            sec = spec.section_by_id(target)
            facts_text, _ = _fact_block(doc, t.get("cited_fact_ids") or [])
            out = _revise_issue(spec, spec.fewshot_for(sec) if sec else "",
                                fb, TEXT_REVISE_TMPL, doc=doc, body=t["body"],
                                facts=facts_text)
            out["section_id"] = target
            out["cited_fact_ids"] = _citations_resolvable(
                doc, out.get("cited_fact_ids") or [])
            texts[ti] = out
            break
    return written, forecast, risks, outline, texts, notes
