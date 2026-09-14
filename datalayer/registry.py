"""数据源注册表：按报告类型的 sources.yaml 绑定列表逐个调 adapter，汇总 facts_doc。

报告类型目录结构（config/report_types/<id>/）：
  report.yaml   报告结构（Spec v2）
  sources.yaml  数据来源与配置：{name, description, status, params_schema,
                vocabulary, features, crosschecks, judge_reference, bindings}
  versions/     report.yaml 版本留痕
  samples/ parsed/ replay_history.json ...（工作区文件）

绑定形态：bindings: [{need, adapter, params: {stock: $stock}}]
  $ 引用：$<运行参数> / $vocabulary.<键> / $ctx.<键>；解析失败的 binding
  跳过并记 warning（宁可显式缺口，不让模型猜）。
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from datalayer.adapters.base import AdapterResult, SourceAdapter, SourceError
from datalayer.settings import settings


def _scan_adapters() -> dict[str, type[SourceAdapter]]:
    from datalayer.adapters import (database, eastmoney, local_file, rag,
                                    web_public, web_search)
    reg: dict[str, type[SourceAdapter]] = {}
    for mod in (eastmoney, local_file, database, rag, web_public, web_search):
        for name in dir(mod):
            obj = getattr(mod, name)
            if (isinstance(obj, type) and issubclass(obj, SourceAdapter)
                    and obj not in (SourceAdapter,) and getattr(obj, "key", "")):
                reg[obj.key] = obj
    return reg


ADAPTERS = _scan_adapters()


# ---------- 报告类型目录 ----------

def type_dir(type_id: str) -> Path:
    """校验 id 合法性并返回目录（不要求存在）。"""
    if (not type_id or "/" in type_id or "\\" in type_id or ".." in type_id
            or type_id.startswith(".")):
        raise ValueError(f"非法报告类型 id：{type_id!r}")
    return settings.resolve(f"config/report_types/{type_id}")


def spec_path(type_id: str) -> Path:
    return type_dir(type_id) / "report.yaml"


def sources_path(type_id: str) -> Path:
    return type_dir(type_id) / "sources.yaml"


def type_exists(type_id: str) -> bool:
    return sources_path(type_id).exists()


def list_types() -> list[dict[str, Any]]:
    root = settings.resolve("config/report_types")
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        sp = sources_path(d.name)
        if not sp.exists():
            continue
        src = load_sources(d.name)
        out.append({
            "id": d.name,
            "name": src.get("name") or d.name,
            "description": src.get("description", ""),
            "status": src.get("status", "draft"),
            "params": len(src.get("params_schema") or {}),
            "bindings": len(src.get("bindings") or []),
            "has_spec": spec_path(d.name).exists(),
            "fingerprint": template_fingerprint(d.name),
            "mtime": datetime.fromtimestamp(sp.stat().st_mtime)
            .isoformat(timespec="seconds"),
        })
    return out


def template_fingerprint(type_id: str) -> str | None:
    """report.yaml 内容指纹（短），生成时钉入报告 meta 供追溯。"""
    p = spec_path(type_id)
    if not p.exists():
        return None
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def load_sources(type_id: str) -> dict[str, Any]:
    with open(sources_path(type_id), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_sources(type_id: str, sources: dict[str, Any]) -> None:
    with open(sources_path(type_id), "w", encoding="utf-8") as f:
        yaml.dump(sources, f, allow_unicode=True, sort_keys=False)


# 兼容别名（旧代码语义：department → 报告类型）
def load_profile(type_id: str) -> dict[str, Any]:
    return load_sources(type_id)


# ---------- $ 引用解析 ----------

def _resolve(value: Any, run_params: dict, vocabulary: dict,
             ctx: dict) -> tuple[bool, Any]:
    """$ 引用解析（dict/list 递归）；返回 (ok, value)。
    $ctx.<键> / $vocabulary.<键> 按命名空间直查；其余（如 $stock）依次在
    运行参数、词表、ctx 中查找。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            ok, rv = _resolve(v, run_params, vocabulary, ctx)
            if not ok:
                return False, None
            out[k] = rv
        return True, out
    if isinstance(value, list):
        out = []
        for v in value:
            ok, rv = _resolve(v, run_params, vocabulary, ctx)
            if not ok:
                return False, None
            out.append(rv)
        return True, out
    if not (isinstance(value, str) and value.startswith("$")):
        return True, value
    path = value[1:]
    ns, _, rest = path.partition(".")
    if ns == "vocabulary" and rest:
        sources, key = (vocabulary,), rest
    elif ns == "ctx" and rest:
        sources, key = (ctx,), rest
    else:
        sources, key = (run_params, vocabulary, ctx), path
    for source in sources:
        node: Any = source
        found = True
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                found = False
                break
        if found and node not in (None, ""):
            return True, node
    return False, None


