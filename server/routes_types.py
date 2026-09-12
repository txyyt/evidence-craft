"""报告类型管理路由：CRUD / 提取 / 结构编辑与 patch / 回放与试跑 / 版本 / 演示载入。

目录结构：config/report_types/<id>/
  report.yaml（Spec v2 结构） sources.yaml（名称/状态/参数/数据绑定）
  versions/report/（版本留痕，自动清理保留 20 份） samples/ parsed/ chat.json
  replay_history.json dryrun_result.json extraction_report.md
"""

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from datalayer import registry
from datalayer.settings import settings
from server import bus
from server.patchlib import apply_ops, diff

router = APIRouter(prefix="/api/types")

MAX_REPORT_VERSIONS = 20


# ---------- 工具 ----------

def _tdir(type_id: str) -> Path:
    try:
        d = registry.type_dir(type_id)
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    if not d.is_dir():
        raise HTTPException(404, f"报告类型不存在：{type_id}")
    return d


def _load_spec_dict(tdir: Path) -> dict | None:
    p = tdir / "report.yaml"
    if not p.exists():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _validate_spec(spec_dict: dict) -> dict:
    from template_factory.schema import SpecV2
    try:
        return json.loads(SpecV2.model_validate(spec_dict).model_dump_json())
    except Exception as e:  # noqa: BLE001 —— pydantic 错误原文报给页面/LLM
        raise HTTPException(422, f"Schema 校验失败：{str(e)[:600]}") from e


def _snapshot_report(tdir: Path) -> None:
    p = tdir / "report.yaml"
    if not p.exists():
        return
    vdir = tdir / "versions" / "report"
    vdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, vdir / f"{datetime.now():%Y%m%d_%H%M%S}.yaml")
    # 只保留最近 20 份
    snaps = sorted(vdir.glob("*.yaml"))
    for old in snaps[:-MAX_REPORT_VERSIONS]:
        old.unlink()


