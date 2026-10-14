"""点位迁移的规划器 —— **纯函数、只算不写**。

## 这个模块存在，是为了把三种"看起来都像搬迁"的动作分开

项目中「位置」这个概念有三层，界面上一旦混用，事故就来了
（CS 决策 67：把草稿型和立即生效型操作混放 = 事故温床）：

| 动作 | 改的是什么 | 走哪条路 |
| --- | --- | --- |
| **地图坐标移动** | `workstations.x/y`，家具挪了个地方 | `PATCH /api/workstations/layout`（已有） |
| **资产换点位** | `assets.workstation_id`，机器换了桌子 | 本模块 `mode=assets` |
| **点位业务迁移** | 整个位置的归属（使用人）+ 上面的设备一起搬到另一个位置 | 本模块 `mode=station` |

本模块**只管后两种**，且**绝不碰坐标** —— 坐标是排版，归属是业务，
这两件事在审计页上都要能分开筛（见 `models.AuditAction.WORKSTATION_MOVE` 的说明）。

## 为什么预览要产出「令牌」

`build_plan()` 是纯函数：同样的输入给同样的输出，顺带产出一个 `token`。
执行时**拿当前数据重算一遍**再比对令牌：

- 一致 → 中间没人动过，可以落库；
- 不一致 → 说明"预览看到的世界"和"现在要动手的世界"不是同一个 → 409，让用户重新预览。

一条机制同时挡住两件事：**并发改动**（别人抢先挪了设备）和**重复提交**
（用户手抖点了两次，第二次的状态已经和第一次的预览不同）。
比起让前端"禁用按钮"，这是真正能兜住的那种。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .relation_check import CheckStatus, pair_state


class RelocationMode:
    ASSETS = "assets"  # 单台 / 多台资产换点位
    STATION = "station"  # 整个点位业务迁移

    LABELS: dict[str, str] = {
        ASSETS: "资产换点位",
        STATION: "点位业务迁移",
    }

    ALL: tuple[str, ...] = (ASSETS, STATION)


# --------------------------------------------------------------------------- #
# 输入（普通数据，方便纯函数测试）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StationRef:
    id: int
    code: str
    user_name: Optional[str] = None
    active: bool = True


@dataclass(frozen=True)
class AssetRef:
    id: int
    code: str
    user_name: Optional[str] = None
    station_id: Optional[int] = None
    station_code: Optional[str] = None


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
@dataclass
class PlannedMove:
    asset_id: int
    asset_code: str
    asset_user: Optional[str]
    from_workstation_id: Optional[int]
    from_workstation_code: Optional[str]
    to_workstation_id: Optional[int]
    to_workstation_code: Optional[str]
    #: 迁移**前**这台设备与它当前点位的关系状态
    relation_state: str = ""

    @property
    def relation_state_label(self) -> str:
        return CheckStatus.LABELS.get(self.relation_state, self.relation_state)


@dataclass
class Plan:
    mode: str
    ok: bool = False
    conflicts: list[str] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    moves: list[PlannedMove] = field(default_factory=list)
    noop_count: int = 0
    station_changes: list[str] = field(default_factory=list)
    source: Optional[StationRef] = None
    target: Optional[StationRef] = None
    token: str = ""

    @property
    def affected_asset_count(self) -> int:
        return len(self.moves)

    @property
    def mode_label(self) -> str:
        return RelocationMode.LABELS.get(self.mode, self.mode)


def _token(parts: list[str]) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()


def _station_sketch(station: Optional[StationRef]) -> str:
    if station is None:
        return "-"
    return f"{station.id}:{station.code}:{(station.user_name or '').strip()}:{int(station.active)}"


def _asset_sketch(assets: list[AssetRef]) -> list[str]:
    return sorted(
        f"{a.id}:{a.code}:{(a.user_name or '').strip()}:{a.station_id if a.station_id is not None else '-'}"
        for a in assets
    )


# --------------------------------------------------------------------------- #
# 预览：资产换点位
# --------------------------------------------------------------------------- #
def plan_asset_move(
    assets: list[AssetRef],
    target: Optional[StationRef],
    stations_by_id: Optional[dict[int, StationRef]] = None,
) -> Plan:
    """把一批设备挪到目标点位。

    只做加法式搬运：**改 `workstation_id`，不碰使用人**。
    设备的使用人属于"这台机器归谁"，与"它在哪张桌子上"是两件事，
    迁移工具替用户改使用人就是"自动推断"（要求里明令禁止）。

    `stations_by_id` 用来算「迁移**前**这台设备与它当前点位的关系状态」，
    好让用户在预览里看到"这台本来就是待核对的，搬完还是"。
    不传也能算，只是拿不到源点位使用人（保守落成"未登记"，不会谎报差异）。
    """
    lookup = stations_by_id or {}
    plan = Plan(mode=RelocationMode.ASSETS, target=target)

    if not assets:
        plan.conflicts.append("没有选中任何设备。")
    if target is None:
        plan.conflicts.append("没有指定目标点位。")
    elif not target.active:
        plan.conflicts.append(f"目标点位 {target.code} 已删除，不能往里挂设备。")

    if plan.conflicts:
        plan.ok = False
        plan.token = _token(["assets", "invalid"])
        return plan

    assert target is not None  # 上面已保证

    for asset in assets:
        if asset.station_id == target.id:
            plan.noop_count += 1
            continue
        source_station = (
            lookup.get(asset.station_id) if asset.station_id is not None else None
        )
        plan.moves.append(
            PlannedMove(
                asset_id=asset.id,
                asset_code=asset.code,
                asset_user=asset.user_name,
                from_workstation_id=asset.station_id,
                from_workstation_code=asset.station_code,
                to_workstation_id=target.id,
                to_workstation_code=target.code,
                # 迁移**前**：拿它当前所在点位的使用人来比
                relation_state=pair_state(
                    source_station.user_name if source_station else None,
                    asset.user_name,
                ),
            )
        )

    if not plan.moves and plan.noop_count:
        plan.conflicts.append(
            f"选中的 {plan.noop_count} 台设备都已经在点位 {target.code} 上了，不用迁移。"
        )
        plan.ok = False
        plan.token = _token(["assets", "noop", _station_sketch(target)])
        return plan

    # 迁移后会出现的差异 —— 只提示，不阻止（要求：合理差异不得阻止正常资产操作）
    mismatched = [
        m for m in plan.moves if pair_state(target.user_name, m.asset_user) == CheckStatus.MISMATCH
    ]
    if mismatched:
        codes = "、".join(m.asset_code for m in mismatched[:4])
        more = f" 等 {len(mismatched)} 台" if len(mismatched) > 4 else ""
        plan.confirmations.append(
            f"点位 {target.code} 的使用人是「{(target.user_name or '').strip()}」，"
            f"但 {codes}{more} 的使用人不是他 —— 迁移后这几台会被标为「待核对」"
            f"（不是错误，系统不会替你改任何一边）。"
        )
    if not (target.user_name or "").strip():
        owned = [m for m in plan.moves if (m.asset_user or "").strip()]
        if owned:
            plan.confirmations.append(
                f"目标点位 {target.code} 没有登记使用人，而 {len(owned)} 台设备是有主的"
                f"（公共设备 / 备用机的常见形态，属正常）。"
            )

    plan.ok = True
    plan.token = _token(
        [
            "assets",
            _station_sketch(target),
            *_asset_sketch(assets),
        ]
    )
    return plan


# --------------------------------------------------------------------------- #
# 预览：点位业务迁移
# --------------------------------------------------------------------------- #
def plan_station_migration(
    source: Optional[StationRef],
    target: Optional[StationRef],
    source_assets: list[AssetRef],
    *,
    move_assets: bool = True,
    move_user: bool = False,
    retire_source: bool = False,
) -> Plan:
    """把一个点位的"业务"（归属 + 设备）搬到另一个点位。

    - `move_user`：把源点位的使用人搬到目标，**源点位使用人清空**
      （"迁移"就是搬走，不是复制两份 —— 两份必然对不上）。
    - `move_assets`：把源点位的设备全搬过去。
    - `retire_source`：搬空后把源点位软删除（编码不释放，审计里那两条记录仍指同一位置）。
    """
    plan = Plan(mode=RelocationMode.STATION, source=source, target=target)

    if source is None:
        plan.conflicts.append("没有指定要迁移的源点位。")
    if target is None:
        plan.conflicts.append("没有指定迁移到的目标点位。")
    if source is not None and target is not None and source.id == target.id:
        plan.conflicts.append("源点位和目标点位是同一个，没有可迁移的东西。")
    if source is not None and not source.active:
        plan.conflicts.append(f"源点位 {source.code} 已删除，不能作为迁移来源。")
    if target is not None and not target.active:
        plan.conflicts.append(f"目标点位 {target.code} 已删除，不能作为迁移目标。")
    if not move_assets and not move_user:
        plan.conflicts.append("既没有勾选迁移设备，也没有勾选迁移使用人 —— 没有可迁移的内容。")
    if retire_source and not move_assets and source_assets:
        plan.conflicts.append(
            f"源点位 {source.code} 上还挂着 {len(source_assets)} 台设备，"
            f"不勾「一并迁移设备」就无法把它退役。"
        )

    if plan.conflicts:
        plan.ok = False
        plan.token = _token(["station", "invalid"])
        return plan

    assert source is not None and target is not None

    if move_assets:
        for asset in source_assets:
            plan.moves.append(
                PlannedMove(
                    asset_id=asset.id,
                    asset_code=asset.code,
                    asset_user=asset.user_name,
                    from_workstation_id=source.id,
                    from_workstation_code=source.code,
                    to_workstation_id=target.id,
                    to_workstation_code=target.code,
                    relation_state=pair_state(source.user_name, asset.user_name),
                )
            )

    if move_user:
        src_user = (source.user_name or "").strip()
        tgt_user = (target.user_name or "").strip()
        if src_user:
            plan.station_changes.append(
                f"点位使用人：{source.code}「{src_user}」→ {target.code}；"
                f"{source.code} 的使用人清空"
            )
            if tgt_user and tgt_user != src_user:
                plan.confirmations.append(
                    f"目标点位 {target.code} 当前的使用人是「{tgt_user}」，"
                    f"迁移后会被改写成「{src_user}」—— 原来的归属会丢失。"
                )
        else:
            plan.confirmations.append(
                f"源点位 {source.code} 没有登记使用人，勾选「迁移使用人」实际不会改变任何东西。"
            )

    if retire_source:
        plan.station_changes.append(f"源点位 {source.code} 退役（软删除，编码不释放）")

    if not plan.moves and not plan.station_changes:
        plan.conflicts.append("按当前选项算下来没有任何要变更的内容。")
        plan.ok = False
        plan.token = _token(["station", "empty"])
        return plan

    # 迁移后待核对提示
    effective_target_user = source.user_name if move_user else target.user_name
    mismatched = [
        m
        for m in plan.moves
        if pair_state(effective_target_user, m.asset_user) == CheckStatus.MISMATCH
    ]
    if mismatched:
        codes = "、".join(m.asset_code for m in mismatched[:4])
        more = f" 等 {len(mismatched)} 台" if len(mismatched) > 4 else ""
        plan.confirmations.append(
            f"迁移后 {codes}{more} 的使用人与 {target.code} 的归属不一致，会被标为「待核对」。"
        )

    plan.ok = True
    plan.token = _token(
        [
            "station",
            _station_sketch(source),
            _station_sketch(target),
            f"ma={int(move_assets)}",
            f"mu={int(move_user)}",
            f"rs={int(retire_source)}",
            *_asset_sketch(source_assets if move_assets else []),
        ]
    )
    return plan
