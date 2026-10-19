"""轻资产：ORM → 响应对象。

放一处，理由同 `services/serialize.py` —— 台账页、品类详情、资产详情、点位详情、
流水时间线五处都要"把一条持有关系说成人话"，各写一份迟早会出现
"同一台设备在两个页面显示得不一样"。

## 展示优先读"活的"，读不到才回落到快照

流水里存的是**当时**的编码与名称（宿主可能已经删了、主键还可能被复用）。
所以这里总是先拿 `ref_id` 去查当前的对象：

- 查得到 → 用**当前**的编码/状态，且 `alive=True`；
- 查不到 → 回落到流水里的快照，`alive=False`，界面提示「已删除」。

这正是「当前使用人 / 点位一律现读，不重复存一份会过期的数据」的落点。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Asset,
    AssetStatus,
    LightCategory,
    LightHolding,
    LightHolderKind,
    LightMovement,
    LightOp,
    LightOrigin,
    Workstation,
)
from ..schemas_light import (
    HoldingOut,
    HolderOut,
    LightCategoryOut,
    LightCategoryStat,
    LightMovementOut,
)
from . import light as light_service

#: 引用类型在界面上的标签
_KIND_LABELS: dict[str, str] = dict(LightHolderKind.LABELS)


def _station_display(station: Workstation) -> str:
    if station.room:
        return f"{station.code}（{station.room}）"
    return station.code


def _station_id_by_code(db: Session, code: Optional[str]) -> Optional[int]:
    """存放位置若恰好是某个**活着的**点位编码，给出它的 id（前端做成可点击链接）。

    这正是设计 §2.4 的取舍产物：库存位置是自由文本，**不建外键**；
    能匹配上就顺手给个链接，匹配不上就是一个普通描述。
    """
    if not code:
        return None
    row = db.execute(
        select(Workstation.id)
        .where(Workstation.code == code)
        .where(Workstation.active.is_(True))
        .limit(1)
    ).first()
    return int(row[0]) if row else None


def to_holder_out(
    db: Session,
    *,
    kind: Optional[str],
    ref_id: Optional[int] = None,
    ref_code: Optional[str] = None,
    ref_label: Optional[str] = None,
    user: Optional[str] = None,
    stock_location: Optional[str] = None,
    side: str = "to",
) -> HolderOut:
    """一处引用 → 界面结构。

    `side` 只影响 `kind is None` 时的文案：来源侧是"外部（新增）"，去向侧是"已注销/报损"。
    """
    if kind is None:
        return HolderOut(
            kind=None,
            kind_label="",
            display="外部（新增）" if side == "from" else "已注销 / 报损",
        )

    if kind == LightHolderKind.STOCK:
        loc = (stock_location or "").strip() or None
        return HolderOut(
            kind=kind,
            kind_label=_KIND_LABELS[kind],
            display=f"库存（{loc}）" if loc else "库存",
            hint="库房备件，还没分配出去",
            stock_location=loc,
            stock_location_station_id=_station_id_by_code(db, loc),
        )

    if kind == LightHolderKind.USER:
        return HolderOut(
            kind=kind,
            kind_label=_KIND_LABELS[kind],
            user=user,
            display=user or "未填使用人",
            # 设计 §2.4：持有位置是"人"时**不带任何点位**，绝不因为某人名下有工位就推断东西在那
            hint="在某人手上，未记录固定位置",
        )

    if kind == LightHolderKind.ASSET:
        asset = db.get(Asset, ref_id) if ref_id else None
        if asset is not None:
            return HolderOut(
                kind=kind,
                kind_label=_KIND_LABELS[kind],
                ref_id=asset.id,
                ref_code=asset.asset_code,
                ref_label=light_service.asset_label(asset),
                display=asset.asset_code,
                alive=True,
                host_user=asset.user_name,
                host_workstation_code=asset.workstation.code if asset.workstation else None,
                host_status=asset.status,
                host_status_label=AssetStatus.LABELS.get(asset.status, asset.status),
            )
        # 宿主已删：回落到快照，并明确标出来（历史仍然成立，但不能假装它还活着）
        return HolderOut(
            kind=kind,
            kind_label=_KIND_LABELS[kind],
            ref_id=ref_id,
            ref_code=ref_code,
            ref_label=ref_label,
            display=(ref_code or f"#{ref_id}") + "（已删除）",
            alive=False,
        )

    if kind == LightHolderKind.WORKSTATION:
        station = db.get(Workstation, ref_id) if ref_id else None
        if station is not None:
            return HolderOut(
                kind=kind,
                kind_label=_KIND_LABELS[kind],
                ref_id=station.id,
                ref_code=station.code,
                ref_label=station.code,
                display=_station_display(station),
                alive=station.active,
                host_user=station.user_name,
            )
        return HolderOut(
            kind=kind,
            kind_label=_KIND_LABELS[kind],
            ref_id=ref_id,
            ref_code=ref_code,
            ref_label=ref_label,
            display=(ref_code or f"#{ref_id}") + "（已删除）",
            alive=False,
        )

    return HolderOut(kind=kind, kind_label=kind, display=kind)


def category_label(category: LightCategory | None) -> str:
    """品类的单行标签：`名称 · 品牌 规格`（拼法与 `light.asset_label` 同口径）。

    只有一行位置的地方用它；界面里能分两行的地方请直接用
    `category_name` + `category_brand/spec`，别把这一整串再拆开。
    """
    if category is None:
        return ""
    spec = " ".join(x for x in (category.brand, category.spec) if x)
    return f"{category.display_name} · {spec}" if spec else category.display_name


def to_holding_out(db: Session, holding: LightHolding) -> HoldingOut:
    """一条**当前分配记录** → 界面结构。"""
    base = to_holder_out(
        db,
        kind=holding.holder_kind,
        ref_id=(
            holding.asset_id
            if holding.holder_kind == LightHolderKind.ASSET
            else holding.workstation_id
        ),
        user=holding.holder_user,
        stock_location=holding.stock_location,
    )
    category = holding.category
    return HoldingOut(
        **base.model_dump(),
        id=holding.id,
        category_id=holding.category_id,
        category_name=category.display_name if category else "",
        category_brand=category.brand if category else None,
        category_spec=category.spec if category else None,
        category_unit=category.unit if category else "",
        category_label=category_label(category),
        category_icon=category.icon if category else None,
        quantity=holding.quantity,
        since=holding.since,
        note=holding.note,
    )


def _stat_out(db: Session, category: LightCategory) -> LightCategoryStat:
    summary = light_service.category_summary(db, category)
    return LightCategoryStat(
        stock=summary["stock"],
        allocated=summary["allocated"],
        net=summary["net"],
        imported_count=summary["imported_count"],
        pending_opening=summary["pending_opening"],
        holdings=[to_holding_out(db, h) for h in summary["holdings"]],
    )


def to_category_out(db: Session, category: LightCategory) -> LightCategoryOut:
    from ..database import light_opening_key, meta_get_on

    return LightCategoryOut(
        id=category.id,
        name=category.name,
        brand=category.brand,
        spec=category.spec,
        unit=category.unit,
        notes=category.notes,
        icon=category.icon,
        active=category.active,
        created_at=category.created_at,
        updated_at=category.updated_at,
        opening_done=meta_get_on(db, light_opening_key(category.id)) == "1",
        stat=_stat_out(db, category),
    )


def can_reverse(movement: LightMovement) -> bool:
    """这条流水有没有**可用的机械反向**。

    前端用它决定「冲正」按钮显示与否 —— 后端 `LightOp.REVERSE_CANDIDATES` 里
    `报损` 的反向是空元组（撤销报损必须由人决定走哪条路），
    `安装（外部→X）` 的反向也拼不出来。这些流水如果照旧渲染按钮，
    用户点下去必然吃一个 400，那是界面在骗人。
    """
    if movement.origin == LightOrigin.IMPORT:
        return False
    if movement.corrects_id is not None:
        # 冲正记录本身不再被冲正（避免 A 冲 B、B 冲 A 绕圈）—— 路由里同样有这条限制。
        return False
    return LightOp.reverse_op(movement.op, movement.from_kind, movement.to_kind) is not None


def to_movement_out(db: Session, movement: LightMovement) -> LightMovementOut:
    return LightMovementOut(
        id=movement.id,
        category_id=movement.category_id,
        category_name=movement.category.display_name if movement.category else "",
        op=movement.op,
        op_label=LightOp.label(movement.op),
        quantity=movement.quantity,
        from_holder=to_holder_out(
            db,
            kind=movement.from_kind,
            ref_id=movement.from_ref_id,
            ref_code=movement.from_ref_code,
            ref_label=movement.from_ref_label,
            user=movement.from_user,
            # stock 的存放位置就存在 ref_code 里（与 services/light.py 写流水时同一口径）
            stock_location=(
                movement.from_ref_code if movement.from_kind == LightHolderKind.STOCK else None
            ),
            side="from",
        ),
        to_holder=to_holder_out(
            db,
            kind=movement.to_kind,
            ref_id=movement.to_ref_id,
            ref_code=movement.to_ref_code,
            ref_label=movement.to_ref_label,
            user=movement.to_user,
            stock_location=(
                movement.to_ref_code if movement.to_kind == LightHolderKind.STOCK else None
            ),
            side="to",
        ),
        operator=movement.operator,
        occurred_at=movement.occurred_at,
        created_at=movement.created_at,
        note=movement.note,
        origin=movement.origin,
        origin_label=LightOrigin.LABELS.get(movement.origin, movement.origin),
        effective=movement.effective,
        import_batch=movement.import_batch,
        corrects_id=movement.corrects_id,
        can_reverse=can_reverse(movement),
    )


__all__ = [
    "can_reverse",
    "category_label",
    "to_category_out",
    "to_holder_out",
    "to_holding_out",
    "to_movement_out",
]
