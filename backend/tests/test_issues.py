#!/usr/bin/env python
"""问题中心的测试 —— 纯函数：不起服务、不连库、不占端口。

跑法（在 backend/ 下）：
    py -3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import issues as iss  # noqa: E402
from app.services import relation_check as rc  # noqa: E402


def scan(
    station_id: int,
    code: str,
    user: str | None,
    assets: list[tuple],
):
    """`assets` 每项是 `(id, 编码, 使用人)` 或 `(id, 编码, 使用人, 状态)`。

    状态是 v2.9.12 加的（「状态 ↔ 归属」规则要用）；不给 = 调用方不关心状态，
    那条规则一律不响，所以老的 3 元组写法全部照旧有效。
    """

    def input_of(row):
        aid, acode, auser, *rest = row
        return rc.AssetInput(
            id=aid, code=acode, user_name=auser, status=rest[0] if rest else None
        )

    return iss.StationScan(
        station=rc.StationInput(id=station_id, code=code, user_name=user),
        assets=tuple(input_of(a) for a in assets),
    )


def keys(problems) -> list[str]:
    return [p.key for p in problems]


class TestKeyStability(unittest.TestCase):
    """`key` 是"身份"，不是"状态"。"""

    def test_key_shape(self):
        self.assertEqual(iss.problem_key("asset_unbound", "asset", 12), "asset_unbound:asset:12")

    def test_same_input_same_key(self):
        s = [scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])]
        a = keys(iss.build_problems(s))
        b = keys(iss.build_problems(s))
        self.assertEqual(a, b)
        self.assertTrue(a)

    def test_key_has_no_state_no_time_no_fingerprint(self):
        """⭐ key 里**不许**出现指纹 / 时间 / 计数 —— 否则用户改一下别的字段它就换身份了。"""
        problems = iss.build_problems(
            [
                scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")]),
                scan(2, "W02", "林晓薇", [(11, "A-2", None)]),
            ],
            unbound=[iss.UnboundAsset(id=12, code="A-3")],
        )
        for p in problems:
            with self.subTest(key=p.key):
                if p.fingerprint:  # 非关系类问题没有指纹
                    self.assertNotIn(p.fingerprint, p.key)
                self.assertNotIn("v1:", p.key)
                # 规则 + 粒度 + 纯数字 id，仅此而已
                rule, scope, object_id = p.key.split(":")
                self.assertIn(rule, iss.IssueRule.ALL)
                self.assertIn(scope, ("asset", "station", "exception"))
                self.assertTrue(object_id.isdigit())

    def test_key_unchanged_when_unrelated_field_changes(self):
        """改了**不影响这条问题**的东西（比如另一台设备的备注），key 必须原样。"""
        before = iss.build_problems([scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])])
        after = iss.build_problems([scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])])
        self.assertEqual(keys(before), keys(after))


class TestRules(unittest.TestCase):
    def test_mismatch_is_review(self):
        problems = iss.build_problems([scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])])
        self.assertEqual(len(problems), 1)
        p = problems[0]
        self.assertEqual(p.rule, iss.IssueRule.STATION_VS_ASSET_USER)
        self.assertEqual(p.severity, rc.Severity.REVIEW)
        self.assertEqual(p.source, iss.IssueSource.RELATION)
        self.assertEqual(p.key, "station_vs_asset_user:asset:10")

    def test_unregistered_asset_is_info_not_error(self):
        """⭐ 设备没登记使用人 = 提示，**不许**判成错误（用户明确要求）。"""
        problems = iss.build_problems([scan(1, "W01", "林晓薇", [(10, "A-1", None)])])
        self.assertEqual(len(problems), 1)
        p = problems[0]
        self.assertEqual(p.rule, iss.IssueRule.ASSET_USER_UNREGISTERED)
        self.assertEqual(p.severity, rc.Severity.INFO)
        self.assertEqual(p.key, "asset_user_unregistered:asset:10")

    def test_slash_counts_as_unregistered(self):
        """`/` 是「没填」不是人名 —— 与 `has_owner` 同口径（决策 76）。"""
        for mark in ("/", "／", "", "   "):
            with self.subTest(mark=mark):
                problems = iss.build_problems([scan(1, "W01", "林晓薇", [(10, "A-1", mark)])])
                self.assertEqual([p.rule for p in problems], [iss.IssueRule.ASSET_USER_UNREGISTERED])

    def test_station_without_owner_is_excluded(self):
        """⭐ 点位没人 + 有设备 = **合法现状，不进问题中心**（否则用户学会无视整个页面）。"""
        problems = iss.build_problems([scan(1, "W01", None, [(10, "A-1", "林晓薇")])])
        self.assertEqual(problems, [])
        problems2 = iss.build_problems([scan(1, "W01", "/", [(10, "A-1", "林晓薇")])])
        self.assertEqual(problems2, [])

    def test_unbound_asset(self):
        problems = iss.build_problems(
            [], unbound=[iss.UnboundAsset(id=12, code="A-3", user_name="林晓薇")]
        )
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].rule, iss.IssueRule.ASSET_UNBOUND)
        self.assertEqual(problems[0].severity, rc.Severity.INFO)
        self.assertEqual(problems[0].source, iss.IssueSource.INTEGRITY)
        self.assertEqual(problems[0].action_url, "/?workstation=none")

    def test_asset_on_deleted_station_is_review(self):
        problems = iss.build_problems(
            [],
            orphans=[
                iss.OrphanAsset(id=13, code="A-4", user_name=None, station_id=99, station_code="W99")
            ],
        )
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].rule, iss.IssueRule.ASSET_ON_DELETED_STATION)
        self.assertEqual(problems[0].severity, rc.Severity.REVIEW)
        self.assertEqual(problems[0].action_url, "/asset/13")

    def test_stale_exception_problem(self):
        """存下来的指纹对不上现算的 → 冒一条"例外已失效"。"""
        s = scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])
        fresh = iss.build_problems([s])[0].fingerprint
        stale = iss.ConfirmedException(
            id=7,
            rule=iss.IssueRule.STATION_VS_ASSET_USER,
            workstation_id=1,
            asset_id=10,
            reason="公共区域",
            fingerprint="v1:这不是当前指纹",
        )
        problems = iss.build_problems([s], exceptions=[stale])
        stale_problems = [p for p in problems if p.rule == iss.IssueRule.RELATION_EXCEPTION_STALE]
        self.assertEqual(len(stale_problems), 1)
        self.assertEqual(stale_problems[0].key, "relation_exception_stale:exception:7")
        self.assertEqual(stale_problems[0].action_url, "/map?station=W01")
        # 差异本身**重新冒出来**（没有被旧例外捂着）
        mismatch = [p for p in problems if p.rule == iss.IssueRule.STATION_VS_ASSET_USER]
        self.assertEqual(len(mismatch), 1)
        self.assertFalse(mismatch[0].handled)
        self.assertNotEqual(fresh, stale.fingerprint)


class TestStatusVsHolder(unittest.TestCase):
    """「在库」的设备挂在有人用的工位上，使用人还是同一个（v2.9.12）。

    真库 151 台里命中 10 台（5 套电脑）。这条规则的价值全在**误报率**上：
    整套「在库的电脑套装」有 10 套，只有工位已归人的那 5 套该报。
    """

    def _one(self, *assets, user="员工A", station_user="员工A", exceptions=()):
        return iss.build_problems(
            [scan(1, "W01", station_user, list(assets))], exceptions=exceptions
        )

    def test_idle_on_occupied_station_is_a_review_problem(self):
        problems = self._one((10, "A-1", "员工A", "idle"))
        self.assertEqual(len(problems), 1)
        p = problems[0]
        self.assertEqual(p.rule, iss.IssueRule.ASSET_STATUS_VS_HOLDER)
        self.assertEqual(p.severity, rc.Severity.REVIEW)
        self.assertEqual(p.source, iss.IssueSource.RELATION)
        self.assertEqual(p.key, "asset_status_vs_holder:asset:10", "身份只随设备 id 变")
        self.assertEqual(p.action_url, "/asset/10", "落点必须是能改状态的那一页")
        self.assertIn("W01", p.detail)
        self.assertIn("A-1", p.detail)

    def test_action_url_is_whitelisted(self):
        """`build_problems` 里生成 URL 就会断言白名单 —— 这里显式再钉一次。"""
        p = self._one((10, "A-1", "员工A", "idle"))[0]
        self.assertTrue(
            any(re.match(pat, p.action_url) for pat in iss.ALLOWED_ACTION_PATTERNS)
        )

    def test_spare_machine_is_silent(self):
        """⭐ 反向断言：空工位上的同款套装（两边都没人）报了就是 100% 误报。"""
        self.assertEqual(self._one((10, "A-1", None, "idle"), station_user=None), [])
        self.assertEqual(self._one((10, "A-1", None, "idle"), station_user="/"), [])

    def test_in_use_is_silent(self):
        self.assertEqual(self._one((10, "A-1", "员工A", "in_use")), [])

    def test_no_status_is_silent(self):
        self.assertEqual(self._one((10, "A-1", "员工A")), [])

    def test_different_user_is_left_to_the_name_rule(self):
        problems = self._one((10, "A-1", "员工B", "idle"))
        self.assertEqual([p.rule for p in problems], [iss.IssueRule.STATION_VS_ASSET_USER])

    def test_valid_exception_marks_handled(self):
        fresh = self._one((10, "A-1", "员工A", "idle"))[0].fingerprint
        exc = iss.ConfirmedException(
            id=11,
            rule=iss.IssueRule.ASSET_STATUS_VS_HOLDER,
            workstation_id=1,
            asset_id=10,
            reason="备用机，人还没验收",
            fingerprint=fresh,
        )
        problems = self._one((10, "A-1", "员工A", "idle"), exceptions=[exc])
        self.assertEqual(len(problems), 1, "盖住了也仍然列着（能看见、能撤销）")
        self.assertTrue(problems[0].handled)
        self.assertEqual(problems[0].exception_reason, "备用机，人还没验收")
        self.assertEqual(
            iss.count_pending(problems), {rc.Severity.REVIEW: 0, rc.Severity.INFO: 0}
        )

    def test_exception_dies_when_the_whole_relation_changes(self):
        """工位换了人、设备跟着换人、状态还在库 → 是**另一次**矛盾，旧例外不能盖住它。

        注意场景的选法：只改设备使用人（不改点位使用人）时，两个名字就不相等了，
        这条规则本身就不成立了 —— 那时问题整个消失，不该再冒"例外失效"（见下一个用例）。
        要让"指纹变了但问题还在"，必须**两边一起换人**。
        """
        fresh = self._one((10, "A-1", "员工A", "idle"), station_user="员工A")[0].fingerprint
        exc = iss.ConfirmedException(
            id=12,
            rule=iss.IssueRule.ASSET_STATUS_VS_HOLDER,
            workstation_id=1,
            asset_id=10,
            reason="备用机",
            fingerprint=fresh,
        )
        problems = self._one(
            (10, "A-1", "员工B", "idle"), station_user="员工B", exceptions=[exc]
        )
        rules = [p.rule for p in problems]
        self.assertIn(iss.IssueRule.RELATION_EXCEPTION_STALE, rules)
        status_problems = [
            p for p in problems if p.rule == iss.IssueRule.ASSET_STATUS_VS_HOLDER
        ]
        self.assertEqual(len(status_problems), 1)
        self.assertFalse(status_problems[0].handled, "旧例外不许压住新矛盾")

    def test_exception_goes_quiet_when_the_status_is_fixed(self):
        """⭐ 用户照建议把状态改成「在用」= 做对了。这时**不许**再冒"你的例外失效了"。

        这条是反向断言：没有它，把状态改对的用户会收到一条"提醒你去处理"，
        而实际上什么都没剩 —— 这种噪音会让例外功能变得不敢用。
        """
        fresh = self._one((10, "A-1", "员工A", "idle"))[0].fingerprint
        exc = iss.ConfirmedException(
            id=13,
            rule=iss.IssueRule.ASSET_STATUS_VS_HOLDER,
            workstation_id=1,
            asset_id=10,
            reason="备用机",
            fingerprint=fresh,
        )
        problems = self._one((10, "A-1", "员工A", "in_use"), exceptions=[exc])
        self.assertEqual(problems, [])

    def test_exception_is_rule_scoped(self):
        """设备级例外挂在 (rule, asset_id) 上 —— 不能跨规则生效。"""
        fresh = self._one((10, "A-1", "员工A", "idle"))[0].fingerprint
        exc = iss.ConfirmedException(
            id=14,
            rule=iss.IssueRule.STATION_VS_ASSET_USER,  # 挂错规则
            workstation_id=1,
            asset_id=10,
            reason="别的规则的理由",
            fingerprint=fresh,
        )
        problems = self._one((10, "A-1", "员工A", "idle"), exceptions=[exc])
        status_problems = [
            p for p in problems if p.rule == iss.IssueRule.ASSET_STATUS_VS_HOLDER
        ]
        self.assertEqual(len(status_problems), 1)
        self.assertFalse(status_problems[0].handled)


class TestLightUserAmbiguity(unittest.TestCase):
    """记在人手上的轻资产，而这个人名下有多个点位 —— 兜底规则（v2.9.6）。

    ⚠️ 这条**不是错误**：人手上没有位置关系，系统只能给出候选。
    它在问题中心存在的意义是"别只存在于搜索里"——员工A今天多了一个工位，
    不会挑他去搜索的时候发生。
    """

    def test_multiple_stations_reports_one_info_problem(self):
        problems = iss.build_problems(
            [],
            light_user_ambiguous=[
                iss.LightUserAmbiguity(
                    holding_id=42, user="员工A", category_name="键鼠套装",
                    station_codes=("W52", "W63"),
                )
            ],
        )
        self.assertEqual(len(problems), 1)
        p = problems[0]
        self.assertEqual(p.rule, iss.IssueRule.LIGHT_USER_STATION_AMBIGUOUS)
        self.assertEqual(p.severity, rc.Severity.INFO, "是提示，不是错误")
        self.assertEqual(p.source, iss.IssueSource.INTEGRITY)
        self.assertEqual(p.key, "light_user_station_ambiguous:light_holding:42")
        self.assertEqual(p.action_url, "/light", "动作只能是已存在且在白名单里的入口")
        self.assertIn("员工A", p.title)
        self.assertIn("2", p.title)
        self.assertIn("W52", p.detail)
        self.assertIn("W63", p.detail)

    def test_station_count_in_title_follows_input(self):
        problems = iss.build_problems(
            [],
            light_user_ambiguous=[
                iss.LightUserAmbiguity(
                    holding_id=7, user="张三", category_name="内存",
                    station_codes=("W01", "W02", "W03"),
                )
            ],
        )
        self.assertIn("3 个点位", problems[0].title)
        self.assertIn("W01、W02、W03", problems[0].detail)

    def test_no_ambiguity_no_problem(self):
        """一个点位都没有 / 只有一个，都不是"歧义" —— 别为了兜底把正常情况也报出来。"""
        self.assertEqual(iss.build_problems([]), [])
        self.assertEqual(
            iss.build_problems([], light_user_ambiguous=[]),
            [],
        )

    def test_key_is_stable_across_station_changes(self):
        """key 只随 holding_id 变 —— 点位从 2 个变 3 个，它还是同一个问题。"""
        a = iss.build_problems(
            [], light_user_ambiguous=[
                iss.LightUserAmbiguity(holding_id=9, user="张三", station_codes=("W01", "W02"))
            ],
        )[0]
        b = iss.build_problems(
            [], light_user_ambiguous=[
                iss.LightUserAmbiguity(holding_id=9, user="张三", station_codes=("W01", "W02", "W03"))
            ],
        )[0]
        self.assertEqual(a.key, b.key)
        self.assertNotEqual(a.title, b.title, "文案可以变，身份不能变")


class TestExceptionsHandled(unittest.TestCase):
    def _pair(self):
        s = scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])
        fresh = iss.build_problems([s])[0].fingerprint
        return s, fresh

    def test_valid_exception_marks_handled_and_leaves_pending(self):
        """⭐ 有效例外 → `handled=True`，**不计入待办**，但仍可查看 / 可撤销。"""
        s, fresh = self._pair()
        exc = iss.ConfirmedException(
            id=3,
            rule=iss.IssueRule.STATION_VS_ASSET_USER,
            workstation_id=1,
            asset_id=10,
            reason="公共区域",
            fingerprint=fresh,
        )
        problems = iss.build_problems([s], exceptions=[exc])
        self.assertEqual(len(problems), 1)
        p = problems[0]
        self.assertTrue(p.handled)
        self.assertEqual(p.exception_id, 3)
        self.assertEqual(p.exception_reason, "公共区域")
        # 待办数为 0，但条目还在（能查看、能撤销）
        self.assertEqual(iss.count_pending(problems), {rc.Severity.REVIEW: 0, rc.Severity.INFO: 0})
        self.assertEqual(len(problems), 1)

    def test_exception_for_other_asset_does_not_leak(self):
        s = scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])
        exc = iss.ConfirmedException(
            id=4,
            rule=iss.IssueRule.STATION_VS_ASSET_USER,
            workstation_id=1,
            asset_id=999,
            reason="别的设备",
            fingerprint="v1:x",
        )
        problems = iss.build_problems([s], exceptions=[exc])
        self.assertFalse(problems[0].handled)

    def test_exception_on_deleted_station_is_not_reported(self):
        """点位被删了 → 这条例外**不报**。

        理由：关系已经不存在，它不会掩盖任何新差异；而点位的侧栏进不去（只认 active），
        报出来也给不出一个能撤销它的真实入口 —— 那会变成"点了处理却只能干看着"。
        它作为历史留在库里；点位哪天被恢复，这条会自己重新参与判定。
        """
        exc = iss.ConfirmedException(
            id=9,
            rule=iss.IssueRule.STATION_VS_ASSET_USER,
            workstation_id=99,
            asset_id=10,
            reason="当时合理",
            fingerprint="v1:whatever",
        )
        problems = iss.build_problems([], exceptions=[exc])
        self.assertEqual(problems, [])


class TestActionWhitelist(unittest.TestCase):
    """`action.url` 只由规则模板生成，且必须落在白名单里。"""

    def test_every_rule_template_is_allowed(self):
        samples = {"station_vs_asset_user": ("W01", 10)}
        for spec in iss.RULES:
            with self.subTest(rule=spec.rule):
                url = iss._action_url(spec, station_code="W01", asset_id=10)
                iss._assert_action_allowed(url, spec.rule)  # 不抛就算过

    def test_all_built_urls_are_allowed(self):
        problems = iss.build_problems(
            [
                scan(1, "W01", "林晓薇", [(10, "A-1", "陈默"), (11, "A-2", None)]),
                scan(2, "W02", "林晓薇", [(12, "A-3", "陈默")]),
            ],
            unbound=[iss.UnboundAsset(id=20, code="A-9")],
            orphans=[iss.OrphanAsset(id=21, code="A-8", user_name=None, station_id=98, station_code="W98")],
            exceptions=[
                iss.ConfirmedException(
                    id=5,
                    rule=iss.IssueRule.STATION_VS_ASSET_USER,
                    workstation_id=1,
                    asset_id=10,
                    reason="r",
                    fingerprint="v1:stale",
                )
            ],
        )
        self.assertTrue(problems)
        for p in problems:
            with self.subTest(key=p.key):
                iss._assert_action_allowed(p.action_url, p.rule)
                self.assertTrue(p.action_url.startswith("/"))

    def test_bogus_template_raises(self):
        """规则写错要**当场炸**，不能把用户导到死链（也用 raise 而不是 assert，防 -O 静默）。"""
        bad = iss.RuleSpec(
            rule="r",
            label="l",
            severity=rc.Severity.INFO,
            source=iss.IssueSource.INTEGRITY,
            reason="x",
            action_label="去",
            action_url="https://evil.example.com/x",
        )
        with self.assertRaises(ValueError):
            iss._assert_action_allowed(iss._action_url(bad, station_code="W01"), "r")
        with self.assertRaises(ValueError):
            iss._assert_action_allowed("/map?station=a&next=//evil", "r")


class TestSummarize(unittest.TestCase):
    def _problems(self):
        return iss.build_problems(
            [
                scan(1, "W01", "林晓薇", [(10, "A-1", "陈默"), (11, "A-2", None)]),
                scan(2, "W02", "陈默", [(12, "A-3", "林晓薇")]),
            ],
            unbound=[iss.UnboundAsset(id=20, code="A-9"), iss.UnboundAsset(id=21, code="A-8")],
        )

    def test_counts_consistent_with_by_rule(self):
        """⭐ 三个数字必须自洽：Σ pending == total，count == pending + handled。"""
        problems = self._problems()
        rows = iss.summarize(problems)
        counts = iss.count_pending(problems)
        self.assertEqual(sum(r["pending"] for r in rows), sum(counts.values()))
        for r in rows:
            self.assertEqual(r["count"], r["pending"] + r["handled_count"])

    def test_only_rules_with_problems_are_listed(self):
        rows = iss.summarize(self._problems())
        rules = [r["rule"] for r in rows]
        self.assertIn(iss.IssueRule.STATION_VS_ASSET_USER, rules)
        self.assertIn(iss.IssueRule.ASSET_UNBOUND, rules)
        self.assertNotIn(iss.IssueRule.ASSET_ON_DELETED_STATION, rules)
        # 输出顺序 = 目录顺序（稳定）
        order = [spec.rule for spec in iss.RULES]
        self.assertEqual(rules, [r for r in order if r in rules])

    def test_common_action_url_only_when_all_same(self):
        """落点一致才给组级入口；分散在多个点位就不给（否则按钮会把一批人导到同一个地方）。"""
        rows = {r["rule"]: r for r in iss.summarize(self._problems())}
        # 两条未挂点位 → 同一个 `/?workstation=none`
        self.assertEqual(rows[iss.IssueRule.ASSET_UNBOUND]["action_url"], "/?workstation=none")
        # 两处差异在两个不同点位 → 没有共同落点
        self.assertIsNone(rows[iss.IssueRule.STATION_VS_ASSET_USER]["action_url"])
        self.assertEqual(rows[iss.IssueRule.STATION_VS_ASSET_USER]["action_label"], "")

    def test_common_action_url_when_single_station(self):
        problems = iss.build_problems([scan(1, "W01", "林晓薇", [(10, "A-1", "陈默")])])
        rows = {r["rule"]: r for r in iss.summarize(problems)}
        self.assertEqual(rows[iss.IssueRule.STATION_VS_ASSET_USER]["action_url"], "/map?station=W01")


class TestSortStability(unittest.TestCase):
    def test_review_before_info_then_stable(self):
        problems = iss.build_problems(
            [
                scan(1, "W01", "林晓薇", [(10, "A-1", None)]),  # info
                scan(2, "W02", "陈默", [(11, "A-2", "林晓薇")]),  # review
            ],
            unbound=[iss.UnboundAsset(id=20, code="A-9")],  # info
        )
        sev = [p.severity for p in problems]
        self.assertEqual(sev[0], rc.Severity.REVIEW)
        # 同档内 key 升序 → 反复排序结果一致（分页才不会漏条 / 重条）
        again = iss.sort_problems(list(reversed(problems)))
        self.assertEqual(keys(problems), keys(again))

    def test_all_keys_unique(self):
        problems = iss.build_problems(
            [
                scan(1, "W01", "林晓薇", [(10, "A-1", "陈默"), (11, "A-2", None)]),
                scan(2, "W02", "陈默", [(12, "A-3", "林晓薇")]),
            ],
            unbound=[iss.UnboundAsset(id=20, code="A-9")],
            orphans=[iss.OrphanAsset(id=21, code="A-8", user_name=None, station_id=98, station_code="W98")],
        )
        self.assertEqual(len(keys(problems)), len(set(keys(problems))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
