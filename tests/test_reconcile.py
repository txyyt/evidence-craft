"""M3 验收用例：塞错数字必须被对账层拦下。

用法：python tests/test_reconcile.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import reconcile  # noqa: E402
from template_factory.schema import load_spec  # noqa: E402


def main() -> None:
    import glob
    run = sorted(glob.glob(str(
        Path(__file__).resolve().parent.parent / "artifacts/000803_*")))[-1]
    doc = json.load(open(run + "/facts.json", encoding="utf-8"))
    outline = {"views": [{"slot_id": s, "heading": "h"} for s in
                         ("a", "b", "c", "d")], "title": "t"}
    risks = {"body": "价格波动风险；政策调整风险；诉讼风险"}

    good = "2026H1公司实现营业收入7.35亿元，同比增长2.62%；归母净利润0.65亿元。"
    bad = "2026H1公司实现营业收入8.71亿元，同比增长33.15%；归母净利润1.20亿元。"

    spec = load_spec()
    r_good = reconcile.reconcile(doc, outline,
                                 [{"slot_id": "a", "heading": "h", "body": good}],
                                 {"body": "预测EPS为0.31元。"}, risks, spec)
    r_bad = reconcile.reconcile(doc, outline,
                                [{"slot_id": "a", "heading": "h", "body": bad}],
                                {"body": "预测EPS为0.31元。"}, risks, spec)

    bad_checks = [c for c in r_bad["checks"] if c["section"] == "core_views.a"][0]
    print("正确数字版：", r_good["status"].upper(),
          "未匹配", sum(len(c["unknown_numbers"]) for c in r_good["checks"]), "个")
    print("篡改数字版：", r_bad["status"].upper(),
          "未匹配", bad_checks["unknown_numbers"])
    assert r_bad["status"] != "pass" and len(bad_checks["unknown_numbers"]) >= 2, \
        "篡改数字未被对账层拦下！"
    assert r_good["status"] == "pass", "正确数字被误报！"
    print("\n验收通过：篡改的数字", bad_checks["unknown_numbers"],
          "全部被拦下，正确数字零误报")

    # —— 治理① 范文数字豁免：fewshot 里的数字不再被判 unknown ——
    from template_factory.schema import SpecV2, Section, ViewSlot
    mini_spec = SpecV2(
        report_type="mini", description="豁免集最小 spec",
        sections=[Section(id="core_views", title="核心观点", kind="views",
                          n_views=1,
                          view_slots=[ViewSlot(
                              id="a",
                              brief="b",
                              fewshot="【范文】一般认为400～600℃的成岩温度"
                                      "更有利于高纯石英矿物形成。")])],
        tables=[], check_rules=[])
    mini_doc = {"facts": [{"id": "rag.00.00", "name": "x", "value": 1.0,
                           "unit": "", "source": "s", "as_of": ""}],
                "collections": {}, "meta": {}}
    mini_outline = {"views": [{"slot_id": "a", "heading": "h"}], "title": "t"}
    r = reconcile.reconcile(
        mini_doc, mini_outline,
        [{"slot_id": "a", "heading": "h",
          "body": "一般认为400～600℃的成岩温度更有利于高纯石英矿物形成。"}],
        {"body": ""}, {"body": ""}, mini_spec)
    c = r["checks"][0]
    assert c["unknown_numbers"] == [], f"范文数字被误拦：{c['unknown_numbers']}"
    assert r["n_ref_exempt"] >= 2, f"豁免计数异常：{r['n_ref_exempt']}"
    assert r["index_size"] == 1, "豁免集不得污染事实索引"
    print(f"① 范文豁免：正文复述 fewshot 的 400～600℃ 放行"
          f"（n_ref_exempt={r['n_ref_exempt']}，索引不受污染）✓")
    # 豁免不覆盖任意编造的数字
    r2 = reconcile.reconcile(
        mini_doc, mini_outline,
        [{"slot_id": "a", "heading": "h",
          "body": "矿床埋深1234.5m处见矿。"}],
        {"body": ""}, {"body": ""}, mini_spec)
    c2 = r2["checks"][0]
    assert 1234.5 in c2["unknown_numbers"], "编造数字未被拦（豁免过宽）！"
    print("② 豁免不过宽：编造的 1234.5 依旧被拦 ✓")

    # —— 治理① 通识数字出口：〔〕标记数字不参与对账 ——
    r3 = reconcile.reconcile(
        mini_doc, mini_outline,
        [{"slot_id": "a", "heading": "h",
          "body": "区域变质温度〔450～580℃〕区间内易形成伟晶岩石英。"}],
        {"body": ""}, {"body": ""}, mini_spec)
    c3 = r3["checks"][0]
    assert c3["unknown_numbers"] == [], \
        f"〔〕标记数字被对账拦截：{c3['unknown_numbers']}"
    print("③ 通识标记：〔450～580℃〕不参与对账 ✓")

    # —— 治理① validate 限量：〔〕超 3 处 fail ——
    from pipeline import validate as validate_mod
    spec4 = SpecV2(report_type="mini", description="d",
                   sections=[Section(id="core_views", title="v", kind="views",
                                     n_views=1,
                                     view_slots=[ViewSlot(id="a", brief="b")]),
                             Section(id="risk_w", title="r", kind="risk")],
                   tables=[], check_rules=[], forbidden_words=[])
    doc4 = {"facts": [], "collections": {}, "meta": {}}
    bodies = ["区间〔400～600℃〕参考", "品位〔1.0g/t〕边界", "工业品位〔2.5g/t〕",
              "第四处〔3.3g/t〕超限"]
    rep4 = validate_mod.run(doc4, {"title": "t", "views": []},
                            [{"slot_id": "a", "heading": "h",
                              "body": "".join(bodies)}],
                            {"body": ""}, {"body": ""}, "", spec4,
                            texts=[], notes={})
    gen = [i for i in rep4["items"] if i["rule"] == "style.genknow_cap"]
    assert gen and gen[0]["status"] == "fail" and gen[0]["metric"] == 4, gen
    print("④ 通识限量：〔〕标记 4 处被 validate 判 fail ✓")

    # —— 治理② stale_forecast：将来时+过期年份拦截，历史引述豁免 ——
    sf = validate_mod._stale_forecasts
    assert sf("预计到2025年我国需求将达38.31万t。", 2026), "过期将来时未拦"
    assert not sf("预计到2030年需求将达到50万t。", 2026), "未来年份误拦"
    assert not sf("此前研究预测到2025年需求将达38.31万t。", 2026), "历史引述误拦"
    assert not sf("据2020年统计，到2025年将达40万t。", 2026), "据…统计误拦"
    assert not sf("预计到2026年需求将达50万t。", 2026), "当前年份误拦"
    rep5 = validate_mod.run(doc4, {"title": "t", "views": []},
                            [{"slot_id": "a", "heading": "h",
                              "body": "预计到2025年我国需求将达38.31万t。"}],
                            {"body": ""}, {"body": ""}, "", spec4,
                            texts=[], notes={})
    sf_item = [i for i in rep5["items"] if i["rule"] == "stale_forecast"]
    assert sf_item and sf_item[0]["status"] == "fail", sf_item
    print("⑤ 时效规则：stale_forecast 单测 5 用例全过（过期将来时 fail、"
          "未来年/历史引述/当前年不拦）✓")

    # —— 治理② 渲染层数据截至声明 ——
    from render.html_report import _data_cutoff, _clean_genknow
    assert _data_cutoff(doc4) == "未注明"
    d5 = {"facts": [{"as_of": "2017年"}, {"as_of": "2025年"},
                    {"as_of": "2019年6月"}]}
    assert _data_cutoff(d5) == "2025年"
    assert _clean_genknow("温度〔400～600℃〕区间") == "温度400～600℃区间"
    print("⑥ 渲染层：数据截至取最大年份/无年份回退未注明；〔x〕→x 清洗 ✓")

    print("\n治理任务验收（豁免/通识/时效）全部通过")


if __name__ == "__main__":
    main()
