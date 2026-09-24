"""主机 ←→ 显示器 配对管理接口。

「一台主机挂几台显示器」是这个阶段要解决的核心问题，所以界面上给的是一个
配对总览（配对管理页）：左边所有主机，右边每台主机当前挂的显示器，
底部是还没配出去的显示器池——拖过去就绑定，点一下就解绑。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, AssetRelation, AuditAction, AuditTarget, DeviceCategory
from ..schemas import (
    AssetBrief,
    HostPairing,
    PairRequest,
    PairingBoard,
    RebindRequest,
    RelationHistoryPage,
    RelationOut,
    UnbindRequest,
)
from ..services import pairing
from ..services.audit import asset_label, record_audit
from ..services.serialize import to_brief, to_relation_out

router = APIRouter(prefix="/api/pairings", tags=["pairings"])


def serialize_relation(relation: AssetRelation) -> RelationOut:
    return to_relation_out(relation)


def _assets_of_category(db: Session, category: str) -> list[Asset]:
    """按设备类别的资产。走 EXISTS 子查询而不是把整表拉进来再在 Python 里过滤。"""
    return list(
        db.execute(
            select(Asset)
            .where(Asset.device_type.has(category=category))
            .order_by(Asset.asset_code.asc())
        ).scalars().all()
    )


def _get_host_or_400(db: Session, host_id: int) -> Asset:
    host = db.get(Asset, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail=f"资产 #{host_id} 不存在")
    if host.device_type is None or host.device_type.category != DeviceCategory.HOST:
        raise HTTPException(
            status_code=400,
            detail=f"「{host.asset_code}」不是主机类设备，不能作为配对的主机侧",
        )
    return host


def _get_monitor_or_400(db: Session, monitor_id: int) -> Asset:
    monitor = db.get(Asset, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail=f"资产 #{monitor_id} 不存在")
    if monitor.device_type is None or monitor.device_type.category != DeviceCategory.DISPLAY:
        raise HTTPException(
            status_code=400,
            detail=f"「{monitor.asset_code}」不是显示类设备，不能挂到主机上",
        )
    return monitor


# --------------------------------------------------------------------------- #
# 配对总览
# --------------------------------------------------------------------------- #
@router.get("/board", response_model=PairingBoard, summary="配对总览（主机 + 其显示器 + 未配对显示器池）")
def get_board(db: Session = Depends(get_db)):
    hosts = _assets_of_category(db, DeviceCategory.HOST)
    monitors = _assets_of_category(db, DeviceCategory.DISPLAY)

    active = pairing.active_relations(db)
    monitors_of: dict[int, list[AssetBrief]] = {}
    paired_monitor_ids: set[int] = set()
    for relation in active:
        monitors_of.setdefault(relation.host_id, []).append(to_brief(relation.monitor))
        paired_monitor_ids.add(relation.monitor_id)

    host_rows = [
        HostPairing(
            host=to_brief(h),
            monitors=monitors_of.get(h.id, []),
            monitor_count=len(monitors_of.get(h.id, [])),
        )
        for h in hosts
    ]
    unpaired = [to_brief(m) for m in monitors if m.id not in paired_monitor_ids]

    hints: list[str] = []
    if not hosts:
        hints.append(
            "还没有任何「主机类」设备。到「资产台账 → 设备类型」里，把主机类型（或笔记本）的类别设成主机类，它就会出现在这里。"
        )
    if not monitors:
        hints.append(
            "还没有任何「显示类」设备。到「资产台账 → 设备类型」里，把显示器类型的类别设成显示类。"
        )
    if hosts and not unpaired:
        hints.append("所有显示器都已经配出去了，没有待配对的显示器。")

    unchecked = db.execute(
        select(func.count(Asset.id)).where(Asset.device_type.has(category=DeviceCategory.OTHER))
    ).scalar_one()

    return PairingBoard(
        hosts=host_rows,
        unpaired_monitors=unpaired,
        stats={
            "hosts": len(hosts),
            "monitors": len(monitors),
            "paired_monitors": len(paired_monitor_ids),
            "unpaired_monitors": len(unpaired),
            "relations": len(active),
            "hosts_without_monitor": sum(1 for h in host_rows if not h.monitors),
            "other_assets": int(unchecked),
        },
        hints=hints,
    )


# --------------------------------------------------------------------------- #
# 绑定 / 换绑 / 解绑
# --------------------------------------------------------------------------- #
@router.post("", response_model=RelationOut, status_code=status.HTTP_201_CREATED, summary="绑定显示器到主机")
def create_pairing(payload: PairRequest, db: Session = Depends(get_db)):
    monitor = _get_monitor_or_400(db, payload.monitor_id)
    host = _get_host_or_400(db, payload.host_id)
    relation = pairing.bind(db, host=host, monitor=monitor, operator=payload.operator, note=payload.note)
    record_audit(
        db,
        AuditAction.ASSET_PAIR,
        # 审计对象记「被插上去的那台显示器」而不是主机 ——
        # 一台主机可能同时挂好几台，显示器才是那个"发生位移"的对象
        target_type=AuditTarget.ASSET,
        target_id=monitor.id,
        target_label=asset_label(monitor),
        summary=f"绑定配对：{monitor.asset_code} ↔ {host.asset_code}"
        + (f"；{payload.note}" if payload.note else ""),
        operator=payload.operator,
        detail={"host_id": host.id, "host_code": host.asset_code,
                "monitor_id": monitor.id, "monitor_code": monitor.asset_code,
                "relation_id": relation.id},
    )
    db.commit()
    db.refresh(relation)
    return serialize_relation(relation)


@router.post("/rebind", response_model=RelationOut, summary="换绑：解绑旧主机 + 绑到新主机（同一事务）")
def rebind_pairing(payload: RebindRequest, db: Session = Depends(get_db)):
    monitor = _get_monitor_or_400(db, payload.monitor_id)
    new_host = _get_host_or_400(db, payload.host_id)
    previous = pairing.active_host_relation_of(db, monitor.id)
    previous_code = previous.host.asset_code if previous is not None else None
    relation = pairing.rebind(
        db, monitor=monitor, new_host=new_host, operator=payload.operator, note=payload.note
    )
    summary = f"更换配对：{monitor.asset_code} "
    summary += f"{previous_code} → {new_host.asset_code}" if previous_code else f"→ {new_host.asset_code}"
    record_audit(
        db,
        AuditAction.ASSET_REBIND,
        target_type=AuditTarget.ASSET,
        target_id=monitor.id,
        target_label=asset_label(monitor),
        summary=summary,
        operator=payload.operator,
        detail={"monitor_id": monitor.id, "monitor_code": monitor.asset_code,
                "from_host_code": previous_code, "to_host_code": new_host.asset_code,
                "to_host_id": new_host.id, "relation_id": relation.id},
    )
    db.commit()
    db.refresh(relation)
    return serialize_relation(relation)


@router.post("/{relation_id}/release", response_model=RelationOut, summary="解绑")
def release_pairing(relation_id: int, payload: UnbindRequest, db: Session = Depends(get_db)):
    relation = db.get(AssetRelation, relation_id)
    if relation is None:
        raise HTTPException(status_code=404, detail=f"配对记录 #{relation_id} 不存在")

    # 解绑前先把两边编号抄下来：unbind 之后 relation 上的关系对象仍在，
    # 但这里提前取出来，审计记录就不会依赖后续状态
    host_code = relation.host.asset_code if relation.host else None
    monitor_code = relation.monitor.asset_code if relation.monitor else None

    pairing.unbind(db, relation=relation, operator=payload.operator, note=payload.note)
    record_audit(
        db,
        AuditAction.ASSET_UNPAIR,
        target_type=AuditTarget.ASSET,
        target_id=relation.monitor_id,
        target_label=asset_label(relation.monitor) if relation.monitor else f"#{relation.monitor_id}",
        summary=f"解除配对：{host_code} ↔ {monitor_code}"
        + (f"；{payload.note}" if payload.note else ""),
        operator=payload.operator,
        detail={"relation_id": relation.id, "host_id": relation.host_id, "host_code": host_code,
                "monitor_id": relation.monitor_id, "monitor_code": monitor_code},
    )
    db.commit()
    db.refresh(relation)
    return serialize_relation(relation)


# --------------------------------------------------------------------------- #
# 配对历史
# --------------------------------------------------------------------------- #
@router.get("/history", response_model=RelationHistoryPage, summary="配对历史（含已解绑）")
def get_history(
    db: Session = Depends(get_db),
    asset_id: Optional[int] = Query(None, description="只看与某台设备相关的记录"),
    active_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    conditions = []
    if asset_id is not None:
        conditions.append(or_(AssetRelation.host_id == asset_id, AssetRelation.monitor_id == asset_id))
    if active_only:
        conditions.append(AssetRelation.active.is_(True))

    stmt = select(AssetRelation)
    count_stmt = select(func.count(AssetRelation.id))
    if conditions:
        stmt = stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(AssetRelation.active.desc(), AssetRelation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    return RelationHistoryPage(total=int(total), items=[serialize_relation(r) for r in rows])
