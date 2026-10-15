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


class TestClearSuggestions(unittest.TestCase):
    """清人方向（第二期）：点位 有人 → 无人。"""

    def test_only_assets_holding_that_user(self):
        """只提示「仍登记为被清那位」的设备；别人名下 / 本来没登记的一律不列。"""
        assets = [
            FakeAsset(1, "A-1", user_name="张三"),   # 命中
            FakeAsset(2, "A-2", user_name="李四"),   # 不是他 → 不列
            FakeAsset(3, "A-3", user_name=None),     # 本来没有 → 不列
            FakeAsset(4, "A-4", user_name="/"),      # 占位符 = 没归属 → 不列
        ]
        r = ls.build_clear_suggestions(assets, "张三")
        self.assertEqual([s["asset_code"] for s in r["suggestions"]], ["A-1"])
        self.assertEqual(r["other_count"], 3)
        self.assertEqual(r["total_assets"], 4)

    def test_default_unchecked(self):
        """⭐ 清人是「丢归属」方向 —— **默认一个都不勾**（勾了才清，不勾=留给下一任）。"""
        r = ls.build_clear_suggestions([FakeAsset(1, "A-1", user_name="张三")], "张三")
        self.assertFalse(r["suggestions"][0]["checked"])

    def test_trimmed_match(self):
        """名字带空格 = 同一个人（判据走 trim，别写成精确相等）。"""
        r = ls.build_clear_suggestions([FakeAsset(1, "A-1", user_name=" 张三 ")], "张三")
        self.assertEqual(len(r["suggestions"]), 1)

    def test_recheck_rejects_changed_state(self):
        """执行前重检：期间已被改名 / 已被清过 → 不是 clear → 跳过不覆盖。"""
        self.assertIsNone(ls.suggest_clear_for_asset("李四", "张三"))
        self.assertIsNone(ls.suggest_clear_for_asset(None, "张三"))
        self.assertIsNone(ls.suggest_clear_for_asset("/", "张三"))
        self.assertEqual(ls.suggest_clear_for_asset(" 张三 ", "张三"), "clear")

    def test_no_suggestion_when_station_user_unknown(self):
        """资产名与"被清的人"无关时永远不提示（防"清 A 却动了 B 的设备"）。"""
        self.assertIsNone(ls.suggest_clear_for_asset("王五", "张三"))


class TestDecoupledFromRelocation(unittest.TestCase):
    """⭐ 用户口径：「清人与迁移各干各的」。

    这条不是靠自觉 —— 直接**读两个迁移模块的真实源码**断言它们没碰本模块。
    （断言口径同理：不在测试里复刻一份逻辑，而是检查引用关系本身。）
    """

    def _src(self, rel: str) -> str:
        path = BACKEND_DIR / "app" / rel
        return path.read_text(encoding="utf-8")

    def test_relocation_modules_do_not_reference_link_suggestions(self):
        for rel in ("services/relocation.py", "routers/relocation.py"):
            with self.subTest(module=rel):
                src = self._src(rel)
                self.assertNotIn("link_suggestions", src,
                                 f"{rel} 引用了关联建议模块 —— 两个场景耦合了")
                self.assertNotIn("apply-unlink", src)
                self.assertNotIn("build_clear_suggestions", src)

    def test_relocation_does_not_clear_asset_users(self):
        """迁移**不碰资产使用人**（预览里已明说）。清人是用户主动操作，不是迁移的副作用。"""
        src = self._src("services/relocation.py")
        self.assertNotIn("asset.user_name =", src, "迁移模块在改资产使用人 —— 越界了")


if __name__ == "__main__":
    unittest.main(verbosity=2)