def _resolve_binding_params(binding: dict, run_params: dict, vocabulary: dict,
                            ctx: dict) -> tuple[dict | None, list[str]]:
    """单绑定 $ 解析；返回 (解析后参数|None, 缺参描述列表)。"""
    resolved, missing = {}, []
    for pk, pv in (binding.get("params") or {}).items():
        good, v = _resolve(pv, run_params, vocabulary, ctx)
        if good:
            resolved[pk] = v
        else:
            missing.append(f"{pk}={pv}")
    return (resolved if not missing else None), missing


def _params_needs_ctx(binding: dict) -> bool:
    """绑定参数是否引用 $ctx（依赖上游绑定产出）——引用者不参与并发。"""
    def _scan(v: Any) -> bool:
        if isinstance(v, str):
            return v.startswith("$ctx.")
        if isinstance(v, dict):
            return any(_scan(x) for x in v.values())
        if isinstance(v, list):
            return any(_scan(x) for x in v)
        return False
    return _scan(binding.get("params") or {})


def _run_binding(binding: dict, index: int, run_params: dict, vocabulary: dict,
                 ctx: dict) -> dict[str, Any]:
    """执行单个绑定（只读 run_params/vocabulary/ctx），返回分类结果。"""
    cls = ADAPTERS.get(binding.get("adapter"))
    if cls is None:
        return {"ok": False, "index": index,
                "log": {"index": index, "adapter": binding.get("adapter"),
                        "ok": False, "error": "未注册的 adapter"},
                "warnings": [f"binding[{index}] 未注册的 adapter: "
                             f"{binding.get('adapter')}"]}
    resolved, missing = _resolve_binding_params(binding, run_params,
                                                vocabulary, ctx)
    if resolved is None:
        return {"ok": False, "index": index,
                "log": {"index": index, "adapter": binding.get("adapter"),
                        "ok": False, "missing": missing},
                "warnings": [f"binding[{index}] {binding.get('adapter')} 缺参数"
                             f"（检查运行参数/词表/上游 ctx）：{missing}"]}
    try:
        result: AdapterResult = cls().fetch(resolved)
    except SourceError as e:
        return {"ok": False, "index": index,
                "log": {"index": index, "adapter": binding.get("adapter"),
                        "ok": False, "error": str(e)},
                "warnings": [f"binding[{index}] {binding.get('adapter')} 失败: {e}"]}
    except Exception as e:  # noqa: BLE001 —— 单源失败不阻塞
        return {"ok": False, "index": index,
                "log": {"index": index, "adapter": binding.get("adapter"),
                        "ok": False, "error": f"{type(e).__name__}: {e}"},
                "warnings": [f"binding[{index}] {binding.get('adapter')} 异常: "
                             f"{type(e).__name__}: {e}"]}
    for f in result.facts:
        f.setdefault("reliability", cls.default_reliability)
    return {"ok": True, "index": index, "result": result, "resolved": resolved,
            "log": {"index": index, "adapter": binding.get("adapter"), "ok": True,
                    "resolved": resolved, "n_facts": len(result.facts)},
            "warnings": list(result.warnings)}


