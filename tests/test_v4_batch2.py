"""V4 批②单测：V4-09 术语层静态契约 + V4-10 树管理（省略 id 创建 /
列表契约 / 前端模板契约）。无 LLM、无网络。

pytest 运行；也可 python tests/test_v4_batch2.py 直接执行。
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from trees import store as tree_store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _cleanup(tids):
    for tid in tids:
        if tree_store.exists(tid):
            tree_store.delete_tree(tid)


# ---------------- V4-09：术语层静态契约 ----------------

REQUIRED_TERMS = [
    "tree", "gap_decision", "preview", "style_card", "lint", "judge",
    "reconcile", "validate", "pass", "fail",
    "structure", "length", "data_support", "professionalism",
    "compliance", "readability", "risk_warning",
    "kind_text", "kind_views", "kind_table", "kind_risk", "kind_figures",
    "renderer_facts_rows", "renderer_generic",
    "src_rag", "src_web", "src_db", "src_file",
]


def test_terms_dict_complete():
    """EC.terms 每个必需键都有非空中文 label 与 shortHelp（静态契约）。"""
    src = (ROOT / "static" / "js" / "fielddict.js").read_text(encoding="utf-8")
    assert "EC.TermHelp" in src and "EC.verdictLabel" in src \
        and "EC.lintText" in src and "EC.gateLabel" in src
    for key in REQUIRED_TERMS:
        # 键存在
        m = re.search(rf"\b{re.escape(key)}:\s*\{{", src)
        assert m, f"术语缺键：{key}"
        # 该键块内有非空 label 与 shortHelp（截取键起始后 600 字符窗口）
        window = src[m.start():m.start() + 600]
        lm = re.search(r"label:\s*'([^']+)'", window)
        hm = re.search(r"shortHelp:\s*'([^']+)'", window)
        assert lm and lm.group(1).strip(), f"{key}.label 为空"
        assert hm and hm.group(1).strip(), f"{key}.shortHelp 为空"
        assert re.search(r"[\u4e00-\u9fff]", lm.group(1)), \
            f"{key}.label 不是中文：{lm.group(1)}"


def test_pages_no_bare_jargon():
    """五个页面主文案不再出现孤立 PASS / FAIL / M2 起 / M8-R1 / 0E/0W 徽标。"""
    views = ["new.js", "detail.js", "reports.js", "settings.js", "templates.js"]
    for name in views:
        src = (ROOT / "static" / "js" / "views" / name).read_text(encoding="utf-8")
        for bad in ("PASS", "FAIL", "M2 起", "M8-R1"):
            assert bad not in src, f"{name} 出现裸术语：{bad}"
        assert not re.search(r"\}\}E/\{\{", src), \
            f"{name} 仍有 N E/M W 徽标"


def test_termhelp_registered_and_used():
    """TermHelp 组件已注册进 app，且 fielddict 提供 label/help/technical 三元结构。"""
    app_src = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "app.component('TermHelp'" in app_src
    src = (ROOT / "static" / "js" / "fielddict.js").read_text(encoding="utf-8")
    for field in ("label", "shortHelp", "technical"):
        assert field in src


# ---------------- V4-10：树管理 ----------------

def test_create_tree_without_id_and_conflict_suffix():
    """id 省略 → 后端从名称派生；纯中文 → tree_ 前缀；显式 id 冲突仍 409。"""
    tids = []
    try:
        with TestClient(app) as c:
            # 纯中文名称 → tree_<日期> 前缀
            r = c.post("/api/trees", json={"name": "批二测试纯中文树"})
            assert r.status_code == 200, r.text
            tid1 = r.json()["id"]
            tids.append(tid1)
            assert tid1.startswith("tree_"), tid1

            # 名称带 ASCII → snake_case 派生
            r2 = c.post("/api/trees", json={"name": "Quartz Monthly Report"})
            assert r2.status_code == 200, r2.text
            tid2 = r2.json()["id"]
            tids.append(tid2)
            assert tid2 == "quartz_monthly_report", tid2

            # 显式 id 冲突 → 409（旧行为不变）
            r3 = c.post("/api/trees", json={"id": tid2, "name": "冲突"})
            assert r3.status_code == 409

            # 空字符串 id 视为省略
            r4 = c.post("/api/trees", json={"id": "", "name": "空 id 自动派生"})
            assert r4.status_code == 200, r4.text
            tids.append(r4.json()["id"])
    finally:
        _cleanup(tids)


def test_list_trees_contract_fields():
    """树列表旧字段齐全（向后兼容契约：id/name/sections/version/lint/updated_at）。"""
    tid = "_t_v4b2_contract"
    try:
        tree_store.save_tree(tid, {
            "report_type": tid, "description": "契约树",
            "sections": [{"id": "a", "title": "A", "kind": "text",
                          "data_needs": ["x"]}]},
            {"name": "契约树"}, actor="system")
        rows = [r for r in tree_store.list_trees() if r["id"] == tid]
        assert rows, "列表缺新树"
        row = rows[0]
        for field in ("id", "name", "subject", "status", "sections",
                      "version", "fingerprint", "lint", "updated_at"):
            assert field in row, f"列表缺字段：{field}"
        assert set(row["lint"].keys()) == {"errors", "warnings"}
    finally:
        _cleanup([tid])


def test_frontend_templates_contract():
    """templates.js 具备搜索/筛选/排序/高级折叠/脏保护；新弹窗不再强制手填 id。"""
    src = (ROOT / "static" / "js" / "views" / "templates.js").read_text(encoding="utf-8")
    for token in ("tree-search", "tree-lint-filter", "treeList", "guardUnsaved",
                  "toggleAdv", "secAdvCount", "treeAdvCount", "_customId",
                  "new-tree-name", "aria-label"):
        assert token in src, f"templates.js 缺 {token}"
    # 高级字段仍在保存载荷中（折叠只是显示层）：specFromForm 引用全部原字段
    for field in ("renderer", "source_prefix", "body_min", "body_max",
                  "controlled_vocab", "forbidden_words", "charts", "view_slots"):
        assert field in src, f"保存载荷可能丢字段：{field}"


if __name__ == "__main__":
    test_terms_dict_complete()
    test_pages_no_bare_jargon()
    test_termhelp_registered_and_used()
    test_create_tree_without_id_and_conflict_suffix()
    test_list_trees_contract_fields()
    test_frontend_templates_contract()
    print("test_v4_batch2 全部通过")
