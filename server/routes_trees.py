"""结构树路由：模板工作台后端（列表/创建/详情/保存/版本/回滚/日志/删除）。

树 = Spec v2 + tree: 元信息块（trees/store.py）。保存一律先过 SpecV2 校验
（错误阻止）与 lint（软警告随响应返回前端展示）。
"""

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server import bus
from trees import lint as tree_lint
from trees import store as tree_store
from trees.store import plan_fingerprint  # noqa: F401 —— V4-03：re-export 供测试/前端契约

router = APIRouter(prefix="/api/trees")


class TreeNewIn(BaseModel):
    # V4-10：id 可省略（后端从名称派生，冲突加短序号）；显式 id 创建不受影响
    id: str | None = None
    name: str
    subject: str = ""
    description: str = ""
    genre: str | None = None          # journal | research | brief
    style_card: str | None = None
    spec_dict: dict | None = None     # 缺省给最小可跑骨架（纯 text 一节）


class TreeSaveIn(BaseModel):
    spec_dict: dict
    meta: dict = Field(default_factory=dict)   # name/subject/status/genre/style_card
    summary: str = ""
    base_version: int | None = None    # B5 乐观锁：编辑器当前看到的版本


class RollbackIn(BaseModel):
    version: int


_DEFAULT_SPEC = {
    "report_type": "{id}",
    "description": "{description}",
    "writer_role": "资深行业研究员",
    "title_style": "短语式标题，15~30 字，概括报告主题与视角",
    "sections": [
        {"id": "overview", "title": "综述", "kind": "text",
         "style": "围绕报告主题写一段综述（2~3 个自然段）。"},
    ],
}


@router.get("/style-cards/all")
def style_cards() -> list[dict]:
    from pipeline.stylecards import list_cards
    return list_cards()


# ---------- C：文风卡管理（写端点，树模式只增不改；内置三卡只读保护） ----------

class StyleCardIn(BaseModel):
    id: str
    name: str = ""
    desc: str = ""
    rules: str = ""
    excerpts: list[dict] = Field(default_factory=list)   # [{text, source?}]


@router.post("/style-cards")
def create_style_card(body: StyleCardIn) -> dict:
    """新建自定义文风卡（id 防覆盖），写入即落盘 config/style_cards/<id>.yaml。"""
    from pipeline import stylecards
    try:
        if stylecards.card_path(body.id).exists():
            raise HTTPException(409, f"文风卡 id 已存在：{body.id}")
        card = stylecards.save_card(body.model_dump())
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "card": card}


@router.put("/style-cards/{card_id}")
def save_style_card(card_id: str, body: StyleCardIn) -> dict:
    """保存自定义文风卡（内置卡 403；手改 YAML 与此处保存都立即生效——无缓存）。"""
    from pipeline import stylecards
    if body.id and body.id != card_id:
        raise HTTPException(422, "请求体 id 与 URL 中的 id 不一致")
    body.id = card_id
    if not stylecards.card_path(card_id).exists():
        raise HTTPException(404, f"文风卡不存在：{card_id}")
    try:
        card = stylecards.save_card(body.model_dump())
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "card": card}


