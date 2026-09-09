"""数据源 adapter 基础契约（M7 数据源插件化）。

AdapterResult = {facts, collections, meta, ctx, warnings}
- facts：事实六元组列表（id/name/value/unit/source/as_of，可带 reliability）
- collections：集合数据（公告、 periods 等供 prompt 组装与引用前缀消费）
- meta：并入 doc.meta（名称/行业/最新期等）
- ctx：跨 binding 传递的运行上下文（如 financial 产出 board_code 供 peer 消费）
- 单个 adapter 失败抛 SourceError，由 registry 捕获记 warning，不阻塞其他源。
"""

from dataclasses import dataclass, field
from typing import Any

from datalayer.sources.base import SourceError  # noqa: F401  (re-export)


@dataclass
class AdapterResult:
    facts: list[dict[str, Any]] = field(default_factory=list)
    collections: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    ctx: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class SourceAdapter:
    """所有数据源的统一接口；子类设 key/kind 并实现 fetch。"""

    key: str = ""
    kind: str = ""                      # local_file | database | rag | web
    default_reliability: str = "authoritative"
    summary: str = ""                   # 一句话中文说明（界面下拉/卡片展示）
    param_schema: list[dict] = []       # 参数声明：{k,label,ph,hint,required,type}
    ctx_keys: list[str] = []            # fetch 产出的 ctx 键（供下游 $ctx 引用）

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError


def rows_to_facts(rows: list[dict[str, Any]], *, source: str,
                  id_prefix: str, id_column: str,
                  name_template: str,
                  value_columns: list[dict[str, str]],
                  as_of_column: str | None = None) -> list[dict[str, Any]]:
    """行记录 → 事实六元组（local_file 与 database 共用的声明式映射）。

    value_columns: [{"column": "Au品位", "key": "au", "unit": "g/t"}, ...]
    事实 id = f"{id_prefix}.{行业务键}.{key}"；name_template 用行字段渲染
    （如 "{孔号} {项目} 品位"）。数值列解析失败（空/"-"/文本）跳过该格。
    """
    facts: list[dict[str, Any]] = []
    for row in rows:
        row_key = str(row.get(id_column, "")).strip()
        if not row_key:
            continue
        as_of = str(row.get(as_of_column, "")).strip() if as_of_column else ""
        try:
            name = name_template.format(**{
                k: ("" if v is None else v) for k, v in row.items()})
        except (KeyError, IndexError):
            name = row_key
        for vc in value_columns:
            raw = row.get(vc["column"])
            if raw is None or raw == "" or raw == "-":
                continue
            try:
                value = float(str(raw).replace(",", ""))
            except ValueError:
                continue
            facts.append({
                "id": f"{id_prefix}.{row_key}.{vc['key']}",
                "name": name, "value": value,
                "unit": vc.get("unit", ""), "source": source,
                "as_of": as_of or "未注明"})
    return facts


def rows_to_table(rows: list[dict[str, Any]], columns: list[str],
                  table_id: str) -> dict[str, Any]:
    """行记录 → collections["tables"][table_id]（generic_rows 渲染器消费）。"""
    return {"columns": columns,
            "rows": [[("" if r.get(c) is None else str(r.get(c))) for c in columns]
                     for r in rows]}
