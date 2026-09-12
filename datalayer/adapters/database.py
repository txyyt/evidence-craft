"""database 类 adapter：查询模板 → 事实（M7 首接 SQLite，标准库零依赖）。

- 连接配置在 settings.yaml 的 databases.{db_ref}（path 等，gitignore）；
  profile 只存 db_ref 引用，不存连接串
- query 为命名参数模板（:param），参数经 registry 的 $ 引用解析
- 动态生成的查询（意图规划器）经 sanitize_sql 代码级安全约束：仅单条
  SELECT、禁止多语句、自动补 LIMIT——不靠提示词自觉
- 真实库型（Oracle/SQLServer/PostgreSQL）接入时在同名 adapter 后换
  SQLAlchemy 驱动，映射逻辑（rows_to_facts）复用
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

from datalayer.adapters.base import (AdapterResult, SourceAdapter,
                                     rows_to_facts, rows_to_table)
from datalayer.settings import settings

_FORBIDDEN_RE = re.compile(r"\b(attach|detach|pragma|vacuum|reindex)\b", re.I)


def _strip_sql_comments(sql: str) -> str:
    """剥 -- 行注释与 /* */ 块注释（引号内的 -- 不算注释）。"""
    out: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None
    while i < n:
        c = sql[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = None
            i += 1
        elif c in ("'", '"', "`"):
            quote = c
            out.append(c)
            i += 1
        elif c == "-" and sql[i:i + 2] == "--":
            while i < n and sql[i] != "\n":
                i += 1
        elif c == "/" and sql[i:i + 2] == "/*":
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def sanitize_sql(query: str, limit: int = 200) -> str:
    """动态 db 查询安全化（代码强制，不靠提示词）。

    规则：仅允许单条 SELECT；剥注释后引号外出现分号即判多语句拒绝；
    含 ATTACH/PRAGMA 等非查询关键字拒绝；无 LIMIT 子句自动追加
    LIMIT {limit}（上限 200 行）。违规抛 ValueError。
    """
    sql = _strip_sql_comments(str(query or "")).strip().rstrip(";").strip()
    if not sql:
        raise ValueError("空 SQL")
    if not re.match(r"(?is)^select\b", sql):
        raise ValueError("仅允许单条 SELECT 查询语句")
    if _FORBIDDEN_RE.search(sql):
        raise ValueError("包含非查询关键字（attach/pragma 等）")
    quote: str | None = None
    for c in sql:
        if quote:
            if c == quote:
                quote = None
        elif c in ("'", '"', "`"):
            quote = c
        elif c == ";":
            raise ValueError("禁止多语句（引号外出现分号）")
    if quote:
        raise ValueError("引号未闭合")
    if not re.search(r"(?is)\blimit\b", sql):
        sql = f"{sql}\n LIMIT {limit}"
    return sql


class SQLiteAdapter(SourceAdapter):
    key = "sqlite_query"
    kind = "database"
    summary = "SQLite 查询：命名参数 SQL 模板 → 事实（连接在系统设置配置）"

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        db_ref = params["db_ref"]
        cfg = (settings.databases or {}).get(db_ref)
        if not cfg or not cfg.get("path"):
            raise ValueError(f"settings.databases.{db_ref} 未配置")
        query = sanitize_sql(params["query"])
        query_params = {str(k): str(v)
                        for k, v in (params.get("query_params") or {}).items()}

        con = sqlite3.connect(cfg["path"])
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute(query, query_params)
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            con.close()
        if not rows:
            raise ValueError(f"{db_ref} 查询无结果：{query[:60]}")

        source = f"database:{db_ref}({Path(cfg['path']).name})"
        facts = rows_to_facts(
            rows, source=source,
            id_prefix=params["id_prefix"], id_column=params["id_column"],
            name_template=params["name_template"],
            value_columns=params.get("value_columns") or [],
            as_of_column=params.get("as_of_column"))
        result = AdapterResult(facts=facts)
        if params.get("table_columns"):
            tid = params.get("table_id") or params["id_prefix"]
            result.collections["tables"] = {
                tid: rows_to_table(rows, params["table_columns"], tid)}
        return result