def _resolve_all(binding_list: list[dict], run_params: dict, vocabulary: dict,
                 ctx: dict, upto: int | None = None) -> dict[str, Any]:
    """顺序解析/执行绑定（$ctx 依赖链；单绑定失败记 warning 继续）；
    upto 限制只执行到第几个绑定（单绑定测试用）。

    加速：连续多个"无 $ctx 依赖"的绑定（rag/web/xlsx 等互相独立的源）
    并发执行，遇 ctx 依赖的绑定回落串行——geology 的样品链等依赖场景不受影响。"""
    from concurrent.futures import ThreadPoolExecutor

    facts: list[dict[str, Any]] = []
    collections: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    warnings: list[str] = []
    resolved_log: list[dict[str, Any]] = []

    binding_list = list(binding_list)[:upto + 1] if upto is not None \
        else list(binding_list)

    def _merge(batch: list[dict[str, Any]]) -> None:
        for r in sorted(batch, key=lambda x: x["index"]):
            entry = r["log"]
            resolved_log.append(entry)
            warnings.extend(r["warnings"])
            if not r["ok"]:
                continue
            result = r["result"]
            facts.extend(result.facts)
            for k, v in result.collections.items():
                prev = collections.get(k)
                if isinstance(v, dict) and isinstance(prev, dict):
                    prev.update(v)          # 多个 xlsx 绑定各自产 table：按键合并
                elif isinstance(v, list) and isinstance(prev, list):
                    prev.extend(v)          # 多个 web_search 绑定各自产 web_pages
                else:
                    collections[k] = v
            ctx.update(result.ctx)
            meta.update(result.meta)

    batch: list[dict[str, Any]] = []
    for i, binding in enumerate(binding_list):
        if _params_needs_ctx(binding):
            _merge(batch)
            batch = []
            _merge([_run_binding(binding, i, run_params, vocabulary, ctx)])
        else:
            batch.append(_run_binding(binding, i, run_params, vocabulary, ctx))
            if len(batch) >= 6:            # 并发上限：6 个绑定一批
                _merge(batch)
                batch = []
    _merge(batch)
    return {"facts": facts, "collections": collections, "meta": meta,
            "warnings": warnings, "resolved_log": resolved_log}


def run_data_layer(type_id: str, run_params: dict[str, Any],
                   extra_bindings: list[dict] | None = None,
                   bindings_override: list[dict] | None = None,
                   sources_override: dict | None = None) -> tuple[dict, Any]:
    """按报告类型绑定取数 → (facts_doc, crosscheck|None)。单源失败不阻塞。

    extra_bindings：追加在模板静态绑定之后（同 schema：need/adapter/params）。
    bindings_override：整体替换静态绑定——意图规划器（datalayer/planner）
    用采集计划接管本次 rag/web 查询并筛选沿用表格时使用。
    sources_override：合成 sources dict（结构树模式：无报告类型目录、
    无静态绑定、crosschecks 不启用）。"""
    src = sources_override if sources_override is not None \
        else load_sources(type_id)
    if bindings_override is not None:
        bindings = list(bindings_override)
    else:
        bindings = list(src.get("bindings") or []) + list(extra_bindings or [])
    summary = _resolve_all(bindings, run_params,
                           src.get("vocabulary") or {}, dict(run_params))
    meta = {"type_id": type_id, **summary["meta"]}
    meta.update({
        "stock": run_params.get("stock") or run_params.get("project") or type_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": summary["warnings"],
    })
    doc = {"meta": meta, "facts": summary["facts"],
           "collections": summary["collections"]}

    crosscheck = None
    if "financial_dual_source" in (src.get("crosschecks") or []):
        from datalayer.assemble import check_financial
        crosscheck = check_financial(run_params["stock"])
    return doc, crosscheck


# 兼容别名
def run_department(type_id: str, run_params: dict[str, Any]) -> tuple[dict, Any]:
    return run_data_layer(type_id, run_params)


def test_binding(type_id: str, index: int,
                 run_params: dict[str, Any]) -> dict[str, Any]:
    """单绑定测试（报告类型管理页）：按顺序累积执行至第 index 个绑定（保证
    $ctx 依赖链，前置失败只影响 ctx 不阻塞），返回该绑定的结果摘要，
    含解析后的实际参数（resolved）。"""
    src = load_sources(type_id)
    bindings = src.get("bindings") or []
    if not (0 <= index < len(bindings)):
        raise ValueError(f"绑定序号越界：{index}（共 {len(bindings)} 个）")
    summary = _resolve_all(bindings, run_params, src.get("vocabulary") or {},
                           dict(run_params), upto=index)
    entry = summary["resolved_log"][-1] if summary["resolved_log"] else None
    if entry is None or entry.get("index") != index:
        binding = bindings[index]
        return {"ok": False, "adapter": binding.get("adapter"),
                "error": "目标绑定未能执行（前置异常）"}
    entry["warnings"] = [w for w in summary["warnings"]
                         if w.startswith(f"binding[{index}]")]
    if entry.get("ok"):
        cls = ADAPTERS[bindings[index].get("adapter")]
        entry["sample"] = summary["facts"][:3]
        entry["reliability"] = cls.default_reliability
    return entry
