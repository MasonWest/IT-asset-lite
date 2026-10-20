"""轻资产接口。

## 路由前缀为什么不挂在 /api/assets 下

同 `/api/transfer` 的理由：轻资产**不是**正式资产，走到 `/api/assets/...` 会让
"资产筛选、统计口径、二维码/标签打印"这些按 assets 表来的功能开始困惑。
独立一个 `/api/light` 前缀，从根上分清。

## 唯一写入口是 `POST /api/light/movements`

品类 CRUD 只是"建档"，**不改变库存与持有关系**；库存在哪、在谁手上，全部由
`light_holdings`（当前分配记录）承载，并由 `services/light.py::apply_holding_delta()`
唯一地改动。所以这里**没有"直接改库存"的接口** —— 想改就走一次业务动作，留下痕迹。

## ⚠️ 路由顺序：字面量必须声明在 /{id} 之前

`/summary`、`/by-asset/...`、`/opening/preview` 等字面量路径**必须**排在
`/categories/{id}` 这类带 id 的路径之前（FastAPI 按声明顺序匹配；
项目里 `/assets/grouped`、`/floor-maps/default` 都为此写过注释）。
新增写操作还要登记进 `main.py::_FAILED_ROUTES`，否则失败请求会被兜底记成"编辑资产/系统"。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db, light_opening_key, meta_set_on
from ..models import (
    Asset,
    AssetStatus,
    AuditAction,
    AuditTarget,
    LightCategory,
    LightHolding,
    LightHolderKind,
    LightMovement,
    LightOp,
    LightOrigin,
    Workstation,
)
from ..schemas_light import (
    AssetHoldingsOut,
    HolderRefIn,
    ImportPreview,
    ImportResult,
    LightCategoryCreate,
    LightCategoryOut,
    LightCategoryPage,
    LightCategoryUpdate,
    LightFilterOptions,
    LightHolderCandidate,
    LightMovementCreate,
    LightMovementOut,
    LightMovementPage,
    LightMovementResult,
    LightReplaceRequest,
    OpeningRequest,
    StationHoldingsOut,
)
from ..services import light as light_service
from ..services import light_import, light_serialize, spreadsheet
from ..services.audit import record_audit
from ..services.light import HolderRef

router = APIRouter(prefix="/api/light", tags=["light"])

_MAX_PAGE_SIZE = 200


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _get_category_or_404(db: Session, category_id: int) -> LightCategory:
    category = db.get(LightCategory, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail=f"轻资产品类 #{category_id} 不存在")
    return category


def _to_ref(payload: HolderRefIn) -> HolderRef:
    # "none" 是前端表达"外部 / 注销"的哨兵值，翻成真正的 None
    return HolderRef(
        kind=None if payload.kind == "none" else payload.kind,
        ref_id=payload.ref_id,
        user=payload.user,
        stock_location=payload.stock_location,
    )


def _category_label(category: LightCategory) -> str:
    parts = [category.display_name]
    if category.brand or category.spec:
        parts.append(" ".join(x for x in (category.brand, category.spec) if x))
    return " · ".join(parts)


def _paginate(page: int, page_size: int) -> tuple[int, int]:
    page = max(1, page)
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    return page, page_size


def _holder_text(movement: LightMovement, side: str) -> str:
    """给审计摘要用的一句短文案（不查库，只用流水里的快照）。"""
    kind = getattr(movement, f"{side}_kind")
    if kind is None:
        return "外部" if side == "from" else "注销"
    if kind == LightHolderKind.STOCK:
        loc = (getattr(movement, f"{side}_ref_code") or "").strip()
        return f"库存（{loc}）" if loc else "库存"
    if kind == LightHolderKind.USER:
        return getattr(movement, f"{side}_user") or "未填使用人"
    return getattr(movement, f"{side}_ref_code") or f"{kind}#{getattr(movement, f'{side}_ref_id')}"


def _movement_summary(movement: LightMovement) -> str:
    return (
        f"{LightOp.label(movement.op)} {movement.quantity}："
        f"{_holder_text(movement, 'from')} → {_holder_text(movement, 'to')}"
    )


# --------------------------------------------------------------------------- #
# 品类
# --------------------------------------------------------------------------- #
@router.get("/categories", response_model=LightCategoryPage, summary="轻资产品类列表（带当前分配统计）")
def list_categories(
    db: Session = Depends(get_db),
    keyword: Optional[str] = Query(None, description="模糊搜索：品类名 / 品牌 / 规格"),
    active: Optional[str] = Query(None, description="true 只看启用的 / false 只看已停用的 / all 全部"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=_MAX_PAGE_SIZE),
):
    stmt = select(LightCategory)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                LightCategory.name.like(like),
                LightCategory.brand.like(like),
                LightCategory.spec.like(like),
            )
        )
    if active and active != "all":
        stmt = stmt.where(LightCategory.active.is_(active.lower() in ("true", "1", "yes")))

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    page, page_size = _paginate(page, page_size)
    rows = (
        db.execute(stmt.order_by(LightCategory.active.desc(), LightCategory.id.asc())
                   .offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return LightCategoryPage(
        items=[light_serialize.to_category_out(db, c) for c in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post(
    "/categories",
    response_model=LightCategoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增轻资产品类",
)
def create_category(payload: LightCategoryCreate, db: Session = Depends(get_db)):
    name = payload.name.strip()
    exists = db.execute(select(LightCategory.id).where(LightCategory.name == name)).first()
    if exists is not None:
        raise HTTPException(status_code=409, detail=f"已经有一个叫「{name}」的轻资产品类了")

    data = payload.model_dump()
    operator = data.pop("operator", None)
    # 空白图标标识归一成 None（= "还没确认过"），避免库里出现空串
    data["icon"] = (data.get("icon") or "").strip() or None
    category = LightCategory(**data)
    db.add(category)
    db.flush()

    record_audit(
        db,
        AuditAction.LIGHT_CATEGORY_CREATE,
        target_type=AuditTarget.LIGHT_CATEGORY,
        target_id=category.id,
        target_label=_category_label(category),
        summary=f"新增品类（单位 {category.unit}）",
        operator=operator,
        detail={
            "name": category.name,
            "brand": category.brand,
            "spec": category.spec,
            "unit": category.unit,
            "icon": category.icon,
        },
    )
    db.commit()
    db.refresh(category)
    return light_serialize.to_category_out(db, category)


@router.get("/categories/{category_id}", response_model=LightCategoryOut, summary="品类详情")
def get_category(category_id: int, db: Session = Depends(get_db)):
    return light_serialize.to_category_out(db, _get_category_or_404(db, category_id))


@router.put("/categories/{category_id}", response_model=LightCategoryOut, summary="编辑品类")
def update_category(category_id: int, payload: LightCategoryUpdate, db: Session = Depends(get_db)):
    category = _get_category_or_404(db, category_id)
    data = payload.model_dump(exclude_unset=True)
    operator = data.pop("operator", None)

    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            data.pop("name")
        else:
            clash = db.execute(
                select(LightCategory.id)
                .where(LightCategory.name == name, LightCategory.id != category_id)
            ).first()
            if clash is not None:
                raise HTTPException(status_code=409, detail=f"已经有一个叫「{name}」的轻资产品类了")
            data["name"] = name

    # 图标：显式传空串 = 清掉（回到"按名称给建议图标"）；不传 = 不动它。
    # 统一归一成 None，避免库里出现空串与 NULL 两种"空"。
    if "icon" in data:
        data["icon"] = (data["icon"] or "").strip() or None

    changes = {}
    for key, value in data.items():
        old = getattr(category, key, None)
        if old != value:
            changes[key] = {"from": old, "to": value}
        setattr(category, key, value)

    if changes:
        record_audit(
            db,
            AuditAction.LIGHT_CATEGORY_UPDATE,
            target_type=AuditTarget.LIGHT_CATEGORY,
            target_id=category.id,
            target_label=_category_label(category),
            summary="；".join(f"{k} {v['from']} → {v['to']}" for k, v in changes.items()),
            operator=operator,
            detail={"changes": changes},
        )

    db.commit()
    db.refresh(category)
    return light_serialize.to_category_out(db, category)


@router.delete("/categories/{category_id}", response_model=LightCategoryOut, summary="停用品类（软删）")
def deactivate_category(
    category_id: int,
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None),
    force: bool = Query(False, description="还有配件在外时是否强行停用（不删任何数据）"),
):
    """停用 ≠ 删除：品类与它的全部流水都留着，只是不再出现在新建的下拉里。

    与点位软删同一设计 —— 硬删会让"这个品类曾经存在过"这段历史直接消失。
    """
    category = _get_category_or_404(db, category_id)
    if not category.active:
        raise HTTPException(status_code=409, detail=f"品类「{category.display_name}」已经是停用状态")

    summary = light_service.category_summary(db, category)
    outstanding = summary["allocated"]
    if outstanding and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                f"品类「{category.display_name}」还有 {outstanding} {category.unit} 没有回到库存"
                f"（在外面或挂在设备/点位上）。请先归还或报损，"
                f"确实要停用请勾选「强行停用」——不会删任何历史。"
            ),
        )

    category.active = False
    record_audit(
        db,
        AuditAction.LIGHT_CATEGORY_DELETE,
        target_type=AuditTarget.LIGHT_CATEGORY,
        target_id=category.id,
        target_label=_category_label(category),
        summary=f"停用品类{f'（仍有 {outstanding} {category.unit} 在外，已强行停用）' if outstanding else ''}",
        operator=operator,
        detail={"outstanding": outstanding, "forced": bool(force)},
    )
    db.commit()
    db.refresh(category)
    return light_serialize.to_category_out(db, category)


# --------------------------------------------------------------------------- #
# 当前分配记录
# --------------------------------------------------------------------------- #
@router.get("/holdings", summary="当前分配记录（列表页 / 详情页用）")
def list_holdings(
    db: Session = Depends(get_db),
    category_id: Optional[int] = Query(None),
    holder_kind: Optional[str] = Query(None, description="stock/user/asset/workstation"),
    keyword: Optional[str] = Query(None, description="模糊搜索：使用人 / 资产号 / 点位号 / 存放位置"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
):
    stmt = select(LightHolding)
    if category_id is not None:
        stmt = stmt.where(LightHolding.category_id == category_id)
    if holder_kind:
        stmt = stmt.where(LightHolding.holder_kind == holder_kind)

    rows = list(db.execute(stmt.order_by(LightHolding.category_id.asc(), LightHolding.id.asc())).scalars().all())
    rows = [r for r in rows if r.quantity > 0]

    # 关键字要能搜到"人 / 资产号 / 点位号 / 存放位置"，而那些在别的表里 —— 在内存里过滤，
    # 一个办公室的配件规模完全不值得为它写 join。
    kw = (keyword or "").strip().lower()
    items = [light_serialize.to_holding_out(db, r) for r in rows]
    if kw:
        def match(h) -> bool:
            hay = " ".join(
                filter(None, [h.display, h.ref_code, h.ref_label, h.user, h.stock_location, h.host_user])
            ).lower()
            return kw in hay

        items = [h for h in items if match(h)]

    total = len(items)
    page, page_size = _paginate(page, page_size)
    start = (page - 1) * page_size
    return {
        "items": [h.model_dump() for h in items[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/by-asset/{asset_id}", response_model=AssetHoldingsOut, summary="某资产的关联配件")
def holdings_by_asset(asset_id: int, db: Session = Depends(get_db)):
    """装在这台正式资产上的配件（走索引，不重放流水）。

    空态给出口由前端负责："这台机器上还没登记任何配件 —— 有的话现在登记"，而不是只写"暂无"。
    """
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"资产 #{asset_id} 不存在")
    rows = light_service.holdings_by_asset(db, asset_id)
    items = [light_serialize.to_holding_out(db, r) for r in rows]
    return AssetHoldingsOut(
        asset_id=asset_id,
        asset_code=asset.asset_code,
        items=items,
        total=sum(h.quantity for h in items),
    )


@router.get("/by-workstation/{station_id}", response_model=StationHoldingsOut, summary="某点位的关联配件（四组）")
def holdings_by_workstation(station_id: int, db: Session = Depends(get_db)):
    """点位详情侧栏用。

    ⚠️ **四组仅是展示分组，不创建任何额外持有记录**（设计 §5.4 四条硬约束）：
    `direct` 是"东西就放在这个工位上"（`holder_kind='workstation'`）；
    `via_assets` 是"东西装在那台主机上，主机正好在这个工位"
    （`holder_kind='asset'` 且该资产**当前** `workstation_id=本点位` —— 查询时算出来的，
    主机换点位这一组自动跟着变，配件上**不另存点位**）；
    `stock` 是"库存位置写的就是这个点位编码"（自由文本，但写明了就是写明了）；
    `via_users` 是"记在某人手上，而这个人名下有本点位" —— **推断**，
    一个人有多个点位时同一条记录会出现在他每一处点位上。
    """
    station = db.get(Workstation, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail=f"点位 #{station_id} 不存在")
    grouped = light_service.holdings_by_workstation(db, station_id)
    return StationHoldingsOut(
        workstation_id=station_id,
        code=station.code,
        direct=[light_serialize.to_holding_out(db, r) for r in grouped["direct"]],
        via_assets=[light_serialize.to_holding_out(db, r) for r in grouped["via_assets"]],
        stock=[light_serialize.to_holding_out(db, r) for r in grouped["stock"]],
        via_users=[light_serialize.to_holding_out(db, r) for r in grouped["via_users"]],
    )


# --------------------------------------------------------------------------- #
# 流水
# --------------------------------------------------------------------------- #
@router.get("/movements", response_model=LightMovementPage, summary="轻资产流水列表（可筛选）")
def list_movements(
    db: Session = Depends(get_db),
    category_id: Optional[int] = Query(None),
    op: Optional[str] = Query(None, description="操作类型，多个用逗号分隔"),
    origin: Optional[str] = Query(None, description="manual / opening / import，多个用逗号分隔"),
    keyword: Optional[str] = Query(None, description="模糊搜索：备注 / 操作人 / 使用人 / 编码"),
    start: Optional[date] = Query(None, description="发生时间起（含当天）"),
    end: Optional[date] = Query(None, description="发生时间止（含当天）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
):
    stmt = select(LightMovement)
    if category_id is not None:
        stmt = stmt.where(LightMovement.category_id == category_id)
    if op:
        stmt = stmt.where(LightMovement.op.in_([x.strip() for x in op.split(",") if x.strip()]))
    if origin:
        stmt = stmt.where(LightMovement.origin.in_([x.strip() for x in origin.split(",") if x.strip()]))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                LightMovement.note.like(like),
                LightMovement.operator.like(like),
                LightMovement.from_user.like(like),
                LightMovement.to_user.like(like),
                LightMovement.to_ref_code.like(like),
                LightMovement.from_ref_code.like(like),
            )
        )
    if start:
        stmt = stmt.where(LightMovement.occurred_at >= datetime.combine(start, datetime.min.time()))
    if end:
        stmt = stmt.where(
            LightMovement.occurred_at < datetime.combine(end + timedelta(days=1), datetime.min.time())
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    page, page_size = _paginate(page, page_size)
    rows = (
        db.execute(
            stmt.order_by(LightMovement.occurred_at.desc(), LightMovement.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return LightMovementPage(
        items=[light_serialize.to_movement_out(db, m) for m in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post("/movements", response_model=LightMovementResult, summary="登记一次流转（唯一写入口）")
def create_movement(payload: LightMovementCreate, db: Session = Depends(get_db)):
    category = _get_category_or_404(db, payload.category_id)
    if not category.active:
        raise HTTPException(status_code=409, detail=f"品类「{category.display_name}」已停用，不能再登记流转")

    movement = light_service.record_operation(
        db,
        category=category,
        op=payload.op,
        from_ref=_to_ref(payload.from_ref),
        to_ref=_to_ref(payload.to_ref),
        quantity=payload.quantity,
        operator=payload.operator,
        note=payload.note,
        occurred_at=payload.occurred_at,
        origin=payload.origin,
        request_id=payload.request_id,
        corrects_id=payload.corrects_id,
    )

    origin_note = "期初确认：" if payload.origin == LightOrigin.OPENING else ""
    record_audit(
        db,
        AuditAction.LIGHT_OPERATE,
        target_type=AuditTarget.LIGHT_CATEGORY,
        target_id=category.id,
        target_label=_category_label(category),
        summary=f"{origin_note}{_movement_summary(movement)}"
                f"{f'（{payload.note}）' if payload.note else ''}",
        operator=payload.operator,
        detail={
            "op": payload.op,
            "quantity": movement.quantity,
            "origin": movement.origin,
            "from": _holder_text(movement, "from"),
            "to": _holder_text(movement, "to"),
            "note": payload.note,
        },
    )
    if payload.origin == LightOrigin.OPENING:
        # ⚠️ 必须用**同一个会话**写记账值：另开连接会在写事务未提交时撞 `database is locked`
        meta_set_on(db, light_opening_key(category.id), "1")
    db.commit()
    db.refresh(movement)
    return LightMovementResult(
        movements=[light_serialize.to_movement_out(db, movement)],
        stat=light_serialize.to_category_out(db, category).stat,
    )


@router.post(
    "/replace",
    response_model=LightMovementResult,
    summary="更换配件（一条业务动作 = 报损 + 安装两条流水，同事务）",
)
def replace_part(payload: LightReplaceRequest, db: Session = Depends(get_db)):
    """B022 上那根 8G 内存坏了，换一根新的 —— 人（和机器）都没变。

    为什么不分两次让用户点：中间那一下如果只做了一半（旧件已报损、新件还没装），
    账上会短暂出现"这台机器少一根内存"，而实际并没有。一次事务提交，要么都成、要么都不成。
    """
    category = _get_category_or_404(db, payload.category_id)
    if not category.active:
        raise HTTPException(status_code=409, detail=f"品类「{category.display_name}」已停用，不能再登记流转")

    asset = db.get(Asset, payload.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"资产 #{payload.asset_id} 不存在")

    movements = light_service.replace_asset_part(
        db,
        category=category,
        asset_ref=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id),
        quantity=payload.quantity,
        operator=payload.operator,
        note=payload.note,
        occurred_at=payload.occurred_at,
        request_id=payload.request_id,
    )

    record_audit(
        db,
        AuditAction.LIGHT_OPERATE,
        target_type=AuditTarget.LIGHT_CATEGORY,
        target_id=category.id,
        target_label=_category_label(category),
        summary=(
            f"更换配件 {payload.quantity} {category.unit}（{asset.asset_code}）："
            f"旧件报损 + 新件装上，一次操作两条流水"
            f"{f'（{payload.note}）' if payload.note else ''}"
        ),
        operator=payload.operator,
        detail={
            "op": LightOp.REPLACE,
            "quantity": payload.quantity,
            "asset_code": asset.asset_code,
            "movement_ids": [m.id for m in movements],
            "note": payload.note,
        },
    )
    db.commit()
    for m in movements:
        db.refresh(m)
    return LightMovementResult(
        movements=[light_serialize.to_movement_out(db, m) for m in movements],
        stat=light_serialize.to_category_out(db, category).stat,
    )


@router.post(
    "/movements/{movement_id}/reverse",
    response_model=LightMovementResult,
    summary="冲正一条流水（追加反向记录，不篡改原记录）",
)
def reverse_movement(
    movement_id: int,
    db: Session = Depends(get_db),
    note: Optional[str] = Query(None, description="冲正原因（建议写，审计里要看得懂）"),
    operator: Optional[str] = Query(None),
    request_id: Optional[str] = Query(None),
):
    """登记错了一条流水，就**追加一条反向的**，而不是改它。

    已确认的历史是证据 —— 改它等于让时间线说谎。原记录与冲正记录通过
    `corrects_id` 互相可追溯，两条都留在台账里。
    """
    original = db.get(LightMovement, movement_id)
    if original is None:
        raise HTTPException(status_code=404, detail=f"流水 #{movement_id} 不存在")
    if original.origin == LightOrigin.IMPORT:
        raise HTTPException(
            status_code=409,
            detail="历史导入的流水只用于追溯、不计入当前状态，不需要（也不能）冲正",
        )
    # 冲正记录本身不再被冲正：否则 A 冲 B、B 再冲 A 就能绕成一个环，
    # 时间线上全是互相抵消的噪声，没人看得懂到底发生了什么。
    if original.corrects_id is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"流水 #{movement_id} 本身是一条冲正记录（在更正 #{original.corrects_id}），"
                f"不能再被冲正。要改它请直接登记正确的操作。"
            ),
        )
    already = db.execute(
        select(LightMovement.id).where(LightMovement.corrects_id == movement_id)
    ).first()
    if already is not None:
        raise HTTPException(status_code=409, detail=f"流水 #{movement_id} 已经被冲正过了")

    swapped_from = HolderRef(
        kind=original.to_kind,
        ref_id=original.to_ref_id,
        user=original.to_user,
        stock_location=original.to_ref_code if original.to_kind == LightHolderKind.STOCK else None,
    )
    swapped_to = HolderRef(
        kind=original.from_kind,
        ref_id=original.from_ref_id,
        user=original.from_user,
        stock_location=original.from_ref_code if original.from_kind == LightHolderKind.STOCK else None,
    )

    # 反向操作往往**不是同一个 op**（分配的冲正是归还、安装的冲正是拆卸），
    # 所以走 LightOp.REVERSE_CANDIDATES 的显式映射，而不是拿原 op 去套反向的 from/to。
    reverse_op = LightOp.reverse_op(original.op, original.from_kind, original.to_kind)
    if reverse_op is None or not LightOp.allows(reverse_op, swapped_from.kind, swapped_to.kind):
        raise HTTPException(
            status_code=400,
            detail=(
                f"「{LightOp.label(original.op)}」不能机械冲正 —— 它的反向没有定义"
                "（例如报损想撤销，必须由人决定是重新入库还是别的方式，系统不替你做这个判断）。"
                "请用对应的操作来更正。"
            ),
        )

    category = _get_category_or_404(db, original.category_id)
    reason = (note or "").strip() or f"冲正流水 #{movement_id}"
    movement = light_service.record_operation(
        db,
        category=category,
        op=reverse_op,
        from_ref=swapped_from,
        to_ref=swapped_to,
        quantity=original.quantity,
        operator=operator,
        note=reason,
        origin=original.origin,
        request_id=request_id,
        corrects_id=movement_id,
    )

    record_audit(
        db,
        AuditAction.LIGHT_OPERATE,
        target_type=AuditTarget.LIGHT_CATEGORY,
        target_id=category.id,
        target_label=_category_label(category),
        summary=f"冲正流水 #{movement_id}（{LightOp.label(original.op)} → {LightOp.label(reverse_op)}）：{reason}",
        operator=operator,
        detail={
            "corrects_id": movement_id,
            "op": reverse_op,
            "origin_op": original.op,
            "quantity": original.quantity,
            "note": reason,
        },
    )
    db.commit()
    db.refresh(movement)
    return LightMovementResult(
        movements=[light_serialize.to_movement_out(db, movement)],
        stat=light_serialize.to_category_out(db, category).stat,
    )


# --------------------------------------------------------------------------- #
# 期初确认（允许补录 / 重做，不永久封锁）
# --------------------------------------------------------------------------- #
def _opening_rows(db: Session, category: LightCategory, payload: OpeningRequest) -> list[dict]:
    """把期初确认的每一条解析成"改前 → 改后"（**不改任何数据**，预览与执行共用）。"""
    rows: list[dict] = []
    for entry in payload.entries:
        ref = _to_ref(entry.holder)
        if ref.kind is None:
            raise HTTPException(status_code=400, detail="期初确认的去向不能是「系统外部」")
        snapshot = light_service.build_holder_snapshot(db, ref)
        before = light_service.quantity_at(db, category.id, ref)
        holder = light_serialize.to_holder_out(
            db,
            kind=ref.kind,
            ref_id=ref.ref_id,
            ref_code=snapshot.ref_code,
            user=ref.user,
            stock_location=ref.stock_location,
        )
        rows.append(
            {
                "holder": holder.model_dump(),
                "before": before,
                "delta": entry.quantity,
                "after": before + entry.quantity,
            }
        )
    return rows


@router.post("/opening/preview", summary="期初确认预览（当前分配 → 登记后，不写任何数据）")
def opening_preview(payload: OpeningRequest, db: Session = Depends(get_db)):
    """预览的职责不是"列出改动"，而是**让用户在点执行前预测结果**（设计 §4.3）。

    所以这里把**每一个持有位置**的「改前 → 改后」都摆出来，不变的也照列。
    """
    category = _get_category_or_404(db, payload.category_id)
    rows = _opening_rows(db, category, payload)
    summary = light_service.category_summary(db, category)
    stock_after = summary["stock"] + sum(
        r["delta"] for r in rows if r["holder"]["kind"] == LightHolderKind.STOCK
    )
    allocated_after = summary["allocated"] + sum(
        r["delta"] for r in rows if r["holder"]["kind"] in LightHolderKind.HELD
    )
    return {
        "category_id": category.id,
        "unit": category.unit,
        "rows": rows,
        "stock": {"before": summary["stock"], "after": stock_after},
        "allocated": {"before": summary["allocated"], "after": allocated_after},
        "net": {"before": summary["net"], "after": stock_after + allocated_after},
        "opening_done": light_serialize.to_category_out(db, category).opening_done,
    }


@router.post("/opening", response_model=LightMovementResult, summary="登记期初确认（建立上线基线）")
def register_opening(payload: OpeningRequest, db: Session = Depends(get_db)):
    """期初确认：按实际盘点结果一次性登记多条基线。

    ⚠️ **不永久封锁**（设计 §6.1）：漏录了品类、或盘点发现数量不对时，可以再登记一次；
    `schema_meta` 里的标记只是"提示用"，不是"禁止再录入"的开关。
    日常数量修正走**有审计的盘点调整**（`adjust`，盘盈 / 盘亏），随时可用。
    """
    category = _get_category_or_404(db, payload.category_id)
    if not category.active:
        raise HTTPException(status_code=409, detail=f"品类「{category.display_name}」已停用")

    movements = light_service.apply_opening(
        db,
        category=category,
        entries=[(_to_ref(e.holder), e.quantity) for e in payload.entries],
        operator=payload.operator,
        note=payload.note,
        occurred_at=payload.occurred_at,
    )
    meta_set_on(db, light_opening_key(category.id), "1")

    record_audit(
        db,
        AuditAction.LIGHT_OPERATE,
        target_type=AuditTarget.LIGHT_CATEGORY,
        target_id=category.id,
        target_label=_category_label(category),
        summary="登记期初确认（按实际盘点结果建立基线）："
                + "；".join(f"{_holder_text(m, 'to')} +{m.quantity}" for m in movements),
        operator=payload.operator,
        detail={
            "origin": LightOrigin.OPENING,
            "entries": [
                {"holder": _holder_text(m, "to"), "quantity": m.quantity} for m in movements
            ],
            "note": payload.note,
        },
    )
    db.commit()
    for m in movements:
        db.refresh(m)
    return LightMovementResult(
        movements=[light_serialize.to_movement_out(db, m) for m in movements],
        stat=light_serialize.to_category_out(db, category).stat,
    )


# --------------------------------------------------------------------------- #
# 历史导入（只写流水，绝不碰当前持有）
# --------------------------------------------------------------------------- #
def _plan_preview(plan: light_import.ImportPlan, limit: int = 200) -> ImportPreview:
    rows = plan.rows[:limit]
    return ImportPreview(
        filename=plan.filename,
        total=plan.total,
        will_import=plan.count(light_import.ACTION_IMPORT),
        duplicate=plan.count(light_import.ACTION_DUPLICATE),
        error=plan.count(light_import.ACTION_ERROR),
        rows=[
            {
                "row": r.row,
                "action": r.action,
                "action_label": r.action_label,
                "message": r.message,
                "summary": r.summary,
            }
            for r in rows
        ],
        rows_truncated=len(plan.rows) > len(rows),
        schema_note=plan.schema_note,
    )


@router.post("/import/preview", response_model=ImportPreview, summary="历史导入预览（不写任何数据）")
async def import_preview(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    plan = light_import.build_plan(db, content, file.filename or "light-history.csv")
    return _plan_preview(plan)


@router.post("/import", response_model=ImportResult, summary="历史导入（只补历史事件，不动当前账）")
async def import_history(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None),
):
    """落库。预览与落库跑**同一段** `build_plan` —— 不存在"预览说没事、确认就报错"。

    ⚠️ **导入后当前状态仍是空的**（除非另外做过期初确认）：这里只写 `light_movements`
    （`origin='import'`），**不碰 `light_holdings`**、不计入守恒式、不参与重放。
    同一份 Excel 导两遍，第二遍 0 新增（按自然键去重）。
    """
    content = await file.read()
    filename = file.filename or "light-history.csv"
    plan = light_import.build_plan(db, content, filename)

    if plan.schema_note:
        raise HTTPException(status_code=400, detail=plan.schema_note)
    if plan.total == 0 or (plan.count(light_import.ACTION_IMPORT) == 0 and plan.count(light_import.ACTION_ERROR) == 0):
        raise HTTPException(status_code=400, detail="这份文件里没有可以导入的行")

    outcome = light_import.apply_plan(db, plan, operator=operator)

    record_audit(
        db,
        AuditAction.LIGHT_IMPORT,
        target_type=AuditTarget.FILE,
        target_id=None,
        target_label=f"轻资产历史导入：{filename}",
        summary=f"导入轻资产历史《{filename}》：新增 {outcome.imported} / 已存在 {outcome.duplicate}"
                f" / 有问题 {outcome.error}（**只补历史，不计入当前库存**）",
        operator=operator,
        detail={
            "filename": filename,
            "batch": outcome.batch,
            "imported": outcome.imported,
            "duplicate": outcome.duplicate,
            "error": outcome.error,
        },
    )
    db.commit()

    preview = _plan_preview(plan)
    # 落库结果以实际发生的为准（不是"计划"）：这点对应"预览与落库跑同一段 plan"。
    return ImportResult(
        filename=preview.filename,
        total=preview.total,
        will_import=outcome.imported,
        duplicate=outcome.duplicate,
        error=preview.error,
        rows=preview.rows,
        rows_truncated=preview.rows_truncated,
        schema_note=preview.schema_note,
    )


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
@router.get("/export", summary="导出轻资产台账（当前分配 + 流水）")
def export_light(
    db: Session = Depends(get_db),
    category_id: Optional[int] = Query(None),
):
    """导出现状与轨迹。**只读**，不写审计以外的东西（导出本身记一条审计）。"""
    categories = list(
        db.execute(
            select(LightCategory).order_by(LightCategory.id.asc())
        ).scalars().all()
    )
    if category_id is not None:
        categories = [c for c in categories if c.id == category_id]
        if not categories:
            raise HTTPException(status_code=404, detail=f"轻资产品类 #{category_id} 不存在")

    unit_of = {c.id: c.unit for c in categories}
    holding_rows: list[list] = [[
        "品类", "品牌规格", "数量", "单位", "持有类型", "持有位置", "使用环境", "起始时间", "备注",
    ]]
    for c in categories:
        label = " ".join(x for x in (c.brand, c.spec) if x)
        for h in light_service.current_holdings(db, c.id):
            out = light_serialize.to_holding_out(db, h)
            holding_rows.append([
                c.name, label, h.quantity, c.unit, out.kind_label, out.display,
                out.host_user or out.host_workstation_code or "",
                h.since.strftime("%Y-%m-%d %H:%M") if h.since else "",
                h.note or "",
            ])

    stmt = select(LightMovement)
    if category_id is not None:
        stmt = stmt.where(LightMovement.category_id == category_id)
    movements = list(
        db.execute(stmt.order_by(LightMovement.occurred_at.asc(), LightMovement.id.asc())).scalars().all()
    )
    movement_rows: list[list] = [[
        "时间", "品类", "操作", "数量", "来源", "去向", "来源类型", "操作人", "备注", "记录来源",
    ]]
    name_of = {c.id: c.name for c in db.execute(select(LightCategory)).scalars().all()}
    for m in movements:
        movement_rows.append([
            m.occurred_at.strftime("%Y-%m-%d %H:%M"),
            name_of.get(m.category_id, f"#{m.category_id}"),
            LightOp.label(m.op),
            m.quantity,
            _holder_text(m, "from"),
            _holder_text(m, "to"),
            LightOrigin.LABELS.get(m.origin, m.origin),
            m.operator or "",
            m.note or "",
        ])

    content = spreadsheet.build_xlsx([
        spreadsheet.Sheet("当前分配", holding_rows, widths=[22, 22, 8, 6, 12, 22, 18, 18, 24]),
        spreadsheet.Sheet("流转流水", movement_rows, widths=[18, 22, 12, 8, 18, 18, 12, 12, 24, 12]),
    ])
    filename = spreadsheet.safe_filename(
        f"轻资产台账-{datetime.now().strftime('%Y%m%d')}.xlsx", "light.xlsx"
    )
    record_audit(
        db,
        AuditAction.AUDIT_EXPORT,
        target_type=AuditTarget.LIGHT_CATEGORY,
        target_id=category_id,
        target_label="轻资产台账导出",
        summary=f"导出轻资产台账（当前分配 {len(holding_rows) - 1} 条 / 流水 {len(movement_rows) - 1} 条）",
        detail={"category_id": category_id},
    )
    db.commit()
    return Response(
        content=content,
        media_type=spreadsheet.XLSX_MIME,
        headers={"Content-Disposition": spreadsheet.content_disposition(filename, "light.xlsx")},
    )


# --------------------------------------------------------------------------- #
# 汇总 / 下拉候选
# --------------------------------------------------------------------------- #
@router.get("/summary", summary="轻资产总览（页头统计）")
def get_summary(db: Session = Depends(get_db)):
    categories = list(
        db.execute(
            select(LightCategory).where(LightCategory.active.is_(True)).order_by(LightCategory.id.asc())
        ).scalars().all()
    )
    summaries = [(c, light_service.category_summary(db, c)) for c in categories]

    movement_total = db.execute(select(func.count()).select_from(LightMovement)).scalar_one()
    imported_total = db.execute(
        select(func.count()).select_from(LightMovement).where(LightMovement.origin == LightOrigin.IMPORT)
    ).scalar_one()

    return {
        "category_count": len(categories),
        "movement_count": int(movement_total),
        "imported_movement_count": int(imported_total),
        #: 还没做期初确认（有历史流水、当前账却是空的）的品类数 —— 页头给一条中性提示
        "pending_opening_count": sum(1 for _, s in summaries if s["pending_opening"]),
        "holding_count": sum(len(s["holdings"]) for _, s in summaries),
        "stock_total": sum(s["stock"] for _, s in summaries),
        "allocated_total": sum(s["allocated"] for _, s in summaries),
    }


@router.get("/filters", response_model=LightFilterOptions, summary="轻资产筛选下拉候选")
def get_filters(db: Session = Depends(get_db)):
    categories = list(
        db.execute(
            select(LightCategory).where(LightCategory.active.is_(True)).order_by(LightCategory.name.asc())
        ).scalars().all()
    )
    users = [
        u
        for (u,) in db.execute(
            select(LightMovement.to_user)
            .where(LightMovement.to_user.is_not(None))
            .distinct()
            .order_by(LightMovement.to_user.asc())
        ).all()
        if u
    ]
    locations = sorted(
        {
            (loc or "").strip()
            for (loc,) in db.execute(
                select(LightHolding.stock_location).where(LightHolding.stock_location.is_not(None))
            ).all()
            if (loc or "").strip()
        },
        key=lambda s: s,
    )
    # 资产 / 点位候选走 `/light/holders` 搜索端点，不在这里全量返回：
    # 150 台设备 + 69 个点位全塞进一个下拉候选接口，每次打开页面都白拉一遍。
    return LightFilterOptions(
        categories=[{"id": c.id, "name": c.name, "unit": c.unit} for c in categories],
        users=users,
        ops=[{"value": op, "label": LightOp.label(op)} for op in LightOp.ALL],
        stock_locations=locations,
    )


@router.get("/holders", response_model=list[LightHolderCandidate], summary="搜索可关联的正式资产 / 点位")
def search_holders(
    db: Session = Depends(get_db),
    kind: str = Query("asset", description="asset = 正式资产 / workstation = 资产点位"),
    keyword: str = Query("", description="按编码 / 名称 / 使用人模糊搜索"),
    limit: int = Query(
        0,
        ge=0,
        le=2000,
        description="返回条数上限；0 = 不限制（下拉要能一直滚到底）",
    ),
):
    """流转对话框选宿主用。**只搜、不建** —— 正式资产和点位仍归各自模块管。

    已报废的设备刻意排除：往报废机器上装配件没有意义，
    真出现了也是先走正式资产那边的流程。

    ⚠️ `limit` 默认 **0（全部）**，不是某个小数字。原先默认 20：
    点位有 ~70 个、资产 ~150 台，打开下拉只看得到前 20 条 —— 用户反馈
    「只能显示前 20，不能下滑看到全部」。这里的数据量本来就不大，
    一次拉完比"让人反复打关键词去猜"好；真到需要分页时再说。
    """
    kw = (keyword or "").strip()
    if kind == "workstation":
        stmt = select(Workstation).where(Workstation.active.is_(True))
        if kw:
            like = f"%{kw}%"
            # 只按编码 / 使用人搜 —— **不含 room**（2026-10-19 口径收口）：
            # 系统里没有"房间"这个维度，真实库 69 个点位只有 2 条 room 有值，
            # 留着它只会让同一个词在别处搜不到、在这里搜得到。
            stmt = stmt.where(
                Workstation.code.ilike(like) | Workstation.user_name.ilike(like)
            )
        stmt = stmt.order_by(Workstation.code.asc())
        if limit > 0:
            stmt = stmt.limit(limit)
        rows = db.execute(stmt).scalars().all()
        return [
            LightHolderCandidate(
                id=w.id,
                code=w.code,
                # 点位没有"名字"列，编码就是它对外的主标识
                label=w.code,
                hint=w.user_name or "",
                kind="workstation",
            )
            for w in rows
        ]

    stmt = select(Asset).where(Asset.status != AssetStatus.SCRAPPED)
    if kw:
        like = f"%{kw}%"
        stmt = stmt.where(
            Asset.asset_code.ilike(like)
            | Asset.user_name.ilike(like)
            | Asset.brand.ilike(like)
            | Asset.model.ilike(like)
            | Asset.serial_number.ilike(like)
        )
    stmt = stmt.order_by(Asset.asset_code.asc())
    if limit > 0:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).scalars().all()
    return [
        LightHolderCandidate(
            id=a.id,
            code=a.asset_code,
            label=a.device_type.name if a.device_type else a.asset_code,
            hint=a.user_name or "",
            kind="asset",
        )
        for a in rows
    ]


# --------------------------------------------------------------------------- #
# 预览：报废 / 删点位前的关联影响（被 assets.py / workstations.py 复用）
# --------------------------------------------------------------------------- #
def light_impact_for_asset(db: Session, asset_id: int) -> dict:
    """这台资产上还挂着什么轻资产（报废前提示用，设计 §7.2）。"""
    rows = light_service.holdings_by_asset(db, asset_id)
    return {
        "items": [light_serialize.to_holding_out(db, r) for r in rows],
        "total": sum(r.quantity for r in rows),
    }


def light_impact_for_station(db: Session, station_id: int) -> dict:
    """删点位前的影响预检（设计 §5.6）：直接关联 / 随主机关联 / 库存位置三类分开。

    ⚠️ **不含 `via_users`**：那条记录挂在人身上，点位删了它也不变
    （只是少一个候选位置），不是"会被影响的东西"。
    """
    grouped = light_service.holdings_by_workstation(db, station_id)
    return {
        "direct": [light_serialize.to_holding_out(db, r) for r in grouped["direct"]],
        "via_assets": [light_serialize.to_holding_out(db, r) for r in grouped["via_assets"]],
        "stock": [light_serialize.to_holding_out(db, r) for r in grouped["stock"]],
    }


__all__ = [
    "light_impact_for_asset",
    "light_impact_for_station",
    "router",
]
