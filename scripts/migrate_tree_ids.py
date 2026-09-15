"""一次性迁移：树 id / 节 id 中文化（V2 方案 §一 §3，2026-09-15）。

做什么（对 config/trees/ 下每棵树）：
1. 树改名：目录 `tree` → `萤石综述`（meta.id / meta.name 同步）；具名夹具
   （smoke_text / smoke_mix / manual_demo）保留原树 id。
2. 节 id 迁移：全部树的节 id 统一改为 节标题（与 tree_agent 新默认一致）；
   同标题节先去重——保留有意义的 id（如 logistics_constraints），删除退化
   id（如 `section`）。
3. 孤儿表模板修复：萤石树 tbl_consumption / tbl_new_deposits 挂到对应位置
   （在 brief 提该表的节之后插入独立 kind=table 节）；无法挂靠的模板删除
   并记日志。
4. lint 清零配套：缺 heading 的节补 heading=标题；缺 data_needs 的节补
   「<标题>相关数据」。
5. 版本 +1 快照，ops_log 记 actor=system 的迁移条目；历史产物 meta.json 的
   tree_id 不回改（反馈工作台对旧 run_dir 给出可读提示）。

用法：
  python scripts/migrate_tree_ids.py            # 实际迁移（幂等，可重跑）
  python scripts/migrate_tree_ids.py --dry-run  # 只看计划不动文件
"""

import argparse
import io
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datalayer.settings import settings          # noqa: E402
from trees import store as tree_store            # noqa: E402
from trees.lint import lint as tree_lint         # noqa: E402

# 树目录改名表（具名夹具不在表中=保留原 id）
TREE_RENAMES = {"tree": "萤石综述"}
# 视为"退化"的节 id（同标题去重时优先删除）
_DEGRADED = re.compile(r"^(section|section_\d+|sec\d*)$")
# 孤儿模板挂靠表：模板 id → 表节定义（title/heading/定位需求/归属节匹配词）
TABLE_ATTACH = {
    "tbl_consumption": {
        "title": "萤石下游消费结构", "heading": "表1　萤石下游消费结构",
        "need": "下游各领域消费量占比", "host_words": ("消费",)},
    "tbl_new_deposits": {
        "title": "近年新增萤石矿点", "heading": "表2　近年新增萤石矿点",
        "need": "近年新发现萤石矿点清单", "host_words": ("勘查", "新发现", "矿点")},
}
_TABLE_NOTE_STYLE = "用两三句话解读下表数据（数字以表格为准，不在正文另写表格）。"


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True, sort_keys=False)


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _unique(base: str, taken: set) -> str:
    bid, n = base, 2
    while bid in taken:
        bid = f"{base}_{n}"
        n += 1
    return bid


def dedup_and_rename_sections(payload: dict, changes: list) -> None:
    """同标题去重（删退化 id）→ 全部节 id 改为标题。"""
    sections = payload.get("sections") or []
    by_title: dict = {}
    drop = set()
    for s in sections:
        t = s.get("title") or s.get("id")
        by_title.setdefault(t, []).append(s)
    for t, group in by_title.items():
        if len(group) < 2:
            continue
        good = [s for s in group if not _DEGRADED.match(s.get("id") or "")]
        keep = good[-1] if good else group[-1]
        for s in group:
            if s is not keep:
                drop.add(id(s))
                changes.append(f"去重：删除重复节「{t}」（id={s.get('id')}，"
                               f"保留 id={keep.get('id')}）")
    payload["sections"] = [s for s in sections if id(s) not in drop]
    taken = set()
    for s in payload["sections"]:
        new_id = _unique(str(s.get("title") or s.get("id")).strip(), taken)
        if s.get("id") != new_id:
            changes.append(f"节 id：{s.get('id')} → {new_id}（标题「{s.get('title')}」）")
            s["id"] = new_id
        taken.add(new_id)


def attach_orphan_tables(payload: dict, changes: list) -> None:
    """孤儿表模板：按挂靠表插独立表格节；无挂靠表的模板删除。"""
    tables = payload.get("tables") or []
    sections = payload.get("sections") or []
    referenced = {s.get("table") for s in sections if s.get("kind") == "table"}
    kept = []
    for t in tables:
        tid = t.get("id")
        if tid in referenced:
            kept.append(t)
            continue
        plan = TABLE_ATTACH.get(tid)
        host = None
        if plan:
            # 优先精确：data_needs 含规划需求 → 再按 host_words 匹配标题/风格
            for i, s in enumerate(sections):
                if s.get("kind") == "text" \
                        and plan["need"] in (s.get("data_needs") or []):
                    host = i
                    break
            if host is None:
                for i, s in enumerate(sections):
                    text = " ".join([s.get("title") or "", s.get("style") or ""])
                    if s.get("kind") == "text" \
                            and any(w in text for w in plan["host_words"]):
                        host = i
                        break
        if host is not None:
            sec = {"id": plan["title"], "title": plan["title"], "kind": "table",
                   "table": tid, "heading": plan["heading"],
                   "style": _TABLE_NOTE_STYLE, "data_needs": [plan["need"]],
                   "origin": "user"}
            sections.insert(host + 1, sec)
            kept.append(t)
            changes.append(f"挂靠：孤儿表模板「{tid}」→ 在节「{sections[host]['title']}"
                           f"」之后插入表格节「{plan['title']}」")
        else:
            changes.append(f"删除：孤儿表模板「{tid}」无可挂靠位置（已记日志，"
                           "如需此表请在工作台重建表格节）")
    if kept:
        payload["tables"] = kept
    else:
        payload.pop("tables", None)


