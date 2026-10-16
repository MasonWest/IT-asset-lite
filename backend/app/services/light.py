"""轻资产的核心逻辑：**当前分配记录（holdings）+ 流转流水（movements）同事务双写**。

## 一句话模型

- `light_holdings` 是**现在的快照** —— 列表页 / 详情页直接读它，不重放历史（快、简单）；
- `light_movements` 是**append-only 的历史** —— 完整轨迹，也是**可重建快照的唯一依据**；
- 两者在**同一个数据库事务里**写入，并且 `light_holdings` **只允许被
  `apply_holding_delta()` 修改**，不暴露任何"直接改库存"的接口。

## 为什么不是纯派生（第一版的做法）

第一版把当前状态 100% 由流水实时重放：心算是对的，但每次列表查询都要把该品类
**全部流水**读出来遍历 —— 台账页 20 个品类就是上百次查询，而且"改错一条流水"
要等下次查询才反映。这一版按《设计方案-轻资产管理模块-修订版》§4 改成双写。

## 守恒式（每次写操作后断言，破了就整体回滚）

```
Σ holdings = Σ(入库 + 期初 + 盘盈) − Σ(报损 + 盘亏)
```

破了不是"记条日志了事"—— 那说明代码有 bug，直接 raise 让事务整体回滚。

## 重放 / 重建必须知道哪些流水有效

`origin ∈ LightOrigin.EFFECTIVE`（manual / opening）才参与重放；
`import`（历史导入）**绝不参与** —— Excel 里只有分配、没有对应入库，算进来会得到
负库存或凭空出现的持有量（复用 `LightOrigin.EFFECTIVE`，**不在别处另写一份判断**）。
冲正流水（`corrects_id` 不为空）**参与**重放：它本身就是一次真实的归属变动，
按时间顺序把原记录与冲正记录都算进去，结果自然抵消，不需要特殊处理。
排序必须稳定：`occurred_at ASC, id ASC`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Asset,
    AssetStatus,
    LightCategory,
    LightHolding,
    LightHolderKind,
    LightMovement,
    LightOp,
    LightOrigin,
    Workstation,
)
from .ownership import has_owner

#: 库存记录的"未填存放位置"在库里的统一取值（None / "" 在唯一索引里等价，统一成 None 避免两套写法）
STOCK_PLACEHOLDER = ""


# --------------------------------------------------------------------------- #
# 引用（from / to / 持有位置）的表达
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HolderRef:
    """一个**持有位置**。

    - `kind is None` → "系统外部 / 已注销"（入库的来源、报损的去向）；
    - `kind == 'stock'` → 库存，`stock_location` 区分"分放的哪一处"；
    - `kind == 'user'` → `user` 必填；
    - `kind ∈ {asset, workstation}` → `ref_id` 必填。
    """

    kind: Optional[str] = None
    ref_id: Optional[int] = None
    user: Optional[str] = None
    #: 仅 kind='stock' 有意义：存放位置描述（自由文本，如 "3 楼库房A柜" / "W01"）
    stock_location: Optional[str] = None

    @property
    def is_external(self) -> bool:
        return self.kind is None


@dataclass
class HolderSnapshot:
    """一条流水的 from / to 落库用的快照（id + 当时的编码 + 当时的名称）。

    展示时**先查活的宿主**，查不到才回落到快照 —— 见 `light_serialize.py`。
    """

    kind: Optional[str]
    ref_id: Optional[int] = None
    ref_code: Optional[str] = None
    ref_label: Optional[str] = None
    user: Optional[str] = None

    def as_columns(self, prefix: str) -> dict[str, Any]:
        return {
            f"{prefix}_kind": self.kind,
            f"{prefix}_ref_id": self.ref_id,
            f"{prefix}_ref_code": self.ref_code,
            f"{prefix}_ref_label": self.ref_label,
            f"{prefix}_user": self.user,
        }


def position_key(ref: HolderRef) -> tuple:
    """把一处持有位置归一成可比较的键（定位 holdings 行 / 去重聚合时用）。"""
    if ref.kind == LightHolderKind.USER:
        return (ref.kind, None, None, (ref.user or "").strip())
    if ref.kind == LightHolderKind.STOCK:
        return (ref.kind, None, None, _norm_location(ref.stock_location))
    return (ref.kind, ref.ref_id, None, None)


def _norm_location(value: Optional[str]) -> Optional[str]:
    """库存位置归一："" 与 None 视为同一个"未填位置"。"""
    text = (value or "").strip()
    return text or None


def holding_ref(holding: LightHolding) -> HolderRef:
    """一条持有记录 → 它对应的持有位置。"""
    return HolderRef(
        kind=holding.holder_kind,
        ref_id=holding.asset_id if holding.holder_kind == LightHolderKind.ASSET else holding.workstation_id,
        user=holding.holder_user,
        stock_location=holding.stock_location,
    )


# --------------------------------------------------------------------------- #
# 快照：把资产 / 点位变成一份"当时长这样"的记录
# --------------------------------------------------------------------------- #
def asset_label(asset: Asset) -> str:
    """资产快照的名称：编号 · 品牌 型号（与审计页的口径一致）。"""
    spec = " ".join(x for x in (asset.brand, asset.model) if x)
    return f"{asset.asset_code} · {spec}" if spec else asset.asset_code


def build_holder_snapshot(db: Session, ref: HolderRef) -> HolderSnapshot:
    """把请求里的引用解析成可落库的快照。非法引用当场 400 / 404。

    ⚠️ 这里是"当前使用人 / 点位一律现读"的落点：只有**写流水的那一刻**才把
    编码与名称抄一份进快照，之后展示仍优先查活的宿主。
    """
    kind = ref.kind
    if kind is None:
        return HolderSnapshot(kind=None)

    if kind == LightHolderKind.STOCK:
        # 库存不指向任何宿主，但它**可能分放在多处**，重放时必须知道"这一笔进的是哪一处"。
        # 复用 `*_ref_code` 这一列承载存放位置 —— 它对 stock 本来就没有别的用途
        # （那两列是为 asset / workstation 的编码快照准备的），且 `*_ref_label` 留给展示。
        # 这样 `light_movements` 不需要新增列就能支持"库存分放多处"的重建。
        return HolderSnapshot(kind=kind, ref_code=_norm_location(ref.stock_location))

    if kind == LightHolderKind.USER:
        user = (ref.user or "").strip()
        # 持有方是"人"时必须是个真人 —— `/`、空串这些占位符不算人（决策 76/82 的口径）。
        # 这里复用 ownership.has_owner，绝不自己再写一次 strip 判断。
        if not user:
            raise HTTPException(status_code=400, detail="「使用人」不能为空")
        if not has_owner(user):
            raise HTTPException(
                status_code=400,
                detail=f"「{user}」不是有效的使用人（「/」这类占位符只表示「没填」，不能作为持有方）",
            )
        return HolderSnapshot(kind=kind, user=user)

    if kind == LightHolderKind.ASSET:
        if ref.ref_id is None:
            raise HTTPException(status_code=400, detail="「正式资产」必须指定资产")
        asset = db.get(Asset, ref.ref_id)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"资产 #{ref.ref_id} 不存在")
        return HolderSnapshot(kind=kind, ref_id=asset.id, ref_code=asset.asset_code, ref_label=asset_label(asset))

    if kind == LightHolderKind.WORKSTATION:
        if ref.ref_id is None:
            raise HTTPException(status_code=400, detail="「点位」必须指定点位")
        station = db.get(Workstation, ref.ref_id)
        if station is None:
            raise HTTPException(status_code=404, detail=f"点位 #{ref.ref_id} 不存在")
        if not station.active:
            raise HTTPException(status_code=409, detail=f"点位「{station.code}」已删除，不能作为去向")
        return HolderSnapshot(kind=kind, ref_id=station.id, ref_code=station.code, ref_label=station.code)

    raise HTTPException(status_code=400, detail=f"未知的引用类型「{kind}」")


def describe_ref(db: Session, ref: HolderRef) -> str:
    """一处持有位置的人话（报错文案用）。"""
    if ref.kind is None:
        return "系统外部"
    if ref.kind == LightHolderKind.STOCK:
        loc = _norm_location(ref.stock_location)
        return f"库存（{loc}）" if loc else "库存"
    if ref.kind == LightHolderKind.USER:
        return f"「{ref.user}」"
    if ref.kind == LightHolderKind.ASSET:
        asset = db.get(Asset, ref.ref_id) if ref.ref_id else None
        return f"主机「{asset.asset_code}」" if asset is not None else f"资产 #{ref.ref_id}"
    if ref.kind == LightHolderKind.WORKSTATION:
        station = db.get(Workstation, ref.ref_id) if ref.ref_id else None
        return f"点位「{station.code}」" if station is not None else f"点位 #{ref.ref_id}"
    return str(ref.kind)


# --------------------------------------------------------------------------- #
# 读：当前分配记录
# --------------------------------------------------------------------------- #
def current_holdings(db: Session, category_id: int) -> list[LightHolding]:
    """某品类的全部持有记录（数量 > 0，库存排最前，其余按类型 / 名称排）。"""
    rows = list(
        db.execute(
            select(LightHolding)
            .options(selectinload(LightHolding.category))
            .where(LightHolding.category_id == category_id)
        ).scalars().all()
    )
    rows = [r for r in rows if r.quantity > 0]
    kind_order = {
        LightHolderKind.STOCK: 0,
        LightHolderKind.USER: 1,
        LightHolderKind.ASSET: 2,
        LightHolderKind.WORKSTATION: 3,
    }
    rows.sort(
        key=lambda h: (
            kind_order.get(h.holder_kind, 9),
            (h.stock_location or ""),
            (h.holder_user or ""),
            (h.asset_id or 0),
            (h.workstation_id or 0),
        )
    )
    return rows


def find_holding(db: Session, category_id: int, ref: HolderRef) -> Optional[LightHolding]:
    """定位**某一处**持有记录。库存按存放位置区分（不同位置的库存是不同行）。"""
    key = position_key(ref)
    for row in current_holdings(db, category_id):
        if position_key(holding_ref(row)) == key:
            return row
    return None


def quantity_at(db: Session, category_id: int, ref: HolderRef) -> int:
    """某一处**当前真的握着**多少（没有就是 0）。"""
    if ref.kind is None:
        return 0
    if ref.kind == LightHolderKind.STOCK:
        rows = [r for r in current_holdings(db, category_id) if r.holder_kind == LightHolderKind.STOCK]
        loc = _norm_location(ref.stock_location)
        if loc is None:
            # 不指明位置时："未填位置的那一行"或"库存只有一处"才算得准，否则读总数
            unnamed = [r for r in rows if _norm_location(r.stock_location) is None]
            if unnamed:
                return sum(r.quantity for r in unnamed)
            if len(rows) == 1:
                return rows[0].quantity
            return sum(r.quantity for r in rows)
        return sum(r.quantity for r in rows if _norm_location(r.stock_location) == loc)
    row = find_holding(db, category_id, ref)
    return row.quantity if row is not None else 0


def resolve_stock_row(db: Session, category_id: int, ref: HolderRef) -> Optional[LightHolding]:
    """把"从库存发出"解析到**具体某一行**库存记录。

    - 指明了 `stock_location` → 找那一行；
    - 没指明 → 优先"未填位置"的那一行；只有一处库存时就用它；
      否则（分散在多处且没有一处是"未填"）**不猜**，返回 None，由调用方给出 409。
    """
    rows = [r for r in current_holdings(db, category_id) if r.holder_kind == LightHolderKind.STOCK]
    loc = _norm_location(ref.stock_location)
    if loc is not None:
        for r in rows:
            if _norm_location(r.stock_location) == loc:
                return r
        return None
    unnamed = [r for r in rows if _norm_location(r.stock_location) is None]
    if unnamed:
        return unnamed[0]
    if len(rows) == 1:
        return rows[0]
    return None


def stock_quantity(db: Session, category_id: int) -> int:
    """该品类当前**库存总量**（Σ 所有存放位置的 stock 记录）。"""
    return sum(
        r.quantity for r in current_holdings(db, category_id) if r.holder_kind == LightHolderKind.STOCK
    )


def allocated_quantity(db: Session, category_id: int) -> int:
    """该品类当前**已分配总量**（user / asset / workstation 三类的数量之和）。"""
    return sum(
        r.quantity for r in current_holdings(db, category_id) if r.holder_kind in LightHolderKind.HELD
    )


def category_has_movements(db: Session, category_id: int) -> bool:
    """品类有没有产生过业务流水（含历史导入）。

    依然保留：界面用它决定"这个品类有没有历史"（如是否要提示做期初确认）。
    """
    row = db.execute(
        select(LightMovement.id).where(LightMovement.category_id == category_id).limit(1)
    ).first()
    return row is not None


def imported_movement_count(db: Session, category_id: int) -> int:
    """该品类有多少条**历史导入**流水（只追溯、不计入当前状态）。"""
    return int(
        db.execute(
            select(func.count())
            .select_from(LightMovement)
            .where(LightMovement.category_id == category_id)
            .where(LightMovement.origin == LightOrigin.IMPORT)
        ).scalar_one()
    )


# --------------------------------------------------------------------------- #
# 派生统计（一次算完，别让前端自己拼，也别对每个品类重复查库）
# --------------------------------------------------------------------------- #
def category_summary(db: Session, category: LightCategory) -> dict[str, Any]:
    """台账每一行要的数字，全部**只读 `light_holdings`**（结构上不可能对不上）。

    第一版这里是"库存走流水聚合、持有明细走另一条除法再过滤负数"，两处口径不一致，
    实测出过"账面凭空多出 3 套"。现在两者都只读同一张表。
    """
    holdings = current_holdings(db, category.id)
    stock = sum(h.quantity for h in holdings if h.holder_kind == LightHolderKind.STOCK)
    allocated = sum(h.quantity for h in holdings if h.holder_kind in LightHolderKind.HELD)
    return {
        "stock": stock,
        "allocated": allocated,
        "net": stock + allocated,
        "imported_count": imported_movement_count(db, category.id),
        #: 有历史流水、当前账却是空的（典型：只导入了分配流水、没做期初确认）。
        #: 界面老实说"未登记入库"，**不猜一个数字** —— 这是诚实，不是缺陷（设计 §6.2）。
        "pending_opening": (not holdings) and category_has_movements(db, category.id),
        "holdings": holdings,
    }


# --------------------------------------------------------------------------- #
# 守恒断言
# --------------------------------------------------------------------------- #
def net_by_movements(db: Session, category_id: int) -> int:
    """按**有效流水**算出"净存在量 = 进入系统的量 − 离开系统的量"。"""
    stmt = (
        select(LightMovement)
        .where(LightMovement.category_id == category_id)
        .where(LightMovement.origin.in_(LightOrigin.EFFECTIVE))
        .order_by(LightMovement.occurred_at.asc(), LightMovement.id.asc())
    )
    total = 0
    for m in db.execute(stmt).scalars().all():
        if m.from_kind is None and m.to_kind is not None:
            total += m.quantity
        elif m.from_kind is not None and m.to_kind is None:
            total -= m.quantity
    return total


class ConservationError(RuntimeError):
    """守恒式被破坏 —— 不是业务错误，是代码 bug，必须整体回滚。"""


def assert_conservation(db: Session, category_id: int) -> None:
    """Σ holdings 必须等于按流水算出的净存在量。破了直接 raise（让事务回滚）。"""
    db.flush()
    holdings_total = sum(
        r.quantity for r in current_holdings(db, category_id)
    )
    net = net_by_movements(db, category_id)
    if holdings_total != net:
        raise ConservationError(
            f"轻资产守恒式被破坏（品类 #{category_id}）："
            f"holdings 合计 {holdings_total}，流水净存在量 {net}。已回滚这次写入。"
        )


# --------------------------------------------------------------------------- #
# 写：唯一入口 apply_holding_delta（holdings 只允许经它修改）
# --------------------------------------------------------------------------- #
def apply_holding_delta(
    db: Session,
    *,
    category_id: int,
    ref: HolderRef,
    delta: int,
    since: Optional[datetime] = None,
    note: Optional[str] = None,
) -> Optional[LightHolding]:
    """**唯一允许改 `light_holdings` 的函数。**

    普通对象里的 `ref` → 找到那一行；`delta > 0` 时不存在就新建，`delta < 0` 时
    减到 0 即删除该行（避免"0 件的持有记录"污染列表与统计）。

    `ref.kind is None`（外部 / 注销）不做任何事 —— 它不占任何位置。
    """
    if ref.kind is None:
        return None
    if ref.kind not in LightHolderKind.ALL:
        raise HTTPException(status_code=400, detail=f"未知的持有类型「{ref.kind}」")

    when = since or datetime.now()
    row = find_holding(db, category_id, ref)

    if row is None:
        if delta <= 0:
            # 走到这里说明校验漏了 —— 不允许出现负数持有
            raise ConservationError(
                f"尝试从不存在的持有位置扣减：{describe_ref(db, ref)}（品类 #{category_id}）"
            )
        row = LightHolding(
            category_id=category_id,
            holder_kind=ref.kind,
            asset_id=ref.ref_id if ref.kind == LightHolderKind.ASSET else None,
            workstation_id=ref.ref_id if ref.kind == LightHolderKind.WORKSTATION else None,
            holder_user=(ref.user or "").strip() or None if ref.kind == LightHolderKind.USER else None,
            stock_location=(
                _norm_location(ref.stock_location) if ref.kind == LightHolderKind.STOCK else None
            ),
            quantity=delta,
            since=when,
            note=note,
        )
        db.add(row)
        db.flush()
        return row

    new_quantity = row.quantity + delta
    if new_quantity < 0:
        raise ConservationError(
            f"持有数量会变成负数：{describe_ref(db, ref)} 现有 {row.quantity}，本次 {delta:+d}"
        )
    if new_quantity == 0:
        db.delete(row)
        db.flush()
        return None
    row.quantity = new_quantity
    if delta > 0:
        row.since = when
    if note:
        row.note = note
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def _require_positive(quantity: int) -> int:
    if quantity is None or quantity < 1:
        raise HTTPException(status_code=400, detail="数量必须是 1 或更大的整数")
    if quantity > 100000:
        raise HTTPException(status_code=400, detail="数量太大了，请检查是不是多打了一位")
    return quantity


def _check_flow(op: str, from_ref: HolderRef, to_ref: HolderRef) -> None:
    if op == LightOp.REPLACE:
        # 更换 = 一条业务动作、两条流水（报损 + 安装），它的 (from, to) 永远是 asset → asset
        if not (from_ref.kind == LightHolderKind.ASSET and to_ref.kind in (LightHolderKind.ASSET, None)):
            raise HTTPException(
                status_code=400,
                detail="「更换配件」只能作用在正式资产上：来源选那台机器，去向留空即可",
            )
        return
    if not LightOp.allows(op, from_ref.kind, to_ref.kind):
        allowed = "；".join(
            f"{LightHolderKind.LABELS.get(f, '外部')} → {LightHolderKind.LABELS.get(t, '外部')}"
            for f, t in LightOp.FLOWS.get(op, ())
        )
        got = f"{LightHolderKind.LABELS.get(from_ref.kind, '外部')} → {LightHolderKind.LABELS.get(to_ref.kind, '外部')}"
        raise HTTPException(
            status_code=400,
            detail=f"「{LightOp.label(op)}」不能从「{got}」这样走。允许的组合：{allowed}",
        )


def _ensure_request_unique(db: Session, request_id: Optional[str]) -> None:
    """防重复提交：同一个 request_id 只允许写一条。"""
    if not request_id:
        return
    exists = db.execute(select(LightMovement.id).where(LightMovement.request_id == request_id)).first()
    if exists is not None:
        raise HTTPException(
            status_code=409,
            detail="这次登记已经提交过了（同一次提交不会重复入账），请刷新页面查看结果",
        )


def _check_source_enough(db: Session, category: LightCategory, from_ref: HolderRef, quantity: int) -> None:
    """**任何"从某处发出"的操作，都要先问那儿真有这么多吗。**

    判据是 `from_kind` 是谁，不是 op 叫什么 —— 只有这样，分配 / 归还 / 转移 / 拆卸 /
    报损五条路径才共用同一道闸门。不足一律 409 + 人话，**禁止扣减**，
    不允许"先记成负数以后再补"（设计 §6.4）。
    """
    kind = from_ref.kind
    if kind is None:
        return

    unit = category.unit or "件"

    if kind == LightHolderKind.STOCK and _norm_location(from_ref.stock_location) is None:
        # 库存分放在多处、又没指明从哪一处发 —— **不猜**（猜错了扣的是别处的账）
        if resolve_stock_row(db, category.id, from_ref) is None and stock_quantity(db, category.id) > 0:
            rows = [r for r in current_holdings(db, category.id) if r.holder_kind == LightHolderKind.STOCK]
            places = "、".join(f"{(r.stock_location or '未填位置')}（{r.quantity}）" for r in rows)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"品类「{category.name}」的库存分放在多处（{places}），"
                    f"请指明从哪一处发出。"
                ),
            )

    held = quantity_at(db, category.id, from_ref)
    if held < quantity:
        held_text = f"{held} {unit}" if held > 0 else f"0 {unit}（没有持有记录）"
        raise HTTPException(
            status_code=409,
            detail=(
                f"{describe_ref(db, from_ref)}当前只有 {held_text}，"
                f"不能发出 {quantity} {unit}。"
                f"「归还 / 转移 / 报损」只能针对它实际持有的数量。"
            ),
        )


# --------------------------------------------------------------------------- #
# 写流水（holdings 的改动由调用方负责，见 record_operation / replace_asset_part）
# --------------------------------------------------------------------------- #
def _new_movement(
    db: Session,
    *,
    category: LightCategory,
    op: str,
    from_snapshot: HolderSnapshot,
    to_snapshot: HolderSnapshot,
    quantity: int,
    operator: Optional[str],
    note: Optional[str],
    occurred_at: Optional[datetime],
    origin: str,
    import_batch: Optional[str],
    request_id: Optional[str],
    corrects_id: Optional[int],
) -> LightMovement:
    movement = LightMovement(
        category_id=category.id,
        op=op,
        quantity=quantity,
        operator=(operator or "").strip() or None,
        occurred_at=occurred_at or datetime.now(),
        note=(note or "").strip() or None,
        origin=origin,
        import_batch=import_batch,
        request_id=(request_id or "").strip() or None,
        corrects_id=corrects_id,
        **from_snapshot.as_columns("from"),
        **to_snapshot.as_columns("to"),
    )
    db.add(movement)
    db.flush()
    return movement


def record_operation(
    db: Session,
    *,
    category: LightCategory,
    op: str,
    from_ref: Optional[HolderRef] = None,
    to_ref: Optional[HolderRef] = None,
    quantity: int = 1,
    operator: Optional[str] = None,
    note: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    origin: str = LightOrigin.MANUAL,
    request_id: Optional[str] = None,
    corrects_id: Optional[int] = None,
) -> LightMovement:
    """**唯一写入口**：登记一次业务动作。

    内部**同时**改 holdings、追加流水、留审计（审计由路由调 `record_audit`），
    并在最后断言守恒式 —— 任何一步抛错，整个事务回滚（不 commit）。

    ⚠️ 禁止"先写 holdings 再异步补流水"或"分两次 commit"。体检是**发现**问题的，
    不是**允许**问题的（设计 §4.2 决策 1 明文要求）。
    """
    from_ref = from_ref or HolderRef()
    to_ref = to_ref or HolderRef()

    if op not in LightOp.ALL:
        raise HTTPException(status_code=400, detail=f"未知的操作类型「{op}」")
    if op == LightOp.REPLACE:
        raise HTTPException(
            status_code=400,
            detail="「更换配件」请走专用入口（它一次写两条流水），不能当作普通流转登记",
        )
    if origin not in LightOrigin.ALL:
        raise HTTPException(status_code=400, detail=f"未知的来源「{origin}」")
    if origin == LightOrigin.IMPORT:
        raise HTTPException(status_code=400, detail="历史导入流水只能由导入接口写入，不能手工登记")

    quantity = _require_positive(quantity)
    _check_flow(op, from_ref, to_ref)
    _ensure_request_unique(db, request_id)
    _check_source_enough(db, category, from_ref, quantity)

    # 期初确认必须留操作人 —— 它建立的是"真实基线"，没人认领的基线等于没建立。
    if origin == LightOrigin.OPENING and not (operator or "").strip():
        raise HTTPException(status_code=400, detail="期初确认必须填写操作人（是谁确认的这份基线）")

    from_snapshot = build_holder_snapshot(db, from_ref)
    to_snapshot = build_holder_snapshot(db, to_ref)

    when = occurred_at or datetime.now()
    # ① 来源扣减 ② 去向增加 —— 就是"从 A 减 q、往 B 加 q"这一件事（设计 §3.1）
    apply_holding_delta(db, category_id=category.id, ref=from_ref, delta=-quantity, note=None)
    apply_holding_delta(db, category_id=category.id, ref=to_ref, delta=+quantity, since=when, note=None)

    movement = _new_movement(
        db,
        category=category,
        op=op,
        from_snapshot=from_snapshot,
        to_snapshot=to_snapshot,
        quantity=quantity,
        operator=operator,
        note=note,
        occurred_at=occurred_at,
        origin=origin,
        import_batch=None,
        request_id=request_id,
        corrects_id=corrects_id,
    )

    assert_conservation(db, category.id)
    return movement


def replace_asset_part(
    db: Session,
    *,
    category: LightCategory,
    asset_ref: HolderRef,
    quantity: int = 1,
    operator: Optional[str] = None,
    note: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    request_id: Optional[str] = None,
) -> list[LightMovement]:
    """**更换**：旧件报废 + 新件顶上，一次业务操作、两条流水，同一个事务。

    业务场景：B022 上那根 8G 内存坏了，换一根新的，人（和机器）都没变。

    1. `报损`：`asset(B022) −1`（旧件退出管理）
    2. `安装`：`stock −1 → asset(B022) +1`（新件装上）

    为什么不分两次让用户点：中间那一下如果只做了一半（旧件已报损、新件还没装），
    账上会短暂出现"这台机器少一根内存"，而实际并没有。一次事务提交，要么都成、要么都不成。
    """
    if asset_ref.kind != LightHolderKind.ASSET or asset_ref.ref_id is None:
        raise HTTPException(status_code=400, detail="「更换配件」必须先指明是哪台正式资产")
    quantity = _require_positive(quantity)
    _ensure_request_unique(db, request_id)

    asset_snapshot = build_holder_snapshot(db, asset_ref)
    stock_ref = HolderRef(kind=LightHolderKind.STOCK)
    if resolve_stock_row(db, category.id, stock_ref) is None and stock_quantity(db, category.id) <= 0:
        raise HTTPException(
            status_code=409,
            detail=f"品类「{category.name}」库存是 0，没有新件可换。请先入库，或改用「报损」把旧件退出。",
        )
    _check_source_enough(db, category, asset_ref, quantity)
    _check_source_enough(db, category, stock_ref, quantity)

    when = occurred_at or datetime.now()
    stock_snapshot = build_holder_snapshot(db, stock_ref)

    # ① 旧件报损（asset → 外部）
    apply_holding_delta(db, category_id=category.id, ref=asset_ref, delta=-quantity)
    out_movement = _new_movement(
        db, category=category, op=LightOp.SCRAP,
        from_snapshot=asset_snapshot, to_snapshot=HolderSnapshot(kind=None),
        quantity=quantity,
        operator=operator,
        note=(note or "").strip() or None or f"更换配件：旧件报损（{describe_ref(db, asset_ref)}）",
        occurred_at=occurred_at, origin=LightOrigin.MANUAL, import_batch=None,
        request_id=None, corrects_id=None,
    )
    # ② 新件装上（库存 → asset）
    apply_holding_delta(db, category_id=category.id, ref=stock_ref, delta=-quantity)
    apply_holding_delta(db, category_id=category.id, ref=asset_ref, delta=+quantity, since=when)
    in_movement = _new_movement(
        db, category=category, op=LightOp.ATTACH,
        from_snapshot=stock_snapshot, to_snapshot=asset_snapshot,
        quantity=quantity,
        operator=operator,
        note=(note or "").strip() or None or f"更换配件：新件装上（{describe_ref(db, asset_ref)}）",
        occurred_at=occurred_at, origin=LightOrigin.MANUAL, import_batch=None,
        request_id=request_id, corrects_id=None,
    )

    assert_conservation(db, category.id)
    return [out_movement, in_movement]


def apply_opening(
    db: Session,
    *,
    category: LightCategory,
    entries: Sequence[tuple[HolderRef, int]],
    operator: Optional[str],
    note: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> list[LightMovement]:
    """**期初确认**：按实际盘点结果一次性登记多条基线（设计 §6.1）。

    - 直接建立 `light_holdings`（库存 X 件、张三 Y 件、B022 上 Z 件…）；
    - 同时写对应的流水（`origin='opening'`），基线本身也可审计、也能被重建；
    - **必须填操作人**；
    - **允许补录 / 重做**（漏录了品类、盘点发现数量不对都可以再来一次）——
      `schema_meta` 里的标记只是"提示用"，不是"禁止再录入"的开关。
    """
    if not (operator or "").strip():
        raise HTTPException(status_code=400, detail="期初确认必须填写操作人（是谁确认的这份基线）")
    if not entries:
        raise HTTPException(status_code=400, detail="期初确认至少要有一条：这个品类现在有多少、都在哪")

    movements: list[LightMovement] = []
    when = occurred_at or datetime.now()
    for ref, quantity in entries:
        if ref.kind is None:
            raise HTTPException(status_code=400, detail="期初确认的去向不能是「系统外部」")
        qty = _require_positive(quantity)
        snapshot = build_holder_snapshot(db, ref)
        apply_holding_delta(db, category_id=category.id, ref=ref, delta=+qty, since=when)
        movements.append(
            _new_movement(
                db, category=category, op=LightOp.ADJUST,
                from_snapshot=HolderSnapshot(kind=None), to_snapshot=snapshot,
                quantity=qty, operator=operator,
                note=(note or "").strip() or "期初确认（按实际盘点结果建立基线）",
                occurred_at=occurred_at, origin=LightOrigin.OPENING, import_batch=None,
                request_id=None, corrects_id=None,
            )
        )
    assert_conservation(db, category.id)
    return movements


def record_import_row(
    db: Session,
    *,
    category: LightCategory,
    op: str,
    from_ref: HolderRef,
    to_ref: HolderRef,
    quantity: int,
    operator: Optional[str] = None,
    note: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    import_batch: Optional[str] = None,
    natural_key: Optional[str] = None,
) -> Optional[LightMovement]:
    """**历史导入**：只写 `light_movements`，**绝不碰 `light_holdings`**（设计 §6.2）。

    它不计入任何当前状态（不参与库存、不参与已分配、不参与守恒式）。
    天然去重：把自然键算成 `request_id`（唯一索引），同一份 Excel 导两遍第二遍 0 新增。

    返回 None 表示这一行已经导过（幂等跳过）。
    """
    if op not in LightOp.ALL or op == LightOp.REPLACE:
        raise HTTPException(status_code=400, detail=f"历史导入不支持的操作「{op}」")
    qty = _require_positive(quantity)
    _check_flow(op, from_ref, to_ref)

    request_id = None
    if natural_key:
        request_id = f"import:{natural_key}"
        already = db.execute(
            select(LightMovement.id).where(LightMovement.request_id == request_id)
        ).first()
        if already is not None:
            return None

    return _new_movement(
        db, category=category, op=op,
        from_snapshot=build_holder_snapshot(db, from_ref),
        to_snapshot=build_holder_snapshot(db, to_ref),
        quantity=qty, operator=operator, note=note, occurred_at=occurred_at,
        origin=LightOrigin.IMPORT, import_batch=import_batch,
        request_id=request_id, corrects_id=None,
    )


def replay_holdings(db: Session, category_id: int) -> dict[tuple, int]:
    """按**有效流水**重放，算出"此刻应有的 holdings"（体检 / 重建共用的唯一实现）。

    排除 `import`；冲正流水参与；排序 `occurred_at ASC, id ASC`。
    """
    stmt = (
        select(LightMovement)
        .where(LightMovement.category_id == category_id)
        .where(LightMovement.origin.in_(LightOrigin.EFFECTIVE))
        .order_by(LightMovement.occurred_at.asc(), LightMovement.id.asc())
    )
    buckets: dict[tuple, int] = {}
    for m in db.execute(stmt).scalars().all():
        for side, sign in (("from", -1), ("to", +1)):
            kind = getattr(m, f"{side}_kind")
            if kind not in LightHolderKind.ALL:
                continue
            ref = HolderRef(
                kind=kind,
                ref_id=getattr(m, f"{side}_ref_id"),
                user=getattr(m, f"{side}_user"),
                # stock 的存放位置就存在 `*_ref_code` 里（写流水时同一口径）
                stock_location=(
                    _norm_location(getattr(m, f"{side}_ref_code"))
                    if kind == LightHolderKind.STOCK
                    else None
                ),
            )
            key = position_key(ref)
            buckets[key] = buckets.get(key, 0) + sign * m.quantity
    return {k: v for k, v in buckets.items() if v != 0}


# --------------------------------------------------------------------------- #
# 按宿主查关联配件（资产详情 / 点位详情）
# --------------------------------------------------------------------------- #
def holdings_by_asset(db: Session, asset_id: int) -> list[LightHolding]:
    """装在这台正式资产上的配件（走索引，不重放流水）。"""
    rows = list(
        db.execute(
            select(LightHolding)
            # 序列化要读品类名（"装的到底是什么东西"），一次带回，别逐行懒加载
            .options(selectinload(LightHolding.category))
            .where(LightHolding.holder_kind == LightHolderKind.ASSET)
            .where(LightHolding.asset_id == asset_id)
        ).scalars().all()
    )
    return [r for r in rows if r.quantity > 0]


#: 点位轻资产的两个分组名。**收成常量**：地图徽标与点位侧栏都要按这两个键取值，
#: 各写一遍字符串迟早会漂成 `via_asset` / `viaAssets` 这种"看着对但取不到"的样子。
STATION_LIGHT_GROUPS: tuple[str, str] = ("direct", "via_assets")


def _station_light_rows(
    db: Session, station_ids: Sequence[int]
) -> dict[int, dict[str, list[LightHolding]]]:
    """按点位分组取出「这个点位上看得见的轻资产行」，**一次查完**（避免列表页 N+1）。

    - `direct`：`holder_kind='workstation'` 且 `workstation_id=本点位` —— 东西就放在这个工位上；
    - `via_assets`：`holder_kind='asset'` 且该资产的**当前** `workstation_id=本点位` ——
      东西装在那台主机上，主机正好在这个工位。

    ⚠️ **这是地图徽标（`station_light_counts`）与点位侧栏（`holdings_by_workstation`）
    唯一的取数实现。** 两处只允许在这一个函数上投影出不同的形状，**绝不允许各写一遍查询** ——
    徽标数字与侧栏那两组的数量之和一旦对不上，用户没有任何办法判断该信哪个：
    地图说"这个位置有 3 件"、点进去一个也没有，两边都"看着像对的"。

    ⚠️ **两组只是展示分组，不创建任何额外持有记录**：随主机组的配件在 `light_holdings`
    里**只有一条** `holder_kind='asset'` 的记录，点位这一层**不落任何数据**。
    主机换点位，这一组自动跟着变（查询时算出来）—— 这就是"不重复位置关系"的落点。

    ⚠️ **只算 `quantity > 0` 的有效持有记录**：数量归零的记录仍留在表里（流水要完整），
    但它不代表"这个位置上有东西"。归零判定放在 SQL 里而不是取回来再过滤 ——
    否则一个点位 n 条历史零行会被搬进内存。
    """
    ids = list(station_ids)
    if not ids:
        return {}

    direct_rows = list(
        db.execute(
            select(LightHolding)
            .options(selectinload(LightHolding.category))
            .where(LightHolding.holder_kind == LightHolderKind.WORKSTATION)
            .where(LightHolding.workstation_id.in_(ids))
            .where(LightHolding.quantity > 0)
        ).scalars().all()
    )
    # 随主机的那一组要连**宿主点位**一起取回来 —— `LightHolding` 上只有 `asset_id`，
    # 拿不到分组依据（这正是"点位这一层不落数据"的代价，也是它值得的地方）。
    via_pairs = db.execute(
        select(LightHolding, Asset.workstation_id)
        .options(selectinload(LightHolding.category))
        .join(Asset, Asset.id == LightHolding.asset_id)
        .where(LightHolding.holder_kind == LightHolderKind.ASSET)
        .where(Asset.workstation_id.in_(ids))
        .where(LightHolding.quantity > 0)
    ).all()

    out: dict[int, dict[str, list[LightHolding]]] = {
        sid: {"direct": [], "via_assets": []} for sid in ids
    }
    for row in direct_rows:
        # `workstation_id.in_(ids)` 已经保证它在 keys 里，且不可能为 NULL
        out[row.workstation_id]["direct"].append(row)
    for holding, sid in via_pairs:
        out[sid]["via_assets"].append(holding)
    return out


def holdings_by_workstation(db: Session, station_id: int) -> dict[str, list[LightHolding]]:
    """某个点位上"看得到的"轻资产，**分两组返回**（设计 §5.4）。

    口径与分组含义见 `_station_light_rows` —— 本函数只是它取单个点位时的**行投影**。
    """
    groups = _station_light_rows(db, [station_id])
    return groups.get(station_id, {"direct": [], "via_assets": []})


def station_light_counts(
    db: Session, station_ids: Sequence[int]
) -> dict[int, dict[str, int]]:
    """{点位 ID: {"direct": 件数, "via_assets": 件数}} —— 地图徽标用（设计 §5.5 第一期）。

    数字是**数量之和**（`Σquantity`），不是记录条数 ——
    与点位侧栏标题的「轻资产（N）」**完全同一个算法**（那里也是 Σquantity，见
    `MapStationPanel.lightSummary`）。徽标因此天然等于"点进去之后看到的那个 N"。

    ⚠️ **不含库存**：`holder_kind='stock'` 的东西还没分配到任何点位，
    即使 `stock_location` 自由文本里写着点位编码（`'W23'`）也不算 —— 那是**人写的备注**，
    不是位置关系（设计 §5.5）。乐观地把它算进来只会让地图与侧栏互相打脸。

    ⚠️ **不发合计**：只给 `direct` / `via_assets` 两个数，让消费方自己相加 ——
    多一个 `total` 字段就多一个"两处算法不一致"的机会，而地图与侧栏的
    "两页数字相等"正是这一页的验收标准。
    """
    groups = _station_light_rows(db, station_ids)
    return {
        sid: {
            "direct": sum(h.quantity for h in group["direct"]),
            "via_assets": sum(h.quantity for h in group["via_assets"]),
        }
        for sid, group in groups.items()
    }


def link_suggestions_for_asset(db: Session, asset_id: int) -> dict[str, Any]:
    """报废前 / 点位删除前的关联影响预检用的数据（设计 §5.6 / §7.2）。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        return {"direct": [], "via_assets": [], "light": []}
    light = holdings_by_asset(db, asset_id)
    return {
        "asset_code": asset.asset_code,
        "asset_status": asset.status,
        "asset_status_label": AssetStatus.LABELS.get(asset.status, asset.status),
        "light": light,
    }


__all__ = [
    "ConservationError",
    "HolderRef",
    "HolderSnapshot",
    "allocated_quantity",
    "apply_holding_delta",
    "apply_opening",
    "asset_label",
    "assert_conservation",
    "build_holder_snapshot",
    "category_has_movements",
    "category_summary",
    "current_holdings",
    "describe_ref",
    "find_holding",
    "holding_ref",
    "holdings_by_asset",
    "holdings_by_workstation",
    "imported_movement_count",
    "link_suggestions_for_asset",
    "net_by_movements",
    "position_key",
    "quantity_at",
    "record_import_row",
    "record_operation",
    "replace_asset_part",
    "replay_holdings",
    "resolve_stock_row",
    "station_light_counts",
    "stock_quantity",
    "STATION_LIGHT_GROUPS",
]
