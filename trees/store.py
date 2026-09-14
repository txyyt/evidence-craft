"""树存储：CRUD / 版本快照 / 指纹 / 统一操作日志 / 回滚。

tree.yaml 结构 = {"tree": 元信息块} + SpecV2 字段。元信息块：
  {id, name, subject, status(draft|confirmed), created_at, updated_at,
   version, provenance}——SpecV2 加载时忽略未知键，旧读取方零影响。
"""

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from datalayer.settings import settings
from template_factory.schema import SpecV2


def _safe_id(tree_id: str) -> str:
    if (not tree_id or "/" in tree_id or "\\" in tree_id or ".." in tree_id
            or tree_id.startswith(".") or " " in tree_id):
        raise ValueError(f"非法结构树 id：{tree_id!r}")
    return tree_id


def tree_dir(tree_id: str) -> Path:
    return settings.resolve(f"config/trees/{_safe_id(tree_id)}")


def tree_path(tree_id: str) -> Path:
    return tree_dir(tree_id) / "tree.yaml"


def exists(tree_id: str) -> bool:
    return tree_path(tree_id).exists()


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   default=str).encode("utf-8")).hexdigest()[:12]


def synthetic_sources(meta: dict[str, Any], spec: SpecV2) -> dict[str, Any]:
    """树 → 合成 sources dict（run_pipeline/planner 消费的形状：无静态绑定，
    取数全靠 planner 现场计划或 --plan 文件）。"""
    return {"name": meta.get("name") or meta.get("id") or spec.report_type,
            "description": spec.description,
            "status": meta.get("status", "draft"),
            "params_schema": {}, "vocabulary": {}, "features": {},
            "crosschecks": [], "bindings": []}


# ---------- 读写 ----------

def _dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True, sort_keys=False)
    tmp.replace(path)


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_tree(tree_id: str) -> dict[str, Any]:
    """读取 → {meta, spec(SpecV2), fingerprint, path}。文件不存在/不合规抛异常。"""
    p = tree_path(tree_id)
    if not p.exists():
        raise ValueError(f"结构树不存在：{tree_id}")
    payload = _read_yaml(p)
    meta = dict(payload.pop("tree", {}) or {})
    meta.setdefault("id", tree_id)
    try:
        spec = SpecV2.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"结构树 {tree_id} 不符合 Spec v2：{e.errors()[0]}") from e
    return {"meta": meta, "spec": spec, "fingerprint": fingerprint(payload),
            "path": str(p)}


def load_spec_dict(tree_id: str) -> dict[str, Any]:
    """同 load_tree 但返回原始 dict（不构建 SpecV2）——编辑器回显用。"""
    p = tree_path(tree_id)
    if not p.exists():
        raise ValueError(f"结构树不存在：{tree_id}")
    payload = _read_yaml(p)
    meta = dict(payload.pop("tree", {}) or {})
    meta.setdefault("id", tree_id)
    return {"meta": meta, "spec_dict": payload,
            "fingerprint": fingerprint(payload), "path": str(p)}


def validate_payload(spec_dict: dict[str, Any]) -> SpecV2:
    """编辑器提交的 spec dict → 校验。失败抛 ValueError（中文摘要）。"""
    try:
        return SpecV2.model_validate(spec_dict)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc") or [])
        raise ValueError(f"结构不合规（{loc}）：{first.get('msg')}") from e


def _spec_dump(spec: SpecV2) -> dict[str, Any]:
    out = spec.model_dump(mode="json", exclude_none=True)
    return out


def save_tree(tree_id: str, spec_dict: dict[str, Any],
              meta_updates: dict[str, Any] | None = None,
              actor: str = "manual", summary: str = "",
              op: dict[str, Any] | None = None) -> dict[str, Any]:
    """校验并保存（版本 +1、快照、日志）。新建（文件不存在）走初始化分支。"""
    _safe_id(tree_id)
    spec = validate_payload(spec_dict)
    p = tree_path(tree_id)
    fresh = not p.exists()
    now = datetime.now().isoformat(timespec="seconds")
    if fresh:
        meta = {"id": tree_id,
                "name": (meta_updates or {}).get("name") or tree_id,
                "subject": (meta_updates or {}).get("subject") or tree_id,
                "status": "draft", "version": 0, "created_at": now,
                "provenance": (meta_updates or {}).get("provenance")
                or {"kind": "manual"}}
        meta.update({k: v for k, v in (meta_updates or {}).items()
                     if k not in ("id", "version", "created_at")})
    else:
        old = _read_yaml(p)
        meta = dict(old.pop("tree", {}) or {})
        meta.update({k: v for k, v in (meta_updates or {}).items()
                     if k not in ("id", "created_at")})
    meta["updated_at"] = now
    meta["version"] = int(meta.get("version") or 0) + 1

    payload = {"tree": meta, **_spec_dump(spec)}
    _dump_yaml(p, payload)
    vdir = tree_dir(tree_id) / "versions"
    _dump_yaml(vdir / f"v{meta['version']}.yaml", payload)
    fp = fingerprint({k: v for k, v in payload.items() if k != "tree"})
    _log(tree_id, {"ts": now, "actor": actor,
                   "op": op or {"action": "create" if fresh else "save"},
                   "summary": summary or ("新建结构树" if fresh else "保存修改"),
                   "version_after": meta["version"], "fingerprint": fp})
    return {"meta": meta, "fingerprint": fp}


