"""轻资产**历史导入** —— 只补过去的流水，**绝不碰当前持有**（设计 §6.2）。

## 为什么必须和"期初确认"分开

Excel 里通常只有"某年某月把这套键鼠给了张三"，**没有对应的入库记录**。
如果把这些直接当当前状态，会得到负库存或凭空出现的持有量（上一轮实测出来过）。

所以：

> 历史导入后，当前状态仍是空的（除非另外做了期初确认），
> 台账上显示"未登记入库"而不是猜一个数字。**这是诚实，不是缺陷。**

流程骨架照抄 `services/importer.py`（"预览与落库跑同一段 plan"、"四桶统计"、
真实行号、BOM 兜底），只是把车换成轻资产这一套数据。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, LightCategory, LightOp, LightOrigin, Workstation
from . import light as light_service
from .light import HolderRef
from .ownership import has_owner
from .spreadsheet import read_rows

ACTION_IMPORT = "import"
ACTION_DUPLICATE = "duplicate"
ACTION_ERROR = "error"

_ACTION_LABELS = {
    ACTION_IMPORT: "将导入",
    ACTION_DUPLICATE: "已存在（跳过）",
    ACTION_ERROR: "有问题",
}

#: 表头别名 → 规范列名。中文版 Excel 各种叫法都得认，否则用户"另存为 CSV"就全错位。
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "category": ("品类", "品名", "轻资产品类", "category"),
    "op": ("操作", "动作", "业务动作", "op"),
    "quantity": ("数量", "件数", "quantity"),
    "from_kind": ("来源类型", "来源类别", "from_kind"),
    "from_value": ("来源", "来源详情", "from"),
    "to_kind": ("去向类型", "去向类别", "to_kind"),
    "to_value": ("去向", "去向详情", "to"),
    "occurred_at": ("发生日期", "日期", "时间", "occurred_at"),
    "note": ("备注", "说明", "note"),
    "operator": ("操作人", "经办人", "operator"),
}

_REQUIRED = ("category", "op", "from_kind", "to_kind")

#: 操作类型的中文 / 英文写法
_OP_ALIASES: dict[str, str] = {
    "入库": LightOp.STOCK_IN, "入库登记": LightOp.STOCK_IN, "stock_in": LightOp.STOCK_IN,
    "分配": LightOp.ALLOCATE, "发给个人": LightOp.ALLOCATE, "allocate": LightOp.ALLOCATE,
    "归还": LightOp.RETURN, "收回": LightOp.RETURN, "return": LightOp.RETURN,
    "安装": LightOp.ATTACH, "装到资产": LightOp.ATTACH, "attach": LightOp.ATTACH,
    "拆卸": LightOp.DETACH, "detach": LightOp.DETACH,
    "转移": LightOp.TRANSFER, "换手": LightOp.TRANSFER, "transfer": LightOp.TRANSFER,
    "报损": LightOp.SCRAP, "报废": LightOp.SCRAP, "scrap": LightOp.SCRAP,
    "调整": LightOp.ADJUST, "盘盈盘亏": LightOp.ADJUST, "adjust": LightOp.ADJUST,
}

_KIND_ALIASES: dict[str, Optional[str]] = {
    "库存": "stock", "库房": "stock", "stock": "stock",
    "使用人": "user", "人": "user", "个人": "user", "user": "user",
    "正式资产": "asset", "资产": "asset", "机器": "asset", "主机": "asset", "asset": "asset",
    "点位": "workstation", "工位": "workstation", "workstation": "workstation",
    "外部": None, "系统外部": None, "注销": None, "无": None, "空": None, "none": None, "": None,
}

_CLEAR_TOKENS = frozenset({"-", "--", "—", "空", "(空)", "（空）", "null", "none", "nil"})


@dataclass
class RowPlan:
    row: int
    action: str
    message: str = ""
    summary: str = ""
    payload: Optional[dict[str, Any]] = None

    @property
    def action_label(self) -> str:
        return _ACTION_LABELS.get(self.action, self.action)


@dataclass
class ImportPlan:
    filename: str
    schema_note: Optional[str] = None
    rows: list[RowPlan] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    def count(self, action: str) -> int:
        return sum(1 for r in self.rows if r.action == action)


@dataclass
class ImportOutcome:
    imported: int = 0
    duplicate: int = 0
    error: int = 0
    batch: str = ""


def _header_index(headers: list[str]) -> tuple[dict[str, int], list[str]]:
    index: dict[str, int] = {}
    lowered = [h.strip().lower() for h in headers]
    for key, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            target = alias.lower()
            if target in lowered:
                index[key] = lowered.index(target)
                break
    missing = [k for k in _REQUIRED if k not in index]
    return index, missing


def _cell(cells: list[str], index: dict[str, int], key: str) -> str:
    pos = index.get(key)
    if pos is None or pos >= len(cells):
        return ""
    return (cells[pos] or "").strip()


def _is_clear(text: str) -> bool:
    return text.strip().lower() in _CLEAR_TOKENS


def _norm(text: str) -> str:
    return "" if _is_clear(text) else text.strip()


def parse_when(text: str) -> tuple[Optional[datetime], Optional[str]]:
    """把"发生日期"单元格解析成 datetime。空 → 今天（0 点）。"""
    raw = _norm(text)
    if not raw:
        return datetime.combine(date.today(), datetime.min.time()), None
    cleaned = raw.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    cleaned = cleaned.split(" ")[0].strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y.%m.%d"):
        try:
            return datetime.strptime(cleaned, fmt), None
        except ValueError:
            continue
    return None, f"看不懂的日期「{raw}」（建议写成 2026-03-12）"


def _natural_key(
    category_id: int, op: str, quantity: int, from_desc: str, to_desc: str, when: datetime
) -> str:
    raw = f"{category_id}|{op}|{quantity}|{from_desc}|{to_desc}|{when.isoformat(timespec='minutes')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def _resolve_holder(
    db: Session, kind: Optional[str], value: str
) -> tuple[Optional[HolderRef], Optional[str]]:
    """把一行里的"来源类型 + 来源"解析成一个持有位置。"""
    if kind is None:
        return HolderRef(), None
    text = _norm(value)
    if kind == "stock":
        return HolderRef(kind="stock", stock_location=text or None), None
    if kind == "user":
        if not text:
            return None, "去向 / 来源是「使用人」时必须填名字"
        if not has_owner(text):
            return None, f"「{text}」不像一个使用人（「/」这类占位符不算人）"
        return HolderRef(kind="user", user=text), None
    if kind == "asset":
        if not text:
            return None, "是「正式资产」时必须填资产编号"
        asset = db.execute(
            select(Asset).where(Asset.asset_code == text).limit(1)
        ).scalars().first()
        if asset is None:
            return None, f"找不到资产编号「{text}」"
        return HolderRef(kind="asset", ref_id=asset.id), None
    if kind == "workstation":
        if not text:
            return None, "是「点位」时必须填点位编码"
        station = db.execute(
            select(Workstation).where(Workstation.code == text).limit(1)
        ).scalars().first()
        if station is None:
            return None, f"找不到点位编码「{text}」"
        return HolderRef(kind="workstation", ref_id=station.id), None
    return None, f"看不懂的类型「{kind}」"


def _holder_desc(ref: HolderRef) -> str:
    if ref.kind is None:
        return ""
    if ref.kind == "stock":
        return (ref.stock_location or "").strip()
    if ref.kind == "user":
        return (ref.user or "").strip()
    return f"#{ref.ref_id}"


def build_plan(db: Session, content: bytes, filename: str) -> ImportPlan:
    """解析上传文件 → 逐行判定（预览与落库跑的是**同一段**逻辑）。"""
    plan = ImportPlan(filename=filename or "light-history.csv")
    try:
        headers, rows = read_rows(content, filename or "")
    except Exception as exc:  # noqa: BLE001 —— 打不开的文件不该 500
        plan.schema_note = f"这个文件读不出来：{exc}"
        return plan
    if not headers:
        plan.schema_note = "文件是空的（第一行应当是表头）"
        return plan

    index, missing = _header_index(headers)
    if missing:
        names = "、".join(_HEADER_ALIASES[k][0] for k in missing)
        plan.schema_note = f"表头缺少必需的列：{names}（现有表头：{'、'.join(headers)}）"
        return plan

    categories = {c.name: c for c in db.execute(select(LightCategory)).scalars().all()}
    seen_keys: set[str] = set()

    for row_no, cells in rows:
        raw_category = _cell(cells, index, "category")
        raw_op = _cell(cells, index, "op")
        raw_from_kind = _cell(cells, index, "from_kind")
        raw_to_kind = _cell(cells, index, "to_kind")
        raw_qty = _cell(cells, index, "quantity")
        raw_when = _cell(cells, index, "occurred_at")
        note = _norm(_cell(cells, index, "note"))
        operator = _norm(_cell(cells, index, "operator"))

        if not raw_category and not raw_op and not raw_from_kind and not raw_to_kind:
            continue  # 整行空白（Excel 底下常有）

        category = categories.get(raw_category.strip())
        if category is None:
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"找不到品类「{raw_category}」"))
            continue

        op = _OP_ALIASES.get(raw_op.strip().lower()) or _OP_ALIASES.get(raw_op.strip())
        if op is None:
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"看不懂的操作「{raw_op}」", summary=category.name))
            continue

        from_kind = _KIND_ALIASES.get((raw_from_kind or "").strip().lower(), "__bad__")
        if from_kind == "__bad__":
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"看不懂的来源类型「{raw_from_kind}」", summary=category.name))
            continue
        to_kind = _KIND_ALIASES.get((raw_to_kind or "").strip().lower(), "__bad__")
        if to_kind == "__bad__":
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"看不懂的去向类型「{raw_to_kind}」", summary=category.name))
            continue

        try:
            quantity = int(float(raw_qty)) if raw_qty else 1
        except ValueError:
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"数量「{raw_qty}」不是整数", summary=category.name))
            continue
        if quantity < 1:
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"数量 {quantity} 不能小于 1", summary=category.name))
            continue

        when, when_err = parse_when(raw_when)
        if when is None:
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=when_err or "日期有问题", summary=category.name))
            continue

        from_ref, err = _resolve_holder(db, from_kind, _cell(cells, index, "from_value"))
        if err:
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"来源：{err}", summary=category.name))
            continue
        to_ref, err = _resolve_holder(db, to_kind, _cell(cells, index, "to_value"))
        if err:
            plan.rows.append(RowPlan(row_no, ACTION_ERROR, message=f"去向：{err}", summary=category.name))
            continue

        assert from_ref is not None and to_ref is not None
        if not LightOp.allows(op, from_ref.kind, to_ref.kind):
            plan.rows.append(
                RowPlan(
                    row_no,
                    ACTION_ERROR,
                    message=f"「{LightOp.label(op)}」不允许从「{raw_from_kind}」到「{raw_to_kind}」",
                    summary=category.name,
                )
            )
            continue

        key = _natural_key(
            category.id, op, quantity,
            f"{from_ref.kind}:{_holder_desc(from_ref)}",
            f"{to_ref.kind}:{_holder_desc(to_ref)}",
            when,
        )
        summary = (
            f"{LightOp.label(op)} {quantity} {category.unit}："
            f"{from_kind or '外部'} → {to_kind or '外部'}"
            f"（{when.date().isoformat()}）"
        )
        if key in seen_keys:
            plan.rows.append(RowPlan(row_no, ACTION_DUPLICATE, message="这份文件里前面已经有一模一样的行了", summary=summary))
            continue
        seen_keys.add(key)

        plan.rows.append(
            RowPlan(
                row_no,
                ACTION_IMPORT,
                summary=summary,
                payload={
                    "category_id": category.id,
                    "op": op,
                    "from_ref": from_ref,
                    "to_ref": to_ref,
                    "quantity": quantity,
                    "occurred_at": when,
                    "note": note or None,
                    "operator": operator or None,
                    "natural_key": key,
                },
            )
        )
    return plan


def apply_plan(db: Session, plan: ImportPlan, *, operator: Optional[str] = None) -> ImportOutcome:
    """落库。**只写 light_movements，绝不碰 light_holdings。**

    一次导入共用同一个批次号；幂等去重靠自然键（写进 `request_id` 的唯一索引）。
    """
    batch = datetime.now().strftime("light-%Y%m%d%H%M%S")
    outcome = ImportOutcome(batch=batch)
    categories = {c.id: c for c in db.execute(select(LightCategory)).scalars().all()}

    for row in plan.rows:
        if row.action == ACTION_ERROR:
            outcome.error += 1
            continue
        if row.action == ACTION_DUPLICATE:
            outcome.duplicate += 1
            continue
        payload = row.payload or {}
        category = categories.get(payload["category_id"])
        if category is None:
            outcome.error += 1
            continue
        movement = light_service.record_import_row(
            db,
            category=category,
            op=payload["op"],
            from_ref=payload["from_ref"],
            to_ref=payload["to_ref"],
            quantity=payload["quantity"],
            operator=payload["operator"] or operator,
            note=payload["note"] or "历史导入",
            occurred_at=payload["occurred_at"],
            import_batch=batch,
            natural_key=payload["natural_key"],
        )
        if movement is None:
            outcome.duplicate += 1
        else:
            outcome.imported += 1
    return outcome


__all__ = [
    "ACTION_DUPLICATE",
    "ACTION_ERROR",
    "ACTION_IMPORT",
    "ImportOutcome",
    "ImportPlan",
    "RowPlan",
    "apply_plan",
    "build_plan",
]
