"""模板工作台路由：上传提取 → 草案查看/手动编辑 → 对话 patch → 回放/试跑 → 定稿。

工作区：studio_workspace/{job_id}/（上传样例、draft.yaml、提取报告、快照、对话记录）。
所有落盘 spec 均过 SpecV2 pydantic 校验；每次修改前自动快照。
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

from datalayer.settings import settings
from server import bus
from server.patchlib import apply_ops, diff

router = APIRouter(prefix="/api/studio")


def _ws() -> Path:
    return settings.resolve("studio_workspace")


def _job_dir(job_id: str) -> Path:
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise HTTPException(403, "非法任务 id")
    p = _ws() / job_id
    if not p.is_dir():
        raise HTTPException(404, "任务不存在")
    return p


def _load_spec(job_dir: Path) -> dict:
    p = job_dir / "draft.yaml"
    if not p.exists():
        raise HTTPException(404, "草案尚未生成")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _validate(spec_dict: dict) -> dict:
    """Schema 校验，返回可安全写盘的规范 dict（非法抛 422）。"""
    from template_factory.schema import SpecV2
    try:
        return json.loads(
            SpecV2.model_validate(spec_dict).model_dump_json())
    except Exception as e:  # noqa: BLE001 —— pydantic 错误原文报给页面/LLM
        raise HTTPException(422, f"Schema 校验失败：{str(e)[:600]}") from e


def _snapshot(job_dir: Path) -> None:
    p = job_dir / "draft.yaml"
    if not p.exists():
        return
    snaps = job_dir / "snapshots"
    snaps.mkdir(exist_ok=True)
    shutil.copy2(p, snaps / f"{datetime.now():%Y%m%d_%H%M%S}.yaml")


def _write_spec(job_dir: Path, spec_dict: dict) -> None:
    with open(job_dir / "draft.yaml", "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True, sort_keys=False)


# ---------- 提取 ----------

@router.get("/templates")
def list_templates() -> list[dict]:
    """config/report_types 下的 spec 清单（/run 页模板下拉用）。"""
    root = settings.resolve("config/report_types")
    return [{"name": f.stem, "file": f"config/report_types/{f.name}",
             "draft": f.stem.endswith("_draft")}
            for f in sorted(root.glob("*.yaml"))]


@router.get("/jobs_list")
def jobs_list() -> list[str]:
    """历史提取任务（新→旧），供工作台"打开历史任务"下拉。"""
    root = _ws()
    if not root.exists():
        return []
    return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True)[:30]


@router.post("/extract")
async def extract(files: list[UploadFile] = File(...),
                  report_type: str = "") -> dict:
    if not files:
        raise HTTPException(422, "请上传至少一个样例文件")
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    job_dir = _ws() / job_id
    (job_dir / "samples").mkdir(parents=True, exist_ok=True)
    names = []
    for f in files:
        safe = Path(f.filename or "sample.docx").name
        (job_dir / "samples" / safe).write_bytes(await f.read())
        names.append(str(job_dir / "samples" / safe))
    (job_dir / "meta.json").write_text(json.dumps(
        {"created_at": datetime.now().isoformat(timespec="seconds"),
         "samples": [Path(n).name for n in names]}, ensure_ascii=False),
        encoding="utf-8")

    out = str(job_dir / "draft.yaml")

    def fn(progress):
        from template_factory import extract as ext
        res = ext.run(names, out, report_type or None, progress=progress)
        # 左栏样例结构树：解析缓存（无 LLM，纯本地）
        from template_factory import parsers
        parsed_dir = job_dir / "parsed"
        parsed_dir.mkdir(exist_ok=True)
        for n in names:
            parsed = parsers.parse(n)
            (parsed_dir / (Path(n).stem + ".json")).write_text(
                json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
        return res

    import asyncio
    task = bus.start_job(fn, "extract", asyncio.get_running_loop())
    return {"job_id": job_id, "events_url": f"/api/studio/jobs/{task.id}/events"}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    t = bus.HUB.get(job_id)
    if not t:
        raise HTTPException(404, "任务不存在或服务已重启")
    q = t.subscribe()

    async def gen():
        try:
            while True:
                import asyncio as _aio
                try:
                    ev = await _aio.wait_for(q.get(), timeout=600)
                except _aio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "end":
                    break
        finally:
            t.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------- 草案读取 / 手动编辑 ----------

@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    d = _job_dir(job_id)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) \
        if (d / "meta.json").exists() else {}
    out = {
        "job_id": job_id, "meta": meta,
        "samples": sorted(p.name for p in (d / "samples").iterdir())
        if (d / "samples").exists() else [],
        "spec": _load_spec(d) if (d / "draft.yaml").exists() else None,
        "extraction_report": (d / "draft.extraction_report.md")
        .read_text(encoding="utf-8") if (d / "draft.extraction_report.md").exists() else None,
        "extractions": json.loads((d / "draft.extractions.json").read_text(encoding="utf-8"))
        if (d / "draft.extractions.json").exists() else None,
        "replay": json.loads((d / "replay_result.json").read_text(encoding="utf-8"))
        if (d / "replay_result.json").exists() else None,
        "dryrun": json.loads((d / "dryrun_result.json").read_text(encoding="utf-8"))
        if (d / "dryrun_result.json").exists() else None,
        "chat": json.loads((d / "chat.json").read_text(encoding="utf-8"))
        if (d / "chat.json").exists() else [],
        "finalized_to": meta.get("finalized_to"),
    }
    return out


@router.get("/jobs/{job_id}/sample/{filename}")
def get_sample(job_id: str, filename: str) -> dict:
    d = _job_dir(job_id)
    p = d / "parsed" / (Path(filename).stem + ".json")
    if not p.exists():
        raise HTTPException(404, "样例解析缓存不存在（提取完成后可用）")
    return json.loads(p.read_text(encoding="utf-8"))


class SpecIn(BaseModel):
    job_id: str
    spec: dict


@router.put("/spec")
def put_spec(body: SpecIn) -> dict:
    d = _job_dir(body.job_id)
    old = _load_spec(d)
    new = _validate(body.spec)
    _snapshot(d)
    _write_spec(d, new)
    return {"ok": True, "spec": new, "diff": diff(old, new)}


class SpecYamlIn(BaseModel):
    job_id: str
    text: str


@router.put("/spec-yaml")
def put_spec_yaml(body: SpecYamlIn) -> dict:
    """YAML 视图整份保存：解析失败/Schema 失败原样报回，不落盘。"""
    d = _job_dir(body.job_id)
    try:
        parsed = yaml.safe_load(body.text)
    except yaml.YAMLError as e:
        raise HTTPException(422, f"YAML 解析失败：{str(e)[:400]}") from e
    old = _load_spec(d)
    new = _validate(parsed)
    _snapshot(d)
    _write_spec(d, new)
    return {"ok": True, "spec": new, "diff": diff(old, new)}


# ---------- 对话 patch ----------

class PatchIn(BaseModel):
    job_id: str
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


@router.post("/patch")
def patch(body: PatchIn) -> dict:
    d = _job_dir(body.job_id)
    cur = _load_spec(d)
    from pipeline.llm import chat_json
    out = chat_json(
        PATCH_SYSTEM,
        f"【当前模板 JSON】\n{json.dumps(cur, ensure_ascii=False)}\n\n"
        f"【用户要求】\n{body.message}\n\n只输出 JSON。",
        schema_hint="只输出一个合法 JSON 对象。", max_tokens=4000)
    ops = out.get("ops") or []
    try:
        new = apply_ops(cur, ops)
        new = _validate(new)          # 只校验不落盘——确认后才应用
        d_out = diff(cur, new)
        err = None
    except HTTPException as e:
        new, d_out, err = None, [], e.detail
    except ValueError as e:
        new, d_out, err = None, [], str(e)
    # 记录对话
    chat_path = d / "chat.json"
    chat = json.loads(chat_path.read_text(encoding="utf-8")) if chat_path.exists() else []
    chat.append({"role": "user", "text": body.message, "ts": datetime.now().isoformat(timespec="seconds")})
    chat.append({"role": "assistant", "text": out.get("reply", ""), "ops": ops,
                 "diff": d_out, "error": err,
                 "ts": datetime.now().isoformat(timespec="seconds")})
    chat_path.write_text(json.dumps(chat, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"reply": out.get("reply", ""), "ops": ops, "diff": d_out,
            "error": err, "chat": chat}


class ApplyIn(BaseModel):
    job_id: str
    ops: list[dict]


@router.post("/apply")
def apply(body: ApplyIn) -> dict:
    d = _job_dir(body.job_id)
    cur = _load_spec(d)
    try:
        new = apply_ops(cur, body.ops)
        new = _validate(new)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    _snapshot(d)
    _write_spec(d, new)
    return {"ok": True, "spec": new, "diff": diff(cur, new)}


# ---------- 回放 / 试跑 ----------

class ReplayIn(BaseModel):
    job_id: str
    sample: str
    rounds: int = 2


@router.post("/replay")
async def replay(body: ReplayIn) -> dict:
    d = _job_dir(body.job_id)
    sample = (d / "samples" / Path(body.sample).name)
    if not sample.exists():
        raise HTTPException(404, "样例不存在")
    spec_path = str(d / "draft.yaml")

    def fn(progress):
        from template_factory import replay as rp
        _p = lambda stage, msg, data=None: progress(stage, msg, data)  # noqa: E731
        res = rp.run_rounds(spec_path, str(sample), max(1, body.rounds))
        _p("result", f"回放结论：{res['verdict']}", res)
        (d / "replay_result.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return res

    import asyncio
    task = bus.start_job(fn, "replay", asyncio.get_running_loop())
    return {"job_id": task.id, "events_url": f"/api/studio/jobs/{task.id}/events"}


class DryrunIn(BaseModel):
    job_id: str
    department: str
    params: dict[str, str] = {}


@router.post("/dryrun")
async def dryrun(body: DryrunIn) -> dict:
    d = _job_dir(body.job_id)
    spec_path = str(d / "draft.yaml")

    def fn(progress):
        from template_factory import dryrun as dr
        res = dr.run(spec_path, body.department, body.params,
                     progress=lambda m: progress("dryrun", m))
        (d / "dryrun_result.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        return res

    import asyncio
    task = bus.start_job(fn, "dryrun", asyncio.get_running_loop())
    return {"job_id": task.id, "events_url": f"/api/studio/jobs/{task.id}/events"}


# ---------- 定稿 ----------

class FinalizeIn(BaseModel):
    job_id: str
    name: str


@router.post("/finalize")
def finalize(body: FinalizeIn) -> dict:
    import re as _re
    name = _re.sub(r"[^0-9a-zA-Z_]+", "_", body.name).strip("_").lower()
    if not name:
        raise HTTPException(422, "模板名不合法")
    d = _job_dir(body.job_id)
    spec = _validate(_load_spec(d))
    target_root = settings.resolve("config/report_types")
    target = target_root / f"{name}.yaml"
    if target.exists():   # 定稿留痕：旧版进快照（git 二道保险）
        snaps = target_root / "versions" / name
        snaps.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, snaps / f"{datetime.now():%Y%m%d_%H%M%S}.yaml")
    with open(target, "w", encoding="utf-8") as f:
        yaml.dump(spec, f, allow_unicode=True, sort_keys=False)
    meta_path = d / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["finalized_to"] = str(target.relative_to(settings.resolve(".")))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": meta["finalized_to"]}