def _log(tree_id: str, entry: dict[str, Any]) -> None:
    log_path = tree_dir(tree_id) / "ops_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------- 列表 / 版本 / 日志 / 回滚 / 删除 ----------

def list_trees() -> list[dict[str, Any]]:
    root = settings.resolve("config/trees")
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "tree.yaml").exists():
            continue
        try:
            loaded = load_tree(d.name)
        except (ValueError, OSError):
            continue
        meta, spec = loaded["meta"], loaded["spec"]
        out.append({
            "id": d.name, "name": meta.get("name") or d.name,
            "subject": meta.get("subject", ""),
            "status": meta.get("status", "draft"),
            "genre": spec.genre, "style_card": spec.style_card,
            "sections": len(spec.sections),
            "n_data_needs": sum(1 for s in spec.sections if s.data_needs),
            "version": meta.get("version", 0),
            "fingerprint": loaded["fingerprint"],
            "updated_at": meta.get("updated_at", ""),
        })
    return out


def versions(tree_id: str) -> list[dict[str, Any]]:
    vdir = tree_dir(tree_id) / "versions"
    if not vdir.exists():
        return []
    out = []
    for f in sorted(vdir.glob("v*.yaml"),
                    key=lambda x: int(x.stem[1:]) if x.stem[1:].isdigit() else 0):
        try:
            payload = _read_yaml(f)
        except OSError:
            continue
        meta = payload.get("tree") or {}
        out.append({"version": meta.get("version"),
                    "updated_at": meta.get("updated_at"),
                    "status": meta.get("status"),
                    "file": f.name})
    return out


def ops_log(tree_id: str, limit: int = 100) -> list[dict[str, Any]]:
    log_path = tree_dir(tree_id) / "ops_log.jsonl"
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries[-limit:]


def rollback(tree_id: str, version: int) -> dict[str, Any]:
    """恢复某版本快照为当前版（版本继续 +1，actor=rollback 可追溯）。"""
    src = tree_dir(tree_id) / "versions" / f"v{int(version)}.yaml"
    if not src.exists():
        raise ValueError(f"版本不存在：v{version}")
    payload = _read_yaml(src)
    meta = dict(payload.pop("tree", {}) or {})
    spec_dict = payload
    validate_payload(spec_dict)     # 快照也过校验，防手改损坏
    now = datetime.now().isoformat(timespec="seconds")
    meta["updated_at"] = now
    cur = load_tree(tree_id)["meta"].get("version", 0)
    meta["version"] = int(cur) + 1
    new_payload = {"tree": meta, **spec_dict}
    _dump_yaml(tree_path(tree_id), new_payload)
    vdir = tree_dir(tree_id) / "versions"
    _dump_yaml(vdir / f"v{meta['version']}.yaml", new_payload)
    fp = fingerprint(spec_dict)
    _log(tree_id, {"ts": now, "actor": "rollback",
                   "op": {"action": "rollback", "to_version": int(version)},
                   "summary": f"回滚到 v{version}",
                   "version_after": meta["version"], "fingerprint": fp})
    return {"meta": meta, "fingerprint": fp}


def delete_tree(tree_id: str) -> None:
    d = tree_dir(tree_id)
    if not d.exists():
        raise ValueError(f"结构树不存在：{tree_id}")
    shutil.rmtree(d)


# ---------- 数据计划（plans/）----------

