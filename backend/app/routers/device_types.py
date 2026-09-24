"""设备类型接口 —— 类型可自定义增删改。

第二阶段给类型加了「类别」（主机类 / 显示类 / 其他）：
配对功能靠它判断哪台设备能当主机、哪台能当显示器。
类别不按名字自动判断 —— 用户完全可以把「主机」这个类型改名成「工控机」，
名字一改，靠名字猜的逻辑就全废了。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, AuditAction, AuditTarget, DeviceCategory, DeviceType
from ..schemas import DeviceTypeCreate, DeviceTypeOut, DeviceTypeUpdate
from ..services.audit import record_audit

router = APIRouter(prefix="/api/device-types", tags=["device-types"])


def _count_map(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(Asset.device_type_id, func.count(Asset.id)).group_by(Asset.device_type_id)
    ).all()
    return {int(tid): int(cnt) for tid, cnt in rows}


def _serialize(dtype: DeviceType, counts: dict[int, int]) -> DeviceTypeOut:
    return DeviceTypeOut(
        id=dtype.id,
        name=dtype.name,
        sort_order=dtype.sort_order,
        category=dtype.category,
        category_label=DeviceCategory.LABELS.get(dtype.category, dtype.category),
        asset_count=counts.get(dtype.id, 0),
    )


@router.get("", response_model=list[DeviceTypeOut], summary="设备类型列表")
def list_device_types(db: Session = Depends(get_db)):
    counts = _count_map(db)
    rows = db.execute(
        select(DeviceType).order_by(DeviceType.sort_order.asc(), DeviceType.id.asc())
    ).scalars().all()
    return [_serialize(d, counts) for d in rows]


@router.post("", response_model=DeviceTypeOut, status_code=status.HTTP_201_CREATED, summary="新增设备类型")
def create_device_type(
    payload: DeviceTypeCreate,
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None, description="操作人，写进审计日志"),
):
    exists = db.execute(select(DeviceType).where(DeviceType.name == payload.name)).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail=f"设备类型「{payload.name}」已存在")

    category = payload.category or DeviceCategory.guess(payload.name)
    dtype = DeviceType(name=payload.name, sort_order=payload.sort_order, category=category)
    db.add(dtype)
    db.flush()
    record_audit(
        db,
        AuditAction.TYPE_CREATE,
        target_type=AuditTarget.DEVICE_TYPE,
        target_id=dtype.id,
        target_label=f"设备类型：{dtype.name}",
        summary=f"新增设备类型「{dtype.name}」，类别 {DeviceCategory.LABELS.get(category, category)}",
        operator=operator,
        detail={"name": dtype.name, "category": category},
    )
    db.commit()
    db.refresh(dtype)
    return _serialize(dtype, {})


@router.put("/{type_id}", response_model=DeviceTypeOut, summary="重命名 / 调整类别 / 排序")
def update_device_type(
    type_id: int,
    payload: DeviceTypeUpdate,
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None, description="操作人，写进审计日志"),
):
    dtype = db.get(DeviceType, type_id)
    if dtype is None:
        raise HTTPException(status_code=404, detail=f"设备类型 #{type_id} 不存在")

    before = {"name": dtype.name, "category": dtype.category, "sort_order": dtype.sort_order}
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        dup = db.execute(
            select(DeviceType).where(DeviceType.name == data["name"], DeviceType.id != type_id)
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"设备类型「{data['name']}」已存在")
        dtype.name = data["name"]
    if data.get("sort_order") is not None:
        dtype.sort_order = data["sort_order"]
    if data.get("category"):
        dtype.category = data["category"]

    changes = []
    if before["name"] != dtype.name:
        changes.append(f"名称 {before['name']} → {dtype.name}")
    if before["category"] != dtype.category:
        changes.append(
            f"类别 {DeviceCategory.LABELS.get(before['category'], before['category'])}"
            f" → {DeviceCategory.LABELS.get(dtype.category, dtype.category)}"
        )
    if before["sort_order"] != dtype.sort_order:
        changes.append(f"排序 {before['sort_order']} → {dtype.sort_order}")

    record_audit(
        db,
        AuditAction.TYPE_UPDATE,
        target_type=AuditTarget.DEVICE_TYPE,
        target_id=dtype.id,
        target_label=f"设备类型：{dtype.name}",
        summary="；".join(changes) or "提交但未产生变更",
        operator=operator,
        detail={"changes": changes, **before, "after_name": dtype.name},
    )

    db.commit()
    db.refresh(dtype)
    return _serialize(dtype, _count_map(db))


@router.delete("/{type_id}", summary="删除设备类型（仍有资产引用时不允许删）")
def delete_device_type(
    type_id: int,
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None, description="操作人，写进审计日志"),
):
    dtype = db.get(DeviceType, type_id)
    if dtype is None:
        raise HTTPException(status_code=404, detail=f"设备类型 #{type_id} 不存在")

    used = db.execute(
        select(func.count(Asset.id)).where(Asset.device_type_id == type_id)
    ).scalar_one()
    if used:
        raise HTTPException(
            status_code=409,
            detail=f"还有 {used} 台设备属于「{dtype.name}」，先改掉这些设备的类型再删除",
        )

    record_audit(
        db,
        AuditAction.TYPE_DELETE,
        target_type=AuditTarget.DEVICE_TYPE,
        target_id=type_id,
        target_label=f"设备类型：{dtype.name}",
        summary=f"删除设备类型「{dtype.name}」（类别 {DeviceCategory.LABELS.get(dtype.category, dtype.category)}）",
        operator=operator,
        detail={"name": dtype.name, "category": dtype.category},
    )
    db.delete(dtype)
    db.commit()
    return {"ok": True, "deleted_id": type_id}
