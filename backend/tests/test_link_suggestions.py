#!/usr/bin/env python
"""关联操作建议的测试 —— 纯函数层：不起服务、不连库、不占端口。

接口层（预检 / 执行、过时数据防护）由集成探针覆盖；
这里守住**规则本身**——规则错了，接口写得再稳也是稳地做错事。

跑法（在 backend/ 下）：
    py -3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import link_suggestions as ls  # noqa: E402
from app.services.ownership import has_owner  # noqa: E402


@dataclass
class FakeAsset:
    """只要 build_link_suggestions 用到的四个字段。"""

    id: int
    asset_code: str
    device_type_name: str | None = None
    user_name: str | None = None


def build(assets, new_user="张三"):
    return ls.build_link_suggestions(assets, new_user)


def by_suggestion(result):
    return {s["asset_id"]: s for s in result["suggestions"]}


class TestSuggestionRules(unittest.TestCase):
    """四种资产现状 → 四种建议。这是本功能的全部业务规则。"""

    def test_no_user_suggests_fill_and_checked(self):
        """空 / `/` / 全角斜杠 = 没归属 → fill，且默认勾选。"""
        for current in (None, "", "   ", "/", "／"):
            with self.subTest(current=current):
                row = by_suggestion(build([FakeAsset(1, "A-1", user_name=current)]))[1]
                self.assertEqual(row["suggestion"], "fill")
                self.assertTrue(row["checked"])
                self.assertIsNone(row["current_user"])

    def test_same_user_not_listed(self):
        """已是新使用人 → 不进建议列表，只进 matched_count。"""
        result = build([FakeAsset(1, "A-1", user_name="张三")])
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["matched_count"], 1)

    def test_other_user_suggests_conflict_not_checked(self):
        """有其他使用人 → conflict，**默认不勾**（不替用户做覆盖决定）。"""
        row = by_suggestion(build([FakeAsset(1, "A-1", user_name="陈默")]))[1]
        self.assertEqual(row["suggestion"], "conflict")
        self.assertFalse(row["checked"])
        self.assertEqual(row["current_user"], "陈默")

    def test_shared_mark_suggests_keep(self):
        """公用 / 共享占位 → keep，可保留现状。"""
        for current in ls.SHARED_MARKS:
            with self.subTest(current=current):
                row = by_suggestion(build([FakeAsset(1, "A-1", user_name=current)]))[1]
                self.assertEqual(row["suggestion"], "keep")
                self.assertFalse(row["checked"])


class TestPriority(unittest.TestCase):
    """fill 最顺理成章排最前，conflict 压最后；同类按 asset_code。"""

    def test_order_fill_keep_conflict(self):
        assets = [
            FakeAsset(1, "B-1", user_name="陈默"),   # conflict
            FakeAsset(2, "A-2", user_name="/"),      # fill
            FakeAsset(3, "A-1", user_name="公用"),   # keep
            FakeAsset(4, "A-0", user_name=None),     # fill
        ]
        rows = build(assets)["suggestions"]
        self.assertEqual(
            [(r["suggestion"], r["asset_code"]) for r in rows],
            [("fill", "A-0"), ("fill", "A-2"), ("keep", "A-1"), ("conflict", "B-1")],
        )


class TestSummary(unittest.TestCase):
    def test_counts_reconcile(self):
        """total = matched + len(suggestions)，对账口径必须闭合。"""
        assets = [
            FakeAsset(1, "A-1", user_name=None),
            FakeAsset(2, "A-2", user_name="张三"),
            FakeAsset(3, "A-3", user_name="陈默"),
        ]
        result = build(assets)
        self.assertEqual(result["total_assets"], 3)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(len(result["suggestions"]), 2)


class TestOwnershipConsistency(unittest.TestCase):
    """本模块不许自写判空逻辑 —— 判据必须与 ownership.has_owner() 一致。"""

    def test_fill_implies_no_owner(self):
        """凡是判成 fill 的，ownership 口径必须也认定「没归属」。
        （反向守卫：将来谁在 suggest_for_asset 里自写判据，这里立刻红。）"""
        for current in (None, "", "/", "／", "  "):
            with self.subTest(current=current):
                if ls.suggest_for_asset(current, "张三") == "fill":
                    self.assertFalse(has_owner(current))

    def test_has_owner_does_not_treat_shared_as_no_user(self):
        """⚠️ `公用` 在全局 ownership 口径里**是**一个使用人（有归属）。
        本模块只在建议层把它单独认成 keep —— 这个分歧是**有意的**，
        改全局口径是独立的一轮决策，不许顺手改。"""
        self.assertTrue(has_owner("公用"))


class TestApplyGuard(unittest.TestCase):
    """执行侧「保存前重新核对」的纯函数部分。"""

    def test_recheck_rejects_non_fill(self):
        """弹窗打开期间资产被别人填了使用人 → 不再是 fill → 执行侧必须跳过。"""
        # 预检时是空（fill），执行时已经登记为李四
        self.assertNotEqual(ls.suggest_for_asset("李四", "张三"), "fill")
        # 预检时是 `/`（fill），执行时变成公用 → keep，同样不许覆盖
        self.assertEqual(ls.suggest_for_asset("公用", "张三"), "keep")

    def test_recheck_allows_still_empty(self):
        self.assertEqual(ls.suggest_for_asset(None, "张三"), "fill")
        self.assertEqual(ls.suggest_for_asset("/", "张三"), "fill")


if __name__ == "__main__":
    unittest.main(verbosity=2)
