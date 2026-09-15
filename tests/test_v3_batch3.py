"""V3 第三批（C 文风卡管理）单测：端点 403/409/往返 + 缓存移除立即生效。

pytest 运行；单测一律 mock LLM（本批无 LLM 调用）。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from datalayer.settings import settings  # noqa: E402
from pipeline import stylecards  # noqa: E402
from server.main import app  # noqa: E402
from trees import store as tree_store  # noqa: E402

CARD = "_t_v3_card"
TREE = "_t_v3_card_tree"

_PAYLOAD = {"id": CARD, "name": "测试文风卡", "desc": "单测用",
            "rules": "- 第一条规则\n- 第二条规则",
            "excerpts": [{"text": "节选正文一。", "source": "出处A"},
                         {"text": "节选正文二。", "source": ""}]}


def _cleanup():
    p = stylecards.card_path(CARD)
    if p.exists():
        p.unlink()
    if tree_store.exists(TREE):
        tree_store.delete_tree(TREE)


@pytest.fixture()
def client():
    _cleanup()
    with TestClient(app) as c:
        yield c
    _cleanup()


def test_create_and_roundtrip(client):
    """POST 新建 → 落盘 → list/load 往返字段完整。"""
    r = client.post("/api/trees/style-cards", json=_PAYLOAD)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    card = stylecards.load_card(CARD)
    assert card is not None and card["source"] == "custom"
    assert card["name"] == "测试文风卡"
    assert "第二条规则" in card["rules"]
    assert [e["text"] for e in card["excerpts"]] == ["节选正文一。", "节选正文二。"]
    assert card["excerpts"][0]["source"] == "出处A"
    all_cards = {c["id"]: c for c in stylecards.list_cards()}
    assert CARD in all_cards and all_cards[CARD]["n_excerpts"] == 2


def test_create_duplicate_id_409(client):
    client.post("/api/trees/style-cards", json=_PAYLOAD)
    r = client.post("/api/trees/style-cards", json=_PAYLOAD)
    assert r.status_code == 409


def test_create_illegal_id_422(client):
    r = client.post("/api/trees/style-cards",
                    json={**_PAYLOAD, "id": "../evil"})
    assert r.status_code == 422


def test_put_updates_without_restart(client):
    """PUT 保存 → load_card 立即读到新值（lru_cache 已移除，P3 根修）。"""
    client.post("/api/trees/style-cards", json=_PAYLOAD)
    r = client.put(f"/api/trees/style-cards/{CARD}",
                   json={**_PAYLOAD, "rules": "- 改过的规则"})
    assert r.status_code == 200, r.text
    assert "改过的规则" in stylecards.load_card(CARD)["rules"]
    # 库函数级验证：改文件后再 load_card = 新值（无缓存 staleness）
    p = stylecards.card_path(CARD)
    text = p.read_text(encoding="utf-8").replace("改过的规则", "手改YAML规则")
    p.write_text(text, encoding="utf-8")
    assert "手改YAML规则" in stylecards.load_card(CARD)["rules"]


def test_put_builtin_card_403(client):
    r = client.put("/api/trees/style-cards/industry_research",
                   json={"id": "industry_research", "name": "x", "rules": "y"})
    assert r.status_code == 403


def test_delete_unused_custom_card(client):
    client.post("/api/trees/style-cards", json=_PAYLOAD)
    r = client.delete(f"/api/trees/style-cards/{CARD}")
    assert r.status_code == 200
    assert not stylecards.card_path(CARD).exists()
    assert stylecards.load_card(CARD) is None


def test_delete_builtin_card_403(client):
    r = client.delete("/api/trees/style-cards/exec_brief")
    assert r.status_code == 403
    assert stylecards.load_card("exec_brief") is not None


def test_delete_referenced_card_409(client):
    """被树引用的自定义卡删除 → 409 附引用树清单。"""
    client.post("/api/trees/style-cards", json=_PAYLOAD)
    tree_store.save_tree(TREE, {
        "report_type": TREE, "description": "引用卡测试",
        "style_card": CARD,
        "sections": [{"id": "a", "title": "A", "kind": "text"}]},
        {"name": "引用树"}, actor="system")
    r = client.delete(f"/api/trees/style-cards/{CARD}")
    assert r.status_code == 409
    assert TREE in r.json()["detail"]
    assert stylecards.card_path(CARD).exists()      # 未被删除
    # 解除引用后可删
    tree_store.save_tree(TREE, {
        "report_type": TREE, "description": "引用卡测试",
        "sections": [{"id": "a", "title": "A", "kind": "text"}]},
        {"name": "引用树"}, actor="system")
    r2 = client.delete(f"/api/trees/style-cards/{CARD}")
    assert r2.status_code == 200
    assert not stylecards.card_path(CARD).exists()


def test_card_path_rejects_bad_id():
    with pytest.raises(ValueError):
        stylecards.card_path("带 空格")
    with pytest.raises(ValueError):
        stylecards.card_path("")
    with pytest.raises(ValueError):
        stylecards.card_path("x" * 41)
    assert stylecards.card_path("ok_id_1").name == "ok_id_1.yaml"