def save_plan(tree_id: str, plan: dict[str, Any]) -> str:
    """数据采集计划落 plans/<ts>.json，返回文件名（--plan 参数用）。"""
    d = tree_dir(tree_id) / "plans"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(d / name, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return name


def load_plan(tree_id: str, plan_name: str) -> dict[str, Any]:
    """计划名（plans/ 下文件名，可省 .json 后缀）或绝对/项目相对路径 → 计划 dict。"""
    if "/" in plan_name or "\\" in plan_name or ".." in plan_name:
        candidates = [settings.resolve(plan_name)]
    else:
        base = tree_dir(tree_id) / "plans" / plan_name
        candidates = [base.with_suffix(".json"), base]
    p = next((c for c in candidates if c.exists()), None)
    if p is None:
        raise ValueError(f"数据计划不存在：{plan_name}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_plan_file(tree_id: str, plan_name: str, plan: dict[str, Any]) -> None:
    """裁决后写回计划文件（保留原文件名）。"""
    if "/" in plan_name or "\\" in plan_name or ".." in plan_name:
        p = settings.resolve(plan_name)
    else:
        p = tree_dir(tree_id) / "plans" / f"{plan_name}.json"
        if not p.exists():
            p = tree_dir(tree_id) / "plans" / plan_name
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def tree_needs(tree_id: str) -> list[str]:
    """树各节 data_needs 汇总去重（规划器 needs_coverage 输入）。"""
    spec = load_tree(tree_id)["spec"]
    out: list[str] = []
    for s in spec.sections:
        for n in s.data_needs:
            if n not in out:
                out.append(n)
    return out


# ---------- 缺口回执裁决（四选一：search|provide_folder|qualitative|drop） ----------

_DECISIONS = ("search", "provide_folder", "qualitative", "drop")


def apply_decisions(tree_id: str, plan_name: str,
                    decisions: list[dict[str, str]]) -> dict[str, Any]:
    """对计划 needs_coverage 的缺口逐条裁决并落效：
    - search：追加 web 查询（need 作查询词），status→search
    - provide_folder：记录 needs_folder（生成时用户给资料文件夹），status→search
    - qualitative：所在节 brief 追加定性声明并摘除该需求，status→qualitative
    - drop：从所在节摘除该需求，status→dropped
    返回更新后的 plan 与 coverage。"""
    plan = load_plan(tree_id, plan_name)
    coverage = plan.get("needs_coverage") or []
    by_need = {c.get("need"): c for c in coverage}
    loaded = load_spec_dict(tree_id)
    spec_dict, meta = loaded["spec_dict"], dict(loaded["meta"])
    tree_changed = False
    recorded = plan.setdefault("decisions", [])

    for d in decisions or []:
        need, decision = str(d.get("need") or ""), str(d.get("decision") or "")
        if decision not in _DECISIONS or need not in by_need:
            continue
        if decision in ("qualitative", "drop"):
            for s in spec_dict.get("sections", []):
                needs = s.get("data_needs") or []
                if need in needs:
                    needs.remove(need)
                    s["data_needs"] = needs
                    if decision == "qualitative":
                        style = s.get("style") or ""
                        if "定性" not in style:
                            s["style"] = (style + "；本节定性论述，不引用具体数字"
                                          ).strip("；")
                    tree_changed = True
        elif decision == "search":
            queries = plan.setdefault("web", [])
            if not any(q.get("need") == f"web_{need}" for q in queries):
                queries.append({"need": f"web_{need}", "query": need,
                                "top_k": 6, "fetch_pages": 3})
        elif decision == "provide_folder":
            plan["needs_folder"] = True
        by_need[need]["status"] = ("search" if decision in ("search",
                                                            "provide_folder")
                                   else "qualitative" if decision == "qualitative"
                                   else "dropped")
        by_need[need]["decision"] = decision
        recorded.append({"need": need, "decision": decision,
                         "ts": datetime.now().isoformat(timespec="seconds")})

    plan["needs_coverage"] = list(by_need.values())
    save_plan_file(tree_id, plan_name, plan)
    if tree_changed:
        save_tree(tree_id, spec_dict, meta, actor="system",
                  summary="缺口裁决（定性/砍掉）", op={"action": "gap_decision"})
    return {"plan": plan, "coverage": plan["needs_coverage"]}


def coverage_gate(plan: dict[str, Any]) -> dict[str, Any]:
    """确认门槛：无未裁决 gap；若全树无任何 covered/search 来源，要求二次确认。"""
    coverage = plan.get("needs_coverage") or []
    unresolved = [c["need"] for c in coverage if c.get("status") == "gap"]
    usable = [c for c in coverage if c.get("status") in ("covered", "search")]
    return {"ok": not unresolved,
            "unresolved": unresolved,
            "all_qualitative": not usable,
            "n_usable": len(usable)}
