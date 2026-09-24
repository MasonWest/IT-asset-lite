"""资产批量导入。

## 整个流程只有一条代码路径

预览和落库都走 `build_plan()`，区别只是预览拿到 plan 就不往下走了。
好处是没有"预览时说没问题、一点确认就报错"这种事 ——
两遍跑的是同一段校验代码，读的也是同一个文件。

（不用「服务端暂存解析结果 + token」那套：多一份状态就多一类 bug ——
token 过期、文件换了、两个人同时导入互相覆盖。文件本来就在用户浏览器里，
让他把同一个 File 再传一次，代价是一次几十毫秒的解析，换来的是确定性。）

## 空值语义（这是导入功能最容易做错的地方）

更新已有设备时，**空单元格 = 不修改**，不是"清空"。

理由：现实中批量改数据最常见的场景是「拿一份只有两列的表，把 30 台机器的使用人刷一遍」。
如果空值等于清空，这一下就把品牌、型号、SN、位置、日期全抹了 —— 而且不可撤销。
反过来，round-trip（导出 → 改 → 再导入）时用户只是想清掉某个字段，
那就在那个单元格里写一个 `-`，语义一样干净。

所以规则是：空 = 不动；`-`（或 空 / null 等几个同义词）= 清成空值。
新增时两者等价，都是空值。

## 为什么新增/更新/无变化要分成三个桶

少了「无变化」这一桶，把同一份文件导第二遍（选「新增或更新」）时，
界面会告诉你"更新了 10 条"——其实一个字节都没变。这种谎报会让用户
再也无法判断导入到底有没有生效。所以如实报 0 新增 / 0 更新 / 10 无变化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Asset, AssetEventType, AssetStatus, DeviceCategory, DeviceType
from . import spreadsheet
from .events import FIELD_LABELS, record_event

# --------------------------------------------------------------------------- #
# 模板列定义 —— 模板、导出、导入校验三处共用这一份，绝不会出现"导出的列导不回来"
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Column:
    key: str
    header: str
    required: bool = False
    width: int = 18
    hint: str = ""
    sample: str = ""


ASSET_COLUMNS: tuple[Column, ...] = (
    Column("asset_code", "IT资产编号", required=True, width=16,
           hint="必填，且全库唯一，用来判断这条是新增还是更新", sample="IT-2026-1001"),
    Column("finance_code", "财务编码", width=16, hint="财务台账里的编码", sample="FIN-2026-2001"),
    Column("device_type_name", "设备类型", required=True, width=12,
           hint="必填。写「主机」「显示器」「笔记本」等，不存在时可选择自动创建", sample="主机"),
    Column("brand", "品牌", width=12, sample="联想"),
    Column("model", "型号", width=26, sample="ThinkCentre M720t"),
    Column("serial_number", "序列号SN", width=20, sample="PC0L8X2B"),
    Column("status", "状态", width=10,
           hint="在用 / 在库 / 维修中 / 已报废，留空按「在用」处理", sample="在用"),
    Column("user_name", "使用人", width=12, sample="张伟"),
    Column("location", "存放位置", width=24, sample="总部 3F 研发区 A-01"),
    Column("purchase_date", "购入日期", width=14, hint="格式 2026-01-02", sample="2026-01-02"),
    Column("warranty_until", "保修到期日", width=14, hint="格式 2027-01-01", sample="2027-01-01"),
    Column("notes", "备注", width=30, sample="批量导入的测试机"),
)

#: 只在导出时追加的列。导入时读到了也直接忽略 ——
#: 这样「导出 → 改 → 再导入」不用先把这一列删掉。
PAIRING_COLUMN = Column("paired_monitors", "配对显示器", width=28,
                        hint="只读参考，导入时忽略")

#: 空值标记。写这些字符等于「把这个字段清空」，而不是「不修改」。
CLEAR_TOKENS = frozenset({"-", "--", "—", "空", "(空)", "（空）", "null", "none", "nil"})

STATUS_ALIASES: dict[str, str] = {
    "在用": AssetStatus.IN_USE, "使用中": AssetStatus.IN_USE, "in_use": AssetStatus.IN_USE,
    "在库": AssetStatus.IDLE, "闲置": AssetStatus.IDLE, "库存": AssetStatus.IDLE, "idle": AssetStatus.IDLE,
    "维修中": AssetStatus.REPAIR, "送修": AssetStatus.REPAIR, "维修": AssetStatus.REPAIR,
    "repair": AssetStatus.REPAIR,
    "已报废": AssetStatus.SCRAPPED, "报废": AssetStatus.SCRAPPED, "scrapped": AssetStatus.SCRAPPED,
}

MAX_LENGTHS: dict[str, int] = {
    "asset_code": 64,
    "finance_code": 64,
    "brand": 64,
    "model": 128,
    "serial_number": 128,
    "user_name": 64,
    "location": 128,
}

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_UNCHANGED = "unchanged"
ACTION_ERROR = "error"

ACTION_LABELS: dict[str, str] = {
    ACTION_CREATE: "新增",
    ACTION_UPDATE: "更新",
    ACTION_UNCHANGED: "无变化",
    ACTION_ERROR: "错误",
}

MODE_CREATE_ONLY = "create_only"
MODE_UPSERT = "upsert"
MODES: dict[str, str] = {MODE_CREATE_ONLY: "仅新增", MODE_UPSERT: "新增或更新"}

#: 这几个字段有专门的解析逻辑，通用「文本字段」循环必须跳过它们。
#: 跳不过去的话会有两种暗病：日期会被先塞成字符串、校验失败时那个字符串还会被写进库；
#: 状态同理 —— 写了个非法值，报错之后原始字符串已经躺在 payload 里等着落库了。
_SPECIAL_KEYS = frozenset({"asset_code", "device_type_name", "status", "purchase_date", "warranty_until"})

_STATUS_COLUMN = Column("status", "状态")
_TYPE_COLUMN = Column("device_type_name", "设备类型")
_DATE_KEYS = ("purchase_date", "warranty_until")


# --------------------------------------------------------------------------- #
# 取值与归一化
# --------------------------------------------------------------------------- #
def _is_clear_token(text: str) -> bool:
    return text.strip().lower() in CLEAR_TOKENS


def _text(raw: dict[str, str], column: Column) -> tuple[Optional[str], bool]:
    """返回 (值, 是否显式清空)。空单元格 → (None, False)。"""
    value = (raw.get(column.key) or "").strip()
    if not value:
        return None, False
    if _is_clear_token(value):
        return None, True
    return value, False


def parse_date_cell(value: str) -> tuple[Optional[date], Optional[str]]:
    """日期解析。Excel 里日期被读成字符串后会带一截时间，先切掉再试几种格式。"""
    text = (value or "").strip()
    if not text:
        return None, None
    normalized = text.replace("T", " ").split(" ")[0]
    normalized = normalized.replace("年", "-").replace("月", "-").replace("日", "")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"):
        try:
            return datetime.strptime(normalized, fmt).date(), None
        except ValueError:
            continue
    return None, f"日期格式无法识别：{text}（请写成 2026-01-02 这样的格式）"


def parse_status_cell(value: str) -> tuple[Optional[str], Optional[str]]:
    text = (value or "").strip()
    if not text:
        return None, None
    key = STATUS_ALIASES.get(text) or STATUS_ALIASES.get(text.lower())
    if key is None:
        allowed = "、".join(AssetStatus.LABELS.values())
        return None, f"状态「{text}」无法识别，只能是：{allowed}"
    return key, None


# --------------------------------------------------------------------------- #
# 计划（预览 = build_plan 后丢弃；落库 = build_plan 后 apply_plan）
# --------------------------------------------------------------------------- #
@dataclass
class RowPlan:
    row: int
    asset_code: str = ""
    action: str = ACTION_CREATE
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    #: 归一化后的字段（不含 asset_code，它是匹配键）
    payload: dict[str, Any] = field(default_factory=dict)
    asset_id: Optional[int] = None
    #: 库里已有的那条，用于「更新」时给出前后对照
    existing_label: str = ""

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)


@dataclass
class ImportPlan:
    filename: str
    mode: str
    rows: list[RowPlan] = field(default_factory=list)
    #: 文件里出现但不认识的列（被忽略）
    unknown_headers: list[str] = field(default_factory=list)
    #: 模板里有、但文件里没有的列
    missing_headers: list[str] = field(default_factory=list)
    #: 缺了就整个文件没意义的列
    missing_required: list[str] = field(default_factory=list)
    #: 需要自动创建的设备类型
    auto_types: list[str] = field(default_factory=list)
    #: 文件里一行数据都没有（只有表头）
    empty: bool = False

    @property
    def total(self) -> int:
        return len(self.rows)

    def _count(self, action: str) -> int:
        return sum(1 for r in self.rows if r.action == action)

    @property
    def create(self) -> int:
        return self._count(ACTION_CREATE)

    @property
    def update(self) -> int:
        return self._count(ACTION_UPDATE)

    @property
    def unchanged(self) -> int:
        return self._count(ACTION_UNCHANGED)

    @property
    def error(self) -> int:
        return self._count(ACTION_ERROR)

    @property
    def will_write(self) -> int:
        return self.create + self.update

    @property
    def can_commit(self) -> bool:
        return bool(self.will_write)


def _header_index(headers: list[str]) -> tuple[dict[str, int], list[str]]:
    """把文件表头映射到列 key。

    同时认中文表头（IT资产编号）和字段名（asset_code）——
    用户从接口文档里抄字段名手搓文件时也能用。
    """
    by_header: dict[str, str] = {}
    for column in ASSET_COLUMNS:
        by_header[column.header] = column.key
        by_header[column.key] = column.key
        # 全角空格、多余空白都容错：从网页复制粘贴出来的表头经常带这些
        by_header[column.header.replace("\u3000", "").replace(" ", "")] = column.key

    index: dict[str, int] = {}
    unknown: list[str] = []
    for position, raw_header in enumerate(headers):
        header = raw_header.strip().lstrip("\ufeff")
        if not header:
            continue
        key = by_header.get(header) or by_header.get(header.replace("\u3000", "").replace(" ", ""))
        if key is None:
            unknown.append(header)
        else:
            index[key] = position
    return index, unknown


def build_plan(
    db: Session,
    content: bytes,
    filename: str,
    *,
    mode: str = MODE_UPSERT,
    auto_create_type: bool = True,
) -> ImportPlan:
    """解析 + 校验，产出一份「打算怎么改」的计划。不写任何数据。"""
    plan = ImportPlan(filename=filename, mode=mode if mode in MODES else MODE_UPSERT)

    headers, raw_rows = spreadsheet.read_rows(content, filename)
    if not headers:
        plan.empty = True
        return plan

    index, unknown = _header_index(headers)
    plan.unknown_headers = unknown
    plan.missing_headers = [c.header for c in ASSET_COLUMNS if c.key not in index]
    plan.missing_required = [c.header for c in ASSET_COLUMNS if c.required and c.key not in index]

    # 缺了必需列就直接返回 —— 逐行报"编号为空"会把用户淹死，而真正原因只有一句话
    if plan.missing_required:
        return plan

    # 先把整张表转成 {key: 原始字符串}，后面校验全在这个结构上做
    records: list[tuple[int, dict[str, str]]] = []
    for row_number, cells in raw_rows:
        record: dict[str, str] = {}
        for column in ASSET_COLUMNS:
            position = index.get(column.key)
            if position is None:
                continue
            record[column.key] = cells[position] if position < len(cells) else ""
        records.append((row_number, record))

    if not records:
        plan.empty = True
        return plan

    # 文件内重复编号：先扫一遍，报错时才能指出"和哪一行撞了"
    seen: dict[str, int] = {}
    duplicate_of: dict[int, int] = {}
    for row_number, record in records:
        code = (record.get("asset_code") or "").strip()
        if not code or _is_clear_token(code):
            continue
        if code in seen:
            duplicate_of[row_number] = seen[code]
        else:
            seen[code] = row_number

    # 一次查完库里已有的编号和已有的类型，避免每行一次查询
    codes = list(seen)
    existing_assets: dict[str, Asset] = {}
    if codes:
        for asset in db.execute(select(Asset).where(Asset.asset_code.in_(codes))).scalars().all():
            existing_assets[asset.asset_code] = asset

    # 类型名 → id。这个字典的键同时也是「系统里已经有哪些类型」的答案，
    # 不用再单独查一次 names。
    type_ids: dict[str, int] = {
        name: tid for tid, name in db.execute(select(DeviceType.id, DeviceType.name)).all()
    }

    for row_number, record in records:
        plan.rows.append(
            _plan_row(
                row_number=row_number,
                record=record,
                mode=plan.mode,
                duplicate_of=duplicate_of.get(row_number),
                existing_assets=existing_assets,
                type_ids=type_ids,
                auto_create_type=auto_create_type,
            )
        )

    # 自动创建的类型只从「真正会写进去的行」里收集：
    # 一行如果本身有别的错被跳过了，就别顺手把它的类型也建出来
    for row in plan.rows:
        if row.action in (ACTION_CREATE, ACTION_UPDATE):
            name = row.payload.get("device_type_name")
            if name and name not in type_ids and name not in plan.auto_types:
                plan.auto_types.append(name)

    return plan


def _plan_row(
    *,
    row_number: int,
    record: dict[str, str],
    mode: str,
    duplicate_of: Optional[int],
    existing_assets: dict[str, Asset],
    type_ids: dict[str, int],
    auto_create_type: bool,
) -> RowPlan:
    row = RowPlan(row=row_number)
    errors = row.errors
    warnings = row.warnings

    # --- 编号：匹配键，必填且唯一 ---
    code = (record.get("asset_code") or "").strip()
    if not code or _is_clear_token(code):
        errors.append("IT资产编号不能为空（它是判断新增还是更新的依据）")
        row.action = ACTION_ERROR
        return row
    row.asset_code = code
    if len(code) > MAX_LENGTHS["asset_code"]:
        errors.append(f"IT资产编号太长（最多 {MAX_LENGTHS['asset_code']} 个字符）")
    if duplicate_of is not None:
        errors.append(f"IT资产编号「{code}」在文件里重复了（第 {duplicate_of} 行已经用过）")

    existing = existing_assets.get(code)
    if existing is not None:
        row.asset_id = existing.id
        spec = " ".join(x for x in (existing.brand, existing.model) if x)
        row.existing_label = spec
        if mode == MODE_CREATE_ONLY:
            errors.append(
                f"IT资产编号「{code}」在系统里已存在"
                f"（当前为「仅新增」模式，不会覆盖已有数据；如需更新请改用「新增或更新」）"
            )
        else:
            row.action = ACTION_UPDATE

    # --- 设备类型（必填，且可能需要自动创建） ---
    type_name, _ = _text(record, _TYPE_COLUMN)
    if type_name is None:
        if row.action == ACTION_CREATE:
            errors.append("设备类型不能为空（漏填的话设备无法归类）")
        # 更新时留空 = 不动原有的类型
    else:
        row.payload["device_type_name"] = type_name
        if type_name in type_ids:
            row.payload["device_type_id"] = type_ids[type_name]
        elif auto_create_type:
            warnings.append(f"设备类型「{type_name}」现在不存在，导入时会自动创建")
        else:
            errors.append(
                f"设备类型「{type_name}」不存在（当前未开启自动创建，请先在「设备类型」里加好）"
            )

    # --- 普通文本字段（注意跳过有专门解析逻辑的那几个） ---
    for column in ASSET_COLUMNS:
        if column.key in _SPECIAL_KEYS:
            continue
        value, cleared = _text(record, column)
        if value is not None:
            limit = MAX_LENGTHS.get(column.key)
            if limit and len(value) > limit:
                errors.append(f"{column.header}太长（{len(value)} 字，最多 {limit} 字）")
                continue
            row.payload[column.key] = value
        elif cleared:
            # 明确写了 "-" 之类：清空该字段。新增时与留空等价
            row.payload[column.key] = None

    # --- 状态 ---
    status_text = (record.get("status") or "").strip()
    if status_text and not _is_clear_token(status_text):
        status, status_error = parse_status_cell(status_text)
        if status_error:
            errors.append(status_error)
        else:
            row.payload["status"] = status
    elif status_text:
        # 状态是必有的，清空没有语义
        warnings.append("状态不能清空，已按「在用」处理")
        row.payload["status"] = AssetStatus.IN_USE
    elif row.action == ACTION_CREATE:
        row.payload["status"] = AssetStatus.IN_USE

    # --- 日期 ---
    for key in _DATE_KEYS:
        column = next((c for c in ASSET_COLUMNS if c.key == key), None)
        if column is None:
            continue
        raw = (record.get(key) or "").strip()
        if not raw:
            continue
        if _is_clear_token(raw):
            row.payload[key] = None
            continue
        parsed, date_error = parse_date_cell(raw)
        if date_error:
            errors.append(f"{column.header}：{date_error}")
        else:
            row.payload[key] = parsed

    # --- 更新时算差异：一个字段都没改的行，要如实报「无变化」而不是「更新成功」 ---
    if row.action == ACTION_UPDATE and not errors and existing is not None:
        row.changes = _diff_against(existing, row.payload)
        if not row.changes:
            row.action = ACTION_UNCHANGED

    if errors:
        row.action = ACTION_ERROR
    return row


def _diff_against(asset: Asset, payload: dict[str, Any]) -> list[str]:
    """把 payload 和库里那条对比，产出「字段 旧 → 新」的人话列表。

    按 device_type_name 比而不是按 device_type_id ——
    类型是「导入时自动创建」的那种新类型时，此刻还没有 id，
    按 id 比会把「换了个新类型」当成没改动，用户以为生效了其实什么都没发生。
    """
    changes: list[str] = []

    for key, new_value in payload.items():
        if key == "device_type_id":
            continue  # 由 device_type_name 代表
        if key == "device_type_name":
            old_name = asset.device_type.name if asset.device_type else None
            if (old_name or "") != (new_value or ""):
                changes.append(f"设备类型 {_display(old_name, key)} → {_display(new_value, key)}")
            continue

        old_value = getattr(asset, key, None)
        if _render(old_value) == _render(new_value):
            continue
        label = FIELD_LABELS.get(key, key)
        changes.append(f"{label} {_display(old_value, key)} → {_display(new_value, key)}")

    return changes


def _render(value: Any) -> str:
    """只用于「是否变化」的判断：统一成字符串再比，避免 date vs str 的假变更。"""
    if value is None or value == "":
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _display(value: Any, key: str) -> str:
    if value is None or value == "":
        return "空"
    if key == "status":
        return AssetStatus.LABELS.get(str(value), str(value))
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# --------------------------------------------------------------------------- #
# 落库
# --------------------------------------------------------------------------- #
@dataclass
class ImportOutcome:
    filename: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    created_types: list[str] = field(default_factory=list)
    failed_rows: list[RowPlan] = field(default_factory=list)
    asset_codes: list[str] = field(default_factory=list)


def apply_plan(
    db: Session,
    plan: ImportPlan,
    *,
    operator: Optional[str] = None,
) -> ImportOutcome:
    """把计划写进库。只 add / flush，不 commit —— 由路由统一提交。"""
    outcome = ImportOutcome(filename=plan.filename)

    # 1) 先建设备类型：后面每一行都要用它的 id
    if plan.auto_types:
        from ..models import DeviceType as DT

        next_order = 100 + len(plan.auto_types)
        for name in plan.auto_types:
            db.add(DT(name=name, sort_order=next_order, category=DeviceCategory.guess(name)))
            outcome.created_types.append(name)
        db.flush()
        for row in plan.rows:
            name = row.payload.get("device_type_name")
            if name in outcome.created_types:
                row.payload["device_type_id"] = db.execute(
                    select(DT.id).where(DT.name == name)
                ).scalar_one()

    # 2) 逐行写入
    for row in plan.rows:
        if row.action == ACTION_ERROR:
            outcome.failed += 1
            outcome.failed_rows.append(row)
            continue
        if row.action == ACTION_UNCHANGED:
            outcome.unchanged += 1
            continue

        payload = dict(row.payload)
        payload.pop("device_type_name", None)

        if row.action == ACTION_CREATE:
            if not payload.get("device_type_id"):
                row.errors.append("设备类型没能解析出对应类型")
                row.action = ACTION_ERROR
                outcome.failed += 1
                outcome.failed_rows.append(row)
                continue

            asset = Asset(asset_code=row.asset_code, **payload)
            db.add(asset)
            db.flush()
            record_event(
                db,
                asset,
                AssetEventType.CREATE,
                to_status=asset.status,
                operator=operator,
                note=f"批量导入录入：{plan.filename}",
            )
            outcome.created += 1
            outcome.asset_codes.append(asset.asset_code)
            continue

        # 更新
        asset = db.get(Asset, row.asset_id) if row.asset_id else None
        if asset is None:
            row.errors.append("要更新的设备在提交时已经不存在了（可能被同时删掉了）")
            row.action = ACTION_ERROR
            outcome.failed += 1
            outcome.failed_rows.append(row)
            continue

        before_status = asset.status
        for key, value in payload.items():
            setattr(asset, key, value)
        db.flush()

        changed_status = asset.status != before_status
        only_status = changed_status and len(row.changes) == 1 and "状态" in row.changes[0]
        record_event(
            db,
            asset,
            AssetEventType.STATUS if only_status else AssetEventType.UPDATE,
            from_status=before_status if changed_status else None,
            to_status=asset.status if changed_status else None,
            operator=operator,
            note="；".join(row.changes) or f"批量导入更新：{plan.filename}",
            detail={"import": plan.filename, "changes": row.changes},
        )
        outcome.updated += 1
        outcome.asset_codes.append(asset.asset_code)

    return outcome


# --------------------------------------------------------------------------- #
# 模板
# --------------------------------------------------------------------------- #
def template_rows(*, with_samples: bool = True) -> list[list[str]]:
    """模板内容。带两行示例，用户照着改比看说明快。"""
    headers = [c.header for c in ASSET_COLUMNS]
    if not with_samples:
        return [headers]
    return [
        headers,
        [c.sample for c in ASSET_COLUMNS],
        [
            "IT-2026-1002", "FIN-2026-2002", "显示器", "戴尔", "U2723QE 27英寸 4K",
            "CN0P7Q2S", "在用", "李娜", "总部 3F 财务室 B-04", "2026-01-05", "2029-01-04",
            "示例行，导入前请删掉",
        ],
    ]


def template_notes() -> list[tuple[str, str]]:
    required = "、".join(c.header for c in ASSET_COLUMNS if c.required)
    optional = "、".join(c.header for c in ASSET_COLUMNS if not c.required)
    return [
        ("模板说明", ""),
        ("必填列", required),
        ("选填列", optional),
        ("编号唯一", "IT资产编号是判断「新增还是更新」的依据，同一份文件里不能重复。"),
        ("重复怎么办", "选「仅新增」时，编号已存在会报错并跳过；选「新增或更新」时，会更新那条已有记录。"),
        ("设备类型", "可以选「不存在时自动创建」；关掉这个选项，类型名写错就会逐行报错。"),
        ("状态取值", "在用 / 在库 / 维修中 / 已报废，留空按「在用」处理。"),
        ("日期格式", "写成 2026-01-02；从 Excel 直接复制的日期也能识别。"),
        ("空单元格", "更新已有设备时，留空表示「保持原值不变」。"),
        ("想清空某字段", "在那个单元格里写一个减号  -  即可。"),
        ("出错的行", "导入时会跳过，不会影响其他正确的行；结果里会逐行说明原因。"),
        ("导出的文件", "本系统导出的表格列与本模板完全一致，可以直接改了再导回来。"),
        ("配对显示器列", "导出时可能多一列「配对显示器」，那是只读参考，导入时会被忽略。"),
    ]