@router.delete("/style-cards/{card_id}")
def delete_style_card(card_id: str) -> dict:
    """删除自定义文风卡（内置卡 403）；被树引用时 409 附引用树清单。"""
    from pipeline import stylecards
    try:
        used = stylecards.delete_card(card_id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, f"文风卡不存在：{card_id}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    if used:
        raise HTTPException(409, f"文风卡正被 {len(used)} 棵树引用："
                                 f"{'、'.join(used)}；请先改这些树的文风卡选择")
    return {"ok": True}


# ---------- V2 §三 §4：内置示例树（config/tree_templates/ 只读目录） ----------

@router.get("/templates")
def list_tree_templates() -> list[dict]:
    import yaml
    from datalayer.settings import settings
    root = settings.resolve("config/tree_templates")
    out = []
    if root.exists():
        for p in sorted(root.glob("*.yaml")):
            try:
                with open(p, encoding="utf-8") as f:
                    t = yaml.safe_load(f)
            except (OSError, Exception):  # noqa: BLE001 —— 单个模板损坏跳过
                continue
            if isinstance(t, dict) and t.get("id"):
                out.append({"id": t["id"], "name": t.get("name", t["id"]),
                            "description": t.get("description", ""),
                            "genre": t.get("genre"),
                            "style_card": t.get("style_card"),
                            "n_sections": len(t.get("sections") or [])})
    return out


class TemplateCopyIn(BaseModel):
    template_id: str


@router.post("/templates/copy")
def copy_tree_template(body: TemplateCopyIn) -> dict:
    """选中示例树 → 复制为可编辑草稿（provenance 记模板来源）。"""
    import yaml
    from datalayer.settings import settings
    if "/" in body.template_id or "\\" in body.template_id \
            or ".." in body.template_id:
        raise HTTPException(403, "非法模板 id")
    p = settings.resolve(f"config/tree_templates/{body.template_id}.yaml")
    if not p.exists():
        raise HTTPException(404, f"示例树不存在：{body.template_id}")
    with open(p, encoding="utf-8") as f:
        t = yaml.safe_load(f) or {}
    spec_dict = {k: v for k, v in t.items()
                 if k not in ("id", "name", "description")}
    spec_dict["report_type"] = t["id"]
    spec_dict["description"] = t.get("description") or t.get("name") or t["id"]
    meta = {"name": t.get("name") or t["id"],
            "subject": t.get("description", "")[:60] or t["id"],
            "provenance": {"kind": "template", "template": t["id"]}}
    base = t["id"].replace("tpl_", "")
    tid = base
    n = 2
    while tree_store.exists(tid):
        tid = f"{base}{n}"
        n += 1
    saved = tree_store.save_tree(tid, spec_dict, meta, actor="system",
                                 summary=f"从示例树 {t['id']} 复制",
                                 op={"action": "template_copy",
                                     "template": t["id"]})
    return {"ok": True, "id": tid, "name": meta["name"],
            "version": saved["meta"]["version"],
            "fingerprint": saved["fingerprint"]}


@router.get("")
def list_trees() -> list[dict]:
    return tree_store.list_trees()


@router.post("")
def create_tree(body: TreeNewIn) -> dict:
    # V4-10：id 省略时由名称派生候选 id（ASCII 词 snake_case；纯中文回退 tree_），
    # 冲突追加短序号 -2/-3…；显式 id 冲突仍 409（旧行为不变）
    import re as _re
    tid = body.id
    if tid is not None and not str(tid).strip():
        tid = None
    if tid is None:
        words = _re.findall(r"[A-Za-z0-9]+", body.name)
        base = "_".join(w.lower() for w in words)[:40].strip("_") \
            or f"tree_{datetime.now():%Y%m%d}"
        tid = base
        n = 2
        while tree_store.exists(tid):
            tid = f"{base}-{n}"
            n += 1
    if tree_store.exists(tid):
        raise HTTPException(409, f"结构树 id 已存在：{tid}")
    spec_dict = body.spec_dict or {}
    if not spec_dict:
        spec_dict = {**_DEFAULT_SPEC,
                     "report_type": _DEFAULT_SPEC["report_type"].format(id=tid),
                     "description": body.description
                     or f"{body.name}（对话式生成）"}
    meta = {"name": body.name, "subject": body.subject or body.name,
            "provenance": {"kind": "manual"}}
    for k in ("genre", "style_card"):
        if getattr(body, k):
            spec_dict[k] = getattr(body, k)
    try:
        result = tree_store.save_tree(tid, spec_dict, meta,
                                      actor="system", summary="创建结构树")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "id": tid, "version": result["meta"]["version"],
            "fingerprint": result["fingerprint"]}


@router.get("/{tree_id}")
def tree_detail(tree_id: str) -> dict:
    try:
        loaded = tree_store.load_spec_dict(tree_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    lint_result = tree_lint.lint(loaded["spec_dict"])
    return {"meta": loaded["meta"], "spec_dict": loaded["spec_dict"],
            "fingerprint": loaded["fingerprint"], "lint": lint_result,
            "versions": tree_store.versions(tree_id),
            "ops": tree_store.ops_log(tree_id, limit=30)}


@router.put("/{tree_id}")
def save_tree(tree_id: str, body: TreeSaveIn) -> dict:
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    lint_result = tree_lint.lint(body.spec_dict)
    if lint_result["errors"]:
        # A2：lint error 拦保存（标题重复等结构问题先修再存）
        raise HTTPException(422, "保存被拦（结构问题）："
                            + "；".join(lint_result["errors"][:3]))
    try:
        result = tree_store.save_tree(tree_id, body.spec_dict, body.meta,
                                      actor="manual",
                                      summary=body.summary or "手动保存",
                                      base_version=body.base_version)
    except tree_store.TreeConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "version": result["meta"]["version"],
            "fingerprint": result["fingerprint"],
            "lint": lint_result}


