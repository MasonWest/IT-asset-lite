"""资产台账相关接口。

第二阶段在三件事上做了扩展，都围绕「设备的生命周期」：
  1. 每次新增 / 编辑 / 状态流转都会往 asset_events 落一条，详情页的时间线就是它；
  2. 详情里带上配对信息（主机挂了哪些显示器 / 显示器挂在哪台主机）；
  3. 状态不再靠编辑表单硬改，而是走 /operations 的「领用 / 归还 / 送修 / 报废」动作，
     每个动作自带状态机校验，改错了会被 409 挡住。

第三阶段加了一层「谁干的」：每个写操作除了 asset_events 之外，
还往 audit_logs 追加一条 —— 带操作人、IP、操作对象和变更摘要。
两者同事务提交，所以不会出现"改了但没记"或"记了但没改"。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Asset,
    AssetEvent,
    AssetEventType,
    AssetOperation,
    AssetRelation,
    AssetStatus,
    AuditAction,
    AuditTarget,
    DeviceCategory,
    DeviceType,
)
from ..schemas import (
    AssetCreate,
    AssetEventOut,
    AssetOperationOut,
    AssetOperationRequest,
    AssetOut,
    AssetPage,
    AssetUpdate,
    RelationHistoryPage,
    RelationOut,
    TimelinePage,
)
from ..services import pairing
from ..services.audit import asset_label, describe_operation, record_audit
from ..services.events import describe_changes, diff_changes, record_event
from ..services.filters import build_conditions
from ..services.serialize import to_asset_out, to_brief, to_event_out, to_relation_out

router = APIRouter(prefix="/api/assets", tags=["assets"])

#: 编辑资产时参与「变更差异」比较的字段（operator 是元数据，不参与）
_TRACKED_FIELDS = (
    "asset_code",
    "finance_code",
    "device_type_id",
    "brand",
    "model",
    "serial_number",
    "status",
    "user_name",
    "location",
    "purchase_date",
    "warranty_until",
    "notes",
)


# --------------------------------------------------------------------------- #
# 序列化（实现都搬到了 services.serialize，这里保留同名薄壳方便阅读）
# --------------------------------------------------------------------------- #
def serialize_asset(asset: Asset, **kwargs) -> AssetOut:
    return to_asset_out(asset, **kwargs)


def serialize_event(event: AssetEvent) -> AssetEventOut:
    return to_event_out(event)


def serialize_relation(relation: AssetRelation) -> RelationOut:
    return to_relation_out(relation)


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _get_asset_or_404(db: Session, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"资产 #{asset_id} 不存在")
    return asset


def generate_asset_code(db: Session) -> str:
    """自动生成 IT 资产编号：IT-<年份>-<4位流水号>。"""
    prefix = f"IT-{datetime.now().year}-"
    rows = db.execute(select(Asset.asset_code).where(Asset.asset_code.like(f"{prefix}%"))).scalars().all()

    max_seq = 0
    for code in rows:
        tail = code.rsplit("-", 1)[-1]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:04d}"


def _ensure_code_unique(db: Session, code: str, exclude_id: Optional[int] = None) -> None:
    stmt = select(Asset.id).where(Asset.asset_code == code)
    if exclude_id is not None:
        stmt = stmt.where(Asset.id != exclude_id)
    if db.execute(stmt).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"IT 资产编号「{code}」已被占用")


def _pairing_maps(db: Session, assets: list[Asset]):
    return pairing.pairing_maps(db, assets)


def _serialize_detail(db: Session, asset: Asset) -> AssetOut:
    """详情：把该设备当前的配对对象一起带上。"""
    category = asset.device_type.category if asset.device_type else DeviceCategory.OTHER

    if category == DeviceCategory.HOST:
        relations = pairing.active_monitors_of(db, asset.id)
        return serialize_asset(
            asset,
            monitor_count=len(relations),
            monitors=[to_brief(r.monitor) for r in relations],
        )

    if category == DeviceCategory.DISPLAY:
        relation = pairing.active_host_relation_of(db, asset.id)
        if relation is not None:
            return serialize_asset(
                asset,
                host_id=relation.host_id,
                host_code=relation.host.asset_code,
                host=to_brief(relation.host),
            )

    return serialize_asset(asset)


# --------------------------------------------------------------------------- #
# 列表 / 详情
# --------------------------------------------------------------------------- #
@router.get("", response_model=AssetPage, summary="资产列表（带筛选与分页）")
def list_assets(
    db: Session = Depends(get_db),
    keyword: Optional[str] = Query(None, description="模糊搜索：编号/品牌/型号/SN/使用人/位置/备注"),
    device_type_id: Optional[str] = Query(None, description="设备类型 ID，多个用逗号分隔"),
    status: Optional[str] = Query(None, description="状态，多个用逗号分隔"),
    user_name: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    paired: Optional[str] = Query(None, description="配对筛选：paired 已配对 / unpaired 未配对"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
):
    stmt = select(Asset)
    count_stmt = select(func.count(Asset.id))

    conditions = build_conditions(
        keyword=keyword,
        device_type_id=device_type_id,
        status=status,
        user_name=user_name,
        location=location,
        paired=paired,
    )

    if conditions:
        stmt = stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(Asset.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    counts, host_map = _pairing_maps(db, list(rows))
    items = []
    for a in rows:
        hid_code = host_map.get(a.id)
        items.append(
            serialize_asset(
                a,
                monitor_count=counts.get(a.id, 0),
                host_id=hid_code[0] if hid_code else None,
                host_code=hid_code[1] if hid_code else None,
            )
        )
    return AssetPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/{asset_id}", response_model=AssetOut, summary="资产详情")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    return _serialize_detail(db, _get_asset_or_404(db, asset_id))


@router.get("/{asset_id}/timeline", response_model=TimelinePage, summary="设备变更历史（时间线）")
def get_timeline(
    asset_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=500),
):
    asset = _get_asset_or_404(db, asset_id)
    rows = db.execute(
        select(AssetEvent)
        .where(AssetEvent.asset_id == asset_id)
        .order_by(AssetEvent.created_at.desc(), AssetEvent.id.desc())
        .limit(limit)
    ).scalars().all()

    total = db.execute(
        select(func.count(AssetEvent.id)).where(AssetEvent.asset_id == asset_id)
    ).scalar_one()

    return TimelinePage(
        asset_id=asset.id,
        asset_code=asset.asset_code,
        total=int(total),
        items=[serialize_event(e) for e in rows],
    )


@router.get(
    "/{asset_id}/relations",
    response_model=RelationHistoryPage,
    summary="该设备的全部配对记录（含已解绑）",
)
def get_relations(asset_id: int, db: Session = Depends(get_db)):
    _get_asset_or_404(db, asset_id)
    rows = db.execute(
        select(AssetRelation)
        .where(or_(AssetRelation.host_id == asset_id, AssetRelation.monitor_id == asset_id))
        .order_by(AssetRelation.active.desc(), AssetRelation.id.desc())
    ).scalars().all()
    return RelationHistoryPage(total=len(rows), items=[serialize_relation(r) for r in rows])


# --------------------------------------------------------------------------- #
# 新增 / 编辑 / 删除
# --------------------------------------------------------------------------- #
@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED, summary="新增资产")
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)):
    if db.get(DeviceType, payload.device_type_id) is None:
        raise HTTPException(status_code=400, detail=f"设备类型 #{payload.device_type_id} 不存在")

    data = payload.model_dump()
    operator = data.pop("operator", None)

    code = (data.get("asset_code") or "").strip()
    if not code:
        code = generate_asset_code(db)
    _ensure_code_unique(db, code)
    data["asset_code"] = code

    asset = Asset(**data)
    db.add(asset)
    db.flush()  # 先拿到 id，事件才能挂上去

    record_event(
        db,
        asset,
        AssetEventType.CREATE,
        to_status=asset.status,
        operator=operator,
        note=f"录入台账：{asset.asset_code}",
    )
    record_audit(
        db,
        AuditAction.ASSET_CREATE,
        target_type=AuditTarget.ASSET,
        target_id=asset.id,
        target_label=asset_label(asset),
        summary=f"录入台账，状态「{AssetStatus.LABELS.get(asset.status, asset.status)}」",
        operator=operator,
        detail={
            "asset_code": asset.asset_code,
            "device_type": asset.device_type.name if asset.device_type else None,
            "status": asset.status,
            "source": "手工录入",
        },
    )
    db.commit()
    db.refresh(asset)
    return _serialize_detail(db, asset)


@router.put("/{asset_id}", response_model=AssetOut, summary="编辑资产")
def update_asset(asset_id: int, payload: AssetUpdate, db: Session = Depends(get_db)):
    asset = _get_asset_or_404(db, asset_id)
    data = payload.model_dump(exclude_unset=True)
    operator = data.pop("operator", None)

    if "device_type_id" in data and data["device_type_id"] is not None:
        if db.get(DeviceType, data["device_type_id"]) is None:
            raise HTTPException(status_code=400, detail=f"设备类型 #{data['device_type_id']} 不存在")
    else:
        data.pop("device_type_id", None)

    if "asset_code" in data:
        code = (data["asset_code"] or "").strip()
        if not code:
            data.pop("asset_code")  # 不允许清空编号
        else:
            _ensure_code_unique(db, code, exclude_id=asset_id)
            data["asset_code"] = code

    # 改动前的快照，用来算「改了什么」写进时间线
    before = {field: getattr(asset, field) for field in _TRACKED_FIELDS}

    for key, value in data.items():
        setattr(asset, key, value)

    after = {field: getattr(asset, field) for field in _TRACKED_FIELDS}
    changes = diff_changes(before, after)

    if changes:
        status_changed = "status" in changes
        event_type = AssetEventType.STATUS if status_changed and len(changes) == 1 else AssetEventType.UPDATE
        summary = describe_changes(changes)
        record_event(
            db,
            asset,
            event_type,
            from_status=changes["status"]["from"] if status_changed else None,
            to_status=changes["status"]["to"] if status_changed else None,
            operator=operator,
            note=summary,
            detail={"changes": changes},
        )
        # 只改了状态就归到「状态流转」，改了别的字段算「编辑」——
        # 审计页按操作类型筛"谁改过状态"时，这样才筛得准
        record_audit(
            db,
            AuditAction.ASSET_OPERATE if status_changed and len(changes) == 1 else AuditAction.ASSET_UPDATE,
            target_type=AuditTarget.ASSET,
            target_id=asset.id,
            target_label=asset_label(asset),
            summary=summary,
            operator=operator,
            detail={"changes": changes, "fields": list(changes.keys())},
        )

    db.commit()
    db.refresh(asset)
    return _serialize_detail(db, asset)


@router.delete("/{asset_id}", status_code=status.HTTP_200_OK, summary="删除资产")
def delete_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None, description="操作人，仅用于日志"),
):
    asset = _get_asset_or_404(db, asset_id)
    code = asset.asset_code
    # 审计要记到设备的编号和型号，所以必须在删之前把标签取出来 —— 删完就查不到了
    label = asset_label(asset)
    spec = {
        "device_type": asset.device_type.name if asset.device_type else None,
        "status": asset.status,
        "user_name": asset.user_name,
        "location": asset.location,
    }

    # 被删的设备如果还挂着显示器，先解开，否则显示器会指向一个不存在的宿主
    unbound = 0
    for relation in pairing.active_monitors_of(db, asset_id):
        pairing.unbind(db, relation=relation, operator=operator, note="宿主设备被删除，自动解绑")
        unbound += 1
    current = pairing.active_host_relation_of(db, asset_id)
    if current is not None:
        pairing.unbind(db, relation=current, operator=operator, note="显示器被删除，自动解绑")
        unbound += 1

    record_audit(
        db,
        AuditAction.ASSET_DELETE,
        target_type=AuditTarget.ASSET,
        target_id=asset_id,
        target_label=label,
        summary=f"删除设备 {code}" + (f"，同时自动解除 {unbound} 条配对" if unbound else ""),
        operator=operator,
        detail={**spec, "auto_unbound": unbound},
    )

    db.flush()
    db.delete(asset)
    db.commit()
    return {"ok": True, "deleted_id": asset_id, "asset_code": code}


# --------------------------------------------------------------------------- #
# 状态流转：领用 / 归还 / 送修 / 维修完成 / 报废 / 恢复入库
# --------------------------------------------------------------------------- #
@router.post(
    "/{asset_id}/operations",
    response_model=AssetOperationOut,
    summary="执行状态流转操作",
)
def run_operation(asset_id: int, payload: AssetOperationRequest, db: Session = Depends(get_db)):
    asset = _get_asset_or_404(db, asset_id)

    action = payload.action
    allowed = AssetOperation.ALLOWED_FROM.get(action, ())
    current = asset.status

    if current not in allowed:
        allowed_text = "、".join(AssetStatus.LABELS.get(s, s) for s in allowed)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"设备当前状态是「{AssetStatus.LABELS.get(current, current)}」，"
                f"不能执行「{AssetOperation.LABELS[action]}」。"
                f"该操作允许的状态：{allowed_text}"
            ),
        )

    if action in AssetOperation.REQUIRES_USER and not payload.user_name:
        raise HTTPException(
            status_code=400,
            detail=f"「{AssetOperation.LABELS[action]}」必须填写使用人",
        )

    target = AssetOperation.TARGET_STATUS[action]

    # 领用/归还时顺手把归属信息一起更新，少一步手改表单
    if action == AssetOperation.CHECKOUT:
        asset.user_name = payload.user_name
    elif action in (AssetOperation.RETURN, AssetOperation.REPAIR_DONE, AssetOperation.RESTORE):
        asset.user_name = None
    if payload.location:
        asset.location = payload.location

    asset.status = target

    note = payload.note
    if not note:
        if action == AssetOperation.CHECKOUT:
            note = f"{payload.user_name} 领用"
        elif action == AssetOperation.RETURN:
            note = "归还入库"
        else:
            note = AssetOperation.LABELS[action]

    event = record_event(
        db,
        asset,
        action,
        from_status=current,
        to_status=target,
        operator=payload.operator,
        note=note,
        detail={
            "action": action,
            "user_name": asset.user_name,
            "location": asset.location,
        },
    )
    record_audit(
        db,
        AuditAction.ASSET_OPERATE,
        target_type=AuditTarget.ASSET,
        target_id=asset.id,
        target_label=asset_label(asset),
        summary=describe_operation(action, current, target, note),
        operator=payload.operator,
        detail={
            "action": action,
            "action_label": AssetOperation.LABELS.get(action, action),
            "from_status": current,
            "to_status": target,
            "user_name": asset.user_name,
            "location": asset.location,
        },
    )

    db.commit()
    db.refresh(asset)
    db.refresh(event)
    return AssetOperationOut(asset=_serialize_detail(db, asset), event=serialize_event(event))
