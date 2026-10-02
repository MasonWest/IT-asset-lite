"""工位（资产空间地图）。

## 这个模块的地基：一条外键，不是一张绑定表

「资产挂在哪个工位」这件事**只存一处**：`assets.workstation_id`。

没建 `asset_workstation_bindings` 之类的中间表，理由：
 1. 关系是纯**多对一**（一台资产同一时刻只在一个工位上），一条外键就够；
 2. 「资产在工位间流转」的历史由 `asset_events` 免费提供 ——
    改 `workstation_id` 时进 `_TRACKED_FIELDS`，时间线自动记一条。
    再造一张带 `active` 状态的绑定表，等于同一件事存两处，必然出现
    "绑定表说在 A、assets 说在 B"而没人知道该信哪个；
 3. 工位下有几台资产是**派生查询**（`asset_count`），不落库。

## 删除：先拦、后确认、软删，绝不级联删资产

 - 数据库层 `ON DELETE RESTRICT`：资产还挂在工位上时，硬删会被拦住。
   **它是最后一道网，不是面向用户的机制** —— 用户看到的是应用层的 409 + 人话。
   正常退役走软删除（`active=False`），那条路径根本不触发外键。
 - `reset.py` 不会被它卡住：那边按 `BUSINESS_TABLES` 的子表优先顺序删，
   删 `assets` 时已经没有任何行引用工位。
 - `force=true` 时也只是把资产的 `workstation_id` 置空（**资产留着**），
   不是删资产。工位只是资产的位置标签，为了删标签而删掉被标记的东西，因果反了。

## 删除后 code 不释放

软删除的工位仍占着它的 `code`，所以不能再建一个同名的。
这是有意的：审计里两条指向「W23」的记录必须永远说的是同一个位置。
报错会给人话（见 `delete_workstation`），不让用户看到数据库约束错误。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Asset,
    AssetEventType,
    AuditAction,
    AuditTarget,
    Facing,
    FloorMap,
    Workstation,
)
from ..schemas import AssetOut
from ..schemas_workstations import (
    WorkstationAssetsRequest,
    WorkstationBulkGenerate,
    WorkstationBulkResult,
    WorkstationCreate,
    WorkstationImportPreview,
    WorkstationImportResult,
    WorkstationImportRow,
    WorkstationLayoutRequest,
    WorkstationMap,
    WorkstationMapCanvas,
    WorkstationOut,
    WorkstationUpdate,
)
from ..services import floor_map_layout, workstation_importer
from ..services.audit import record_audit
from ..services.events import record_event
from ..services.serialize import to_asset_out

router = APIRouter(prefix="/api/workstations", tags=["workstations"])

#: 预览里最多回多少行。62 条其实全回也没事，但保持一致口径。
PREVIEW_ROW_LIMIT = 200

#: 画布基准尺寸。与原型一致（960×1320），网格 20px 与前端视觉网格对齐。
CANVAS = WorkstationMapCanvas()


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _get_station_or_404(db: Session, station_id: int, *, include_inactive: bool = False) -> Workstation:
    station = db.get(Workstation, station_id)
    if station is None or (not include_inactive and not station.active):
        raise HTTPException(status_code=404, detail=f"工位 #{station_id} 不存在")
    return station


def _asset_counts(db: Session, station_ids: list[int]) -> dict[int, int]:
    """{工位 ID: 挂着几台资产}。一次查完，避免列表页 N+1。"""
    if not station_ids:
        return {}
    rows = db.execute(
        select(Asset.workstation_id, func.count(Asset.id))
        .where(Asset.workstation_id.in_(station_ids))
        .group_by(Asset.workstation_id)
    ).all()
    return {int(sid): int(cnt) for sid, cnt in rows if sid is not None}


def _default_floor_map(db: Session) -> Optional[FloorMap]:
    """默认平面图。单图语义下这就是"那张图"，取不到返回 None。

    这里**不做 404**：地图渲染接口本来就该在"图丢了"时还能返回工位，
    否则用户看到的是整页报错而不是"底图没画出来"。
    """
    stmt = (
        select(FloorMap)
        .where(FloorMap.active.is_(True))
        .order_by(FloorMap.is_default.desc(), FloorMap.id.asc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def _serialize(station: Workstation, asset_count: int = 0) -> WorkstationOut:
    return WorkstationOut(
        id=station.id,
        code=station.code,
        x=station.x,
        y=station.y,
        facing=station.facing,
        user_name=station.user_name,
        room=station.room,
        active=station.active,
        created_at=station.created_at,
        updated_at=station.updated_at,
        asset_count=asset_count,
    )


def _station_label(station: Workstation) -> str:
    parts = [station.code]
    if station.user_name:
        parts.append(station.user_name)
    if station.room:
        parts.append(station.room)
    return " · ".join(parts)


def _next_code(db: Session) -> str:
    """生成下一个可用工位编码，形如 W63。

    在**后端**生成而不是让前端算：前端要"先看看最大的是多少"再 +1，
    两个人同时点新增就会撞出同一个编码。后端查一次最大值也一样有竞态，
    但唯一约束会当场拦住，且这里是在同一个请求/事务里，概率低得多。
    """
    codes = [
        str(code)
        for (code,) in db.execute(select(Workstation.code)).all()
        if str(code).startswith("W") and str(code)[1:].isdigit()
    ]
    biggest = max((int(c[1:]) for c in codes), default=0)
    return f"W{biggest + 1:02d}"


def _allocate_codes(
    db: Session, prefix: str, start_code: Optional[str], count: int
) -> list[str]:
    """给批量生成算 N 个编码。**冲突就地报 409，不跳号。**

    ## 为什么不跳号
    用户看到的预览是「W63~W86」这种连续号段。如果发现 W70 被一个已软删除的工位占着
    就静默跳到 W71，那界面显示的号段和实际落库的对不上 —— 用户拿这个号段去贴标签会贴错。
    编码曾经用过就永远占用（见 `_ensure_code_unique` 的说明），所以正确做法是
    报错让用户换起始号，而不是替他把账算平。

    ## start_code 是"建议"
    `start_code` 为空时按 prefix 查现有最大值续号；给了就按它起。**两种都查全量**，
    因为真相在后端 —— 前端预览只是让用户有个预期，不是权威。
    """
    existing = {
        str(code) for (code,) in db.execute(select(Workstation.code)).all()
    }

    if start_code:
        match = re.match(r"^([A-Za-z]*)(\d+)$", start_code.strip())
        if not match:
            raise HTTPException(
                status_code=422,
                detail=f"起始编码「{start_code}」格式不对，应当是字母前缀 + 数字（如 W63）",
            )
        prefix = match.group(1) or prefix
        number = int(match.group(2))
    else:
        # 按前缀找现有最大数字续号
        biggest = 0
        for code in existing:
            m = re.match(r"^([A-Za-z]*)(\d+)$", code)
            if m and m.group(1).upper() == prefix.upper():
                biggest = max(biggest, int(m.group(2)))
        number = biggest + 1

    codes: list[str] = []
    cursor = number
    for _ in range(count):
        candidate = f"{prefix}{cursor:02d}"
        if candidate in existing:
            # 撞了就报错，不跳号 —— 跳号会让用户对不上账
            raise HTTPException(
                status_code=409,
                detail=(
                    f"编码「{candidate}」已被占用（可能是已删除的工位，编码不释放）。"
                    f"请把起始编码换成更大的号，或换一个前缀。"
                ),
            )
        codes.append(candidate)
        existing.add(candidate)
        cursor += 1
    return codes


def _ensure_code_unique(db: Session, code: str, *, exclude_id: Optional[int] = None) -> None:
    stmt = select(Workstation).where(Workstation.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Workstation.id != exclude_id)
    other = db.execute(stmt).scalar_one_or_none()
    if other is None:
        return
    if other.active:
        raise HTTPException(status_code=409, detail=f"工位编码「{code}」已经被占用，换一个吧")
    # 软删除的工位也占着编码 —— 这是有意的，报错要说清为什么，
    # 否则用户会以为系统坏了（"明明没有 W23，为什么不让我建"）
    raise HTTPException(
        status_code=409,
        detail=(
            f"工位编码「{code}」曾经用过（{other.updated_at:%Y-%m-%d} 已删除）。"
            f"为了让审计记录里两条「{code}」不产生歧义，已删除工位的编码不会释放。"
            f"请换一个编码，或把已删除的那个改成「{code}-旧」。"
        ),
    )


# --------------------------------------------------------------------------- #
# 地图渲染
# --------------------------------------------------------------------------- #
@router.get("", response_model=WorkstationMap, summary="工位列表（地图渲染用）")
def list_workstations(
    db: Session = Depends(get_db),
    include_inactive: bool = Query(False, description="是否带上已删除的工位（排查历史用）"),
):
    stmt = select(Workstation)
    if not include_inactive:
        stmt = stmt.where(Workstation.active.is_(True))
    rows = list(db.execute(stmt.order_by(Workstation.y.asc(), Workstation.x.asc(), Workstation.id.asc())).scalars().all())

    counts = _asset_counts(db, [s.id for s in rows])
    items = [_serialize(s, counts.get(s.id, 0)) for s in rows]

    # 画布尺寸 + 建筑图元都来自默认平面图（不再写死在前端模板里）。
    # 取不到图时退回模块级 CANVAS 默认值，保证老库/被 reset 过的库也能渲染工位。
    floor = _default_floor_map(db)
    canvas = (
        WorkstationMapCanvas(width=floor.width, height=floor.height, grid=floor.grid)
        if floor is not None
        else CANVAS
    )
    layout = floor_map_layout.parse_layout(floor.layout) if floor is not None else floor_map_layout.empty_layout()

    return WorkstationMap(
        canvas=canvas,
        layout=layout,
        floor_map_id=floor.id if floor is not None else None,
        total=len(items),
        assigned=sum(1 for i in items if (i.user_name or "").strip()),
        empty=sum(1 for i in items if i.asset_count == 0),
        items=items,
    )


@router.post("/bulk", response_model=WorkstationBulkResult, summary="批量生成工位（行列排布）")
def bulk_generate(payload: WorkstationBulkGenerate, db: Session = Depends(get_db)):
    """一次生成 N 个工位，按行列排布。**1 次操作 = 1 条审计**。

    排列规则：`i` 从 0 数，`row = i // cols`、`col = i % cols`，
    位置 = `(origin_x + col*step_x, origin_y + row*step_y)`。

    朝向：`alternate` 时按**列**交替（`col % 2`），这样一列朝下、一列朝上 ——
    面对面工位的真实形态。按行交替会变成"一排里左右乱转"，不像工位图。

    编码：整批先算出来再落库（不能边插边算，`_next_code` 查的是库里已有的）。
    分配真相在后端，`start_code` 只是一个"从哪儿起"的建议。
    """
    prefix = payload.code_prefix or "W"
    codes = _allocate_codes(db, prefix, payload.start_code, payload.count)

    # 整批在同一个事务里建。任一条撞唯一约束 → 整批 500 回滚（不会留半批）
    created: list[Workstation] = []
    for index, code in enumerate(codes):
        row = index // payload.cols
        col = index % payload.cols
        if payload.facing == "alternate":
            facing = Facing.DOWN if col % 2 == 0 else Facing.UP
        else:
            facing = Facing.UP if payload.facing == "up" else Facing.DOWN

        station = Workstation(
            code=code,
            x=payload.origin_x + col * payload.step_x,
            y=payload.origin_y + row * payload.step_y,
            facing=facing,
            user_name=None,
            room=payload.room,
            active=True,
        )
        db.add(station)
        created.append(station)

    try:
        db.flush()
    except IntegrityError as exc:  # pragma: no cover - 兜底，正常走不到
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"批量生成时编码冲突（{exc.orig}）。换一个起始编码或前缀再试。",
        ) from exc

    # 一次批量操作 = 一条审计（与批量导入、布局攒批同一口径）
    record_audit(
        db,
        AuditAction.WORKSTATION_CREATE,
        target_type=AuditTarget.WORKSTATION,
        target_id=created[0].id if created else None,
        target_label=f"批量生成 {len(created)} 个工位",
        summary=(
            f"批量生成 {len(created)} 个工位（{codes[0]}~{codes[-1]}），"
            f"起点 ({payload.origin_x},{payload.origin_y})，"
            f"{payload.cols} 列 × 间距 ({payload.step_x},{payload.step_y})"
            + (f"，房间「{payload.room}」" if payload.room else "")
        ),
        operator=payload.operator,
        detail={
            "count": len(created),
            "codes": codes,
            "origin": [payload.origin_x, payload.origin_y],
            "step": [payload.step_x, payload.step_y],
            "cols": payload.cols,
            "facing": payload.facing,
        },
    )

    db.commit()
    for station in created:
        db.refresh(station)

    return WorkstationBulkResult(
        created=len(created),
        codes=codes,
        first_code=codes[0] if codes else None,
        last_code=codes[-1] if codes else None,
        items=[_serialize(s, 0) for s in created],
    )


@router.get("/{station_id}", response_model=WorkstationOut, summary="单个工位")
def get_workstation(station_id: int, db: Session = Depends(get_db)):
    station = _get_station_or_404(db, station_id)
    return _serialize(station, _asset_counts(db, [station.id]).get(station.id, 0))


@router.get("/{station_id}/assets", response_model=list[AssetOut], summary="工位上挂着的资产")
def list_station_assets(station_id: int, db: Session = Depends(get_db)):
    """侧边栏「资产清单」的数据源。

    查的是 `assets.workstation_id`（走索引），**不是**工位表里存的列表 ——
    资产归属只存这一处。
    """
    _get_station_or_404(db, station_id)
    rows = list(
        db.execute(
            select(Asset)
            .where(Asset.workstation_id == station_id)
            .order_by(Asset.asset_code.asc())
        ).scalars().all()
    )
    return [to_asset_out(a) for a in rows]


# --------------------------------------------------------------------------- #
# 写操作
# --------------------------------------------------------------------------- #
@router.post("", response_model=WorkstationOut, status_code=status.HTTP_201_CREATED, summary="新增工位")
def create_workstation(payload: WorkstationCreate, db: Session = Depends(get_db)):
    code = (payload.code or "").strip() or _next_code(db)
    _ensure_code_unique(db, code)

    station = Workstation(
        code=code,
        x=payload.x,
        y=payload.y,
        facing=payload.facing or Facing.DOWN,
        user_name=payload.user_name,
        room=payload.room,
        active=True,
    )
    db.add(station)
    db.flush()

    record_audit(
        db,
        AuditAction.WORKSTATION_CREATE,
        target_type=AuditTarget.WORKSTATION,
        target_id=station.id,
        target_label=_station_label(station),
        summary=f"新增工位 {code}，位置 ({station.x},{station.y})",
        operator=payload.operator,
        detail={"workstation_id": station.id, "code": code, "x": station.x, "y": station.y,
                "facing": station.facing},
    )

    db.commit()
    db.refresh(station)
    return _serialize(station, 0)


@router.patch("/layout", response_model=dict, summary="批量保存工位坐标（拖动后一次提交）")
def save_layout(payload: WorkstationLayoutRequest, db: Session = Depends(get_db)):
    """一次布局调整 = **1 条审计**，不是 N 条。

    界面上的「调整位置」是拖动 + 松手反复微调，一次排版可能几十次动作。
    逐次提交会让审计页瞬间被布局记录刷屏，真正的资产操作被淹掉 ——
    这和系统拒绝在时间线里记"点保存没改东西"是同一个理由。
    （与「批量导入 30 台设备 = 1 条审计」的口径也一致。）

    没有任何变化时不写审计。
    """
    ids = [item.id for item in payload.items]
    stations = {
        s.id: s for s in db.execute(select(Workstation).where(Workstation.id.in_(ids))).scalars().all()
    }

    missing = [i for i in ids if i not in stations]
    if missing:
        raise HTTPException(status_code=404, detail=f"工位不存在：{missing}")

    changes: list[dict[str, object]] = []
    for item in payload.items:
        station = stations[item.id]
        if station.x == item.x and station.y == item.y:
            continue
        changes.append({
            "id": station.id,
            "code": station.code,
            "from": [station.x, station.y],
            "to": [item.x, item.y],
        })
        station.x = item.x
        station.y = item.y

    if changes:
        head = "；".join(
            f"{c['code']} ({c['from'][0]},{c['from'][1]})→({c['to'][0]},{c['to'][1]})"
            for c in changes[:3]
        )
        more = f" 等 {len(changes)} 处" if len(changes) > 3 else ""
        record_audit(
            db,
            AuditAction.WORKSTATION_MOVE,
            target_type=AuditTarget.WORKSTATION,
            target_id=None,
            target_label=f"工位布局（{len(changes)} 处）",
            summary=f"调整工位布局：{head}{more}",
            operator=payload.operator,
            detail={"changes": changes, "count": len(changes)},
        )

    db.commit()
    return {"updated": len(changes), "unchanged": len(payload.items) - len(changes), "changes": changes}


@router.patch(
    "/{station_id}",
    response_model=WorkstationOut,
    summary="编辑工位（改人 / 编码 / 备注 / 单改坐标）",
)
def update_workstation(station_id: int, payload: WorkstationUpdate, db: Session = Depends(get_db)):
    station = _get_station_or_404(db, station_id)
    data = payload.model_dump(exclude_unset=True)
    operator = data.pop("operator", None)

    if "code" in data:
        code = (data["code"] or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="工位编码不能清空")
        _ensure_code_unique(db, code, exclude_id=station_id)
        data["code"] = code

    if not data:
        return _serialize(station, _asset_counts(db, [station.id]).get(station.id, 0))

    # 改动前的快照，用来算「改了什么」
    before = {key: getattr(station, key) for key in data}
    for key, value in data.items():
        setattr(station, key, value)
    after = {key: getattr(station, key) for key in data}

    changes = {
        key: {"from": _as_text(before[key]), "to": _as_text(after[key])}
        for key in data
        if _as_text(before[key]) != _as_text(after[key])
    }

    if changes:
        # 只动了坐标 → 归到「调整布局」；动了归属/编码/备注 → 归到「编辑工位」。
        # 分开的理由和 ASSET_UPDATE / ASSET_OPERATE 分开一样：
        # 用户在审计页按类型筛「谁改过工位归属」时，布局微调不该混进来。
        only_position = set(changes) <= {"x", "y"}
        action = AuditAction.WORKSTATION_MOVE if only_position else AuditAction.WORKSTATION_UPDATE
        labels = {**workstation_importer.FIELD_LABELS, "code": "编码"}
        summary = "；".join(
            f"{labels.get(k, k)} {v['from']} → {v['to']}" for k, v in changes.items()
        )
        record_audit(
            db,
            action,
            target_type=AuditTarget.WORKSTATION,
            target_id=station.id,
            target_label=_station_label(station),
            summary=summary,
            operator=operator,
            detail={"changes": changes, "fields": list(changes.keys())},
        )

    db.commit()
    db.refresh(station)
    return _serialize(station, _asset_counts(db, [station.id]).get(station.id, 0))


def _as_text(value: object) -> str:
    if value is None or value == "":
        return "空"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


@router.post("/{station_id}/assets", response_model=dict, summary="把资产挂到工位上")
def bind_assets(station_id: int, payload: WorkstationAssetsRequest, db: Session = Depends(get_db)):
    """把一批资产挪到这个工位上。

    **每台资产各写 1 条 asset_events**（设备视角，履历要各自完整），
    但**只写 1 条审计**（一次用户操作 = 1 条）——
    这正是 asset_events 与 audit_logs 分工的地方。
    """
    station = _get_station_or_404(db, station_id)
    assets = list(db.execute(select(Asset).where(Asset.id.in_(payload.asset_ids))).scalars().all())
    missing = sorted(set(payload.asset_ids) - {a.id for a in assets})
    if missing:
        raise HTTPException(status_code=404, detail=f"资产不存在：{missing}")

    moved: list[str] = []
    for asset in assets:
        if asset.workstation_id == station.id:
            continue
        before = asset.workstation_id
        asset.workstation_id = station.id
        moved.append(asset.asset_code)
        record_event(
            db,
            asset,
            AssetEventType.UPDATE,
            operator=payload.operator,
            note=f"所在工位 {_station_code(db, before)} → {station.code}",
            detail={"changes": {"workstation_id": {
                "from": _station_code(db, before), "to": station.code}}},
        )

    if moved:
        record_audit(
            db,
            AuditAction.WORKSTATION_UPDATE,
            target_type=AuditTarget.WORKSTATION,
            target_id=station.id,
            target_label=_station_label(station),
            summary=f"把 {len(moved)} 台设备挂到工位 {station.code}：{'、'.join(moved[:3])}"
                    + (f" 等 {len(moved)} 台" if len(moved) > 3 else ""),
            operator=payload.operator,
            detail={"workstation_id": station.id, "code": station.code,
                    "asset_codes": moved, "count": len(moved)},
        )

    db.commit()
    return {"moved": len(moved), "asset_codes": moved}


@router.delete("/{station_id}/assets/{asset_id}", response_model=dict, summary="把资产从工位摘下来")
def unbind_asset(
    station_id: int,
    asset_id: int,
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None),
):
    """摘下来**不等于删掉资产** —— 资产留着，只是没有工位了。

    它同时会写一条"所在工位 → 空"的时间线，这条履历正是
    「这台机器现在放哪了」的答案。
    """
    station = _get_station_or_404(db, station_id)
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"资产 #{asset_id} 不存在")
    if asset.workstation_id != station.id:
        raise HTTPException(
            status_code=409,
            detail=f"「{asset.asset_code}」并不挂在工位 {station.code} 上",
        )

    asset.workstation_id = None
    record_event(
        db,
        asset,
        AssetEventType.UPDATE,
        operator=operator,
        note=f"所在工位 {station.code} → 空",
        detail={"changes": {"workstation_id": {"from": station.code, "to": ""}}},
    )
    record_audit(
        db,
        AuditAction.WORKSTATION_UPDATE,
        target_type=AuditTarget.WORKSTATION,
        target_id=station.id,
        target_label=_station_label(station),
        summary=f"把 {asset.asset_code} 从工位 {station.code} 摘下来",
        operator=operator,
        detail={"workstation_id": station.id, "code": station.code, "asset_id": asset.id,
                "asset_code": asset.asset_code},
    )
    db.commit()
    return {"ok": True, "asset_id": asset_id, "asset_code": asset.asset_code}


def _station_code(db: Session, station_id: Optional[int]) -> str:
    if station_id is None:
        return "空"
    station = db.get(Workstation, station_id)
    return station.code if station else f"#{station_id}"


@router.delete("/{station_id}", response_model=dict, summary="删除工位（软删除）")
def delete_workstation(
    station_id: int,
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None),
    force: bool = Query(False, description="工位上还有资产时，是否一并清空这些资产的工位归属"),
):
    """删工位。**工位上还有资产时先拦住**，让调用方决定。

    绝不级联删资产：工位只是资产的位置标签，为了删标签而删掉被标记的东西，
    因果是反的。（对比：删资产会自动解绑显示器，因为配对关系是从属于资产的 ——
    方向不同，处理就不能照抄。）

    即使 `force=true`，也只是把资产的 workstation_id 置空，**资产留着**。
    """
    station = _get_station_or_404(db, station_id)

    attached = list(
        db.execute(select(Asset).where(Asset.workstation_id == station.id)).scalars().all()
    )

    if attached and not force:
        preview = "、".join(a.asset_code for a in attached[:5])
        more = f" 等 {len(attached)} 台" if len(attached) > 5 else ""
        raise HTTPException(
            status_code=409,
            detail=(
                f"工位 {station.code} 上还挂着 {len(attached)} 台设备（{preview}{more}）。"
                f"请先把它们移到别的工位；如果确定要删，就带上「同时清空这些资产的工位归属」，"
                f"设备本身不会被删除。"
            ),
        )

    # 先清归属（每台留一条时间线），再软删工位
    detached: list[str] = []
    for asset in attached:
        asset.workstation_id = None
        detached.append(asset.asset_code)
        record_event(
            db,
            asset,
            AssetEventType.UPDATE,
            operator=operator,
            note=f"所在工位 {station.code} → 空（工位已删除）",
            detail={"changes": {"workstation_id": {"from": station.code, "to": ""}}},
        )

    label = _station_label(station)
    code = station.code
    station.active = False

    record_audit(
        db,
        AuditAction.WORKSTATION_DELETE,
        target_type=AuditTarget.WORKSTATION,
        target_id=station_id,
        target_label=label,
        summary=f"删除工位 {code}"
        + (f"，同时清空 {len(detached)} 台设备的工位归属（设备保留）" if detached else ""),
        operator=operator,
        detail={"workstation_id": station_id, "code": code,
                "detached_assets": detached, "detached_count": len(detached)},
    )

    db.commit()
    return {
        "ok": True,
        "deleted_id": station_id,
        "code": code,
        "detached_assets": detached,
        "note": "工位是软删除；编码不会释放，避免审计里出现两个不同的位置都叫这个名字",
    }


# --------------------------------------------------------------------------- #
# 导入
# --------------------------------------------------------------------------- #
def _row_out(row: workstation_importer.RowPlan) -> WorkstationImportRow:
    return WorkstationImportRow(
        row=row.row,
        code=row.code,
        action=row.action,
        action_label=row.action_label,
        errors=list(row.errors),
        warnings=list(row.warnings),
        changes=list(row.changes),
    )


def _plan_to_preview(plan: workstation_importer.ImportPlan) -> WorkstationImportPreview:
    rows = plan.rows[:PREVIEW_ROW_LIMIT]
    return WorkstationImportPreview(
        filename=plan.filename,
        total=plan.total,
        create=plan.create,
        update=plan.update,
        unchanged=plan.unchanged,
        error=plan.error,
        will_write=plan.will_write,
        can_commit=plan.can_commit,
        empty=plan.empty,
        schema_ok=plan.schema_ok,
        schema_note=plan.schema_note,
        rows=[_row_out(r) for r in rows],
        rows_truncated=len(plan.rows) > len(rows),
    )


@router.post("/import/preview", response_model=WorkstationImportPreview, summary="导入预览（不写任何数据）")
async def import_preview(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    plan = workstation_importer.build_plan(db, content, file.filename or "workstations.json")
    return _plan_to_preview(plan)


@router.post("/import", response_model=WorkstationImportResult, summary="导入工位（按 code upsert）")
async def import_workstations(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    operator: Optional[str] = Query(None),
):
    """落库。预览与落库跑**同一段** `build_plan` —— 不存在"预览说没事、确认就报错"。

    一次导入写 **1 条审计**（不是 62 条），与「批量导入 30 台设备 = 1 条」口径一致。
    工位新增/变动**不写 asset_events** —— 没碰任何资产，硬写就得挑一台代表资产来挂，
    那是在为满足表结构而编造事实。
    """
    content = await file.read()
    filename = file.filename or "workstations.json"
    plan = workstation_importer.build_plan(db, content, filename)

    if not plan.schema_ok:
        raise HTTPException(status_code=400, detail=plan.schema_note)
    if plan.empty:
        raise HTTPException(status_code=400, detail="这份文件里一个工位都没有（workstations 为空）")

    outcome = workstation_importer.apply_plan(db, plan)

    if outcome.created or outcome.updated:
        record_audit(
            db,
            AuditAction.WORKSTATION_CREATE,
            target_type=AuditTarget.WORKSTATION,
            target_id=None,
            target_label=f"工位导入：{filename}",
            summary=f"导入工位文件《{filename}》：新增 {outcome.created} / 更新 {outcome.updated}"
                    f" / 无变化 {outcome.unchanged} / 失败 {outcome.failed}",
            operator=operator,
            detail={
                "filename": filename,
                "created": outcome.created,
                "updated": outcome.updated,
                "unchanged": outcome.unchanged,
                "failed": outcome.failed,
                "created_codes": outcome.created_codes[:50],
            },
        )

    db.commit()

    preview = _plan_to_preview(plan)
    return WorkstationImportResult(**preview.model_dump())
