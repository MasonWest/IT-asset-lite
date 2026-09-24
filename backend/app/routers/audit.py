"""操作审计日志查询与导出。

**这个模块只有读接口，一个写接口都没有。**

不是疏忽，是刻意的：审计日志一旦能被 API 改写或删除，"审计"这两个字就白写了。
表里也没有任何"清理旧日志"的逻辑 —— 要归档就整体备份数据库文件（backup.bat 干的就是这个）。
以后真要加保留策略，也应该是在备份之后整段归档，而不是让应用去删。

关于筛选的时间口径：按「日期」筛，结束日期当天算全天。
用户选了 9-01 到 9-24，他的意思是"到 24 号为止"，不是"到 24 号 00:00 为止"。
所以 end 用 `< end + 1 天` 而不是 `<= end`。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    AuditAction,
    AuditLog,
    AuditStatus,
    AuditTarget,
)
from ..schemas import AuditActionOption, AuditGroup, AuditLogOut, AuditMeta, AuditPage
from ..services import spreadsheet
from ..services.audit import record_audit
from ..services.serialize import parse_json_dict

router = APIRouter(prefix="/api/audit", tags=["audit"])

MAX_PAGE_SIZE = 200
EXPORT_ROW_LIMIT = 20000

EXPORT_COLUMNS = [
    ("created_at", "时间", 18),
    ("operator", "操作人", 12),
    ("ip", "IP 地址", 16),
    ("action_label", "操作类型", 14),
    ("target_label", "操作对象", 30),
    ("summary", "操作内容", 60),
    ("status_label", "结果", 8),
    ("detail_text", "补充信息", 40),
]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def serialize_log(entry: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=entry.id,
        created_at=entry.created_at,
        operator=entry.operator,
        ip=entry.ip,
        action=entry.action,
        action_label=AuditAction.label(entry.action),
        action_icon=AuditAction.icon(entry.action),
        target_type=entry.target_type,
        target_type_label=AuditTarget.LABELS.get(entry.target_type, entry.target_type),
        target_id=entry.target_id,
        target_label=entry.target_label,
        summary=entry.summary,
        status=entry.status,
        status_label=AuditStatus.LABELS.get(entry.status, entry.status),
        ok=entry.ok,
        detail=parse_json_dict(entry.detail),
    )


def _build_conditions(
    *,
    start: Optional[date],
    end: Optional[date],
    actions: Optional[str],
    operator: Optional[str],
    ip: Optional[str],
    log_status: Optional[str],
    keyword: Optional[str],
) -> list:
    conditions = []

    if start is not None:
        conditions.append(AuditLog.created_at >= datetime.combine(start, time.min))
    if end is not None:
        # 结束日当天算全天：用「小于次日零点」而不是「小于等于当日零点」。
        # 用户选 9-01 到 9-24 的意思是"到 24 号为止"，不是"到 24 号 00:00 为止"。
        conditions.append(AuditLog.created_at < datetime.combine(end + timedelta(days=1), time.min))

    selected = [a.strip() for a in (actions or "").split(",") if a.strip()]
    if selected:
        conditions.append(AuditLog.action.in_(selected))

    if operator:
        conditions.append(AuditLog.operator == operator)
    if ip:
        conditions.append(AuditLog.ip == ip)
    if log_status:
        conditions.append(AuditLog.status == log_status)

    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                AuditLog.target_label.ilike(kw),
                AuditLog.summary.ilike(kw),
                AuditLog.operator.ilike(kw),
                AuditLog.ip.ilike(kw),
            )
        )

    return conditions


def _export_rows(entries: list[AuditLog]) -> list[list[str]]:
    rows: list[list[str]] = []
    for entry in entries:
        detail = parse_json_dict(entry.detail) or {}
        flat = "；".join(f"{k}={v}" for k, v in detail.items() if not isinstance(v, (dict, list)))
        record = {
            "created_at": entry.created_at.strftime("%Y-%m-%d %H:%M:%S") if entry.created_at else "",
            "operator": entry.operator or "",
            "ip": entry.ip or "",
            "action_label": AuditAction.label(entry.action),
            "target_label": entry.target_label or "",
            "summary": entry.summary or "",
            "status_label": AuditStatus.LABELS.get(entry.status, entry.status),
            "detail_text": flat,
        }
        rows.append([str(record.get(key, "")) for key, _, _ in EXPORT_COLUMNS])
    return rows


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
@router.get("", response_model=AuditPage, summary="审计日志列表（按时间/类型/操作人/IP 筛选）")
def list_logs(
    db: Session = Depends(get_db),
    start: Optional[date] = Query(None, description="起始日期（含当天）"),
    end: Optional[date] = Query(None, description="结束日期（含当天）"),
    action: Optional[str] = Query(None, description="操作类型，多个用逗号分隔"),
    operator: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    log_status: Optional[str] = Query(None, alias="status", description="success / failed"),
    keyword: Optional[str] = Query(None, description="搜索操作对象 / 操作内容 / 操作人 / IP"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=MAX_PAGE_SIZE),
):
    conditions = _build_conditions(
        start=start, end=end, actions=action, operator=operator,
        ip=ip, log_status=log_status, keyword=keyword,
    )

    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    if conditions:
        stmt = stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = int(db.execute(count_stmt).scalar_one())
    rows = db.execute(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    return AuditPage(
        items=[serialize_log(e) for e in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/meta", response_model=AuditMeta, summary="审计页筛选项与统计")
def get_meta(db: Session = Depends(get_db)):
    operators = [
        o for (o,) in db.execute(
            select(AuditLog.operator)
            .where(AuditLog.operator.is_not(None), AuditLog.operator != "")
            .distinct()
            .order_by(AuditLog.operator.asc())
        ).all()
    ]
    ips = [
        v for (v,) in db.execute(
            select(AuditLog.ip)
            .where(AuditLog.ip.is_not(None), AuditLog.ip != "")
            .distinct()
            .order_by(AuditLog.ip.asc())
        ).all()
    ]

    today_start = datetime.combine(date.today(), time.min)
    total = int(db.execute(select(func.count(AuditLog.id))).scalar_one())
    today = int(
        db.execute(select(func.count(AuditLog.id)).where(AuditLog.created_at >= today_start)).scalar_one()
    )
    failed = int(
        db.execute(
            select(func.count(AuditLog.id)).where(AuditLog.status == AuditStatus.FAILED)
        ).scalar_one()
    )

    return AuditMeta(
        groups=[
            AuditGroup(
                name=name,
                options=[
                    AuditActionOption(value=key, label=AuditAction.label(key), icon=AuditAction.icon(key))
                    for key in keys
                ],
            )
            for name, keys in AuditAction.GROUPS.items()
        ],
        operators=operators,
        ips=ips,
        total=total,
        today=today,
        failed=failed,
    )


@router.get("/export", summary="导出审计日志（xlsx / csv）")
def export_logs(
    db: Session = Depends(get_db),
    fmt: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    action: Optional[str] = Query(None),
    operator: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    log_status: Optional[str] = Query(None, alias="status"),
    keyword: Optional[str] = Query(None),
    log_export: bool = Query(True, description="是否把「导出审计日志」这件事本身也记一笔"),
    actor: Optional[str] = Query(None, description="执行导出的人，写进审计日志"),
):
    """导出当前筛选结果。

    `operator` 是**筛选条件**（只看某个人干的），`actor` 是**执行导出的人**。
    两个名字必须分开 —— 合成一个参数的话，前端一边筛"张伟的操作"、
    一边又把导出记成"张伟导出的"，语义会串。
    """
    conditions = _build_conditions(
        start=start, end=end, actions=action, operator=operator,
        ip=ip, log_status=log_status, keyword=keyword,
    )
    stmt = select(AuditLog)
    if conditions:
        stmt = stmt.where(*conditions)
    entries = list(
        db.execute(
            stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(EXPORT_ROW_LIMIT)
        ).scalars().all()
    )

    headers = [label for _, label, _ in EXPORT_COLUMNS]
    widths = [width for _, _, width in EXPORT_COLUMNS]
    rows = _export_rows(entries)
    stamp = date.today().isoformat()
    scope = "筛选结果" if conditions else "全部记录"

    if log_export:
        # 先把要导出的行取完再记这一笔，否则这次导出会把自己也导进去
        record_audit(
            db,
            AuditAction.AUDIT_EXPORT,
            target_type=AuditTarget.FILE,
            target_label=f"审计日志导出（{scope}）",
            summary=f"导出 {len(entries)} 条审计记录为 {fmt.upper()}（{scope}）",
            operator=actor,
            detail={"format": fmt, "rows": len(entries), "truncated": len(entries) >= EXPORT_ROW_LIMIT},
        )
        db.commit()

    if fmt == "csv":
        return Response(
            content=spreadsheet.build_csv(headers, rows),
            media_type=spreadsheet.CSV_MIME,
            headers={
                "Content-Disposition": spreadsheet.content_disposition(
                    f"操作审计日志_{stamp}.csv", "audit_logs"
                )
            },
        )

    sheets = [
        spreadsheet.Sheet(title="审计日志", rows=[headers, *rows], widths=widths),
        spreadsheet.Sheet(
            title="说明",
            rows=[
                ["项目", "说明"],
                ["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M")],
                ["导出范围", scope],
                ["记录条数", len(entries)],
                ["", ""],
                ["审计日志只读", "系统不提供修改或删除审计记录的接口。"],
                ["操作人可能为空", "系统没有登录体系，操作人是用户自己填的；留空不代表无人操作。"],
                ["IP 的局限", "局域网内多台机器可能共用同一个出口 IP，IP 是辅助线索不是身份。"],
                ["失败记录", "被系统拒绝的操作（如非法状态流转）也会留痕，结果为「失败」。"],
            ],
            widths=[18, 78],
            header=True,
            bold_first_col=True,
        ),
    ]
    return Response(
        content=spreadsheet.build_xlsx(sheets),
        media_type=spreadsheet.XLSX_MIME,
        headers={
            "Content-Disposition": spreadsheet.content_disposition(
                f"操作审计日志_{stamp}.xlsx", "audit_logs"
            )
        },
    )