def _write_report(tdir: Path, spec_dict: dict) -> None:
    with open(tdir / "report.yaml", "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True, sort_keys=False)


def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _events_response(task: bus.RunTask) -> StreamingResponse:
    q = task.subscribe()

    async def gen():
        import asyncio as _aio
        try:
            while True:
                try:
                    ev = await _aio.wait_for(q.get(), timeout=600)
                except _aio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "end":
                    break
        finally:
            task.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------- CRUD ----------

class NewTypeIn(BaseModel):
    id: str
    name: str
    description: str = ""


@router.post("")
def create_type(body: NewTypeIn) -> dict:
    import re as _re
    type_id = _re.sub(r"[^0-9a-zA-Z_]+", "_", body.id).strip("_").lower()
    if not type_id:
        raise HTTPException(422, "id 不合法（需英文/数字/下划线）")
    if registry.type_exists(type_id):
        raise HTTPException(409, f"报告类型已存在：{type_id}")
    d = registry.type_dir(type_id)
    (d / "samples").mkdir(parents=True)
    sources = {"name": body.name or type_id, "description": body.description,
               "status": "draft", "params_schema": {}, "vocabulary": {},
               "features": {}, "crosschecks": [], "judge_reference": "",
               "bindings": []}
    registry.save_sources(type_id, sources)
    return {"ok": True, "id": type_id}


@router.get("")
def list_types() -> list[dict]:
    return registry.list_types()


@router.get("/{type_id}")
def type_detail(type_id: str) -> dict:
    tdir = _tdir(type_id)
    sources = registry.load_sources(type_id)
    samples_dir = tdir / "samples"
    return {
        "id": type_id,
        "name": sources.get("name") or type_id,
        "description": sources.get("description", ""),
        "status": sources.get("status", "draft"),
        "params_schema": sources.get("params_schema") or {},
        "vocabulary": sources.get("vocabulary") or {},
        "features": sources.get("features") or {},
        "crosschecks": sources.get("crosschecks") or [],
        "judge_reference": sources.get("judge_reference") or "",
        "bindings": sources.get("bindings") or [],
        "spec": _load_spec_dict(tdir),
        "has_draft": (tdir / "report.draft.yaml").exists(),
        "draft_spec": _load_draft(tdir),
        "fingerprint": registry.template_fingerprint(type_id),
        "samples": sorted(p.name for p in samples_dir.iterdir())
        if samples_dir.exists() else [],
        "extraction_report": (tdir / "report.extraction_report.md").read_text(
            encoding="utf-8") if (tdir / "report.extraction_report.md").exists() else None,
        "extractions": _read_json(tdir / "extractions.json", None),
        "replay_history": _read_json(tdir / "replay_history.json", []),
        "last_replay": _read_json(tdir / "replay_history.json", [{}])[0]
        if (tdir / "replay_history.json").exists() else None,
        "dryrun": _read_json(tdir / "dryrun_result.json", None),
        "chat": _read_json(tdir / "chat.json", []),
    }


class SourcesIn(BaseModel):
    sources: dict


@router.put("/{type_id}/sources")
def save_sources(type_id: str, body: SourcesIn) -> dict:
    tdir = _tdir(type_id)
    cur = registry.load_sources(type_id)
    src = body.sources
    # status 只经 /status 端点流转，避免不一致
    allowed = ("name", "description", "params_schema", "vocabulary",
               "features", "crosschecks", "judge_reference", "bindings")
    out = {k: src.get(k) for k in allowed}
    for k in allowed:
        if out[k] is None and k in cur:
            out[k] = cur[k]
    out["status"] = cur.get("status", "draft")
    registry.save_sources(type_id, out)
    return {"ok": True}


class StatusIn(BaseModel):
    status: str  # draft | verified | published


@router.post("/{type_id}/status")
def set_status(type_id: str, body: StatusIn) -> dict:
    if body.status not in ("draft", "verified", "published"):
        raise HTTPException(422, "非法状态")
    tdir = _tdir(type_id)
    src = registry.load_sources(type_id)
    if body.status == "verified":
        history = _read_json(tdir / "replay_history.json", [])
        last = history[0] if history else None
        if not last or last.get("verdict") not in ("PASS", "PASS_WITH_WARN"):
            raise HTTPException(422, "尚未通过回放验证（需先完成一次回放且判定通过）")
    if body.status == "published" and not (tdir / "report.yaml").exists():
        raise HTTPException(422, "尚无报告结构（report.yaml），不能发布")
    src["status"] = body.status
    registry.save_sources(type_id, src)
    return {"ok": True, "status": body.status}


@router.delete("/{type_id}")
def delete_type(type_id: str) -> dict:
    tdir = _tdir(type_id)
    shutil.rmtree(tdir)
    return {"ok": True}


class CopyIn(BaseModel):
    new_id: str
    new_name: str = ""


@router.post("/{type_id}/copy")
def copy_type(type_id: str, body: CopyIn) -> dict:
    import re as _re
    new_id = _re.sub(r"[^0-9a-zA-Z_]+", "_", body.new_id).strip("_").lower()
    if not new_id:
        raise HTTPException(422, "新 id 不合法")
    src_dir = _tdir(type_id)
    if registry.type_exists(new_id):
        raise HTTPException(409, f"报告类型已存在：{new_id}")
    dst = registry.type_dir(new_id)
    shutil.copytree(src_dir, dst,
                    ignore=shutil.ignore_patterns("parsed", "__pycache__"))
    src = registry.load_sources(new_id)
    src["name"] = body.new_name or f"{src.get('name', type_id)} 副本"
    src["status"] = "draft"
    registry.save_sources(new_id, src)
    return {"ok": True, "id": new_id}


# ---------- 提取 ----------

@router.post("/load-demo")
def load_demo() -> dict:
    demo_root = settings.resolve("config/demo_types")
    created, skipped = [], []
    if demo_root.exists():
        for d in sorted(p for p in demo_root.iterdir() if p.is_dir()):
            if registry.type_exists(d.name):
                skipped.append(d.name)
                continue
            shutil.copytree(d, registry.type_dir(d.name))
            created.append(d.name)
    return {"created": created, "skipped": skipped}


@router.post("/{type_id}/extract")
async def extract(type_id: str, files: list[UploadFile] = File(...),
                  report_type: str = "") -> dict:
    tdir = _tdir(type_id)
    if (tdir / "report.yaml").exists():
        # 定稿结构不允许被提取覆盖（覆盖即毁掉已精修的 spec）
        raise HTTPException(409, "该类型已有 report.yaml（已定稿）。"
                                 "请新建一个空类型后再上传范文提取，避免覆盖。")
    samples_dir = tdir / "samples"
    samples_dir.mkdir(exist_ok=True)
    names = []
    for f in files:
        safe = Path(f.filename or "sample.txt").name
        (samples_dir / safe).write_bytes(await f.read())
        names.append(str(samples_dir / safe))
    # 提取结果先落草稿，用户在页面上确认/微调结构后才定稿为 report.yaml
    out = str(tdir / "report.draft.yaml")

    def fn(progress):
        from template_factory import extract as ext
        res = ext.run(names, out, report_type or None, progress=progress)
        _write_json(tdir / "extractions.json", res.get("extractions"))
        from template_factory import parsers
        parsed_dir = tdir / "parsed"
        parsed_dir.mkdir(exist_ok=True)
        for n in names:
            parsed = parsers.parse(n)
            (parsed_dir / (Path(n).stem + ".json")).write_text(
                json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
        return res

    import asyncio
    task = bus.start_job(fn, "extract", asyncio.get_running_loop())
    return {"job_id": task.id, "events_url": f"/api/types/{type_id}/jobs/{task.id}/events"}


# ---------- 结构确认（提取草稿 → 用户确认 → 定稿 report.yaml）----------

def _load_draft(tdir: Path) -> dict | None:
    p = tdir / "report.draft.yaml"
    if not p.exists():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8"))


@router.get("/{type_id}/structure-draft")
def get_structure_draft(type_id: str) -> dict:
    draft = _load_draft(_tdir(type_id))
    return {"exists": draft is not None, "spec": draft}


class StructureConfirmIn(BaseModel):
    spec: dict | None = None      # 缺省用草稿原样定稿；传编辑后的结构则以其为准


@router.post("/{type_id}/structure-confirm")
def structure_confirm(type_id: str, body: StructureConfirmIn) -> dict:
    tdir = _tdir(type_id)
    if (tdir / "report.yaml").exists():
        raise HTTPException(409, "该类型已有定稿 report.yaml，无需重复确认")
    spec_dict = body.spec if body.spec else _load_draft(tdir)
    if not spec_dict:
        raise HTTPException(404, "没有可确认的结构草稿（先上传范文提取）")
    validated = _validate_spec(spec_dict)
    _write_report(tdir, validated)
    return {"ok": True, "spec": validated}


@router.delete("/{type_id}/structure-draft")
def discard_structure_draft(type_id: str) -> dict:
    p = _tdir(type_id) / "report.draft.yaml"
    if p.exists():
        p.unlink()
    return {"ok": True}


@router.get("/{type_id}/jobs/{job_id}/events")
async def job_events(type_id: str, job_id: str) -> StreamingResponse:
    t = bus.HUB.get(job_id)
    if not t:
        raise HTTPException(404, "任务不存在或服务已重启")
    return _events_response(t)


@router.get("/{type_id}/sample/{filename}")
def get_sample(type_id: str, filename: str) -> dict:
    tdir = _tdir(type_id)
    p = tdir / "parsed" / (Path(filename).stem + ".json")
    if not p.exists():
        raise HTTPException(404, "样例解析缓存不存在（提取完成后可用）")
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- 结构编辑 ----------

class SpecIn(BaseModel):
    spec: dict


@router.put("/{type_id}/report")
def put_report(type_id: str, body: SpecIn) -> dict:
    tdir = _tdir(type_id)
    old = _load_spec_dict(tdir)
    new = _validate_spec(body.spec)
    _snapshot_report(tdir)
    _write_report(tdir, new)
    return {"ok": True, "spec": new, "diff": diff(old or {}, new)}


class SpecYamlIn(BaseModel):
    text: str


@router.put("/{type_id}/report-yaml")
def put_report_yaml(type_id: str, body: SpecYamlIn) -> dict:
    tdir = _tdir(type_id)
    try:
        parsed = yaml.safe_load(body.text)
    except yaml.YAMLError as e:
        raise HTTPException(422, f"YAML 解析失败：{str(e)[:400]}") from e
    old = _load_spec_dict(tdir)
    new = _validate_spec(parsed)
    _snapshot_report(tdir)
    _write_report(tdir, new)
    return {"ok": True, "spec": new, "diff": diff(old or {}, new)}


# ---------- 对话修改 ----------

class PatchIn(BaseModel):
    message: str


PATCH_SYSTEM = """你是报告模板工程师助手。用户会提出对报告模板（Spec v2 JSON）的修改要求，
你输出"结构化操作列表"（不是全文重写）。可用操作：
  {"op": "set", "path": "sections[0].title", "value": <新值>}
  {"op": "del", "path": "sections[2].check.count"}
  {"op": "insert", "path": "sections", "value": {<完整章节对象>}}
  {"op": "insert", "path": "sections[0].view_slots", "value": {<完整槽位对象>}}
路径语法：点号+下标，如 sections[1].view_slots[0].fewshot。
只允许修改这些顶层键：description/title_style/writing_rules/forbidden_words/
controlled_vocab/check_rules/judge_reference/disclaimer/sections/tables。
章节对象字段（务必给全必填项）：id/title/kind 必填；views 需 n_views/view_slots
（每槽位 id/brief/data_needs 必填，fewshot 可 null）/view_style；table 需 table
（表格 id，且须同步在 tables 列表加对应条目 {id, renderer: consensus_pe|generic_rows,
columns}）与 style；risk 可选 strategy(mirror|enumerate)/style/check{count,
item_suffix,min_shaped}。数值区间一律 [下限,上限] 两元素数组。
要求：改动最小化（不要动与请求无关的字段）；用户要求含糊时按对模板最合理的
解释处理并在 reply 里说明。只输出 JSON：
{"reply": "<给用户的一句话说明>", "ops": [ ... ]}"""


@router.post("/{type_id}/patch")
def patch(type_id: str, body: PatchIn) -> dict:
    tdir = _tdir(type_id)
    cur = _load_spec_dict(tdir) or {}
    from pipeline.llm import chat_json
    out = chat_json(
        PATCH_SYSTEM,
        f"【当前模板 JSON】\n{json.dumps(cur, ensure_ascii=False)}\n\n"
        f"【用户要求】\n{body.message}\n\n只输出 JSON。",
        schema_hint="只输出一个合法 JSON 对象。", max_tokens=4000)
    ops = out.get("ops") or []
    try:
        new = apply_ops(cur, ops) if cur else None
        if new is not None:
            new = _validate_spec(new)
        d_out = diff(cur, new) if new is not None else []
        err = None
    except HTTPException as e:
        new, d_out, err = None, [], e.detail
    except ValueError as e:
        new, d_out, err = None, [], str(e)
    chat_path = tdir / "chat.json"
    chat = _read_json(chat_path, [])
    chat.append({"role": "user", "text": body.message,
                 "ts": datetime.now().isoformat(timespec="seconds")})
    chat.append({"role": "assistant", "text": out.get("reply", ""), "ops": ops,
                 "diff": d_out, "error": err,
                 "ts": datetime.now().isoformat(timespec="seconds")})
    _write_json(chat_path, chat)
    return {"reply": out.get("reply", ""), "ops": ops, "diff": d_out,
            "error": err, "chat": chat}


class ApplyIn(BaseModel):
    ops: list[dict]


@router.post("/{type_id}/apply")
def apply(type_id: str, body: ApplyIn) -> dict:
    tdir = _tdir(type_id)
    cur = _load_spec_dict(tdir) or {}
    try:
        new = apply_ops(cur, body.ops)
        new = _validate_spec(new)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    _snapshot_report(tdir)
    _write_report(tdir, new)
    return {"ok": True, "spec": new, "diff": diff(cur, new)}


# ---------- 数据来源 ----------

class BindingTestIn(BaseModel):
    index: int
    params: dict[str, str] = {}


@router.post("/{type_id}/bindings/test")
def bindings_test(type_id: str, body: BindingTestIn) -> dict:
    """单绑定测试：$ctx 链累积执行到目标绑定，返回事实数/样例/解析后参数。"""
    try:
        return registry.test_binding(type_id, body.index, body.params)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


# ---------- 回放 / 试跑 ----------

class ReplayIn(BaseModel):
    sample: str
    rounds: int = 2


@router.post("/{type_id}/replay")
async def replay(type_id: str, body: ReplayIn) -> dict:
    tdir = _tdir(type_id)
    sample = tdir / "samples" / Path(body.sample).name
    if not sample.exists():
        raise HTTPException(404, "样例不存在")
    spec_path = str(tdir / "report.yaml")

    def fn(progress):
        from template_factory import replay as rp
        res = rp.run_rounds(spec_path, str(sample), max(1, body.rounds))
        progress("result", f"回放结论：{res['verdict']}", res)
        # 追加回放历史（改进闭环：判定/字数合规/未对账数）
        history = _read_json(tdir / "replay_history.json", [])
        n_ok = sum(1 for r in res.get("length", []) if r["status"] == "ok")
        history.insert(0, {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "sample": Path(body.sample).name,
            "rounds_used": body.rounds,
            "verdict": res["verdict"],
            "title": res.get("title", ""),
            "n_facts": res.get("n_facts"),
            "length_ok": n_ok,
            "length_total": len(res.get("length") or []),
            "unknown_total": sum(len(v) for v in
                                 (res.get("unknown_by_slot") or {}).values()),
            "detail": {k: res.get(k) for k in
                       ("length", "unknown_by_slot", "cited_missing")},
        })
        _write_json(tdir / "replay_history.json", history[:50])
        # 状态机：草稿 + 回放通过 → 已验证
        src = registry.load_sources(type_id)
        if res["verdict"] in ("PASS", "PASS_WITH_WARN") \
                and src.get("status", "draft") == "draft":
            src["status"] = "verified"
            registry.save_sources(type_id, src)
        return res

    import asyncio
    task = bus.start_job(fn, "replay", asyncio.get_running_loop())
    return {"job_id": task.id, "events_url": f"/api/types/{type_id}/jobs/{task.id}/events"}


class DryrunIn(BaseModel):
    params: dict[str, str] = {}


@router.post("/{type_id}/dryrun")
async def dryrun(type_id: str, body: DryrunIn) -> dict:
    tdir = _tdir(type_id)
    spec_path = str(tdir / "report.yaml")

    def fn(progress):
        from template_factory import dryrun as dr
        res = dr.run(spec_path, type_id, body.params,
                     progress=lambda m: progress("dryrun", m))
        _write_json(tdir / "dryrun_result.json", res)
        return res

    import asyncio
    task = bus.start_job(fn, "dryrun", asyncio.get_running_loop())
    return {"job_id": task.id, "events_url": f"/api/types/{type_id}/jobs/{task.id}/events"}


# ---------- 版本 ----------

@router.get("/{type_id}/versions")
def versions(type_id: str) -> dict:
    tdir = _tdir(type_id)
    vdir = tdir / "versions" / "report"
    files = sorted(vdir.glob("*.yaml"), reverse=True) if vdir.exists() else []
    return {"current": "report.yaml",
            "versions": [{"file": v.name,
                          "ts": datetime.fromtimestamp(v.stat().st_mtime)
                          .isoformat(timespec="seconds")} for v in files]}


def _resolve_version_side(type_id: str, side: str) -> str:
    tdir = _tdir(type_id)
    if side == "current":
        return (tdir / "report.yaml").read_text(encoding="utf-8")
    if "/" in side or "\\" in side or ".." in side:
        raise HTTPException(403, "非法版本文件名")
    p = tdir / "versions" / "report" / side
    if not p.exists():
        raise HTTPException(404, f"版本不存在：{side}")
    return p.read_text(encoding="utf-8")


@router.get("/{type_id}/versions/diff")
def versions_diff(type_id: str, a: str = "current", b: str = "current") -> dict:
    import difflib
    la = _resolve_version_side(type_id, a).splitlines()
    lb = _resolve_version_side(type_id, b).splitlines()
    dl = list(difflib.unified_diff(la, lb, fromfile=a, tofile=b, lineterm=""))
    return {"diff": "\n".join(dl) or "（两版完全一致）",
            "changed": bool([x for x in dl if x[:1] in "+-"
                             and x[:3] not in ("+++", "---")])}


class RollbackIn(BaseModel):
    version: str


@router.post("/{type_id}/versions/rollback")
def rollback(type_id: str, body: RollbackIn) -> dict:
    tdir = _tdir(type_id)
    _snapshot_report(tdir)   # 当前版先留痕
    src = tdir / "versions" / "report" / body.version
    if not src.exists():
        raise HTTPException(404, f"版本不存在：{body.version}")
    shutil.copy2(src, tdir / "report.yaml")
    return {"ok": True, "restored_from": body.version}