@router.get("/{tree_id}/versions")
def tree_versions(tree_id: str) -> list[dict]:
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    return tree_store.versions(tree_id)


@router.get("/{tree_id}/ops")
def tree_ops(tree_id: str) -> list[dict]:
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    return tree_store.ops_log(tree_id)


@router.post("/{tree_id}/rollback")
def tree_rollback(tree_id: str, body: RollbackIn) -> dict:
    try:
        result = tree_store.rollback(tree_id, body.version)
    except ValueError as e:
        raise HTTPException(404, str(e))
    loaded = tree_store.load_spec_dict(tree_id)
    return {"ok": True, "version": result["meta"]["version"],
            "fingerprint": result["fingerprint"],
            "lint": tree_lint.lint(loaded["spec_dict"])}


@router.delete("/{tree_id}")
def delete_tree(tree_id: str) -> dict:
    try:
        tree_store.delete_tree(tree_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ---------- 树对话（M2）：需求生成树 / 自然语言编辑树 ----------

class GenerateIn(BaseModel):
    messages: list[dict] = Field(default_factory=list)   # [{role: user|assistant, text}]
    tree_id: str | None = None          # 给出且不冲突时用它，否则自动起 id


class ChatEditIn(BaseModel):
    message: str
    base_version: int | None = None   # B5 乐观锁（对话改树与手动保存共用）


def _slug(name: str) -> str:
    """树 id 生成（C5 中文化）：中文/字母/数字保留，其余折 _；退化兜底时间戳。"""
    import re
    import time
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(name or "").strip()).strip("_")
    return s[:40] or f"tree_{int(time.time())}"


@router.post("/generate")
def generate_tree(body: GenerateIn) -> dict:
    """需求对话 → 树（创建并返回）或反问。同步单次 LLM 调用。"""
    from pipeline import tree_agent
    try:
        result = tree_agent.generate(body.messages)
    except tree_agent.TreeAgentError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"树生成失败：{type(e).__name__}: {e}")
    if result["kind"] == "questions":
        return result
    spec_dict, meta = result["spec_dict"], result["meta"]
    tid = body.tree_id or _slug(meta.get("name") or "tree")
    base = tid
    n = 2
    while tree_store.exists(tid):
        tid = f"{base}_{n}"
        n += 1
    saved = tree_store.save_tree(tid, spec_dict,
                                 {"name": meta.get("name") or tid,
                                  "subject": meta.get("subject") or tid,
                                  "provenance": {"kind": "dialogue"}},
                                 actor="agent", summary="对话生成整棵树")
    return {"kind": "tree", "tree_id": tid, "name": meta.get("name"),
            "spec_dict": spec_dict, "version": saved["meta"]["version"],
            "fingerprint": saved["fingerprint"],
            "lint": tree_lint.lint(spec_dict),
            "warnings": result.get("warnings") or []}


@router.post("/{tree_id}/chat")
def chat_edit(tree_id: str, body: ChatEditIn) -> dict:
    """编辑对话：NL → 树操作 → 自动应用（actor=agent，版本 +1）。"""
    from pipeline import tree_agent
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    try:
        return tree_agent.edit(tree_id, body.message,
                               base_version=body.base_version)
    except tree_agent.TreeAgentError as e:
        raise HTTPException(422, str(e))
    except tree_store.TreeConflict as e:
        raise HTTPException(409, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"树编辑失败：{type(e).__name__}: {e}")


# ---------- 数据计划（M2 扩展 needs_coverage / 裁决；M1 先提供启动与沿用） ----------

class PlanStartIn(BaseModel):
    intent: str = ""                 # 写作意图（与 data_needs 汇总一起给规划器）
    folder: str | None = None        # 本地资料文件夹（现场建语料库）


