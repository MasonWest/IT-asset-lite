#!/usr/bin/env python
"""关系核对引擎的边界用例测试 —— **纯函数，不起服务、不连库、不占端口**。

跑法（在 backend/ 下）：
    py -3 -m unittest discover -s tests -v
或直接：
    py -3 backend/tests/test_relation_check.py

## 这份测试存在的意义

要求里那条「无人使用的资产点位不会被一律判为错误」**只有测试守得住** ——
它是个"没有发生的事"，界面上看不出来，代码评审也容易漏。
把它写成断言，将来有人图省事把 `none_asset` 判成红色错误，这里会当场红。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import relation_check as rc  # noqa: E402


def station(id_: int = 1, code: str = "W01", user: str | None = "林晓薇") -> rc.StationInput:
    return rc.StationInput(id=id_, code=code, user_name=user)


# 状态字面量。引擎刻意不 import `models`（理由见 `relation_check.IDLE_STATUS` 的注释），
# 所以这里用字面量；它们与 `models.AssetStatus` 相等由 `TestStatusLiteral` 钉住。
IDLE, IN_USE, REPAIR, SCRAPPED = "idle", "in_use", "repair", "scrapped"


def asset(
    id_: int, code: str, user: str | None, status: str | None = None
) -> rc.AssetInput:
    return rc.AssetInput(id=id_, code=code, user_name=user, status=status)


class TestPairState(unittest.TestCase):
    """`pair_state` 是核对与迁移预览**共用**的那一个判断，先把它的四态钉死。"""

    def test_both_registered_same(self):
        self.assertEqual(rc.pair_state("林晓薇", "林晓薇"), rc.CheckStatus.CONSISTENT)

    def test_both_registered_different(self):
        self.assertEqual(rc.pair_state("林晓薇", "陈默"), rc.CheckStatus.MISMATCH)

    def test_strip_whitespace_counts_as_same(self):
        self.assertEqual(rc.pair_state(" 林晓薇 ", "林晓薇"), rc.CheckStatus.CONSISTENT)

    def test_station_owner_asset_blank(self):
        self.assertEqual(rc.pair_state("林晓薇", None), rc.CheckStatus.UNREGISTERED)
        self.assertEqual(rc.pair_state("林晓薇", ""), rc.CheckStatus.UNREGISTERED)

    def test_station_blank_asset_owner(self):
        self.assertEqual(rc.pair_state(None, "林晓薇"), rc.CheckStatus.UNREGISTERED)
        self.assertEqual(rc.pair_state("", "林晓薇"), rc.CheckStatus.UNREGISTERED)

    def test_placeholder_slash_is_not_an_owner(self):
        # 「/」是"没填"的占位符，不是人名 —— 口径来自 ownership.has_owner
        self.assertEqual(rc.pair_state("/", "林晓薇"), rc.CheckStatus.UNREGISTERED)
        self.assertEqual(rc.pair_state("林晓薇", "/"), rc.CheckStatus.UNREGISTERED)
        self.assertEqual(rc.pair_state("/", "／"), rc.CheckStatus.UNDETERMINED)

    def test_both_blank(self):
        self.assertEqual(rc.pair_state(None, None), rc.CheckStatus.UNDETERMINED)
        self.assertEqual(rc.pair_state("   ", ""), rc.CheckStatus.UNDETERMINED)


class TestEmptyStation(unittest.TestCase):
    """没有设备的点位：无可核对对象。**不冒充"正常"**，也不报警。"""

    def test_no_assets(self):
        result = rc.check_station(station(), [])
        self.assertFalse(result.applicable)
        self.assertEqual(result.verdict, rc.CheckStatus.UNDETERMINED)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.asset_checks, [])

    def test_no_assets_no_owner(self):
        result = rc.check_station(station(user=None), [])
        self.assertFalse(result.applicable)
        self.assertEqual(result.issues, [])


class TestConsistent(unittest.TestCase):
    def test_single_matching_asset(self):
        result = rc.check_station(station(), [asset(10, "A-1", "林晓薇")])
        self.assertTrue(result.applicable)
        self.assertEqual(result.verdict, rc.CheckStatus.CONSISTENT)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.review_count, 0)

    def test_multiple_matching(self):
        result = rc.check_station(
            station(), [asset(10, "A-1", "林晓薇"), asset(11, "A-2", "林晓薇")]
        )
        self.assertEqual(result.verdict, rc.CheckStatus.CONSISTENT)
        self.assertEqual(result.issues, [])


class TestMismatch(unittest.TestCase):
    def test_mismatch_produces_review_not_error(self):
        result = rc.check_station(station(), [asset(10, "A-1", "陈默")])
        self.assertEqual(result.verdict, rc.CheckStatus.MISMATCH)
        self.assertEqual(result.review_count, 1)
        self.assertEqual(len(result.issues), 1)
        issue = result.issues[0]
        self.assertEqual(issue.status, rc.CheckStatus.MISMATCH)
        # 「待核对」不是「错误」：severity 是 review，没有任何 error 语义
        self.assertEqual(issue.severity, rc.Severity.REVIEW)
        self.assertNotIn("error", issue.severity)
        # 问题挂在**单台设备**粒度上，才能单独确认例外
        self.assertEqual(issue.scope, rc.Scope.ASSET)
        self.assertEqual(issue.asset_id, 10)
        self.assertEqual(len(issue.subjects), 2)

    def test_mixed_assets_verdict_is_worst(self):
        result = rc.check_station(
            station(),
            [
                asset(10, "A-1", "林晓薇"),   # 一致
                asset(11, "A-2", "陈默"),     # 差异
            ],
        )
        self.assertEqual(result.verdict, rc.CheckStatus.MISMATCH)
        self.assertEqual(result.review_count, 1)

    def test_station_side_unknown_is_not_mismatch(self):
        """点位没登记、设备有主 → **不是差异**（公共设备 / 备用机）。"""
        result = rc.check_station(station(user=None), [asset(10, "A-1", "陈默")])
        self.assertEqual(result.verdict, rc.CheckStatus.UNREGISTERED)
        self.assertEqual(result.review_count, 0)
        for issue in result.issues:
            self.assertNotEqual(issue.status, rc.CheckStatus.MISMATCH)
            self.assertNotEqual(issue.severity, rc.Severity.REVIEW)


class TestUnregistered(unittest.TestCase):
    def test_station_owner_asset_blank(self):
        result = rc.check_station(station(), [asset(10, "A-1", None)])
        self.assertEqual(result.verdict, rc.CheckStatus.UNREGISTERED)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.issues[0].status, rc.CheckStatus.UNREGISTERED)
        self.assertEqual(result.issues[0].severity, rc.Severity.INFO)
        self.assertEqual(result.issues[0].scope, rc.Scope.STATION)

    def test_asset_placeholder_slash_counts_as_unregistered(self):
        result = rc.check_station(station(), [asset(10, "A-1", "/")])
        self.assertEqual(result.verdict, rc.CheckStatus.UNREGISTERED)
        self.assertEqual(result.review_count, 0)

    def test_none_asset_station_is_info_not_error(self):
        """⭐ 要求点名的那条：**无人使用的资产点位不会被一律判为错误。**"""
        result = rc.check_station(station(user="/"), [asset(10, "A-1", "陈默")])
        self.assertEqual(result.review_count, 0)
        self.assertEqual(result.verdict, rc.CheckStatus.UNREGISTERED)
        self.assertTrue(result.issues)
        for issue in result.issues:
            self.assertEqual(issue.severity, rc.Severity.INFO)
        # 提示文本必须把"这是正常的"说出来，否则用户还是会当成错误
        self.assertIn("正常", result.issues[0].suggestion)

    def test_both_blank_no_issue_but_undetermined(self):
        result = rc.check_station(station(user=None), [asset(10, "A-1", None)])
        self.assertEqual(result.verdict, rc.CheckStatus.UNDETERMINED)
        self.assertEqual(result.issues, [])


class TestFingerprint(unittest.TestCase):
    def test_stable_for_same_input(self):
        s = station()
        a = asset(10, "A-1", "陈默")
        self.assertEqual(
            rc.asset_fingerprint(s.id, s.user_name, a),
            rc.asset_fingerprint(s.id, s.user_name, a),
        )

    def test_changes_when_asset_user_changes(self):
        s = station()
        f1 = rc.asset_fingerprint(s.id, s.user_name, asset(10, "A-1", "陈默"))
        f2 = rc.asset_fingerprint(s.id, s.user_name, asset(10, "A-1", "林晓薇"))
        self.assertNotEqual(f1, f2)

    def test_changes_when_station_user_changes(self):
        a = asset(10, "A-1", "陈默")
        f1 = rc.asset_fingerprint(1, "林晓薇", a)
        f2 = rc.asset_fingerprint(1, "赵敏", a)
        self.assertNotEqual(f1, f2)

    def test_includes_asset_code_against_id_reuse(self):
        """主键会被复用（INTEGER PRIMARY KEY，非 AUTOINCREMENT）。

        指纹里带上编码，旧例外才不会被一台**新设备**蹭上。
        """
        s = station()
        f1 = rc.asset_fingerprint(s.id, s.user_name, asset(10, "A-1", "陈默"))
        f2 = rc.asset_fingerprint(s.id, s.user_name, asset(10, "B-9", "陈默"))
        self.assertNotEqual(f1, f2)

    def test_station_fingerprint_tracks_membership(self):
        s = station()
        base = [asset(10, "A-1", "陈默")]
        f1 = rc.station_fingerprint(s, base)
        f2 = rc.station_fingerprint(s, base + [asset(11, "A-2", "林晓薇")])
        self.assertNotEqual(f1, f2)

    def test_station_fingerprint_ignores_order(self):
        s = station()
        rows = [asset(10, "A-1", "陈默"), asset(11, "A-2", "林晓薇")]
        self.assertEqual(
            rc.station_fingerprint(s, rows),
            rc.station_fingerprint(s, list(reversed(rows))),
        )


class TestIssueKeying(unittest.TestCase):
    def test_asset_issue_key_uses_zero_for_station_scope(self):
        st = rc.check_station(station(), [asset(10, "A-1", None)])
        station_issue = st.issues[0]
        self.assertEqual(station_issue.exception_key, (rc.RuleKey.STATION_VS_ASSET_USER, 0))

    def test_asset_scope_issue_key_uses_asset_id(self):
        st = rc.check_station(station(), [asset(10, "A-1", "陈默")])
        self.assertEqual(st.issues[0].exception_key, (rc.RuleKey.STATION_VS_ASSET_USER, 10))


class TestStatusLiteral(unittest.TestCase):
    """引擎里那个 `IDLE_STATUS` 必须和模型枚举是同一个值。

    ⚠️ 这是"两处写同一个字面量"的代价，所以配一条断言：
    `relation_check.py` 为了让引擎不背数据库副作用（会把 DB 路径固定在 import 时刻，
    弄崩 `test_light.py` 的临时库前提门）**没有** import `models`。
    哪天有人把 `AssetStatus.IDLE` 改成别的值，这里会当场红。
    """

    def test_idle_literal_matches_model_enum(self):
        from app.models import AssetStatus

        self.assertEqual(rc.IDLE_STATUS, AssetStatus.IDLE)


class TestStatusVsHolder(unittest.TestCase):
    """「在库」的设备，人和工位却说它在被这个人用（v2.9.12）。

    ## 这份测试守的是什么

    这条规则最容易死的两种方式都不是崩溃，而是**误报**和**漏报**：

    - 判据写成"在库且有点位" → 空工位上的备用机会全部中招（真库里正好有 5 套
      这样的同款套装），用户第一次用就会把整页关掉；
    - `status` 没接上（调用方不给）→ 规则静默永不触发，界面上永远 0 条，
      而这种"什么都没发生"在页面上是看不出来的。

    所以正反两面都要钉住：该报的必须报，不该报的必须一条都不出。
    """

    def test_idle_on_occupied_station_reports_review(self):
        st = rc.check_station(
            station(), [asset(10, "A-1", "林晓薇", status=IDLE)]
        )
        hits = [i for i in st.issues if i.rule == rc.RuleKey.ASSET_STATUS_VS_HOLDER]
        self.assertEqual(len(hits), 1)
        issue = hits[0]
        self.assertEqual(issue.severity, rc.Severity.REVIEW, "是待核对，不是错误")
        self.assertEqual(issue.status, rc.CheckStatus.MISMATCH)
        self.assertEqual(issue.scope, rc.Scope.ASSET, "粒度必须是单台，才能单独确认例外")
        self.assertEqual(issue.asset_id, 10)
        self.assertEqual(issue.exception_key, (rc.RuleKey.ASSET_STATUS_VS_HOLDER, 10))
        self.assertIn("在库", issue.title)

    def test_verdict_reflects_the_conflict(self):
        """⭐ 不能出现"卡片说自己一致、徽标却在报 1 条"这种自相矛盾。"""
        st = rc.check_station(
            station(), [asset(10, "A-1", "林晓薇", status=IDLE)]
        )
        self.assertEqual(st.verdict, rc.CheckStatus.MISMATCH)
        self.assertEqual(st.review_count, 1)

    def test_in_use_is_fine(self):
        st = rc.check_station(
            station(), [asset(10, "A-1", "林晓薇", status=IN_USE)]
        )
        self.assertEqual(st.issues, [])
        self.assertEqual(st.verdict, rc.CheckStatus.CONSISTENT)

    def test_spare_machine_on_empty_station_is_not_reported(self):
        """⭐ 反向断言：空工位上的在库设备是**备用机 / 待分配**，报了就是 100% 误报。"""
        st = rc.check_station(
            station(user=None), [asset(10, "A-1", None, status=IDLE)]
        )
        self.assertFalse(
            [i for i in st.issues if i.rule == rc.RuleKey.ASSET_STATUS_VS_HOLDER]
        )

    def test_idle_without_asset_owner_is_not_reported(self):
        """在库 + 点位有人 + 设备没登记人 = 已经有 `asset_user_unregistered` 在管。"""
        st = rc.check_station(
            station(), [asset(10, "A-1", None, status=IDLE)]
        )
        self.assertFalse(
            [i for i in st.issues if i.rule == rc.RuleKey.ASSET_STATUS_VS_HOLDER]
        )

    def test_idle_with_different_user_is_only_the_name_rule(self):
        """人和工位本来就不一致 → 由「使用人」那条规则报，不要同一台出两条同因的题。"""
        st = rc.check_station(
            station(), [asset(10, "A-1", "陈默", status=IDLE)]
        )
        rules = [i.rule for i in st.issues]
        self.assertEqual(rules, [rc.RuleKey.STATION_VS_ASSET_USER])

    def test_missing_status_never_fires(self):
        """⭐ 「没数据」不等于「矛盾」：调用方没给状态，这条规则必须一声不吭。"""
        for blank in (None, ""):
            with self.subTest(status=blank):
                st = rc.check_station(station(), [asset(10, "A-1", "林晓薇", status=blank)])
                self.assertFalse(
                    [i for i in st.issues if i.rule == rc.RuleKey.ASSET_STATUS_VS_HOLDER]
                )

    def test_other_statuses_are_not_reported(self):
        """维修中 / 已报废 挂在有人点位上「算不算异常」业务还没定，先不报（决策 4）。"""
        for status in (REPAIR, SCRAPPED):
            with self.subTest(status=status):
                st = rc.check_station(station(), [asset(10, "A-1", "林晓薇", status=status)])
                self.assertFalse(
                    [i for i in st.issues if i.rule == rc.RuleKey.ASSET_STATUS_VS_HOLDER]
                )


class TestStatusFingerprint(unittest.TestCase):
    def test_changes_with_status(self):
        """状态变了 → 旧例外必须作废（同一个人、同一个点位也一样）。"""
        s = station()
        f1 = rc.status_fingerprint(s.id, s.user_name, asset(10, "A-1", "林晓薇", "idle"))
        f2 = rc.status_fingerprint(s.id, s.user_name, asset(10, "A-1", "林晓薇", "in_use"))
        self.assertNotEqual(f1, f2)

    def test_changes_with_both_users(self):
        s = station()
        f1 = rc.status_fingerprint(s.id, s.user_name, asset(10, "A-1", "林晓薇", "idle"))
        f2 = rc.status_fingerprint(s.id, s.user_name, asset(10, "A-1", "陈默", "idle"))
        self.assertNotEqual(f1, f2)
        f3 = rc.status_fingerprint(s.id, "赵敏", asset(10, "A-1", "林晓薇", "idle"))
        self.assertNotEqual(f1, f3)

    def test_includes_asset_code_against_id_reuse(self):
        s = station()
        f1 = rc.status_fingerprint(s.id, s.user_name, asset(10, "A-1", "林晓薇", "idle"))
        f2 = rc.status_fingerprint(s.id, s.user_name, asset(10, "B-9", "林晓薇", "idle"))
        self.assertNotEqual(f1, f2)

    def test_differs_from_the_name_rule_fingerprint(self):
        """两条规则的指纹不能撞 —— 撞了会让一条规则的例外压住另一条。"""
        s = station()
        a = asset(10, "A-1", "林晓薇", "idle")
        self.assertNotEqual(
            rc.status_fingerprint(s.id, s.user_name, a),
            rc.asset_fingerprint(s.id, s.user_name, a),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
