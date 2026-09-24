"""资产筛选条件构造。

列表页和「发起盘点」用的是同一套筛选口径 —— 抽出来共用，
免得哪天列表支持了新条件、盘点范围却没跟上，盘出来的东西和界面对不上。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import or_, select

from ..models import Asset, AssetRelation


def split_ids(raw: Optional[str]) -> list[int]:
    """把 "1,3,5" 这种逗号串拆成 [1,3,5]，非数字的块直接丢掉。"""
    if not raw:
        return []
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.append(int(chunk))
    return out


def split_values(raw: Optional[str]) -> list[str]:
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def active_host_ids():
    return select(AssetRelation.host_id).where(AssetRelation.active.is_(True))


def active_monitor_ids():
    return select(AssetRelation.monitor_id).where(AssetRelation.active.is_(True))


def build_conditions(
    *,
    keyword: Optional[str] = None,
    device_type_id: Optional[str] = None,
    status: Optional[str] = None,
    user_name: Optional[str] = None,
    location: Optional[str] = None,
    paired: Optional[str] = None,
) -> list:
    """返回 SQLAlchemy 条件列表，调用方自己拼到 select 上。"""
    conditions = []

    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Asset.asset_code.ilike(kw),
                Asset.finance_code.ilike(kw),
                Asset.brand.ilike(kw),
                Asset.model.ilike(kw),
                Asset.serial_number.ilike(kw),
                Asset.user_name.ilike(kw),
                Asset.location.ilike(kw),
                Asset.notes.ilike(kw),
            )
        )

    type_ids = split_ids(device_type_id)
    if type_ids:
        conditions.append(Asset.device_type_id.in_(type_ids))

    statuses = split_values(status)
    if statuses:
        conditions.append(Asset.status.in_(statuses))

    if user_name:
        conditions.append(Asset.user_name == user_name)
    if location:
        conditions.append(Asset.location == location)

    if paired == "paired":
        conditions.append(or_(Asset.id.in_(active_host_ids()), Asset.id.in_(active_monitor_ids())))
    elif paired == "unpaired":
        conditions.append(~or_(Asset.id.in_(active_host_ids()), Asset.id.in_(active_monitor_ids())))

    return conditions
