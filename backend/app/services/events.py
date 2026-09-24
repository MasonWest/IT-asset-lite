"""资产变更历史：统一往 asset_events 落一条记录。

时间线是这个阶段的验收重点之一，所以「什么时候该记一笔」必须集中管，
不能让每个路由各写各的 —— 漏记一处，那台设备的履历就断了。

规矩：
  1. 任何会改变资产「状态 / 归属 / 配对 / 关键字段」的动作，都要记；
  2. 编辑资产时只有真的改动了才记，点一下保存没改东西也记一条纯属噪音；
  3. 记录只写不删 —— 资产删掉时靠外键 CASCADE 一起清掉；
  4. 调用方负责 commit，这里只 add，保证「改资产 + 记历史」在同一个事务里。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import Asset, AssetEvent, AssetEventType

#: 字段 → 中文名。用于把「改了什么」翻译成人话写进时间线
FIELD_LABELS: dict[str, str] = {
    "asset_code": "IT 资产编号",
    "finance_code": "财务编码",
    "device_type_id": "设备类型",
    "brand": "品牌",
    "model": "型号",
    "serial_number": "序列号 SN",
    "status": "状态",
    "user_name": "使用人",
    "location": "存放位置",
    "purchase_date": "购入日期",
    "warranty_until": "保修到期日",
    "notes": "备注",
}

#: 这些字段不参与「信息变更」的差异比较：状态流转有专门的操作动作，备注太长会刷屏
_IGNORED_IN_DIFF = {"updated_at"}


def _stringify(value: Any) -> str:
    if value is None or value == "":
        return "空"
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def record_event(
    db: Session,
    asset: Asset,
    event_type: str,
    *,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    operator: Optional[str] = None,
    note: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> AssetEvent:
    """写一条变更历史。只 add，不 commit。"""
    event = AssetEvent(
        asset_id=asset.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        operator=(operator or "").strip() or None,
        note=(note or "").strip() or None,
        detail=json.dumps(detail, ensure_ascii=False) if detail else None,
    )
    db.add(event)
    return event


def diff_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, str]]:
    """比较两个快照，返回 {字段: {"from": ..., "to": ...}}。

    值统一转成字符串再比 —— 前端传来的是 "2026-01-01" 字符串、
    ORM 里是 date 对象，不转的话同一个值会被判成改动，时间线里冒出一堆假变更。
    """
    changes: dict[str, dict[str, str]] = {}
    for key, new_value in after.items():
        if key in _IGNORED_IN_DIFF:
            continue
        old_value = before.get(key)
        if _stringify(old_value) != _stringify(new_value):
            changes[key] = {"from": _stringify(old_value), "to": _stringify(new_value)}
    return changes


def describe_changes(changes: dict[str, dict[str, str]], limit: int = 4) -> Optional[str]:
    """把差异字典压成一行摘要，例如「品牌 空 → 联想；使用人 张伟 → 李娜」。"""
    if not changes:
        return None
    parts: list[str] = []
    for key, value in list(changes.items())[:limit]:
        label = FIELD_LABELS.get(key, key)
        parts.append(f"{label} {value['from']} → {value['to']}")
    if len(changes) > limit:
        parts.append(f"等 {len(changes)} 项")
    return "；".join(parts)
