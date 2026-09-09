"""Spec v2：报告规格 Schema 与加载校验。

v2 相对 v1 的差异（对应《多部门泛化方案》M6）：
- 章节带 kind（views/table/risk/text/figures），流水线按 kind 分发，不假设 sections[0]
- 范文下放到槽位/章节级（slot.fewshot → section.fewshot），无全局 fewshot——
  长报告各章节写法异质，每槽位 1~2 段精准范文即有效剂量
- 槽位兜底从代码硬编码变为 fallback 声明（语法 "source:selector:limit"）
- 表格模板带 renderer，代码渲染器按名分发，LLM 不碰表格数字
- 行文规则、禁用词、受控词表、派生校验规则、标题/字数校验参数全部配置化
- judge 对标范文挂模板级（judge_reference），部门 profile 可覆盖（M7）

模板工厂（template_factory/extract.py）的 LLM 输出必须通过本 Schema 校验才能成为
spec 草案——流水线执行的是 Schema，不是散文。
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

import yaml

from datalayer.settings import settings

DEFAULT_SPEC_PATH = "config/report_types/company_review/report.yaml"

Kind = Literal["views", "table", "risk", "text", "figures"]


class ViewSlot(BaseModel):
    """views 章节的固定视角槽位。"""

    id: str
    brief: str
    data_needs: list[str] = Field(default_factory=list)   # 语义需求，M7 由部门 profile 绑定
    fallback: list[str] = Field(default_factory=list)     # 兜底事实声明，如 ann:catalyst:3
    fewshot: str | None = None                            # 槽位级范文（优先于章节级）


class Check(BaseModel):
    """校验参数：validate.py 按 section.kind 解读对应字段，缺省用内置宽松值。"""

    title_len: tuple[int, int] | None = None      # 标题字数区间（spec 级）
    require_comma: bool | None = None             # 标题须逗号分隔两判断（spec 级）
    heading_len: tuple[int, int] | None = None    # 观点小标题字数（views）
    body_len: tuple[int, int] | None = None       # 正文字数（views/table）
    count: tuple[int, int] | None = None          # 条数区间（risk）
    item_suffix: str | None = None                # 每条结尾词（risk，如"风险"）
    min_shaped: int | None = None                 # 至少几条以 item_suffix 结尾（risk）


class Section(BaseModel):
    id: str
    title: str
    kind: Kind
    style: str | None = None          # 本节写作要求（喂给对应阶段的 prompt）
    fewshot: str | None = None        # 章节级范文（槽位未配置时兜底）
    check: Check = Field(default_factory=Check)
    # kind=views
    n_views: int | None = None
    view_slots: list[ViewSlot] = Field(default_factory=list)
    view_style: str | None = None
    # kind=table
    table: str | None = None          # 引用 tables[].id
    # kind=risk
    strategy: Literal["mirror", "enumerate"] | None = None

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "Section":
        if self.kind == "views" and (not self.view_slots or not self.n_views):
            raise ValueError(f"views 章节 {self.id} 须配置 view_slots 与 n_views")
        if self.kind == "table" and not self.table:
            raise ValueError(f"table 章节 {self.id} 须配置 table 引用")
        return self


class TableTemplate(BaseModel):
    """表格模板：代码渲染器按 renderer 分发，LLM 不产表格数字。"""

    id: str
    renderer: Literal["consensus_pe", "generic_rows"]
    columns: list[str] = Field(default_factory=list)


class SpecV2(BaseModel):
    report_type: str
    version: int = 2
    description: str
    writer_role: str = "资深报告撰写人"
    title_style: str = ""
    title_check: Check = Field(default_factory=Check)
    sections: list[Section]
    tables: list[TableTemplate] = Field(default_factory=list)
    writing_rules: list[str] = Field(default_factory=list)
    forbidden_words: list[str] = Field(default_factory=list)
    controlled_vocab: dict[str, list[str]] = Field(default_factory=dict)
    check_rules: list[str] = Field(default_factory=list)   # validate.py 内置派生规则开关
    judge_reference: str | None = None                     # 模板级对标范文，部门可覆盖
    disclaimer: str | None = None                          # 渲染层免责/说明段
    industry_keywords: list[str] = Field(default_factory=list)  # M7 迁部门 profile

    @model_validator(mode="after")
    def _check_sections(self) -> "SpecV2":
        ids = [s.id for s in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError(f"章节 id 重复: {ids}")
        return self

    def section(self, kind: str) -> Section | None:
        return next((s for s in self.sections if s.kind == kind), None)

    def section_by_id(self, sid: str) -> Section | None:
        return next((s for s in self.sections if s.id == sid), None)

    def table(self, tid: str | None) -> TableTemplate | None:
        if tid is None:
            return None
        return next((t for t in self.tables if t.id == tid), None)

    def rules_text(self) -> str:
        return "\n".join(f"- {r}" for r in self.writing_rules)

    def fewshot_for(self, section: Section, slot_id: str | None = None) -> str:
        """范文解析链：槽位级 → 章节级 → 空。"""
        if slot_id:
            slot = next((v for v in section.view_slots if v.id == slot_id), None)
            if slot and slot.fewshot:
                return slot.fewshot
        return section.fewshot or ""


def load_spec(path: str | Path | None = None) -> SpecV2:
    """加载并校验 spec；相对路径按项目根解析。"""
    p = settings.resolve(str(path)) if path else \
        Path(__file__).resolve().parent.parent / DEFAULT_SPEC_PATH
    with open(p, encoding="utf-8") as f:
        return SpecV2.model_validate(yaml.safe_load(f))
