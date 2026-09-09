"""database 类 adapter：查询模板 → 事实（M7 首接 SQLite，标准库零依赖）。

- 连接配置在 settings.yaml 的 databases.{db_ref}（path 等，gitignore）；
  profile 只存 db_ref 引用，不存连接串
- query 为命名参数模板（:param），参数经 registry 的 $ 引用解析
- 真实库型（Oracle/SQLServer/PostgreSQL）接入时在同名 adapter 后换
  SQLAlchemy 驱动，映射逻辑（rows_to_facts）复用
"""

import sqlite3
from pathlib import Path
from typing import Any

from datalayer.adapters.base import (AdapterResult, SourceAdapter,
                                     rows_to_facts, rows_to_table)
from datalayer.settings import settings


class SQLiteAdapter(SourceAdapter):
    key = "sqlite_query"
    kind = "database"
    summary = "SQLite 查询：命名参数 SQL 模板 → 事实（连接在系统设置配置）"

    def fetch(self, params: dict[str, Any]) -> AdapterResult:
        db_ref = params["db_ref"]
        cfg = (settings.databases or {}).get(db_ref)
        if not cfg or not cfg.get("path"):
            raise ValueError(f"settings.databases.{db_ref} 未配置")
        query = params["query"]
        query_params = params.get("query_params") or {}

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
