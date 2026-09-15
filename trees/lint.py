"""树 linter：确定性结构检查（不依赖 LLM）。

errors（拦保存）：Spec v2 schema 违规——views 节缺槽位、table 节缺引用、
  章节 id 重复等由 SpecV2 校验兜住；节标题重复（A2，同标题节会在渲染与
  反馈定位时歧义）。
warnings（随保存返回前端展示的软警告）：
  - 节未声明 data_needs → 生成时可能无数据支撑
  - 图引用 table:<tid> 但 tables[] 无此 id → 渲染期缺表
  - facts_rows 表缺 source_prefix
  - heading 缺失且非 inline（渲染层会退回 title，提示用户确认编号形态）
  - 孤儿表模板：tables[] 中 id 无任何节引用（A3）
  - 节 brief/style 提到"表"但既非 table 节也未挂表格模板（A3，模型被迫手写表）
"""

from typing import Any

from pydantic import ValidationError

from template_factory.schema import SpecV2


def lint(spec_dict: dict[str, Any]) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        spec = SpecV2.model_validate(spec_dict)
    except ValidationError as e:
        for err in e.errors()[:6]:
            loc = ".".join(str(x) for x in err.get("loc") or [])
            errors.append(f"{loc}: {err.get('msg')}")
        if len(e.errors()) > 6:
            errors.append(f"……另有 {len(e.errors()) - 6} 处不合规")
        return {"errors": errors, "warnings": warnings}

    table_ids = {t.id for t in spec.tables}
    referenced = {s.table for s in spec.sections if s.kind == "table" and s.table}
    has_table_sec = any(s.kind == "table" for s in spec.sections)
    # A2：同标题查重（error，拦保存）
    seen: dict[str, str] = {}
    for s in spec.sections:
        if s.title in seen:
            errors.append(f"节标题重复：「{s.title}」（{seen[s.title]} 与 {s.id}），"
                          "请删除或改名其一")
        else:
            seen[s.title] = s.id
    for s in spec.sections:
        if not s.data_needs:
            warnings.append(f"节「{s.title}」未声明数据需求（data_needs），"
                            "生成时该节可能只有定性叙述")
        for c in s.charts:
            if c.source.startswith("table:") \
                    and c.source.split(":", 1)[1] not in table_ids:
                warnings.append(f"图「{c.title}」引用表格 {c.source}，"
                                "但 tables[] 中无此 id，渲染会缺图")
        if s.kind == "table" and not s.inline:
            tpl = spec.table(s.table)
            if tpl is not None and tpl.renderer == "facts_rows" \
                    and not tpl.source_prefix:
                warnings.append(f"表「{tpl.id}」用 facts_rows 渲染器但未配置 "
                                "source_prefix（事实 id 前缀）")
        if not s.heading and not s.inline and not s.subheading:
            warnings.append(f"节「{s.title}」未配置 heading，渲染将按顺序自动编号")
        # A3：brief/style 点名要表但全树没有任何表格节（模型会被迫手写 markdown 表）
        if not has_table_sec and s.kind != "table" and not s.table \
                and "表" in (s.style or "") and "表明" not in (s.style or "") \
                and "表达" not in (s.style or ""):
            warnings.append(f"节「{s.title}」的写作要求提到表格，但全树没有任何"
                            "表格节——请加 kind=table 节并挂表格模板，"
                            "否则模型会在正文手写 markdown 表")
    # A3：孤儿表模板（没有任何 table 节引用）
    for t in spec.tables:
        if t.id not in referenced:
            warnings.append(f"表格模板「{t.id}」没有任何节引用（孤儿模板），"
                            "请在某个 kind=table 节上引用它，或删除该模板")
    return {"errors": errors, "warnings": warnings}
