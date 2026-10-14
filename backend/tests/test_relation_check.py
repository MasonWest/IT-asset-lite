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


def asset(id_: int, code: str, user: str | None) -> rc.AssetInput:
    return rc.AssetInput(id=id_, code=code, user_name=user)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