@router.post("/{tree_id}/plan/start")
async def plan_start(tree_id: str, body: PlanStartIn) -> dict:
    """出数据采集计划（后台任务 + SSE）：树 data_needs + 意图 → planner。
    须为 async def：start_job 需要 running loop。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")

    def fn(progress) -> dict:
        from datalayer.planner import make_plan
        loaded = tree_store.load_tree(tree_id)
        spec, meta = loaded["spec"], loaded["meta"]
        sources = tree_store.synthetic_sources(meta, spec)
        needs = tree_store.tree_needs(tree_id)
        # 意图缺省用树描述（树场景 data_needs 本身就是规划输入）
        intent = body.intent or f"{spec.description}（主体：{meta.get('subject') or ''}）"
        progress("plan", "按树的数据需求生成采集计划 ...", None)
        plan = make_plan(spec, sources, intent,
                         body.folder or None, needs=needs or None)
        if body.folder and not (plan.get("corpus") or {}).get("fragments_rel"):
            progress("plan", "警告：资料文件夹未产出语料", None)
        # V4-03：计划快照当时树版本与稳定指纹（计划复用的匹配依据）
        plan["tree_version"] = meta.get("version")
        plan["tree_fingerprint"] = loaded["fingerprint"]
        plan["focus"] = intent
        if body.folder:
            plan["folder"] = body.folder
        name = tree_store.save_plan(tree_id, plan)
        progress("plan", f"计划已保存 {name}", {"plan_file": name, "plan": plan})
        return {"plan_file": name, "plan": plan}

    task = bus.start_job(fn, f"plan-{tree_id}", asyncio.get_running_loop())
    return {"id": task.id, "events_url": f"/api/trees/{tree_id}/jobs/{task.id}/events"}


class DecisionIn(BaseModel):
    need: str
    decision: str          # search | provide_folder | qualitative | drop


class PlanConfirmIn(BaseModel):
    plan_file: str
    decisions: list[DecisionIn] = Field(default_factory=list)
    all_qualitative: bool = False      # 全定性二次确认


@router.post("/{tree_id}/plan/confirm")
def plan_confirm(tree_id: str, body: PlanConfirmIn) -> dict:
    """缺口四选一裁决 → 落效（计划 + 树）→ 过确认门槛。
    通过时在计划文件持久化 confirmed: true（A4：生成门禁复查依据）。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    result = tree_store.apply_decisions(tree_id, body.plan_file,
                                        [d.model_dump() for d in body.decisions])
    gate = tree_store.coverage_gate(result["plan"])
    if not gate["ok"]:
        return {**gate, "ok": False, "coverage": result["coverage"]}
    if gate["all_qualitative"] and not body.all_qualitative:
        # 注意 **gate 必须在前：gate 里也有 ok 键，字面量要后置覆盖
        return {**gate, "ok": False, "coverage": result["coverage"],
                "need_confirm": "全树无数据来源（全定性生成），需二次确认"}
    plan = result["plan"]
    if not plan.get("confirmed"):
        from datetime import datetime
        plan["confirmed"] = True
        plan["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        tree_store.save_plan_file(tree_id, body.plan_file, plan)
    return {**gate, "ok": True, "coverage": result["coverage"]}


@router.post("/{tree_id}/copy")
def copy_tree(tree_id: str) -> dict:
    """F10 树派生：复制为可编辑草稿（新 id=原名_派生N）。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    result = tree_store.copy_tree(tree_id)
    return {"ok": True, **result}


class PlanPreviewIn(BaseModel):
    plan_file: str


def _preview_core(tree_id: str, plan_file: str, progress=None) -> dict:
    """V4-02：预检核心（同步/后台两用）。指纹一致复用缓存；跑数据层；
    返回事实数/来源分布/样本/警告。progress 可选（后台模式回报进度）。"""
    import json as _json
    plan = tree_store.load_plan(tree_id, plan_file)
    fp = plan_fingerprint(plan)
    cache = tree_store.tree_dir(tree_id) / "plans" / f"_preview_{fp}.json"
    if cache.exists():
        try:
            cached = _json.loads(cache.read_text(encoding="utf-8"))
            cached["cached"] = True
            cached["fingerprint"] = fp
            return cached
        except ValueError:
            pass

    def _run() -> dict:
        from datalayer.planner import plan_to_bindings
        from datalayer import registry
        loaded = tree_store.load_tree(tree_id)
        spec, meta = loaded["spec"], loaded["meta"]
        sources = tree_store.synthetic_sources(meta, spec)
        doc, crosscheck = registry.run_data_layer(
            tree_id, {"project": meta.get("subject") or tree_id},
            bindings_override=plan_to_bindings(plan, sources),
            sources_override=sources)
        by_src: dict[str, int] = {}
        for f in doc["facts"]:
            src = str(f.get("source", ""))[:20] or "unknown"
            by_src[src] = by_src.get(src, 0) + 1
        return {"ok": len(doc["facts"]) > 0,
                "n_facts": len(doc["facts"]),
                "by_source": by_src,
                "crosscheck": (crosscheck or {}).get("status") if crosscheck else None,
                "sample": doc["facts"][:5],
                "warnings": (doc["meta"].get("warnings") or [])[:5],
                "error": None if doc["facts"] else "计划执行后 0 事实（检查查询/语料）",
                "cached": False}

    if progress is not None:
        progress("preview", "按计划跑数据层（预检）...", {"fingerprint": fp})
    try:
        result = _run()
    except FileNotFoundError as e:
        raise HTTPException(422, f"数据文件不存在：{e}")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 —— 预检就是把问题提前报出来
        raise HTTPException(502, f"预检失败：{type(e).__name__}: {e}")
    result["fingerprint"] = fp
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(_json.dumps(result, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return result


@router.post("/{tree_id}/plan/preview")
async def plan_preview(tree_id: str, body: PlanPreviewIn) -> dict:
    """F9 树模式数据预检（同步版，保留兼容——V4-02 新增 /start 后台版）。
    须为 async def：数据层跑在线程里，不阻塞事件循环。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    import asyncio
    return await asyncio.to_thread(_preview_core, tree_id, body.plan_file)


@router.post("/{tree_id}/plan/preview/start")
async def plan_preview_start(tree_id: str, body: PlanPreviewIn) -> dict:
    """V4-02：预检后台化——同一核心跑在 job 里，返回 {id, events_url}。
    前端经任务中心跨页跟踪；同步端点保留不变。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    _check = _preview_core   # 引用校验（防拼写）；实际执行在 fn 内

    def fn(progress) -> dict:
        result = _preview_core(tree_id, body.plan_file, progress=progress)
        progress("preview", "预检完成", result)
        return result

    task = bus.start_job(fn, f"preview-{tree_id}", asyncio.get_running_loop())
    return {"id": task.id,
            "events_url": f"/api/trees/{tree_id}/jobs/{task.id}/events"}


@router.get("/{tree_id}/plans/{plan_name}")
def tree_plan_content(tree_id: str, plan_name: str) -> dict:
    """读取计划内容（含 needs_coverage 回执）——前端载入展示。"""
    try:
        return tree_store.load_plan(tree_id, plan_name)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{tree_id}/plans")
def tree_plans(tree_id: str) -> list[dict]:
    """树目录 plans/ 下的数据计划文件列表（生成入口选择沿用）。
    V4-03 增量字段（读不到旧字段返回 null，不报错）：confirmed / gap_count /
    qualitative_count / tree_version / tree_fingerprint / focus / folder /
    preview_cached。"""
    if not tree_store.exists(tree_id):
        raise HTTPException(404, f"结构树不存在：{tree_id}")
    d = tree_store.tree_dir(tree_id) / "plans"
    out = []
    if d.exists():
        for f in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime,
                        reverse=True):
            if f.name.startswith("_preview_"):
                continue
            entry = {"name": f.name, "size": f.stat().st_size,
                     "mtime": f.stat().st_mtime,
                     "confirmed": None, "gap_count": None,
                     "qualitative_count": None, "tree_version": None,
                     "tree_fingerprint": None, "focus": None, "folder": None,
                     "preview_cached": None}
            try:
                plan = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                out.append(entry)
                continue
            if isinstance(plan, dict):
                cov = plan.get("needs_coverage") or []
                entry["confirmed"] = bool(plan.get("confirmed")) \
                    if plan.get("confirmed") is not None else None
                entry["gap_count"] = sum(1 for c in cov
                                         if isinstance(c, dict)
                                         and c.get("status") == "gap")
                entry["qualitative_count"] = sum(
                    1 for c in cov if isinstance(c, dict)
                    and c.get("decision") == "qualitative")
                entry["tree_version"] = plan.get("tree_version")
                entry["tree_fingerprint"] = plan.get("tree_fingerprint")
                entry["focus"] = plan.get("focus")
                entry["folder"] = plan.get("folder")
                fp = plan_fingerprint(plan)
                entry["preview_cached"] = (d / f"_preview_{fp}.json").exists()
            out.append(entry)
    return out


@router.get("/{tree_id}/jobs/{job_id}/events")
async def job_events(tree_id: str, job_id: str):
    t = bus.HUB.get(job_id)
    if not t:
        raise HTTPException(404, "服务已重启，该任务状态不可查")
    return bus.sse_response(t)