def fill_lint_gaps(payload: dict, changes: list) -> None:
    """缺 heading 补 heading=标题；缺 data_needs 补「<标题>相关数据」。"""
    for s in payload.get("sections") or []:
        if not s.get("heading") and not s.get("inline") and not s.get("subheading"):
            s["heading"] = s.get("title")
            changes.append(f"补 heading：节「{s.get('title')}」")
        if not s.get("data_needs"):
            s["data_needs"] = [f"{s.get('title')}相关数据"]
            changes.append(f"补 data_needs：节「{s.get('title')}」")


def migrate_tree_dir(d: Path, apply: bool) -> dict:
    tp = d / "tree.yaml"
    payload = _read(tp)
    meta = dict(payload.get("tree") or {})
    old_id = d.name
    new_id = TREE_RENAMES.get(old_id, old_id)
    changes: list = []

    if old_id in TREE_RENAMES and meta.get("id") != new_id:
        changes.append(f"树改名：{old_id} → {new_id}（meta.name 同步）")

    before = json.dumps(payload.get("sections"), ensure_ascii=False)
    dedup_and_rename_sections(payload, changes)
    attach_orphan_tables(payload, changes)
    fill_lint_gaps(payload, changes)
    sections_changed = json.dumps(payload.get("sections"), ensure_ascii=False) != before

    report = {"tree": old_id, "target": new_id, "changes": changes,
              "warnings_after": [], "skipped": False}
    if not changes:
        report["skipped"] = True
        return report

    # 校验（迁移产物必须过 SpecV2）
    tree_store.validate_payload(payload)

    lint_after = tree_lint(payload)
    report["warnings_after"] = lint_after["warnings"] + lint_after["errors"]
    if apply:
        now = datetime.now().isoformat(timespec="seconds")
        meta["id"] = new_id
        if old_id in TREE_RENAMES:
            meta["name"] = TREE_RENAMES[old_id]
        meta["updated_at"] = now
        meta["version"] = int(meta.get("version") or 0) + 1
        payload["tree"] = meta
        target = settings.resolve(f"config/trees/{new_id}")
        if new_id != old_id:
            if target.exists():
                raise SystemExit(f"目标目录已存在，中止：{target}")
            shutil.move(str(d), str(target))
        _dump(target / "tree.yaml", payload)
        _dump(target / "versions" / f"v{meta['version']}.yaml", payload)
        fp = tree_store.fingerprint({k: v for k, v in payload.items()
                                     if k != "tree"})
        log_path = target / "ops_log.jsonl"
        entry = {"ts": now, "actor": "system",
                 "op": {"action": "migrate_ids", "renamed_from": old_id
                        if new_id != old_id else None,
                        "changes": changes},
                 "summary": "树 id 中文化迁移（V2 §一 §3）",
                 "version_after": meta["version"], "fingerprint": fp}
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        report["version"] = meta["version"]
        report["fingerprint"] = fp
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="树 id 中文化一次性迁移")
    ap.add_argument("--dry-run", action="store_true", help="只打印迁移计划")
    args = ap.parse_args()

    root = settings.resolve("config/trees")
    if not root.exists():
        print("没有 config/trees 目录，无事可做")
        return
    total_changes = 0
    for d in sorted(p for p in root.iterdir() if (p / "tree.yaml").exists()):
        report = migrate_tree_dir(d, apply=not args.dry_run)
        tag = "[跳过]" if report["skipped"] else \
              ("[计划]" if args.dry_run else "[完成]")
        print(f"{tag} {report['tree']}"
              + (f" → {report['target']}" if report["target"] != report["tree"] else ""))
        for c in report["changes"]:
            print(f"    - {c}")
        for w in report["warnings_after"]:
            print(f"    ! 迁移后 lint：{w}")
        total_changes += len(report["changes"])
    print(f"\n共 {total_changes} 项变更"
          + ("（dry-run，未写文件）" if args.dry_run else ""))
    # 终检：全树 lint 报告
    print("\n—— 迁移后全树 lint ——")
    clean = True
    for t in tree_store.list_trees():
        loaded = tree_store.load_spec_dict(t["id"])
        r = tree_lint(loaded["spec_dict"])
        status = "OK" if not (r["errors"] or r["warnings"]) else "有告警"
        if r["errors"] or r["warnings"]:
            clean = False
        print(f"  {t['id']}: {status}")
        for e in r["errors"]:
            print(f"    [error] {e}")
        for w in r["warnings"]:
            print(f"    [warn] {w}")
    print("lint 全绿" if clean else "lint 有告警（见上）")


if __name__ == "__main__":
    main()
