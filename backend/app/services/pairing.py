"""主机 ←→ 显示器 配对逻辑。

配对是「一台主机挂 N 台显示器」的一对多结构，所以关系表里存的是
(host_id, monitor_id) 二元组，约束落在显示器那一侧：

    同一时刻，一台显示器最多只能出现在一条 active 的配对里。

「换绑」= 先解绑旧的再加入新的。不直接改 host_id ——
那样会把「它以前在哪台机器上」这段历史抹掉，而这正是要留痕的东西。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Asset, AssetEventType, AssetRelation, DeviceCategory
from .events import record_event


def active_monitors_of(db: Session, host_id: int) -> list[AssetRelation]:
    """某台主机当前挂着的显示器。"""
    return list(
        db.execute(
            select(AssetRelation)
            .where(AssetRelation.host_id == host_id, AssetRelation.active.is_(True))
            .order_by(AssetRelation.id.asc())
        ).scalars().all()
    )


def active_host_relation_of(db: Session, monitor_id: int) -> Optional[AssetRelation]:
    """某台显示器当前挂在哪台主机上。没配就是 None。"""
    return db.execute(
        select(AssetRelation)
        .where(AssetRelation.monitor_id == monitor_id, AssetRelation.active.is_(True))
        .order_by(AssetRelation.id.desc())
    ).scalars().first()


def active_relations(db: Session) -> list[AssetRelation]:
    return list(
        db.execute(
            select(AssetRelation).where(AssetRelation.active.is_(True)).order_by(AssetRelation.id.asc())
        ).scalars().all()
    )


def monitor_counts(db: Session, host_ids: Optional[Iterable[int]] = None) -> dict[int, int]:
    """{host_id: 当前挂了几台显示器}"""
    stmt = (
        select(AssetRelation.host_id, func.count(AssetRelation.id))
        .where(AssetRelation.active.is_(True))
        .group_by(AssetRelation.host_id)
    )
    if host_ids is not None:
        ids = list(host_ids)
        if not ids:
            return {}
        stmt = stmt.where(AssetRelation.host_id.in_(ids))
    return {int(hid): int(cnt) for hid, cnt in db.execute(stmt).all()}


def relation_count(db: Session) -> int:
    return int(
        db.execute(
            select(func.count(AssetRelation.id)).where(AssetRelation.active.is_(True))
        ).scalar_one()
    )


def pairing_maps(db: Session, assets: list[Asset]) -> tuple[dict[int, int], dict[int, tuple[int, str]]]:
    """批量取配对信息，避免列表页 N+1。

    返回 (主机 ID → 显示器台数, 显示器 ID → (主机 ID, 主机编号))。
    """
    if not assets:
        return {}, {}

    host_ids: list[int] = []
    monitor_ids: list[int] = []
    for a in assets:
        category = a.device_type.category if a.device_type else DeviceCategory.OTHER
        if category == DeviceCategory.HOST:
            host_ids.append(a.id)
        elif category == DeviceCategory.DISPLAY:
            monitor_ids.append(a.id)

    counts = monitor_counts(db, host_ids) if host_ids else {}

    host_map: dict[int, tuple[int, str]] = {}
    if monitor_ids:
        rows = db.execute(
            select(AssetRelation.monitor_id, Asset.id, Asset.asset_code)
            .join(Asset, Asset.id == AssetRelation.host_id)
            .where(AssetRelation.active.is_(True), AssetRelation.monitor_id.in_(monitor_ids))
        ).all()
        host_map = {int(mid): (int(hid), str(code)) for mid, hid, code in rows}

    return counts, host_map


def _ensure_pairable(host: Asset, monitor: Asset) -> None:
    if host.id == monitor.id:
        raise HTTPException(status_code=400, detail="不能把设备配到它自己身上")
    if _category_of_asset(host) != DeviceCategory.HOST:
        raise HTTPException(
            status_code=400,
            detail=f"「{host.asset_code}」不是主机类设备，不能作为主机去挂显示器。"
            f"如果它确实是主机，请到「设备类型」里把类型类别改成主机类",
        )
    if _category_of_asset(monitor) != DeviceCategory.DISPLAY:
        raise HTTPException(
            status_code=400,
            detail=f"「{monitor.asset_code}」不是显示类设备，不能挂到主机上。"
            f"如果它确实是显示器，请到「设备类型」里把类型类别改成显示类",
        )


def _category_of_asset(asset: Asset) -> str:
    """给已加载的 Asset 用，避免为了拿 category 再查一次类型表。"""
    dtype = asset.device_type
    if dtype is None:
        return DeviceCategory.OTHER
    return dtype.category


def bind(
    db: Session,
    *,
    host: Asset,
    monitor: Asset,
    operator: Optional[str] = None,
    note: Optional[str] = None,
) -> AssetRelation:
    """把一台显示器挂到一台主机上。显示器已经挂在别处时会 409，让调用方先解绑。"""
    _ensure_pairable(host, monitor)

    existing = active_host_relation_of(db, monitor.id)
    if existing is not None:
        if existing.host_id == host.id:
            raise HTTPException(
                status_code=409,
                detail=f"「{monitor.asset_code}」已经挂在「{host.asset_code}」上了，不用重复绑定",
            )
        old_host = db.get(Asset, existing.host_id)
        raise HTTPException(
            status_code=409,
            detail=f"「{monitor.asset_code}」当前挂在「{old_host.asset_code if old_host else existing.host_id}」上，"
            f"请先解绑再挂到本机（界面上直接点「换绑」会一次做完）",
        )

    relation = AssetRelation(
        host_id=host.id,
        monitor_id=monitor.id,
        active=True,
        note=(note or "").strip() or None,
        created_by=(operator or "").strip() or None,
    )
    db.add(relation)
    db.flush()  # 拿到 relation.id 再写事件

    detail = {
        "relation_id": relation.id,
        "host_id": host.id,
        "host_code": host.asset_code,
        "monitor_id": monitor.id,
        "monitor_code": monitor.asset_code,
    }
    record_event(
        db,
        host,
        AssetEventType.PAIR,
        operator=operator,
        note=note or f"绑定显示器 {monitor.asset_code}",
        detail={**detail, "role": "host"},
    )
    record_event(
        db,
        monitor,
        AssetEventType.PAIR,
        operator=operator,
        note=note or f"挂到主机 {host.asset_code}",
        detail={**detail, "role": "monitor"},
    )
    return relation


def unbind(
    db: Session,
    *,
    relation: AssetRelation,
    operator: Optional[str] = None,
    note: Optional[str] = None,
) -> AssetRelation:
    """解绑。不删行，把 active 置 0 并记下时间和操作人，历史永远查得到。"""
    if not relation.active:
        raise HTTPException(status_code=409, detail="这条配对关系已经解除过了")

    host = db.get(Asset, relation.host_id)
    monitor = db.get(Asset, relation.monitor_id)

    relation.active = False
    relation.released_at = datetime.now()
    relation.released_by = (operator or "").strip() or None
    relation.release_note = (note or "").strip() or None

    detail = {
        "relation_id": relation.id,
        "host_id": relation.host_id,
        "host_code": host.asset_code if host else None,
        "monitor_id": relation.monitor_id,
        "monitor_code": monitor.asset_code if monitor else None,
    }
    if host is not None:
        record_event(
            db,
            host,
            AssetEventType.UNPAIR,
            operator=operator,
            note=note or f"解绑显示器 {monitor.asset_code if monitor else relation.monitor_id}",
            detail={**detail, "role": "host"},
        )
    if monitor is not None:
        record_event(
            db,
            monitor,
            AssetEventType.UNPAIR,
            operator=operator,
            note=note or f"从主机 {host.asset_code if host else relation.host_id} 上拆下",
            detail={**detail, "role": "monitor"},
        )
    return relation


def rebind(
    db: Session,
    *,
    monitor: Asset,
    new_host: Asset,
    operator: Optional[str] = None,
    note: Optional[str] = None,
) -> AssetRelation:
    """换绑：一个事务里「解绑旧的 + 绑上新的」，中途不会出现悬空状态。"""
    current = active_host_relation_of(db, monitor.id)
    if current is not None:
        if current.host_id == new_host.id:
            raise HTTPException(status_code=409, detail=f"「{monitor.asset_code}」已经挂在这台主机上了")
        suffix = f"（换绑：{note}）" if note else "（换绑）"
        unbind(db, relation=current, operator=operator, note=suffix)
        db.flush()
    return bind(db, host=new_host, monitor=monitor, operator=operator, note=note)
