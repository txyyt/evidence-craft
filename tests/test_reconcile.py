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


if __name__ == "__main__":
    main()
