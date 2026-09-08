"""数据源注册表：按部门 profile 的绑定列表逐个调 adapter，汇总 facts_doc。

profile 形态（config/departments/{dept}/profile.yaml）：
  bindings:
    - {adapter: em_quote, params: {stock: $stock}}
    - {adapter: em_peer,  params: {board_code: $ctx.board_code}}
  $ 引用：$<运行参数> / $vocabulary.<键> / $ctx.<键>；解析失败的 binding
  跳过并记 warning（宁可显式缺口，不让模型猜）。
"""

from datetime import datetime
from typing import Any

import yaml

from datalayer.adapters.base import AdapterResult, SourceAdapter, SourceError
from datalayer.settings import settings


def _scan_adapters() -> dict[str, type[SourceAdapter]]:
    from datalayer.adapters import database, eastmoney, local_file, rag, web_public
    reg: dict[str, type[SourceAdapter]] = {}
    for mod in (eastmoney, local_file, database, rag, web_public):
        for name in dir(mod):
            obj = getattr(mod, name)
            if (isinstance(obj, type) and issubclass(obj, SourceAdapter)
                    and obj not in (SourceAdapter,) and getattr(obj, "key", "")):
                reg[obj.key] = obj
    return reg


ADAPTERS = _scan_adapters()


def load_profile(department: str) -> dict[str, Any]:
    path = settings.resolve(f"config/departments/{department}/profile.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


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


def test_binding(department: str, index: int,
                 run_params: dict[str, Any]) -> dict[str, Any]:
    """单绑定测试（M8 部门管理页）：按顺序累积执行至第 index 个绑定（保证
    $ctx 依赖链，前置失败只影响 ctx 不阻塞），返回该绑定的结果摘要。"""
    profile = load_profile(department)
    bindings = profile.get("bindings") or []
    if not (0 <= index < len(bindings)):
        raise ValueError(f"绑定序号越界：{index}（共 {len(bindings)} 个）")
    vocabulary = profile.get("vocabulary") or {}
    ctx: dict[str, Any] = dict(run_params)
    for binding in bindings[:index]:
        cls = ADAPTERS.get(binding.get("adapter"))
        if cls is None:
            continue
        resolved, ok = {}, True
        for pk, pv in (binding.get("params") or {}).items():
            good, v = _resolve(pv, run_params, vocabulary, ctx)
            if not good:
                ok = False
                break
            resolved[pk] = v
        if ok:
            try:
                ctx.update(cls().fetch(resolved).ctx)
            except Exception:  # noqa: BLE001 —— 前置失败不阻塞目标绑定
                pass

    binding = bindings[index]
    key = binding.get("adapter")
    cls = ADAPTERS.get(key)
    if cls is None:
        return {"ok": False, "adapter": key, "error": f"未注册的 adapter: {key}"}
    resolved, missing = {}, []
    for pk, pv in (binding.get("params") or {}).items():
        good, v = _resolve(pv, run_params, vocabulary, ctx)
        if good:
            resolved[pk] = v
        else:
            missing.append(f"{pk}={pv}")
    if missing:
        return {"ok": False, "adapter": key,
                "error": f"缺参数（检查运行参数/词表/上游 ctx）：{missing}"}
    try:
        result: AdapterResult = cls().fetch(resolved)
    except SourceError as e:
        return {"ok": False, "adapter": key, "error": str(e)}
    except Exception as e:  # noqa: BLE001 —— 测试端点把异常报给页面
        return {"ok": False, "adapter": key, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "adapter": key, "facts": len(result.facts),
            "collections": list(result.collections),
            "warnings": result.warnings,
            "sample": result.facts[:3]}


def run_department(department: str, run_params: dict[str, Any]) -> tuple[dict, Any]:
    """按部门 profile 拉数 → (facts_doc, crosscheck|None)。单源失败不阻塞。"""
    profile = load_profile(department)
    vocabulary = profile.get("vocabulary") or {}
    facts: list[dict[str, Any]] = []
    collections: dict[str, Any] = {}
    meta: dict[str, Any] = {"department": department}
    ctx: dict[str, Any] = dict(run_params)
    warnings: list[str] = []

    for binding in profile.get("bindings") or []:
        key = binding.get("adapter")
        cls = ADAPTERS.get(key)
        if cls is None:
            warnings.append(f"binding 未注册的 adapter: {key}")
            continue
        resolved, ok = {}, True
        for pk, pv in (binding.get("params") or {}).items():
            good, v = _resolve(pv, run_params, vocabulary, ctx)
            if not good:
                warnings.append(f"binding {key} 缺参数 {pk}（{pv}），跳过")
                ok = False
                break
            resolved[pk] = v
        if not ok:
            continue
        try:
            result: AdapterResult = cls().fetch(resolved)
        except SourceError as e:
            warnings.append(f"{key} 失败: {e}")
            continue
        for f in result.facts:
            f.setdefault("reliability", cls.default_reliability)
        facts.extend(result.facts)
        collections.update(result.collections)
        ctx.update(result.ctx)
        meta.update(result.meta)
        warnings.extend(result.warnings)

    meta.update({
        "stock": run_params.get("stock") or run_params.get("project") or department,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "warnings": warnings,
    })
    doc = {"meta": meta, "facts": facts, "collections": collections}

    crosscheck = None
    if "financial_dual_source" in (profile.get("crosschecks") or []):
        from datalayer.assemble import check_financial
        crosscheck = check_financial(run_params["stock"])
    return doc, crosscheck
