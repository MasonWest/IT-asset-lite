#!/usr/bin/env python
"""点位迁移规划器的测试 —— 同样是**纯函数**：不起服务、不连库、不占端口。

跑法（在 backend/ 下）：
    py -3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import relocation as rl  # noqa: E402


def st(id_: int, code: str, user: str | None = None, active: bool = True) -> rl.StationRef:
    return rl.StationRef(id=id_, code=code, user_name=user, active=active)


def a(id_: int, code: str, user: str | None, station_id: int | None, station_code: str | None = None):
    return rl.AssetRef(
        id=id_, code=code, user_name=user, station_id=station_id, station_code=station_code
    )


class TestAssetMove(unittest.TestCase):
    def test_happy_path(self):
        target = st(2, "W02", "林晓薇")
        sources = {1: st(1, "W01", "林晓薇")}
        plan = rl.plan_asset_move(
            [a(10, "A-1", "林晓薇", 1, "W01"), a(11, "A-2", "林晓薇", 1, "W01")],
            target,
            sources,
        )
        self.assertTrue(plan.ok)
        self.assertEqual(len(plan.moves), 2)
        self.assertEqual(plan.affected_asset_count, 2)
        self.assertEqual(plan.conflicts, [])

    def test_no_assets_is_conflict(self):
        plan = rl.plan_asset_move([], st(2, "W02"))
        self.assertFalse(plan.ok)
        self.assertTrue(plan.conflicts)

    def test_inactive_target_is_conflict(self):
        plan = rl.plan_asset_move(
            [a(10, "A-1", None, 1, "W01")], st(2, "W02", active=False)
        )
        self.assertFalse(plan.ok)
        self.assertTrue(any("已删除" in c for c in plan.conflicts))

    def test_all_noop_is_conflict(self):
        plan = rl.plan_asset_move(
            [a(10, "A-1", None, 2, "W02")], st(2, "W02")
        )
        self.assertFalse(plan.ok)
        self.assertEqual(plan.noop_count, 1)

    def test_relation_mismatch_becomes_confirmation_not_conflict(self):
        """目标点位的人和设备的人不一样 → **只提示，不阻止**。"""
        plan = rl.plan_asset_move(
            [a(10, "A-1", "陈默", 1, "W01")], st(2, "W02", "林晓薇")
        )
        self.assertTrue(plan.ok)
        self.assertEqual(plan.conflicts, [])
        self.assertTrue(plan.confirmations)

    def test_moves_never_change_asset_user(self):
        """迁移**绝不**自动改设备使用人（要求：不得自动推断使用人变更）。"""
        plan = rl.plan_asset_move(
            [a(10, "A-1", "陈默", 1, "W01")], st(2, "W02", "林晓薇")
        )
        self.assertEqual(plan.moves[0].asset_user, "陈默")

    def test_relation_state_before_move(self):
        plan = rl.plan_asset_move(
            [a(10, "A-1", "陈默", 1, "W01")], st(2, "W02"), {1: st(1, "W01", "林晓薇")}
        )
        self.assertEqual(plan.moves[0].relation_state, "mismatch")


class TestToken(unittest.TestCase):
    def test_stable_for_same_input(self):
        p1 = rl.plan_asset_move([a(10, "A-1", "陈默", 1, "W01")], st(2, "W02", "林晓薇"))
        p2 = rl.plan_asset_move([a(10, "A-1", "陈默", 1, "W01")], st(2, "W02", "林晓薇"))
        self.assertEqual(p1.token, p2.token)

    def test_changes_when_target_user_changes(self):
        p1 = rl.plan_asset_move([a(10, "A-1", "陈默", 1, "W01")], st(2, "W02", "林晓薇"))
        p2 = rl.plan_asset_move([a(10, "A-1", "陈默", 1, "W01")], st(2, "W02", "赵敏"))
        self.assertNotEqual(p1.token, p2.token)

    def test_changes_when_asset_moves(self):
        p1 = rl.plan_asset_move([a(10, "A-1", "陈默", 1, "W01")], st(2, "W02"))
        p2 = rl.plan_asset_move([a(10, "A-1", "陈默", 3, "W03")], st(2, "W02"))
        self.assertNotEqual(p1.token, p2.token)


class TestStationMigration(unittest.TestCase):
    def test_requires_some_content(self):
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02"),
            [a(10, "A-1", "林晓薇", 1, "W01")],
            move_assets=False, move_user=False,
        )
        self.assertFalse(plan.ok)
        self.assertTrue(plan.conflicts)

    def test_same_station_is_conflict(self):
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(1, "W01"),
            [], move_assets=True, move_user=True,
        )
        self.assertFalse(plan.ok)
        self.assertTrue(any("同一个" in c for c in plan.conflicts))

    def test_retire_with_assets_without_move_is_conflict(self):
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02"),
            [a(10, "A-1", "林晓薇", 1, "W01")],
            move_assets=False, move_user=False, retire_source=True,
        )
        self.assertFalse(plan.ok)

    def test_move_assets_happy(self):
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02", "林晓薇"),
            [a(10, "A-1", "林晓薇", 1, "W01"), a(11, "A-2", "林晓薇", 1, "W01")],
            move_assets=True, move_user=False,
        )
        self.assertTrue(plan.ok)
        self.assertEqual(len(plan.moves), 2)

    def test_move_user_overwrite_is_confirmation(self):
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02", "陈默"),
            [a(10, "A-1", "林晓薇", 1, "W01")],
            move_assets=True, move_user=True,
        )
        self.assertTrue(plan.ok)
        self.assertEqual(plan.conflicts, [])
        self.assertTrue(any("会丢失" in c or "改写成" in c for c in plan.confirmations))
        self.assertTrue(plan.station_changes)

    def test_move_user_blank_source_never_overwrites_target(self):
        """源点位没有归属时，勾了「迁移使用人」也**绝不写** —— 更不许拿空去覆盖目标点位。

        这条是从一个真实缺陷反推出来的：以前判"有没有人"用的是 `(x or '').strip()`，
        于是源点位的 `/` 被当成一个**真使用人**，"迁移"它就等于把目标点位的
        「林晓薇」覆盖成 `/`（丢归属），而预览里还写着"不会改变任何东西"。
        """
        plan = rl.plan_station_migration(
            st(1, "W01", None), st(2, "W02", "陈默"),
            [a(10, "A-1", None, 1, "W01")],
            move_assets=True, move_user=True,
        )
        self.assertTrue(plan.ok)
        # 不要求点头：这是"说明"，不是"差异"
        self.assertEqual(plan.confirmations, [])
        self.assertTrue(any("没有归属可搬" in n for n in plan.notes))
        self.assertFalse(plan.user_move)
        self.assertEqual(plan.target_user_after, "陈默")  # 目标点位的归属原样保留
        self.assertEqual(plan.source_user_after, None)
        # 也不许出现"要清空/要改写"这类变更说明
        self.assertFalse(any("使用人清空" in c for c in plan.station_changes))

    def test_slash_source_is_not_an_owner(self):
        """`/` 是「没填」，不是人名 —— 与 `ownership.has_owner()` 同口径（决策 76）。

        半角 `/` 与全角 `／` 都要认（中文输入法下打全角太正常了）。
        """
        for mark in ("/", "／"):
            with self.subTest(mark=mark):
                plan = rl.plan_station_migration(
                    st(1, "W01", mark), st(2, "W02", "林晓薇"),
                    [a(10, "A-1", "林晓薇", 1, "W01")],
                    move_assets=True, move_user=True,
                )
                self.assertFalse(plan.user_move, f"{mark} 不该被当成使用人")
                self.assertEqual(plan.target_user_after, "林晓薇")
                self.assertTrue(any("没有归属可搬" in n for n in plan.notes))

    def test_slash_target_is_not_an_owner(self):
        """目标点位只有 `/` 时，等于"没有登记使用人"，要给出「公共设备」的那句提醒。"""
        plan = rl.plan_asset_move([a(10, "A-1", "林晓薇", 1, "W01")], st(2, "W02", "/"), {1: st(1, "W01", "/")})
        self.assertTrue(plan.ok)
        self.assertTrue(any("没有登记使用人" in c for c in plan.confirmations))

    def test_owner_before_after_when_moving(self):
        """归属会跟着走时，前后值要算对（界面靠它显示「迁移前 → 迁移后」）。"""
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02", "陈默"),
            [a(10, "A-1", "林晓薇", 1, "W01")],
            move_assets=True, move_user=True,
        )
        self.assertTrue(plan.user_move)
        self.assertEqual(
            (plan.source_user_before, plan.source_user_after), ("林晓薇", None)
        )
        self.assertEqual(
            (plan.target_user_before, plan.target_user_after), ("陈默", "林晓薇")
        )

    def test_owner_before_after_when_not_moving(self):
        """不搬归属时，两侧前后都必须是原值 —— 界面据此说"不变"。"""
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02", "陈默"),
            [a(10, "A-1", "林晓薇", 1, "W01")],
            move_assets=True, move_user=False,
        )
        self.assertFalse(plan.user_move)
        self.assertEqual(plan.source_user_after, "林晓薇")
        self.assertEqual(plan.target_user_after, "陈默")

    def test_retire_adds_station_change(self):
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02"),
            [a(10, "A-1", "林晓薇", 1, "W01")],
            move_assets=True, move_user=False, retire_source=True,
        )
        self.assertTrue(plan.ok)
        self.assertTrue(any("退役" in c for c in plan.station_changes))

    def test_never_touches_coordinates(self):
        """规划器的产物里**没有任何坐标字段** —— 排版与业务归属不许混。

        这条断言是防回归的：将来有人"顺手"在迁移里加 x/y，这里会红。
        """
        plan = rl.plan_station_migration(
            st(1, "W01", "林晓薇"), st(2, "W02"),
            [a(10, "A-1", "林晓薇", 1, "W01")],
            move_assets=True, move_user=True,
        )
        fields = set()
        for move in plan.moves:
            fields |= set(vars(move))
        self.assertFalse({"x", "y", "facing"} & fields)
        self.assertFalse({"x", "y", "facing"} & set(vars(rl.Plan(mode="assets"))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
