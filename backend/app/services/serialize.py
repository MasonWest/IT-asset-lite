"""把 ORM 对象转成对外结构的小工具。

配对关系里要展示「对方设备」，但不需要对方的全部字段，
统一走 to_brief()，避免每个路由各写一份拼装逻辑。
to_relation_out() 同理 —— 资产详情页和配对管理页都要输出配对记录，
两边各写一份迟早会不一致。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from ..models import (
    Asset,
    AssetEvent,
    AssetEventType,
    AssetOperation,
    AssetRelation,
    AssetStatus,
    DeviceCategory,
)
from ..schemas import AssetBrief, AssetEventOut, AssetGroupFlags, AssetGroupOut, AssetOut, RelationOut


def parse_json_dict(raw: Optional[str]) -> Optional[dict[str, Any]]:
    """解析存在 Text 列里的 JSON 对象。

    detail / scope / changes 这些字段在各表里都是「JSON 塞进 Text」的写法，
    解析失败一律当没有 —— 一条脏数据不该把整个列表接口打成 500。
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def to_asset_out(
    asset: Asset,
    *,
    monitor_count: int = 0,
    host_id: Optional[int] = None,
    host_code: Optional[str] = None,
    monitors: Optional[list[AssetBrief]] = None,
    host: Optional[AssetBrief] = None,
) -> AssetOut:
    """资产 → 对外结构。

    列表页和盘点页都要这个结构，放一处免得两边字段慢慢长歪。
    """
    dtype = asset.device_type
    days_left: Optional[int] = None
    if asset.warranty_until:
        days_left = (asset.warranty_until - date.today()).days

    return AssetOut(
        id=asset.id,
        asset_code=asset.asset_code,
        finance_code=asset.finance_code,
        device_type_id=asset.device_type_id,
        device_type_name=dtype.name if dtype else "",
        device_category=dtype.category if dtype else DeviceCategory.OTHER,
        device_type={"id": dtype.id, "name": dtype.name, "category": dtype.category} if dtype else None,
        brand=asset.brand,
        model=asset.model,
        serial_number=asset.serial_number,
        status=asset.status,
        status_label=AssetStatus.LABELS.get(asset.status, asset.status),
        user_name=asset.user_name,
        location=asset.location,
        purchase_date=asset.purchase_date,
        warranty_until=asset.warranty_until,
        warranty_days_left=days_left,
        notes=asset.notes,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        monitor_count=monitor_count,
        host_id=host_id,
        host_code=host_code,
        monitors=monitors or [],
        host=host,
        actions=AssetOperation.actions_for(asset.status),
    )


def to_group_out(bundle) -> AssetGroupOut:
    """套装聚合行 → 对外结构。

    bundle 是 services.bundles.AssetBundle —— 这里按**鸭子类型**用它，不 import 那个模块，
    也就不用关心 serialize ←→ bundles 的依赖方向（kind == "bundle" 是这个结构对外约定的字面量）。
    """
    primary = bundle.primary
    is_bundle = bundle.kind == "bundle"
    host_code = primary.asset_code if is_bundle else None

    def member_out(asset: Asset, *, as_host: bool) -> AssetOut:
        if as_host:
            return to_asset_out(asset, monitor_count=len(bundle.monitors))
        return to_asset_out(asset, host_id=primary.id, host_code=host_code)

    return AssetGroupOut(
        group_key=f"{bundle.kind}-{primary.id}",
        kind=bundle.kind,
        primary=member_out(primary, as_host=True),
        extra_hosts=[member_out(a, as_host=True) for a in bundle.extra_hosts],
        monitors=[member_out(a, as_host=False) for a in bundle.monitors],
        monitor_count=len(bundle.monitors),
        asset_ids=[a.id for a in bundle.members],
        asset_count=len(bundle.members),
        sort_id=bundle.sort_id,
        flags=AssetGroupFlags(**bundle.flags),
    )


def to_brief(asset: Asset) -> AssetBrief:
    dtype = asset.device_type
    return AssetBrief(
        id=asset.id,
        asset_code=asset.asset_code,
        device_type_id=asset.device_type_id,
        device_type_name=dtype.name if dtype else "",
        brand=asset.brand,
        model=asset.model,
        serial_number=asset.serial_number,
        status=asset.status,
        status_label=AssetStatus.LABELS.get(asset.status, asset.status),
        user_name=asset.user_name,
        location=asset.location,
    )


def to_relation_out(relation: AssetRelation) -> RelationOut:
    return RelationOut(
        id=relation.id,
        active=relation.active,
        host_id=relation.host_id,
        host=to_brief(relation.host),
        monitor_id=relation.monitor_id,
        monitor=to_brief(relation.monitor),
        note=relation.note,
        created_by=relation.created_by,
        created_at=relation.created_at,
        released_by=relation.released_by,
        released_at=relation.released_at,
        release_note=relation.release_note,
    )


def to_event_out(event: AssetEvent) -> AssetEventOut:
    """变更历史 → 对外结构。detail 是 JSON 文本，解析不成功就当没有，不让它把接口搞挂。"""
    def label(value: Optional[str]) -> Optional[str]:
        return AssetStatus.LABELS.get(value, value) if value else None

    return AssetEventOut(
        id=event.id,
        asset_id=event.asset_id,
        event_type=event.event_type,
        event_label=AssetEventType.label(event.event_type),
        from_status=event.from_status,
        from_status_label=label(event.from_status),
        to_status=event.to_status,
        to_status_label=label(event.to_status),
        operator=event.operator,
        note=event.note,
        detail=parse_json_dict(event.detail),
        created_at=event.created_at,
    )
