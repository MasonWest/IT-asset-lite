"""关系核对引擎 —— **纯函数、零 IO、可脱离页面独立测试**。

## 它解决什么

点位（`workstations`）自己有一个「使用人」，挂在它上面的资产（`assets`）各自
也有一个「使用人」。**这是两个不同的事实**（见 `models.Workstation.user_name`
的说明），现实里可以不一致 —— 而不一致正是现场要找的信号。

这个模块只做一件事：**把这两侧的名字比一遍，给出结构化结论**。

## 三条铁律

1. **只读、不写、不改数据。** 它是纯函数：进的是普通数据（不是 ORM 对象、
   不碰 `Session`），出的是 dataclass。所以「会不会误改资产」在类型层面就不可能发生
   —— 它连改的能力都没有。要求里的「不擅自改写数据」靠这个结构性保证，不靠自觉。
2. **结论是当前数据的派生结果，不落库。** 没有「问题状态表」，每次调用现算
   （与地图四态关系层同一原则：`utils/workstationMap.ts::relationOf` 也只算不存）。
   落库的只有「例外确认」那一件事 —— 那是**业务决定**，不是派生结果（见 `models.RelationException`）。
3. **复用 `ownership` 的唯一口径。** 「算不算有人」只认 `has_owner()`
   （空值与 `/`、`／` 都算没归属）。这里**绝不**另写一套判断 ——
   前后端两套口径打架是这个项目踩过的老坑（见 `ownership.py` 模块头）。

## 四种状态（要求里点名要区分的）

| 状态 | 含义 | 是不是错误 |
| --- | --- | --- |
| `consistent` | 两侧一致，或属于已知的正常形态 | 否 |
| `mismatch` | 两侧都登记了、但**不是同一个人** → 待核对 | **不是错误**，是待核对信号 |
| `unregistered` | 某一侧**没登记**使用人，无从对照 | **不是错误**（公共设备 / 还没配人） |
| `undetermined` | 数据不足，连比都比不了（例如点位无设备） | 否 |

**⚠️ 最要紧的一条：`mismatch` 只产生「待核对」，永远不阻断任何资产操作。**
「无人使用的资产点位」在业务上很正常（`none_asset`：公共设备 / 备用机），
把它一律判成红色错误，用户第一次用就会把这功能关掉。

## 谁在用它

- `routers/relation_check.py` —— 接口层，负责把 DB 里的行取出来喂进来、
  再把「例外确认」叠上去。
- `backend/scan_relations.py` —— 只读数据质量扫描，用同一套函数跑全库，
  **不重复实现规则**（扫描结果反过来检验规则的误报率）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .ownership import has_owner

# --------------------------------------------------------------------------- #
# 枚举（用类常量而不是 Enum：与项目其它地方一致，值是直接进 JSON 的字符串）
# --------------------------------------------------------------------------- #


class CheckStatus:
    """一条核对结论的状态。"""

    CONSISTENT = "consistent"
    MISMATCH = "mismatch"
    UNREGISTERED = "unregistered"
    UNDETERMINED = "undetermined"

    LABELS: dict[str, str] = {
        CONSISTENT: "一致",
        MISMATCH: "差异（待核对）",
        UNREGISTERED: "未登记",
        UNDETERMINED: "无法判断",
    }

    ALL: tuple[str, ...] = (CONSISTENT, MISMATCH, UNREGISTERED, UNDETERMINED)

    #: 整体结论取「最值得注意」的那一个。**mismatch 优先，但不含 error 语义** ——
    #: 它压过 unregistered 只是因为它更需要人看一眼，不代表它是错误。
    RANK: dict[str, int] = {
        MISMATCH: 3,
        UNREGISTERED: 2,
        UNDETERMINED: 1,
        CONSISTENT: 0,
    }


class Severity:
    """展示权重。`review` 只是"值得看一眼"，不是"出错了"。"""

    OK = "ok"
    INFO = "info"
    REVIEW = "review"

    LABELS: dict[str, str] = {OK: "正常", INFO: "提示", REVIEW: "待核对"}


class RuleKey:
    """规则标识。前端按它决定怎么渲染 / 怎么挂例外。"""

    STATION_VS_ASSET_USER = "station_vs_asset_user"

    LABELS: dict[str, str] = {
        STATION_VS_ASSET_USER: "点位使用人 ↔ 设备使用人",
    }


class Scope:
    """一条问题的挂靠粒度 —— 决定「例外确认」确认的是整条还是单台。"""

    STATION = "station"
    ASSET = "asset"


# --------------------------------------------------------------------------- #
# 输入：普通数据（不是 ORM 对象，方便纯函数测试直接构造）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StationInput:
    id: int
    code: str
    user_name: Optional[str] = None


@dataclass(frozen=True)
class AssetInput:
    id: int
    code: str
    user_name: Optional[str] = None


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Subject:
    """一条问题涉及的对象（点位 / 设备）。前端拿它渲染「涉及对象」那一行。"""

    kind: str  # workstation | asset
    id: int
    code: str
    user_name: Optional[str] = None
    label: str = ""


@dataclass
class Issue:
    """一条结构化的核对结论。

    `fingerprint` 是**这条关系当下的指纹**：例外确认把它一起存下来，
    下次比对不一致 → 例外自动失效（要求：「相关关系变化后，旧例外确认不会继续掩盖新差异」）。
    """

    rule: str
    status: str
    severity: str
    scope: str
    title: str
    detail: str
    suggestion: str
    fingerprint: str
    subjects: list[Subject] = field(default_factory=list)
    #: scope == asset 时是哪台设备；station 级为 None
    asset_id: Optional[int] = None

    @property
    def rule_label(self) -> str:
        return RuleKey.LABELS.get(self.rule, self.rule)

    @property
    def status_label(self) -> str:
        return CheckStatus.LABELS.get(self.status, self.status)

    @property
    def severity_label(self) -> str:
        return Severity.LABELS.get(self.severity, self.severity)

    @property
    def exception_key(self) -> tuple[str, int]:
        """例外表里这一条的唯一键：(rule, asset_id)；点位级 asset_id 记 0。"""
        return (self.rule, self.asset_id or 0)


@dataclass
class AssetUserCheck:
    """单台设备的对照结论 —— 侧栏要在资产行上就地显示，所以逐台给。"""

    asset_id: int
    asset_code: str
    asset_user: Optional[str]
    state: str

    @property
    def state_label(self) -> str:
        return CheckStatus.LABELS.get(self.state, self.state)


@dataclass
class StationCheck:
    """一个点位的整体核对结论。"""

    workstation_id: int
    code: str
    user_name: Optional[str]
    applicable: bool
    verdict: str
    issues: list[Issue] = field(default_factory=list)
    asset_checks: list[AssetUserCheck] = field(default_factory=list)

    @property
    def verdict_label(self) -> str:
        return CheckStatus.LABELS.get(self.verdict, self.verdict)

    @property
    def review_count(self) -> int:
        """需要人看一眼的条数（mismatch）。前端用它决定要不要冒小红点。"""
        return sum(1 for i in self.issues if i.status == CheckStatus.MISMATCH)


# --------------------------------------------------------------------------- #
# 指纹
# --------------------------------------------------------------------------- #
_SEP = "\x1f"  # 单元分隔符，正常人名 / 编码里不会出现，避免拼接歧义


def _digest(parts: list[str]) -> str:
    raw = _SEP.join(parts).encode("utf-8")
    return "v1:" + hashlib.sha1(raw).hexdigest()


def asset_fingerprint(
    station_id: int,
    station_user: Optional[str],
    asset: AssetInput,
) -> str:
    """单台设备关系的指纹。

    ⚠️ 把 `asset.code` 也放进来是有意的：主键是 `INTEGER PRIMARY KEY`（非 AUTOINCREMENT），
    删空 / 删末行之后 **id 会被复用**（见 reset.py 的说明）。只记 id 的话，
    一个旧例外可能挂到一台新设备上，指纹里带上编码就避开了这种串号。
    """
    return _digest(
        [
            "asset",
            str(station_id),
            (station_user or "").strip(),
            str(asset.id),
            asset.code,
            (asset.user_name or "").strip(),
        ]
    )


def station_fingerprint(station: StationInput, assets: list[AssetInput]) -> str:
    """整个点位关系的指纹：点位使用人 + 每台设备的（id, 编码, 使用人）。

    任何一台设备换了人、被摘走、被挂上来，指纹都会变 ——
    所以点位级的例外确认不会在关系变了之后继续捂着眼睛。
    """
    rows = sorted(
        f"{a.id}:{a.code}:{(a.user_name or '').strip()}" for a in assets
    )
    return _digest(
        ["station", str(station.id), (station.user_name or "").strip(), *rows]
    )


def pair_state(station_user: Optional[str], asset_user: Optional[str]) -> str:
    """一对（点位使用人, 设备使用人）的状态。

    **抽出来是为了让「核对」与「迁移预览」用同一套判断** ——
    迁移预览要告诉用户"挪过去之后会不会还是待核对"，如果那边自己再写一遍
    if/else，两边迟早会漂（而漂的时候不会有任何报错，只是结论悄悄不一样）。
    """
    station_owner = has_owner(station_user)
    asset_owner = has_owner(asset_user)
    if station_owner and asset_owner:
        return (
            CheckStatus.CONSISTENT
            if (station_user or "").strip() == (asset_user or "").strip()
            else CheckStatus.MISMATCH
        )
    if station_owner or asset_owner:
        # 只有一侧登记了 → 无从对照（公共设备 / 还没配人）
        return CheckStatus.UNREGISTERED
    return CheckStatus.UNDETERMINED


# --------------------------------------------------------------------------- #
# 主函数
# --------------------------------------------------------------------------- #
def check_station(station: StationInput, assets: list[AssetInput]) -> StationCheck:
    """核对一个点位的「点位使用人 ↔ 设备使用人」。

    **纯函数**：同样的输入永远给同样的输出，不读库、不写库、不看时钟。
    """
    station_owner = has_owner(station.user_name)
    station_name = (station.user_name or "").strip()

    asset_checks: list[AssetUserCheck] = []
    mismatched: list[AssetInput] = []
    unregistered_assets: list[AssetInput] = []
    both_blank = 0

    for asset in assets:
        state = pair_state(station.user_name, asset.user_name)

        if state == CheckStatus.MISMATCH:
            mismatched.append(asset)
        elif state == CheckStatus.UNREGISTERED and station_owner:
            # 点位有人、设备没登记使用人 —— **不是错误**，只是账还没填全
            unregistered_assets.append(asset)
        elif state == CheckStatus.UNDETERMINED:
            both_blank += 1

        asset_checks.append(
            AssetUserCheck(
                asset_id=asset.id,
                asset_code=asset.code,
                asset_user=asset.user_name,
                state=state,
            )
        )

    issues: list[Issue] = []
    station_subject = Subject(
        kind="workstation",
        id=station.id,
        code=station.code,
        user_name=station.user_name,
    )

    # ---- 规则 1：两侧都登记了、但不是同一个人 → 待核对 ----
    for asset in mismatched:
        issues.append(
            Issue(
                rule=RuleKey.STATION_VS_ASSET_USER,
                status=CheckStatus.MISMATCH,
                severity=Severity.REVIEW,
                scope=Scope.ASSET,
                asset_id=asset.id,
                title="点位使用人与这台设备的使用人不一致",
                detail=(
                    f"点位 {station.code} 记的是「{station_name}」，"
                    f"而设备 {asset.code} 记的是「{(asset.user_name or '').strip()}」。"
                ),
                suggestion=(
                    "就地核对：是设备归属记错了，还是这个位置换了人。"
                    "系统**不会**自动改任何一侧 —— 哪边对由你判断。"
                    "确认属于正常情况（例如这位置是共用的），可以记为「例外」不再提示。"
                ),
                fingerprint=asset_fingerprint(station.id, station.user_name, asset),
                subjects=[
                    station_subject,
                    Subject(
                        kind="asset",
                        id=asset.id,
                        code=asset.code,
                        user_name=asset.user_name,
                    ),
                ],
            )
        )

    # ---- 规则 2：点位有人，但挂着的设备没登记使用人 → 提示（不是错误）----
    if unregistered_assets:
        codes = "、".join(a.code for a in unregistered_assets[:5])
        more = f" 等 {len(unregistered_assets)} 台" if len(unregistered_assets) > 5 else ""
        issues.append(
            Issue(
                rule=RuleKey.STATION_VS_ASSET_USER,
                status=CheckStatus.UNREGISTERED,
                severity=Severity.INFO,
                scope=Scope.STATION,
                title="有设备还没登记使用人",
                detail=(
                    f"点位 {station.code} 归「{station_name}」，但 {len(unregistered_assets)} 台"
                    f"设备的使用人是空的（{codes}{more}）。"
                ),
                suggestion=(
                    "这不是错误，只是台账还没填全。到设备详情把使用人补上，"
                    "或者此位置确实是共用设备，忽略即可。"
                ),
                fingerprint=station_fingerprint(station, assets),
                subjects=[
                    station_subject,
                    *[
                        Subject(kind="asset", id=a.id, code=a.code, user_name=a.user_name)
                        for a in unregistered_assets[:5]
                    ],
                ],
            )
        )

    # ---- 规则 3：点位没登记使用人，但有设备 → 提示（明确「不是错误」）----
    if not station_owner and any(has_owner(a.user_name) for a in assets):
        owners = sorted({(a.user_name or "").strip() for a in assets if has_owner(a.user_name)})
        issues.append(
            Issue(
                rule=RuleKey.STATION_VS_ASSET_USER,
                status=CheckStatus.UNREGISTERED,
                severity=Severity.INFO,
                scope=Scope.STATION,
                title="点位未登记使用人",
                detail=(
                    f"点位 {station.code} 自己没有登记使用人，上面的设备分属："
                    + "、".join(owners[:5])
                    + ("。" if len(owners) <= 5 else " 等。")
                ),
                suggestion=(
                    "**这在业务上是正常的**：公共设备、备用机、会议室机器都长这样。"
                    "如果这个位置确实分给了某个人，到「点位设置」把使用人填上即可。"
                ),
                fingerprint=station_fingerprint(station, assets),
                subjects=[station_subject],
            )
        )

    # ---- 整体结论 ----
    if not assets:
        # 没有设备 = 没有可核对的对象。**不冒充"正常"**，也不报警。
        verdict = CheckStatus.UNDETERMINED
    else:
        verdict = max(
            (c.state for c in asset_checks),
            key=lambda s: CheckStatus.RANK.get(s, 0),
        )
        if verdict == CheckStatus.CONSISTENT and both_blank:
            verdict = CheckStatus.UNDETERMINED

    return StationCheck(
        workstation_id=station.id,
        code=station.code,
        user_name=station.user_name,
        applicable=bool(assets),
        verdict=verdict,
        issues=issues,
        asset_checks=asset_checks,
    )
