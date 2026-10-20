"""问题中心 —— 把检查引擎的结论组装成一套统一的「问题」。

## 这个模块存在的理由

手册要一个"统一问题中心"。直觉做法是**建一张 `issues` 表**存待办 + 状态。**那条路是错的**：
问题是从数据现算出来的**投影**，存下来必然过期且无人更新
（红线 2 —— 与决策 71 拒掉"点位状态字段"、决策 80 拒掉"问题状态列"同一个病）。

所以这里**只有纯函数**：吃普通数据、吐结构化结果，不 commit、不写审计、不碰 session、不看时钟。

「处理结果」靠**事件驱动** —— 用户点了动作去做真实操作（补使用人 / 挂点位 / 换点位），
操作本身写 `audit_logs` + `asset_events`，下次算的时候那个条件自然就不成立了。
**这就是"处理后问题消失"的物理解释，不需要"已解决"状态字段。**

## 与 `relation_check` 的分工

| 模块 | 职责 |
| --- | --- |
| `relation_check` | **判定**：一条关系到底一不一致。它完全不认识"问题中心" |
| `issues`（本模块） | **组装**：把判定结果 + 数据完整性问题 + 例外状态，装成一套对外的统一契约 |

关系类问题的判定**一个字都不在这里重写** —— 全部来自 `relation_check.check_station()`。
本模块只做两件它不做的事：

1. 把"设备没登记使用人"从**点位级聚合**拆到**每台设备**
   （引擎给的是"这个点位有 3 台设备没登记"，而动作要落到单台设备上才点得动）；
2. 把"例外还作不作数"折成 `handled`（走 `relation_check.exception_still_valid`，唯一实现）。

## ⚠️ `key` 与 `fingerprint` 是两件事，不许混用

| 字段 | 随什么变 | 用途 |
| --- | --- | --- |
| `key` | **只随对象身份变** | 去重 / 定位 / 跨页面一致 / 分页稳定 |
| `fingerprint` | **随关系状态变** | 只用来判"例外还算不算数" |

拿 `fingerprint` 当问题标识的后果：用户改一下使用人，同一条问题就**换了身份**，
去重、跳转、跨刷新比对全乱。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from . import relation_check as rc
from .ownership import has_owner
from .relation_check import CheckStatus, Severity, Subject

# --------------------------------------------------------------------------- #
# 来源
# --------------------------------------------------------------------------- #
class IssueSource:
    """谁算出来的（对应手册表格里的「来源」列）。"""

    RELATION = "relation_check"  # 关系核对引擎
    INTEGRITY = "data_integrity"  # 数据完整性（字段级，不是关系级）

    LABELS: dict[str, str] = {
        RELATION: "关系核对",
        INTEGRITY: "数据完整性",
    }


# --------------------------------------------------------------------------- #
# 规则
# --------------------------------------------------------------------------- #
class IssueRule:
    """规则标识。**加一条规则 = 在这里加一个常量 + 在 RULES 里加一条 + 在 issues.py 里加一段判定**，
    绝不在路由或页面里写 if。"""

    #: 点位使用人 ↔ 设备使用人（复用关系核对引擎的同一套判定）
    STATION_VS_ASSET_USER = rc.RuleKey.STATION_VS_ASSET_USER
    #: 设备没登记使用人（点位有人）
    ASSET_USER_UNREGISTERED = "asset_user_unregistered"
    #: 资产没有点位
    ASSET_UNBOUND = "asset_unbound"
    #: 资产挂在一个已删除的点位上
    ASSET_ON_DELETED_STATION = "asset_on_deleted_station"
    #: 例外确认因关系变化而失效
    RELATION_EXCEPTION_STALE = "relation_exception_stale"
    #: 记在人手上的轻资产，而这个人名下有多个点位 → 地图只能给候选，定不了位置
    LIGHT_USER_STATION_AMBIGUOUS = "light_user_station_ambiguous"

    ALL: tuple[str, ...] = (
        STATION_VS_ASSET_USER,
        ASSET_USER_UNREGISTERED,
        ASSET_UNBOUND,
        ASSET_ON_DELETED_STATION,
        RELATION_EXCEPTION_STALE,
        LIGHT_USER_STATION_AMBIGUOUS,
    )


# --------------------------------------------------------------------------- #
# 动作白名单
#
# `action.url` **只由下面的模板生成**，永远不回显用户输入拼出来的路径。
# 每个模板都必须匹配 ALLOWED_ACTION_PATTERNS 里的某一条 ——
# 有单测逐条钉住（`test_issues.py::TestActionWhitelist`）。
# --------------------------------------------------------------------------- #
ALLOWED_ACTION_PATTERNS: tuple[str, ...] = (
    # 地图页：定位到某个点位（复用页面已有的 query 约定，见 SpaceMapView 的 ?task=）
    r"^/map(\?station=[^&#\s]{1,64})?$",
    # 资产列表：只看没有点位的资产（这个 query 与深链早就存在，见 AssetListView 的 ?workstation=none）
    r"^/\?workstation=none$",
    # 设备详情：页面上有「编辑」入口，改使用人 / 改点位都在这儿
    r"^/asset/\d+$",
    # 轻资产台账：处理"记在某人手上但此人有多个点位"的兜底入口
    # （在那儿把这条转成"放到点位"或"装到主机"，问题就自然消失，不写任何状态字段）
    r"^/light$",
)


@dataclass(frozen=True)
class RuleSpec:
    """一条规则的元信息 —— 界面上的"为什么值得关注"全部来自这里，前端一个字都不拼。"""

    rule: str
    label: str
    severity: str
    source: str
    #: 一句话说清"为什么值得关注"。**这是问题中心不是一个冷冰冰清单的关键。**
    reason: str
    action_label: str
    #: 动作 URL 模板，可用占位符：{station_code} / {asset_id}
    action_url: str


#: 规则的展示顺序 = 这个元组的顺序（界面按它排，稳定）
RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule=IssueRule.STATION_VS_ASSET_USER,
        label="点位使用人 ↔ 设备使用人",
        severity=Severity.REVIEW,
        source=IssueSource.RELATION,
        reason="这个点位登记的使用人，与挂在它上面的设备登记的使用人不是同一个人。"
        "需要人看一眼 —— **这不是错误**，也不该由系统替你改任何一侧。",
        action_label="去地图核对",
        action_url="/map?station={station_code}",
    ),
    RuleSpec(
        rule=IssueRule.ASSET_USER_UNREGISTERED,
        label="设备还没登记使用人",
        severity=Severity.INFO,
        source=IssueSource.RELATION,
        reason="所挂点位已经登记了使用人，但这台设备的使用人是空的。"
        "不是错误，只是台账还没填全 —— 补上以后它就能参与归属对照了。",
        action_label="去补使用人",
        action_url="/asset/{asset_id}",
    ),
    RuleSpec(
        rule=IssueRule.ASSET_UNBOUND,
        label="资产没有挂到点位",
        severity=Severity.INFO,
        source=IssueSource.INTEGRITY,
        reason="这台设备没有落在任何一个点位上。有位置的设备才能在地图上看得到、才谈得上对照归属。",
        action_label="去挂靠点位",
        action_url="/?workstation=none",
    ),
    RuleSpec(
        rule=IssueRule.ASSET_ON_DELETED_STATION,
        label="资产挂在已删除的点位上",
        severity=Severity.REVIEW,
        source=IssueSource.INTEGRITY,
        reason="它挂着的那个点位已经被删除了 —— 点位没了，这条归属对照也就没有依据了。",
        action_label="去改点位",
        action_url="/asset/{asset_id}",
    ),
    RuleSpec(
        rule=IssueRule.RELATION_EXCEPTION_STALE,
        label="例外确认已失效",
        severity=Severity.INFO,
        source=IssueSource.RELATION,
        reason="之前把某处差异确认为例外，但后来相关关系变了，那条确认自动作废了（这是设计如此）。"
        "现在需要按最新数据重新看一眼。",
        action_label="重新核对",
        action_url="/map?station={station_code}",
    ),
    RuleSpec(
        rule=IssueRule.LIGHT_USER_STATION_AMBIGUOUS,
        label="轻资产按使用人推断有歧义",
        severity=Severity.INFO,
        source=IssueSource.INTEGRITY,
        reason="这件配件记在某人手上（没有落点位），而这个人名下有好几个点位 —— "
        "地图只能把这几处**都**标成「可能在这里」，但它到底在哪一处，系统没法确定。"
        "不是错误，只是位置信息不够；把配件登记到具体点位或主机上，这条就会消失。",
        action_label="去轻资产核对",
        action_url="/light",
    ),
)

RULE_BY_KEY: dict[str, RuleSpec] = {spec.rule: spec for spec in RULES}


# --------------------------------------------------------------------------- #
# 输入
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StationScan:
    """要点位 + 它挂着的设备。"""

    station: rc.StationInput
    assets: tuple[rc.AssetInput, ...] = ()


@dataclass(frozen=True)
class UnboundAsset:
    """没有点位的资产（`workstation_id IS NULL`）。"""

    id: int
    code: str
    user_name: Optional[str] = None


@dataclass(frozen=True)
class OrphanAsset:
    """挂在一个**已删除**点位上的资产。"""

    id: int
    code: str
    user_name: Optional[str]
    station_id: int
    station_code: Optional[str] = None


@dataclass(frozen=True)
class ConfirmedException:
    """一条已确认的例外（`relation_exceptions` 里的行）。"""

    id: int
    rule: str
    workstation_id: int
    #: 0 = 点位级
    asset_id: int
    reason: str
    #: 确认时存下来的关系指纹
    fingerprint: str


@dataclass(frozen=True)
class LightUserAmbiguity:
    """记在某人手上的轻资产，而这个人名下有**多个**点位。

    ⚠️ 这不是错误，是**位置信息不够**：人手上本来就没有位置关系，
    而"人在哪几个工位"只能给出候选。地图的做法是把候选位置都标成「可能在这里」，
    这条规则负责在问题中心留个兜底 —— 不让"多点位的人手上有一件配件"永远没人看见。
    """

    holding_id: int
    user: str
    category_name: str = ""
    station_codes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
@dataclass
class Problem:
    """一条对外的「问题」。字段含义见模块头。"""

    key: str
    rule: str
    severity: str
    source: str
    reason: str
    title: str
    detail: str
    action_label: str
    action_url: str
    subjects: list[Subject] = field(default_factory=list)
    #: 关系类问题才有（它随关系状态变；**不要拿它当标识**）
    fingerprint: str = ""
    #: 已被**有效**例外盖住 → 不计入待办，但仍可查看 / 可撤销
    handled: bool = False
    exception_id: Optional[int] = None
    exception_reason: Optional[str] = None

    @property
    def rule_label(self) -> str:
        spec = RULE_BY_KEY.get(self.rule)
        return spec.label if spec else self.rule


def problem_key(rule: str, scope: str, object_id: int) -> str:
    """**稳定标识的唯一构造入口。**

    只由「规则 + 粒度 + 对象 id」组成 —— **不含状态、不含时间、不含计数、不含指纹**。
    这样同一个对象上的同一条问题，无论刷新多少次、无论有没有人来改过别的字段，
    身份都是同一个：能去重、能定位、能跨页面比对。

    粒度（`scope`）：`asset` / `station` / `exception`。
    """
    return f"{rule}:{scope}:{object_id}"


def _action_url(spec: RuleSpec, *, station_code: Optional[str] = None, asset_id: Optional[int] = None) -> str:
    """按模板生成动作 URL。**模板只在 RULES 里，不接受任何外部输入。**"""
    return spec.action_url.format(
        station_code=station_code or "", asset_id=asset_id if asset_id is not None else ""
    )


def _assert_action_allowed(url: str, rule: str) -> None:
    """动作 URL 必须在白名单里。

    ⚠️ 用 `raise` 而不是 `assert` —— `python -O` 会把 assert 整个删掉，
    那样这条自检就静默失效了。规则写错要当场炸，而不是把用户导到一个死链。
    """
    if not url.startswith("/") or not any(
        re.match(pattern, url) for pattern in ALLOWED_ACTION_PATTERNS
    ):
        raise ValueError(f"规则 {rule} 生成的动作 URL 不在白名单里：{url!r}")


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def build_problems(
    scans: Sequence[StationScan],
    *,
    unbound: Iterable[UnboundAsset] = (),
    orphans: Iterable[OrphanAsset] = (),
    exceptions: Iterable[ConfirmedException] = (),
    light_user_ambiguous: Iterable[LightUserAmbiguity] = (),
) -> list[Problem]:
    """把一次全库扫描的结果组装成问题清单。**纯函数。**

    输入全是普通数据（不是 ORM 对象），所以测试里直接构造即可，不用起库、不用起服务。
    """
    exc_rows = list(exceptions)
    # 例外按 (rule, asset_id) 索引 —— 与 `Issue.exception_key`、`relation_exceptions` 的唯一约束同一口径。
    # ⚠️ 点位级的 rule 2 / rule 3 共用 `(rule, 0)` 这一个键（两条规则的确认粒度都是点位级），
    # 所以下面判 handled 时查的就是它。
    exc_by_key: dict[tuple[str, int], ConfirmedException] = {}
    for e in exc_rows:
        exc_by_key.setdefault((e.rule, int(e.asset_id)), e)

    problems: list[Problem] = []
    #: 每个点位**现算**出来的指纹，用来判"例外还算不算数"
    fresh_by_station: dict[int, dict[tuple[str, int], str]] = {}

    # ---------- 关系类：跑引擎，一个字都不重写判定 ----------
    for scan in scans:
        station = scan.station
        assets = list(scan.assets)
        result = rc.check_station(station, assets)
        fresh = {i.exception_key: i.fingerprint for i in result.issues}
        fresh_by_station[station.id] = fresh

        station_subject = Subject(kind="workstation", id=station.id, code=station.code, user_name=station.user_name)
        asset_by_id = {a.id: a for a in assets}

        # --- 规则 1：逐台差异（引擎本来就是逐台给的）---
        spec = RULE_BY_KEY[IssueRule.STATION_VS_ASSET_USER]
        for issue in result.issues:
            if issue.rule != IssueRule.STATION_VS_ASSET_USER:
                continue
            if issue.status != CheckStatus.MISMATCH or issue.asset_id is None:
                continue
            asset = asset_by_id.get(issue.asset_id)
            exc = exc_by_key.get(issue.exception_key)
            handled = bool(exc and rc.exception_still_valid(exc.fingerprint, issue.fingerprint))
            url = _action_url(spec, station_code=station.code, asset_id=issue.asset_id)
            _assert_action_allowed(url, issue.rule)
            problems.append(
                Problem(
                    key=problem_key(issue.rule, "asset", issue.asset_id),
                    rule=issue.rule,
                    severity=issue.severity,
                    source=IssueSource.RELATION,
                    reason=spec.reason,
                    title=issue.title,
                    detail=issue.detail,
                    action_label=spec.action_label,
                    action_url=url,
                    subjects=[
                        station_subject,
                        Subject(
                            kind="asset",
                            id=issue.asset_id,
                            code=asset.code if asset else "",
                            user_name=asset.user_name if asset else None,
                        ),
                    ],
                    fingerprint=issue.fingerprint,
                    handled=handled,
                    exception_id=exc.id if exc else None,
                    exception_reason=exc.reason if exc else None,
                )
            )

        # --- 规则 2：点位有人、设备没登记使用人 → **拆到每台设备** ---
        # 引擎给的是点位级聚合（"这个点位有 3 台设备没登记"），但动作要落到单台设备上才点得动，
        # 所以这里按它给的**逐台结论**（`asset_checks`）展开。
        # ⚠️ 判"点位到底有没有人"仍然只认 `has_owner`（决策 76），不在这里另写判据。
        if has_owner(station.user_name):
            spec2 = RULE_BY_KEY[IssueRule.ASSET_USER_UNREGISTERED]
            # 例外是点位级粒度：确认了"这个点位的未登记提示不用管"，则该点位下每台都算已处理
            exc2 = exc_by_key.get((IssueRule.STATION_VS_ASSET_USER, 0))
            exc2_fresh = fresh.get((IssueRule.STATION_VS_ASSET_USER, 0), "")
            handled2 = bool(exc2 and rc.exception_still_valid(exc2.fingerprint, exc2_fresh))
            for check in result.asset_checks:
                if check.state != CheckStatus.UNREGISTERED:
                    continue
                url = _action_url(spec2, asset_id=check.asset_id)
                _assert_action_allowed(url, IssueRule.ASSET_USER_UNREGISTERED)
                problems.append(
                    Problem(
                        key=problem_key(IssueRule.ASSET_USER_UNREGISTERED, "asset", check.asset_id),
                        rule=IssueRule.ASSET_USER_UNREGISTERED,
                        severity=Severity.INFO,
                        source=IssueSource.RELATION,
                        reason=spec2.reason,
                        title="这台设备还没登记使用人",
                        detail=f"设备 {check.asset_code} 的使用人是空的，而它所在的点位 "
                        f"{station.code} 归「{(station.user_name or '').strip()}」。",
                        action_label=spec2.action_label,
                        action_url=url,
                        subjects=[
                            Subject(kind="asset", id=check.asset_id, code=check.asset_code, user_name=check.asset_user),
                            station_subject,
                        ],
                        fingerprint=exc2_fresh,
                        handled=handled2,
                        exception_id=exc2.id if exc2 else None,
                        exception_reason=exc2.reason if exc2 else None,
                    )
                )

    # ---------- 规则 3：资产没有点位 ----------
    spec3 = RULE_BY_KEY[IssueRule.ASSET_UNBOUND]
    for a in unbound:
        url = _action_url(spec3)
        _assert_action_allowed(url, spec3.rule)
        problems.append(
            Problem(
                key=problem_key(IssueRule.ASSET_UNBOUND, "asset", a.id),
                rule=IssueRule.ASSET_UNBOUND,
                severity=Severity.INFO,
                source=IssueSource.INTEGRITY,
                reason=spec3.reason,
                title="这台设备没有挂到点位上",
                detail=f"设备 {a.code} 的「所在点位」是空的。",
                action_label=spec3.action_label,
                action_url=url,
                subjects=[Subject(kind="asset", id=a.id, code=a.code, user_name=a.user_name)],
            )
        )

    # ---------- 规则 4：资产挂在已删除的点位上 ----------
    spec4 = RULE_BY_KEY[IssueRule.ASSET_ON_DELETED_STATION]
    for o in orphans:
        url = _action_url(spec4, asset_id=o.id)
        _assert_action_allowed(url, spec4.rule)
        problems.append(
            Problem(
                key=problem_key(IssueRule.ASSET_ON_DELETED_STATION, "asset", o.id),
                rule=IssueRule.ASSET_ON_DELETED_STATION,
                severity=Severity.REVIEW,
                source=IssueSource.INTEGRITY,
                reason=spec4.reason,
                title="这台设备挂在已删除的点位上",
                detail=f"设备 {o.code} 的所在点位是「{o.station_code or o.station_id}」，那个点位已被删除。",
                action_label=spec4.action_label,
                action_url=url,
                subjects=[
                    Subject(
                        kind="asset", id=o.id, code=o.code, user_name=o.user_name
                    ),
                    Subject(
                        kind="workstation",
                        id=o.station_id,
                        code=o.station_code or str(o.station_id),
                        label="已删除",
                    ),
                ],
            )
        )

    # ---------- 规则 5：例外确认已失效 ----------
    # 失效是**算出来的**（存下来的指纹 ≠ 现算的指纹），**不落库、不写事件**。
    spec5 = RULE_BY_KEY[IssueRule.RELATION_EXCEPTION_STALE]
    station_codes = {s.station.id: s.station.code for s in scans}
    for e in exc_rows:
        if e.rule != IssueRule.STATION_VS_ASSET_USER:
            continue  # 目前只有这一条规则会挂例外
        if e.workstation_id not in station_codes:
            # 所属点位已被删除 → **不报**。两个理由：
            #   ① 关系已经不存在了，这条例外不会"掩盖"任何新差异（它要防的就是那个）；
            #   ② 点位的侧栏进不去（`_get_station_or_404` 只认 active），
            #      报出来也给不出一个能撤销它的真实入口 —— 那就成了"点处理却只能干看着"。
            # 它作为历史留在 `relation_exceptions` 里；点位哪天被恢复，这条会自己重新参与判定。
            continue
        fresh_fp = fresh_by_station.get(e.workstation_id, {}).get((e.rule, int(e.asset_id)), "")
        if rc.exception_still_valid(e.fingerprint, fresh_fp):
            continue
        code = station_codes.get(e.workstation_id)
        url = _action_url(spec5, station_code=code)
        _assert_action_allowed(url, spec5.rule)
        problems.append(
            Problem(
                key=problem_key(IssueRule.RELATION_EXCEPTION_STALE, "exception", e.id),
                rule=IssueRule.RELATION_EXCEPTION_STALE,
                severity=Severity.INFO,
                source=IssueSource.RELATION,
                reason=spec5.reason,
                title="这是当初确认过的例外，现在已经失效",
                detail=(
                    f"当时的理由：{e.reason}。"
                    + (f"所属点位 {code} 的" if code else "所属点位的")
                    + "关系后来变了，所以这条确认不再算数。"
                ),
                action_label=spec5.action_label,
                action_url=url,
                subjects=[
                    Subject(
                        kind="workstation",
                        id=e.workstation_id,
                        code=code or str(e.workstation_id),
                    )
                ],
                fingerprint=fresh_fp,
                handled=False,
                exception_id=e.id,
                exception_reason=e.reason,
            )
        )

    # ---------- 规则 6：记在人手上的轻资产，人有多个点位 ----------
    # 不是错误，是**位置信息不够**：人手上没有位置关系，系统只知道"可能是这几个工位之一"。
    # 地图把候选位置都标「可能在这里」，这条规则负责让它别只存在于搜索里 ——
    # 「员工A今天多了一个工位」不会挑他去搜索的时候发生。
    spec6 = RULE_BY_KEY[IssueRule.LIGHT_USER_STATION_AMBIGUOUS]
    for amb in light_user_ambiguous:
        places = "、".join(amb.station_codes) if amb.station_codes else "多个点位"
        url = _action_url(spec6)
        _assert_action_allowed(url, spec6.rule)
        problems.append(
            Problem(
                key=problem_key(IssueRule.LIGHT_USER_STATION_AMBIGUOUS, "light_holding", amb.holding_id),
                rule=IssueRule.LIGHT_USER_STATION_AMBIGUOUS,
                severity=Severity.INFO,
                source=IssueSource.INTEGRITY,
                reason=spec6.reason,
                title=f"「{amb.category_name or '配件'}」记在 {amb.user} 手上，"
                f"而 {amb.user} 名下有 {len(amb.station_codes)} 个点位",
                detail=(
                    f"这件配件没有落在具体位置，只知道是 {amb.user} 在用；"
                    f"而 {amb.user} 名下的点位有 {places}。"
                    "地图会把这几个位置都标成「按使用人推断」，但无法确定实际在哪一处。"
                    "把它登记到具体点位或某台主机上，这条就不存在了。"
                ),
                action_label=spec6.action_label,
                action_url=url,
            )
        )

    return sort_problems(problems)


def sort_problems(problems: Iterable[Problem]) -> list[Problem]:
    """**稳定排序** —— 分页要它，不然会漏条 / 重条。

    次序：严重程度（review 在前）→ 规则在目录里的次序 → key（字典序）。
    最后一项兜底保证"任何两条问题都有确定先后"。
    """
    order = {spec.rule: idx for idx, spec in enumerate(RULES)}
    return sorted(
        problems,
        key=lambda p: (
            -Severity.RANK.get(p.severity, 0),
            order.get(p.rule, 99),
            p.key,
        ),
    )


def summarize(problems: Sequence[Problem]) -> list[dict]:
    """按规则汇总。**与 items 同源**：`count` 是该规则的全部条数（分页不改变它）。

    - `count`：该规则的**全部**问题数（**含**已被有效例外盖住的）
    - `handled_count`：其中已被有效例外盖住的
    - `pending`：`count - handled_count`，也就是"还需要看一眼"的条数（`total` 就是它的和）
    - `action_url`：**只有当该规则下待办问题的动作落点是同一个时**才给
      （例如"资产没挂点位"28 条都指向 `/?workstation=none`，那就给一个组级入口）。
      落点各不相同（比如差异分散在多个点位）→ 给 `None`，界面只提供"展开逐条"。
      **不把带占位符的模板漏给前端** —— 界面拿到的永远是可直接跳的完整路径。
    """
    by_rule: dict[str, list[Problem]] = {}
    for p in problems:
        by_rule.setdefault(p.rule, []).append(p)

    out: list[dict] = []
    for spec in RULES:  # 目录顺序 = 输出顺序，稳定
        rows = by_rule.get(spec.rule)
        if not rows:
            continue
        handled = sum(1 for p in rows if p.handled)
        pending_rows = [p for p in rows if not p.handled]
        urls = {p.action_url for p in pending_rows}
        common_url = urls.pop() if len(urls) == 1 else None
        out.append(
            {
                "rule": spec.rule,
                "label": spec.label,
                "severity": spec.severity,
                "source": spec.source,
                "reason": spec.reason,
                "count": len(rows),
                "handled_count": handled,
                "pending": len(rows) - handled,
                "action_label": spec.action_label if common_url else "",
                "action_url": common_url,
            }
        )
    return out


def count_pending(problems: Sequence[Problem]) -> dict[str, int]:
    """`counts`：**只数"还需要看一眼"的**（已被确认过的不再计入待办）。

    键的顺序固定（review 在前），界面直接按它渲染。
    """
    out: dict[str, int] = {Severity.REVIEW: 0, Severity.INFO: 0}
    for p in problems:
        if p.handled:
            continue
        out[p.severity] = out.get(p.severity, 0) + 1
    return out
