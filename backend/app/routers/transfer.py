"""资产批量导入 / 导出。

## 为什么单独开一个 /api/transfer 前缀，而不是挂在 /api/assets 下面

`/api/assets/{asset_id}` 是个路径参数路由，`/api/assets/export` 会被它当成
asset_id="export" 抢先匹配（FastAPI 按声明顺序匹配，谁先声明谁赢）。
要靠调整声明顺序来躲这个坑，改动一次路由就得重新想一遍顺序，迟早踩。
换成独立前缀，从根上没这个问题。

## 导出和导入用同一套列

「导出 → 改 → 再导入」是这套功能的核心用法（现场盘点完批量修数据），
所以两边共用 importer.ASSET_COLUMNS，不允许各写一份。
导出时可以额外带一列「配对显示器」，导入时会自动忽略它 ——
这样用户不用为了导回而去手删一列。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Asset,
    AssetRelation,
    AssetStatus,
    AuditAction,
    AuditTarget,
    DeviceCategory,
)
from ..schemas import ImportPreview, ImportResultOut, ImportRowOut
from ..services import importer, spreadsheet
from ..services.audit import asset_label, record_audit
from ..services.filters import build_conditions

router = APIRouter(prefix="/api/transfer", tags=["transfer"])

#: 预览里最多回多少行。一千行的文件也回一千行的话，
#: 光是渲染这一坨就能把手机浏览器卡死；前端会提示"仅显示前 N 行"。
PREVIEW_ROW_LIMIT = 200

#: 导出时附带配对信息用的列宽
_EXPORT_WIDTHS = [c.width for c in importer.ASSET_COLUMNS] + [importer.PAIRING_COLUMN.width]


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _mode_label(mode: str) -> str:
    return importer.MODES.get(mode, mode)


def _row_out(row: importer.RowPlan) -> ImportRowOut:
    return ImportRowOut(
        row=row.row,
        asset_code=row.asset_code,
        action=row.action,
        action_label=row.action_label,
        errors=list(row.errors),
        warnings=list(row.warnings),
        changes=list(row.changes),
        existing_label=row.existing_label,
    )


def _plan_to_preview(plan: importer.ImportPlan) -> ImportPreview:
    rows = plan.rows[:PREVIEW_ROW_LIMIT]
    return ImportPreview(
        filename=plan.filename,
        mode=plan.mode,
        mode_label=_mode_label(plan.mode),
        total=plan.total,
        create=plan.create,
        update=plan.update,
        unchanged=plan.unchanged,
        error=plan.error,
        will_write=plan.will_write,
        can_commit=plan.can_commit,
        auto_types=plan.auto_types,
        unknown_headers=plan.unknown_headers,
        missing_headers=plan.missing_headers,
        missing_required=plan.missing_required,
        empty=plan.empty,
        rows=[_row_out(r) for r in rows],
        rows_truncated=len(plan.rows) > len(rows),
    )


def _pairing_columns(db: Session, assets: list[Asset]) -> dict[int, str]:
    """{资产 ID: 配对情况文本}。一次查完，不要每行查一次。"""
    if not assets:
        return {}

    rows = db.execute(
        select(AssetRelation.host_id, AssetRelation.monitor_id).where(AssetRelation.active.is_(True))
    ).all()
    if not rows:
        return {}

    monitors_of: dict[int, list[int]] = {}
    host_of: dict[int, int] = {}
    for host_id, monitor_id in rows:
        monitors_of.setdefault(int(host_id), []).append(int(monitor_id))
        host_of[int(monitor_id)] = int(host_id)

    # 把涉及的设备编号一次性查出来，避免为了显示编号再逐条查
    involved = {int(m) for group in monitors_of.values() for m in group} | set(host_of.values())
    codes = {
        aid: code
        for aid, code in db.execute(select(Asset.id, Asset.asset_code).where(Asset.id.in_(involved))).all()
    } if involved else {}

    out: dict[int, str] = {}
    for asset in assets:
        category = asset.device_type.category if asset.device_type else DeviceCategory.OTHER
        if category == DeviceCategory.HOST:
            mine = monitors_of.get(asset.id, [])
            out[asset.id] = "、".join(codes.get(m, f"#{m}") for m in mine) if mine else ""
        elif category == DeviceCategory.DISPLAY:
            host_id = host_of.get(asset.id)
            out[asset.id] = codes.get(host_id, "") if host_id else ""
    return out


def _export_row(asset: Asset, pairing_text: str = "", *, with_pairing: bool) -> list[str]:
    """一行导出数据。键顺序照 ASSET_COLUMNS 走，导出列 = 模板列。"""
    values = {
        "asset_code": asset.asset_code,
        "finance_code": asset.finance_code,
        "device_type_name": asset.device_type.name if asset.device_type else "",
        "brand": asset.brand,
        "model": asset.model,
        "serial_number": asset.serial_number,
        # 导出的是中文标签不是 in_use 这种键 —— 用户拿着表格要能直接看懂，
        # 而且这样导回去也能被 parse_status_cell 认出来
        "status": AssetStatus.LABELS.get(asset.status, asset.status),
        "user_name": asset.user_name,
        "location": asset.location,
        "purchase_date": asset.purchase_date.isoformat() if asset.purchase_date else "",
        "warranty_until": asset.warranty_until.isoformat() if asset.warranty_until else "",
        "notes": asset.notes,
    }
    row = [values.get(c.key) or "" for c in importer.ASSET_COLUMNS]
    if with_pairing:
        row.append(pairing_text)
    return row


# --------------------------------------------------------------------------- #
# 模板
# --------------------------------------------------------------------------- #
@router.get("/template", summary="下载导入模板（xlsx / csv）")
def download_template(
    fmt: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    samples: bool = Query(True, description="是否带两行示例数据"),
    db: Session = Depends(get_db),
):
    headers = [c.header for c in importer.ASSET_COLUMNS]
    rows = importer.template_rows(with_samples=samples)
    stamp = date.today().isoformat()

    if fmt == "csv":
        return Response(
            content=spreadsheet.build_csv(headers, rows[1:]),
            media_type=spreadsheet.CSV_MIME,
            headers={
                "Content-Disposition": spreadsheet.content_disposition(
                    f"资产导入模板_{stamp}.csv", "asset_import_template"
                )
            },
        )

    sheets = [
        spreadsheet.Sheet(
            title="资产导入",
            rows=rows,
            widths=[c.width for c in importer.ASSET_COLUMNS],
        ),
        spreadsheet.Sheet(
            title="填写说明",
            rows=[["项目", "说明"], *importer.template_notes()],
            widths=[18, 88],
            header=True,
            bold_first_col=True,
        ),
    ]
    if samples:
        # 模板里也列一遍列的含义，用户不用来回翻说明书
        sheets.append(
            spreadsheet.Sheet(
                title="列说明",
                rows=[["列名", "是否必填", "说明"], *[
                    [c.header, "必填" if c.required else "选填", c.hint or "—"] for c in importer.ASSET_COLUMNS
                ]],
                widths=[18, 10, 70],
                header=True,
            )
        )

    return Response(
        content=spreadsheet.build_xlsx(sheets),
        media_type=spreadsheet.XLSX_MIME,
        headers={
            "Content-Disposition": spreadsheet.content_disposition(
                f"资产导入模板_{stamp}.xlsx", "asset_import_template"
            )
        },
    )


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #
@router.post(
    "/import",
    response_model=ImportPreview | ImportResultOut,
    summary="批量导入资产（dry_run=true 只预览，false 落库）",
)
def import_assets(
    db: Session = Depends(get_db),
    file: UploadFile = File(..., description=".xlsx 或 .csv"),
    mode: str = Query(importer.MODE_UPSERT, pattern="^(create_only|upsert)$"),
    auto_create_type: bool = Query(True, description="设备类型不存在时是否自动创建"),
    dry_run: bool = Query(True, description="true 只预览不写库"),
    operator: Optional[str] = Query(None, description="操作人，写进审计日志"),
):
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件是空的，请检查后重新上传")

    filename = file.filename or "未命名文件"
    if not filename.lower().endswith((".xlsx", ".xlsm", ".csv")):
        raise HTTPException(
            status_code=400,
            detail=f"只支持 .xlsx 和 .csv 两种格式，你上传的是「{filename}」。"
            f"如果手里是 .xls（老版 Excel），请先「另存为」xlsx 再上传。",
        )

    try:
        plan = importer.build_plan(
            db, content, filename, mode=mode, auto_create_type=auto_create_type
        )
    except Exception as exc:  # noqa: BLE001 —— 解析失败要说人话，不能甩 500
        raise HTTPException(
            status_code=400,
            detail=f"文件解析失败：{exc}。请确认文件没有损坏，并且第一行是表头（可以先下载模板照着填）。",
        ) from exc

    if plan.missing_required:
        raise HTTPException(
            status_code=400,
            detail=(
                "文件里缺少必需列："
                + "、".join(plan.missing_required)
                + "。请下载模板，把数据填到模板里再上传（列名要和模板一致）。"
            ),
        )

    if dry_run:
        return _plan_to_preview(plan)

    if not plan.can_commit:
        raise HTTPException(
            status_code=400,
            detail=(
                f"没有可导入的数据：共 {plan.total} 行，其中错误 {plan.error} 行、无变化 {plan.unchanged} 行。"
                "请先修正文件里的错误再导入。"
            ),
        )

    outcome = importer.apply_plan(db, plan, operator=operator)

    # 一次导入只留一条审计 —— 30 台设备各记一条会把审计页刷屏，
    # 而用户想知道的是"谁在什么时候导了这个文件"，不是"改过 30 次"。
    summary = (
        f"导入《{plan.filename}》：新增 {outcome.created} 条，更新 {outcome.updated} 条，"
        f"跳过 {outcome.failed} 条"
    )
    if outcome.created_types:
        summary += f"；自动创建设备类型 {'、'.join(outcome.created_types)}"
    record_audit(
        db,
        AuditAction.ASSET_IMPORT,
        target_type=AuditTarget.FILE,
        target_label=plan.filename,
        summary=summary,
        operator=operator,
        detail={
            "mode": plan.mode,
            "mode_label": _mode_label(plan.mode),
            "created": outcome.created,
            "updated": outcome.updated,
            "unchanged": outcome.unchanged,
            "failed": outcome.failed,
            "created_types": outcome.created_types,
            "auto_create_type": auto_create_type,
        },
    )

    db.commit()

    return ImportResultOut(
        filename=plan.filename,
        mode=plan.mode,
        mode_label=_mode_label(plan.mode),
        created=outcome.created,
        updated=outcome.updated,
        unchanged=outcome.unchanged,
        failed=outcome.failed,
        created_types=outcome.created_types,
        failed_rows=[_row_out(r) for r in outcome.failed_rows],
        asset_codes=outcome.asset_codes[:PREVIEW_ROW_LIMIT],
    )


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
@router.get("/export", summary="导出资产（xlsx / csv，列与导入模板一致）")
def export_assets(
    db: Session = Depends(get_db),
    fmt: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    with_pairing: bool = Query(False, description="是否附带一列「配对显示器」"),
    keyword: Optional[str] = Query(None),
    device_type_id: Optional[str] = Query(None, description="设备类型 ID，多个用逗号分隔"),
    status_: Optional[str] = Query(None, alias="status", description="状态，多个用逗号分隔"),
    user_name: Optional[str] = Query(None, description="使用人，多个用逗号分隔"),
    location: Optional[str] = Query(None, description="存放位置，多个用逗号分隔"),
    paired: Optional[str] = Query(None),
    operator: Optional[str] = Query(None, description="操作人，写进审计日志"),
):
    """导出走的是列表页那套筛选（build_conditions），所以「导出当前筛选结果」是准的。"""
    conditions = build_conditions(
        keyword=keyword,
        device_type_id=device_type_id,
        status=status_,
        user_name=user_name,
        location=location,
        paired=paired,
    )
    stmt = select(Asset)
    if conditions:
        stmt = stmt.where(*conditions)
    assets = list(db.execute(stmt.order_by(Asset.asset_code.asc())).scalars().all())

    pairing_text = _pairing_columns(db, assets) if with_pairing else {}
    headers = [c.header for c in importer.ASSET_COLUMNS]
    if with_pairing:
        headers.append(importer.PAIRING_COLUMN.header)
    rows = [
        _export_row(a, pairing_text.get(a.id, ""), with_pairing=with_pairing) for a in assets
    ]

    stamp = date.today().isoformat()
    scope_desc = "全部设备" if not conditions else "筛选结果"

    # 导出记审计：数据离开系统这件事本身也需要留痕
    record_audit(
        db,
        AuditAction.ASSET_EXPORT,
        target_type=AuditTarget.FILE,
        target_label=f"资产台账导出（{scope_desc}）",
        summary=f"导出 {len(assets)} 台设备为 {fmt.upper()}（{scope_desc}）",
        operator=operator,
        detail={
            "format": fmt,
            "rows": len(assets),
            "with_pairing": with_pairing,
            "filters": {
                "keyword": keyword,
                "device_type_id": device_type_id,
                "status": status_,
                "user_name": user_name,
                "location": location,
                "paired": paired,
            },
        },
    )
    db.commit()

    if fmt == "csv":
        return Response(
            content=spreadsheet.build_csv(headers, rows),
            media_type=spreadsheet.CSV_MIME,
            headers={
                "Content-Disposition": spreadsheet.content_disposition(
                    f"IT资产台账_{stamp}.csv", "assets"
                )
            },
        )

    sheets = [
        spreadsheet.Sheet(title="资产台账", rows=[headers, *rows], widths=_EXPORT_WIDTHS),
        spreadsheet.Sheet(
            title="说明",
            rows=[
                ["项目", "说明"],
                ["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M")],
                ["导出范围", scope_desc],
                ["设备台数", len(assets)],
                ["是否含配对", "是" if with_pairing else "否"],
                ["", ""],
                ["这份文件可以直接导回", "列与本系统的导入模板完全一致，改完在「批量导入」页上传即可。"],
                ["配对显示器列", "只读参考信息，导入时会被自动忽略。"],
                ["空值语义", "导入更新时，留空表示保持原值不变；想清空某字段就写一个减号 - 。"],
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
                f"IT资产台账_{stamp}.xlsx", "assets"
            )
        },
    )
