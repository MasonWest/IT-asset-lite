"""操作审计：往 audit_logs 追加一条「谁、从哪、什么时候、干了什么」。

## 为什么用 ContextVar 存 IP，而不是往每个路由塞 Request

IP 只有一个来源（HTTP 连接），但需要它的地方散落在十几个路由里。
如果每个 handler 都加一个 `request: Request` 参数，纯粹是为了调用时能取一个值，
函数签名会被这个与业务无关的参数污染。所以改成：
main.py 里的中间件在请求进入时把 IP 写进 ContextVar，
record_audit() 直接读 —— 业务代码一行都不用改。

ContextVar 是按「异步任务」隔离的，不是全局变量，并发请求之间不会串号。
这一点很关键：用普通模块级全局变量在 FastAPI 下一定会串。

## 两条写入路径，别用混

1. `record_audit(db, ...)` —— 同事务写入，只 add 不 commit。
   成功的业务操作走这条。审计和业务变更在同一个事务里，
   业务回滚了审计也跟着回滚 —— 不会出现"记了却没改"的假记录。

2. `write_audit_standalone(...)` —— 自己开会话、自己 commit、异常全吞。
   中间件记录**失败请求**走这条。因为这时候业务事务已经炸了，
   挂在上面写必然一起被回滚；而且审计本身失败绝不能把 500 变成更糟的东西。

## 失败请求为什么也要记

审计要回答的不只是"谁改了什么"，还有"谁试过但没干成"——
比如有人反复尝试删别人的设备、或者前端传了非法状态被 409 挡住。
这些在业务表里查不到任何痕迹，但恰恰是审计最有价值的信号。
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import Asset, AuditAction, AuditLog, AuditStatus, AuditTarget

logger = logging.getLogger("it_asset.audit")

#: 当前请求的客户端 IP。由 main.py 的中间件写入，record_audit() 读取。
_current_ip: ContextVar[Optional[str]] = ContextVar("audit_current_ip", default=None)
#: 当前请求的 HTTP 路径，失败审计时用来兜底描述「干了什么」
_current_path: ContextVar[Optional[str]] = ContextVar("audit_current_path", default=None)


def set_request_context(ip: Optional[str], path: Optional[str] = None) -> None:
    _current_ip.set(ip)
    _current_path.set(path)


def current_ip() -> Optional[str]:
    return _current_ip.get()


def current_path() -> Optional[str]:
    return _current_path.get()


def asset_label(asset: Asset) -> str:
    """审计页「操作对象」那一列要显示的东西：编号 + 品牌型号。

    只给编号，用户还得回台账里查这是哪台机器；带上品牌型号才能一眼认出来。
    """
    parts = [asset.asset_code]
    spec = " ".join(x for x in (asset.brand, asset.model) if x)
    if spec:
        parts.append(spec)
    return " · ".join(parts)


def asset_label_by(db: Session, asset_id: Optional[int]) -> Optional[str]:
    """删除类操作要在删之前把标签取出来 —— 删完就查不到了。"""
    if asset_id is None:
        return None
    asset = db.get(Asset, asset_id)
    return asset_label(asset) if asset is not None else f"#{asset_id}"


def record_audit(
    db: Session,
    action: str,
    *,
    target_type: str = AuditTarget.SYSTEM,
    target_id: Optional[int] = None,
    target_label: Optional[str] = None,
    summary: Optional[str] = None,
    operator: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
) -> AuditLog:
    """写一条审计。只 add，不 commit —— 调用方负责提交，和业务变更同事务。"""
    entry = AuditLog(
        operator=(operator or "").strip() or None,
        ip=(ip or current_ip() or None),
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=(target_label or "").strip()[:200] or None,
        summary=(summary or "").strip() or None,
        status=AuditStatus.SUCCESS,
        detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
    )
    db.add(entry)
    return entry


def write_audit_standalone(
    action: str,
    *,
    target_type: str = AuditTarget.SYSTEM,
    target_id: Optional[int] = None,
    target_label: Optional[str] = None,
    summary: Optional[str] = None,
    operator: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
    status: str = AuditStatus.SUCCESS,
) -> None:
    """独立事务写审计，只给「业务事务已经失败」的场景用。

    自己 catch 所有异常并只记日志：审计写不进去是要查的问题，
    但绝不能因为审计挂了再抛一次，把用户看到的错误信息盖掉。
    """
    from ..database import SessionLocal  # 延迟导入，避免 database ↔ services 循环依赖

    try:
        with SessionLocal() as db:
            entry = AuditLog(
                operator=(operator or "").strip() or None,
                ip=(ip or current_ip() or None),
                action=action,
                target_type=target_type,
                target_id=target_id,
                target_label=(target_label or "").strip()[:200] or None,
                summary=(summary or "").strip() or None,
                status=status,
                detail=json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
            )
            db.add(entry)
            db.commit()
    except Exception:  # noqa: BLE001 —— 审计不能把主流程带崩
        logger.exception("审计日志写入失败（已忽略，不影响主流程）")


# --------------------------------------------------------------------------- #
# 常用摘要的拼装：放一处，免得三个路由写出三种说法
# --------------------------------------------------------------------------- #
def describe_operation(action_key: str, from_status: Optional[str], to_status: Optional[str],
                       extra: str = "") -> str:
    from ..models import AssetOperation, AssetStatus

    label = AssetOperation.LABELS.get(action_key, action_key)
    from_label = AssetStatus.LABELS.get(from_status or "", from_status or "")
    to_label = AssetStatus.LABELS.get(to_status or "", to_status or "")
    text = f"{label}：状态 {from_label} → {to_label}" if from_label and to_label else label
    return f"{text}；{extra}" if extra else text


def describe_pairing(host_code: str, monitor_code: str, verb: str = "绑定") -> str:
    return f"{verb}：{host_code} ↔ {monitor_code}"
