"""点位迁移接口 —— 资产换点位 / 整个点位业务迁移。

## 为什么必须是**独立路由**，不并进 `/api/transfer`

`/api/transfer` 是**导入导出**（Excel 进、Excel 出），它改的是"台账里有哪些设备"；
这里改的是"设备摆在哪 / 这个位置归谁"。名字上都有个 transfer/迁移，
语义却完全不同。塞在一起的最坏后果是：有人对着一个"迁移"按钮点下去，
以为是搬设备，实际触发了一次批量导入 —— 不可逆的那种错。

## 与坐标移动的边界（要求里点名的"不得混淆"）

| 动作 | 接口 | 改什么 |
| --- | --- | --- |
| 地图上拖格子 | `PATCH /api/workstations/layout` | `x` / `y`（纯排版） |
| 资产换点位 | `POST /api/relocations/*` `mode=assets` | `assets.workstation_id` |
| 点位业务迁移 | `POST /api/relocations/*` `mode=station` | 上面一项 + 点位使用人（+ 可选退役源点位） |

本路由**从不修改 x / y**，一个字都不碰。

## 一次操作 = 一条审计

搬 12 台设备仍是 1 条 `relocation_apply`（与"批量导入 30 台 = 1 条"同口径）；
但**每台设备各写一条 `asset_events`** —— 设备履历是"每台各自完整"，
这是 asset_events 与 audit_logs 分工的地方（见 models.py 开头）。

## 三道闸

1. **预览绝不写库**（`plan_*` 是纯函数，路由只读）；
2. **执行前重算 + 令牌比对**：预览到执行之间数据被人动过 → 409，逼用户重新预览。
   这同时挡住并发改动和重复提交；
3. **执行在单事务里**：任一步抛错就整体回滚，不留"搬了一半"的中间态。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, AssetEventType, AuditAction, AuditTarget, Workstation
from ..schemas_relations import (
    AssetMoveOut,
    RelocationApplyRequest,
    RelocationApplyResult,
    RelocationPreviewOut,
    RelocationPreviewRequest,
)
from ..services import relocation
from ..services.audit import record_audit
from ..services.events import record_event
from ..services.ownership import has_owner
from .relation_check import build_check

router = APIRouter(prefix="/api/relocations", tags=["relocations"])


# --------------------------------------------------------------------------- #
# 取数：把 ORM 转成纯函数要的数据
# --------------------------------------------------------------------------- #
def _station_or_404(db: Session, station_id: int, *, include_inactive: bool = False) -> Workstation:
    station = db.get(Workstation, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail=f"点位 #{station_id} 不存在")
    if not include_inactive and not station.active:
        raise HTTPException(status_code=404, detail=f"点位 {station.code} 已删除")
    return station


def _station_ref(station: Optional[Workstation]) -> Optional[relocation.StationRef]:
    if station is None:
        return None
    return relocation.StationRef(
        id=station.id,
        code=station.code,
        user_name=station.user_name,
        active=bool(station.active),
    )


def _asset_ref(asset: Asset) -> relocation.AssetRef:
    station = asset.workstation
    return relocation.AssetRef(
        id=asset.id,
        code=asset.asset_code,
        user_name=asset.user_name,
        station_id=asset.workstation_id,
        station_code=station.code if station is not None else None,
    )


def _assets_by_ids(db: Session, asset_ids: list[int]) -> list[Asset]:
    if not asset_ids:
        return []
    return list(
        db.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars().all()
    )


def _assets_of_station(db: Session, station_id: int) -> list[Asset]:
    return list(
        db.execute(
            select(Asset)
            .where(Asset.workstation_id == station_id)
            .order_by(Asset.asset_code.asc())
        ).scalars().all()
    )


def _build_plan(db: Session, req: RelocationPreviewRequest | RelocationApplyRequest) -> tuple[relocation.Plan, dict]:
    """按请求把数据取出来，跑一遍纯函数规划。**纯读。**

    返回 (plan, ctx)，ctx 里带上路由执行时还要用的 ORM 对象，避免再查一遍。
    """
    if req.mode == relocation.RelocationMode.ASSETS:
        assets = _assets_by_ids(db, list(req.asset_ids))
        found = {a.id for a in assets}
        missing = sorted(set(req.asset_ids) - found)
        target = (
            _station_or_404(db, req.target_workstation_id)
            if req.target_workstation_id is not None
            else None
        )
        if missing:
            raise HTTPException(status_code=404, detail=f"设备不存在：{missing}")

        # 设备当前所在点位 → 用于算「迁移前的状态」
        station_ids = {a.workstation_id for a in assets if a.workstation_id is not None}
        stations_by_id: dict[int, relocation.StationRef] = {}
        if station_ids:
            rows = db.execute(
                select(Workstation).where(Workstation.id.in_(station_ids))
            ).scalars().all()
            stations_by_id = {s.id: _station_ref(s) for s in rows if s is not None}

        plan = relocation.plan_asset_move(
            [_asset_ref(a) for a in assets], _station_ref(target), stations_by_id
        )
        return plan, {"assets": assets, "target": target}

    # mode == station
    source = (
        _station_or_404(db, req.source_workstation_id, include_inactive=True)
        if req.source_workstation_id is not None
        else None
    )
    target = (
        _station_or_404(db, req.target_workstation_id, include_inactive=True)
        if req.target_workstation_id is not None
        else None
    )
    source_assets = _assets_of_station(db, source.id) if source is not None else []
    plan = relocation.plan_station_migration(
        _station_ref(source),
        _station_ref(target),
        [_asset_ref(a) for a in source_assets],
        move_assets=req.move_assets,
        move_user=req.move_user,
        retire_source=req.retire_source,
    )
    return plan, {"source": source, "target": target, "source_assets": source_assets}


def _preview_out(plan: relocation.Plan) -> RelocationPreviewOut:
    return RelocationPreviewOut(
        mode=plan.mode,
        mode_label=plan.mode_label,
        ok=plan.ok,
        conflicts=list(plan.conflicts),
        confirmations=list(plan.confirmations),
        notes=list(plan.notes),
        moves=[
            AssetMoveOut(
                asset_id=m.asset_id,
                asset_code=m.asset_code,
                asset_user=m.asset_user,
                from_workstation_id=m.from_workstation_id,
                from_workstation_code=m.from_workstation_code,
                to_workstation_id=m.to_workstation_id,
                to_workstation_code=m.to_workstation_code,
                relation_state=m.relation_state,
            )
            for m in plan.moves
        ],
        noop_count=plan.noop_count,
        station_changes=list(plan.station_changes),
        user_move=plan.user_move,
        source_user_before=plan.source_user_before,
        source_user_after=plan.source_user_after,
        target_user_before=plan.target_user_before,
        target_user_after=plan.target_user_after,
        source={
            "id": plan.source.id, "code": plan.source.code,
            "user_name": plan.source.user_name, "active": plan.source.active,
        } if plan.source else None,
        target={
            "id": plan.target.id, "code": plan.target.code,
            "user_name": plan.target.user_name, "active": plan.target.active,
        } if plan.target else None,
        preview_token=plan.token,
        affected_asset_count=plan.affected_asset_count,
    )


# --------------------------------------------------------------------------- #
# 预览
# --------------------------------------------------------------------------- #
@router.post(
    "/preview",
    response_model=RelocationPreviewOut,
    summary="迁移预览（**不写任何数据**）",
)
def preview(payload: RelocationPreviewRequest, db: Session = Depends(get_db)):
    """先看"会改哪些东西、有哪些差异要确认"，再决定动不动手。"""
    plan, _ctx = _build_plan(db, payload)
    return _preview_out(plan)


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
def _apply_moves(
    db: Session,
    moves: list[relocation.PlannedMove],
    target: Workstation,
    operator: Optional[str],
) -> list[str]:
    """把设备挪到目标点位。每台写 1 条 asset_events；返回实际变更的编号。"""
    moved: list[str] = []
    for move in moves:
        asset = db.get(Asset, move.asset_id)
        if asset is None or asset.workstation_id == target.id:
            continue
        before_code = move.from_workstation_code or "空"
        asset.workstation_id = target.id
        moved.append(asset.asset_code)
        record_event(
            db,
            asset,
            AssetEventType.RELOCATE,
            operator=operator,
            note=f"所在点位 {before_code} → {target.code}（点位迁移）",
            detail={"changes": {"workstation_id": {"from": before_code, "to": target.code}}},
        )
    return moved


@router.post(
    "/apply",
    response_model=RelocationApplyResult,
    summary="执行迁移（单事务；预览令牌不一致则 409）",
)
def apply_relocation(payload: RelocationApplyRequest, db: Session = Depends(get_db)):
    plan, ctx = _build_plan(db, payload)

    # 闸 2：令牌比对。挡住"预览后被别人改了"和"连点两次"。
    if payload.preview_token and plan.token != payload.preview_token:
        raise HTTPException(
            status_code=409,
            detail=(
                "预览之后数据发生了变化（可能有人同时在改），为避免误操作已中止。"
                "请重新预览、确认无误后再执行。"
            ),
        )
    if not plan.ok:
        raise HTTPException(
            status_code=409,
            detail="；".join(plan.conflicts) or "这次迁移不成立。",
        )
    if plan.confirmations and not payload.acknowledge_warnings:
        # 有需要用户点头的差异却直接执行 → 挡回去，让他先看明白
        raise HTTPException(
            status_code=409,
            detail=(
                "这次迁移存在需要你确认的差异，请先在预览里确认："
                + "；".join(plan.confirmations)
            ),
        )

    operator = payload.operator
    station_changes: list[str] = []
    moved: list[str] = []

    try:
        if payload.mode == relocation.RelocationMode.ASSETS:
            target: Workstation = ctx["target"]
            moved = _apply_moves(db, plan.moves, target, operator)
            summary = (
                f"资产换点位：{len(moved)} 台设备 → 点位 {target.code}"
                + (f"（{'、'.join(moved[:3])}{' 等' if len(moved) > 3 else ''}）" if moved else "")
            )
            record_audit(
                db,
                AuditAction.RELOCATION_APPLY,
                target_type=AuditTarget.WORKSTATION,
                target_id=target.id,
                target_label=target.code,
                summary=summary,
                operator=operator,
                detail={
                    "mode": payload.mode,
                    "target_workstation_id": target.id,
                    "target_code": target.code,
                    "moved": moved,
                },
            )
            db.commit()
            affected = [target]

        else:  # station
            source: Workstation = ctx["source"]
            target = ctx["target"]

            if payload.move_assets:
                moved = _apply_moves(db, plan.moves, target, operator)
                if moved:
                    station_changes.append(f"{len(moved)} 台设备：{source.code} → {target.code}")

            if payload.move_user and has_owner(source.user_name):
                # ⚠️ 门槛是 `has_owner()` 而不是"勾了框"。
                # 源点位没登记归属（空值，或 `/` / `／` 这种"没填"占位符）时**一个字都不写**：
                # 拿"空"去覆盖目标点位的使用人不是迁移，是丢归属。
                # 曾经这里只看 `payload.move_user`，于是勾上它会把目标点位的
                # 「林晓薇」覆盖成 `/` —— 而且预览里还写着"不会改变任何东西"。
                new_owner = source.user_name
                if (target.user_name or "") != (new_owner or ""):
                    station_changes.append(
                        f"点位使用人：{target.code}「{target.user_name or '未填'}」→「{new_owner}」"
                    )
                target.user_name = new_owner
                source.user_name = None
                station_changes.append(f"{source.code} 的使用人清空")

            if payload.retire_source:
                # 退役前确认它已经空了 —— 业务上"退役一个还挂着设备的点位"没有意义
                remaining = _assets_of_station(db, source.id)
                if remaining:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"点位 {source.code} 上还有 {len(remaining)} 台设备，"
                            f"不能退役。请先勾选「一并迁移设备」。"
                        ),
                    )
                source.active = False
                station_changes.append(f"源点位 {source.code} 已退役（软删除，编码不释放）")

            summary = (
                f"点位业务迁移：{source.code} → {target.code}"
                + (f"；{'；'.join(station_changes)}" if station_changes else "")
            )
            record_audit(
                db,
                AuditAction.RELOCATION_APPLY,
                target_type=AuditTarget.WORKSTATION,
                target_id=target.id,
                target_label=target.code,
                summary=summary,
                operator=operator,
                detail={
                    "mode": payload.mode,
                    "source_workstation_id": source.id,
                    "source_code": source.code,
                    "target_workstation_id": target.id,
                    "target_code": target.code,
                    "move_assets": payload.move_assets,
                    "move_user": payload.move_user,
                    "retire_source": payload.retire_source,
                    "moved": moved,
                    "station_changes": station_changes,
                },
            )
            db.commit()
            affected = [target] + ([source] if source.active else [])

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 —— 任何意外都整体回滚，不留半截
        db.rollback()
        raise HTTPException(status_code=500, detail=f"迁移失败，已回滚，数据未改动：{exc}") from exc

    # 迁移后立刻重新核对受影响点位（要求：迁移后 P0 检查结果与最新数据一致）
    checks = []
    for station in affected:
        db.refresh(station)
        checks.append(build_check(db, station))

    return RelocationApplyResult(
        ok=True,
        mode=payload.mode,
        moved=len(moved),
        moved_codes=moved,
        station_changes=station_changes,
        notes=list(plan.notes),
        relation_checks=checks,
        summary=summary,
    )
