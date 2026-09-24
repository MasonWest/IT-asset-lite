"""筛选条件元数据接口。

「使用人」「存放位置」不做字典表，直接从资产表现有数据里 distinct 出来，
用户随手输入一个新值就能用，不用先去后台维护字典。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Asset, AssetStatus, DeviceCategory, DeviceType
from ..schemas import DeviceTypeOut, FilterOptions
from ..services import pairing

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/filters", response_model=FilterOptions, summary="筛选下拉候选值 + 统计")
def get_filters(db: Session = Depends(get_db)):
    types = db.execute(
        select(DeviceType).order_by(DeviceType.sort_order.asc(), DeviceType.id.asc())
    ).scalars().all()
    type_counts = {
        int(tid): int(cnt)
        for tid, cnt in db.execute(
            select(Asset.device_type_id, func.count(Asset.id)).group_by(Asset.device_type_id)
        ).all()
    }

    status_counts = {
        str(s): int(cnt)
        for s, cnt in db.execute(
            select(Asset.status, func.count(Asset.id)).group_by(Asset.status)
        ).all()
    }

    users = [
        u for (u,) in db.execute(
            select(Asset.user_name)
            .where(Asset.user_name.is_not(None), Asset.user_name != "")
            .distinct()
            .order_by(Asset.user_name.asc())
        ).all()
    ]

    locations = [
        loc for (loc,) in db.execute(
            select(Asset.location)
            .where(Asset.location.is_not(None), Asset.location != "")
            .distinct()
            .order_by(Asset.location.asc())
        ).all()
    ]

    host_count = db.execute(
        select(func.count(Asset.id)).where(Asset.device_type.has(category=DeviceCategory.HOST))
    ).scalar_one()
    monitor_count = db.execute(
        select(func.count(Asset.id)).where(Asset.device_type.has(category=DeviceCategory.DISPLAY))
    ).scalar_one()

    return FilterOptions(
        device_types=[
            DeviceTypeOut(
                id=t.id,
                name=t.name,
                sort_order=t.sort_order,
                category=t.category,
                category_label=DeviceCategory.LABELS.get(t.category, t.category),
                asset_count=type_counts.get(t.id, 0),
            )
            for t in types
        ],
        statuses=[{"value": v, "label": AssetStatus.LABELS[v]} for v in AssetStatus.ALL],
        users=users,
        locations=locations,
        total=int(db.execute(select(func.count(Asset.id))).scalar_one()),
        status_counts=status_counts,
        device_type_counts={str(k): v for k, v in type_counts.items()},
        pair_stats={
            "hosts": int(host_count),
            "monitors": int(monitor_count),
            "paired_monitors": pairing.relation_count(db),
        },
    )
