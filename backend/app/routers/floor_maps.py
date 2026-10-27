"""平面图接口（单图）。

## 这个模块的地基：**后端可以有 map，前端只能有"这一张图"**

数据库层 `floor_maps` 是张正常的表（有 id、有 is_default），但接口刻意收窄成
"一张图"的形状：`GET /api/floor-maps` 返回的 list 里**永远只有一张**。

为什么这么做：多图一旦在前端露头，就会长出选择器、`?map=`、Tab、"复制到另一层"，
而业务侧现在根本没有"多楼层"这个概念 —— 于是滑向
「数据库支持多图 / API 半支持多图 / 前端假装支持多图 / 业务其实单图」
那种所有 AI 项目都会掉进去的状态。宁可现在多写一行注释，也不留这个坑。

将来真要做多图，改的是这一层：把 list 放开、加 `?map=`，数据不用迁。

## 保存是整张覆盖 + 乐观锁

图元没有独立身份，永远是"整张图一起编辑一起保存" → `PUT` 覆盖 layout 是
最自然的形式。配 `expected_updated_at` 做乐观锁：两个人同时开着编辑器，
后保存的那个会拿到 409 而不是静默把前一个人涂掉。

`expected_updated_at` 不带则跳过检查 —— 让 CLI/脚本能直接写，
不至于每次都要先 GET 一次拿版本号。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditAction, AuditTarget, FloorMap
from ..schemas_floor_maps import (
    FloorMapCanvas,
    FloorMapCreate,
    FloorMapList,
    FloorMapOut,
    FloorMapSave,
)
from ..services import floor_map_layout
from ..services.audit import record_audit

router = APIRouter(prefix="/api/floor-maps", tags=["floor-maps"])

#: 新建空白图时的默认名。刻意与 `database.DEFAULT_FLOOR_NAME`（"主办公室"，
#: 那是给老库补图用的）分开：那是"替你认领已有的办公室"，这是"给你一张白纸"。
DEFAULT_NEW_FLOOR_NAME = "我的办公室"


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _serialize(floor: FloorMap) -> FloorMapOut:
    """行 → 出参。layout 出参一律是**解析后的对象**，不是字符串。"""
    layout = floor_map_layout.parse_layout(floor.layout)
    return FloorMapOut(
        id=floor.id,
        name=floor.name,
        canvas=FloorMapCanvas(width=floor.width, height=floor.height, grid=floor.grid),
        layout=layout,
        is_default=bool(floor.is_default),
        created_at=floor.created_at.isoformat() if floor.created_at else None,
        updated_at=floor.updated_at.isoformat() if floor.updated_at else None,
    )


def _get_floor_or_404(db: Session, floor_id: int) -> FloorMap:
    floor = db.get(FloorMap, floor_id)
    if floor is None or not floor.active:
        raise HTTPException(status_code=404, detail=f"平面图 #{floor_id} 不存在")
    return floor


def _default_floor(db: Session) -> Optional[FloorMap]:
    """取默认图。单图语义下这就是"那张图"。"""
    stmt = (
        select(FloorMap)
        .where(FloorMap.active.is_(True))
        .order_by(FloorMap.is_default.desc(), FloorMap.id.asc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
@router.get("", response_model=FloorMapList, summary="平面图列表（当前恒为一张）")
def list_floor_maps(db: Session = Depends(get_db)):
    rows = list(
        db.execute(
            select(FloorMap)
            .where(FloorMap.active.is_(True))
            .order_by(FloorMap.is_default.desc(), FloorMap.id.asc())
        )
        .scalars()
        .all()
    )
    items = [_serialize(f) for f in rows]
    return FloorMapList(total=len(items), items=items)


@router.get("/default", response_model=FloorMapOut, summary="默认平面图（地图渲染用）")
def get_default_floor_map(db: Session = Depends(get_db)):
    """⚠️ 这条**必须排在 `/{floor_id}` 之前**：FastAPI 按声明顺序匹配，
    否则 "default" 会被当成 floor_id 直接 422。
    """
    floor = _default_floor(db)
    if floor is None:
        # 这句文案原来写的是「重启后端会重建默认图，或执行 python reset.py 后重启」——
        # **两条都是假的**（v2.3.0 之后空库既不重建、reset 后也不重建，2026-10-27 实测确认）。
        # 现在的出路是真的：POST 建一张空白图，前端编辑器那个空态按钮调的就是它。
        raise HTTPException(
            status_code=404,
            detail="还没有平面图。去「编辑底图」页点「创建空白底图」就能建一张。",
        )
    return _serialize(floor)


@router.get("/{floor_id}", response_model=FloorMapOut, summary="单张平面图")
def get_floor_map(floor_id: int, db: Session = Depends(get_db)):
    return _serialize(_get_floor_or_404(db, floor_id))


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
@router.post(
    "",
    response_model=FloorMapOut,
    status_code=201,
    summary="新建一张空白平面图（库里一张图都没有时用）",
)
def create_floor_map(payload: FloorMapCreate, db: Session = Depends(get_db)):
    """给"一张图都没有"的人一个起点。

    ## 为什么必须有这条接口（2026-10-27 在真实 clone 的空库上实测发现）

    v2.3.0 决定「真·空库不塞默认底图」—— 不把本项目办公室的布局
    （总经理办公室 / 直播间 / 大会议室）塞给每个新用户，这个判断是对的。
    但当时**只做了"不塞"，没做"给一张白纸"**：`floor_maps` 只有 GET 和 PUT，
    于是新 clone 的用户进编辑器只看到 404 报错，**没有任何办法建出第一张图** ——
    点位能建、底图永远画不出来。那条 404 文案还写着"重启后端会重建默认图"，
    而按 v2.3.0 的逻辑，空库重启、reset 后重启**都不会**重建（两条都实测过）。

    这条接口补的就是那扇门：建一张**空白**图（`shapes: []`）。
    空白是刻意的 —— 仍然不替用户猜办公室长什么样，只是把"没有"变成"一张白纸"。

    ## 单图语义：已经有图就 409，不覆盖、也不新建第二张

    前端只在 404 时才显示这个入口，正常撞不到。真撞到了必须明确报错：
    静默返回旧图 = "点了没反应"，新建第二张 = 破坏单图心智模型，
    而且 `_default_floor` 按 `is_default desc, id asc` 取图，冒出第二张会让
    "哪张才是地图"变成运气问题。
    """
    existing = _default_floor(db)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"已经有一张平面图「{existing.name}」，当前版本只支持单张，不能新建第二张。",
        )

    floor = FloorMap(
        name=(payload.name or "").strip() or DEFAULT_NEW_FLOOR_NAME,
        width=payload.width,
        height=payload.height,
        grid=payload.grid,
        layout=floor_map_layout.dump_layout(floor_map_layout.empty_layout()),
        is_default=True,
        active=True,
    )
    db.add(floor)
    db.flush()  # 先拿到 id，审计要引用它

    record_audit(
        db,
        AuditAction.MAP_CREATE,
        target_type=AuditTarget.MAP,
        target_id=floor.id,
        target_label=floor.name,
        summary=f"新建平面图「{floor.name}」（{floor.width}×{floor.height}，空白画布）",
        operator=payload.operator,
    )
    db.commit()
    db.refresh(floor)
    return _serialize(floor)


@router.put("/{floor_id}", response_model=FloorMapOut, summary="保存平面图（整张覆盖）")
def save_floor_map(floor_id: int, payload: FloorMapSave, db: Session = Depends(get_db)):
    floor = _get_floor_or_404(db, floor_id)

    # 乐观锁：库里版本与前端打开时看到的不一致 → 有人先存过了。
    if payload.expected_updated_at:
        current = floor.updated_at.isoformat() if floor.updated_at else ""
        if payload.expected_updated_at != current:
            raise HTTPException(
                status_code=409,
                detail=(
                    "这张平面图在你编辑期间被改过了，为避免覆盖别人的修改，这次保存没有生效。"
                    "请刷新页面拿到最新版本后重新编辑。"
                ),
            )

    changed: list[str] = []
    if payload.name is not None and payload.name.strip() and payload.name.strip() != floor.name:
        floor.name = payload.name.strip()
        changed.append("名称")

    if payload.layout is not None:
        layout = floor_map_layout.parse_layout(payload.layout)
        old_ids = set(floor_map_layout.layout_shape_ids(floor_map_layout.parse_layout(floor.layout)))
        new_ids = set(floor_map_layout.layout_shape_ids(layout))
        if old_ids != new_ids:
            changed.append(f"图元 {len(old_ids)}→{len(new_ids)}")
        elif floor.layout != floor_map_layout.dump_layout(layout):
            changed.append("图元位置")
        floor.layout = floor_map_layout.dump_layout(layout)

    if not changed:
        # 什么都没变就不更新 updated_at —— 否则一次空保存会把别人的乐观锁版本顶掉。
        return _serialize(floor)

    floor.updated_at = datetime.now()

    record_audit(
        db,
        AuditAction.MAP_UPDATE,
        target_type=AuditTarget.MAP,
        target_id=floor.id,
        target_label=floor.name,
        summary=f"保存平面图「{floor.name}」（{'、'.join(changed)}）",
        operator=payload.operator,
    )
    db.commit()
    db.refresh(floor)
    return _serialize(floor)
