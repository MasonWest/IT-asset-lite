"""请求 / 响应数据结构（Pydantic v2）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

AssetStatusLiteral = Literal["in_use", "idle", "repair", "scrapped"]
DeviceCategoryLiteral = Literal["host", "display", "other"]
OperationLiteral = Literal["checkout", "return", "repair", "repair_done", "scrap", "restore"]
InventoryResultLiteral = Literal["pending", "checked", "abnormal"]


def _blank_to_none(value: Any) -> Any:
    """前端清空输入框时会给空字符串，统一转成 None，避免日期字段解析报错。"""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


# --------------------------------------------------------------------------- #
# 设备类型
# --------------------------------------------------------------------------- #
class DeviceTypeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    sort_order: int = 0
    category: Optional[DeviceCategoryLiteral] = Field(
        default=None, description="留空则按类型名自动猜一个"
    )

    _norm_name = field_validator("name")(lambda v: v.strip())


class DeviceTypeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    sort_order: Optional[int] = None
    category: Optional[DeviceCategoryLiteral] = None

    _norm = field_validator("name")(lambda v: v.strip() if isinstance(v, str) else v)


class DeviceTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sort_order: int
    category: str = "other"
    category_label: str = ""
    asset_count: int = 0


# --------------------------------------------------------------------------- #
# 资产
# --------------------------------------------------------------------------- #
class AssetBase(BaseModel):
    asset_code: Optional[str] = Field(default=None, max_length=64)
    finance_code: Optional[str] = Field(default=None, max_length=64)
    device_type_id: int
    brand: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=128)
    serial_number: Optional[str] = Field(default=None, max_length=128)
    status: AssetStatusLiteral = "in_use"
    user_name: Optional[str] = Field(default=None, max_length=64)
    location: Optional[str] = Field(default=None, max_length=128)
    purchase_date: Optional[date] = None
    warranty_until: Optional[date] = None
    notes: Optional[str] = None

    _clean = field_validator(
        "asset_code",
        "finance_code",
        "brand",
        "model",
        "serial_number",
        "user_name",
        "location",
        "notes",
        mode="before",
    )(_blank_to_none)

    _clean_dates = field_validator("purchase_date", "warranty_until", mode="before")(
        lambda v: _blank_to_none(v)
    )


class AssetCreate(AssetBase):
    """新增。asset_code 留空则后端自动生成。"""

    operator: Optional[str] = Field(default=None, max_length=64, description="经办人，写进时间线")


class AssetUpdate(BaseModel):
    """编辑。所有字段可选，未传的字段保持原值。"""

    asset_code: Optional[str] = Field(default=None, max_length=64)
    finance_code: Optional[str] = Field(default=None, max_length=64)
    device_type_id: Optional[int] = None
    brand: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=128)
    serial_number: Optional[str] = Field(default=None, max_length=128)
    status: Optional[AssetStatusLiteral] = None
    user_name: Optional[str] = Field(default=None, max_length=64)
    location: Optional[str] = Field(default=None, max_length=128)
    purchase_date: Optional[date] = None
    warranty_until: Optional[date] = None
    notes: Optional[str] = None
    operator: Optional[str] = Field(default=None, max_length=64, description="经办人，写进时间线")

    _clean = field_validator(
        "asset_code",
        "finance_code",
        "brand",
        "model",
        "serial_number",
        "user_name",
        "location",
        "notes",
        mode="before",
    )(_blank_to_none)

    _clean_dates = field_validator("purchase_date", "warranty_until", mode="before")(
        lambda v: _blank_to_none(v)
    )


class DeviceTypeBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str = "other"


class AssetBrief(BaseModel):
    """配对关系里要展示的对方设备，只带够用的字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_code: str
    device_type_name: str = ""
    device_type_id: int = 0
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: str = ""
    status_label: str = ""
    user_name: Optional[str] = None
    location: Optional[str] = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_code: str
    finance_code: Optional[str] = None
    device_type_id: int
    device_type_name: str = ""
    device_category: str = "other"
    device_type: Optional[DeviceTypeBrief] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: str
    status_label: str = ""
    user_name: Optional[str] = None
    location: Optional[str] = None
    purchase_date: Optional[date] = None
    warranty_until: Optional[date] = None
    warranty_days_left: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    # --- 配对相关（列表 / 详情都用得上） ---
    #: 作为主机，当前挂了几台显示器
    monitor_count: int = 0
    #: 作为显示器，当前挂在哪台主机上
    host_id: Optional[int] = None
    host_code: Optional[str] = None
    #: 详情页用：作为主机当前挂着的显示器 / 作为显示器挂在哪台主机上
    monitors: list[AssetBrief] = []
    host: Optional[AssetBrief] = None
    #: 当前状态下可执行的操作动作，前端按钮直接照着渲染
    actions: list[str] = []


class AssetPage(BaseModel):
    items: list[AssetOut]
    total: int
    page: int
    page_size: int


class FilterOptions(BaseModel):
    """筛选下拉的候选值，全部从现有资产数据里现算，不额外维护字典表。"""

    device_types: list[DeviceTypeOut]
    statuses: list[dict[str, str]]
    users: list[str]
    locations: list[str]
    total: int
    status_counts: dict[str, int]
    device_type_counts: dict[str, int]
    #: 配对概况：主机数 / 显示器数 / 已配对显示器数
    pair_stats: dict[str, int] = {}


class SystemInfo(BaseModel):
    app_name: str
    version: str
    port: int
    lan_ip: str
    base_url: str
    database_file: str
    #: 备份目录（backup.bat 往这里写）。页面上直接显示出来，用户就不用去翻 README
    backup_dir: str = ""
    asset_count: int


# --------------------------------------------------------------------------- #
# 变更历史
# --------------------------------------------------------------------------- #
class AssetEventOut(BaseModel):
    id: int
    asset_id: int
    event_type: str
    event_label: str
    from_status: Optional[str] = None
    from_status_label: Optional[str] = None
    to_status: Optional[str] = None
    to_status_label: Optional[str] = None
    operator: Optional[str] = None
    note: Optional[str] = None
    detail: Optional[dict[str, Any]] = None
    created_at: datetime


class TimelinePage(BaseModel):
    asset_id: int
    asset_code: str
    total: int
    items: list[AssetEventOut]


# --------------------------------------------------------------------------- #
# 状态流转操作
# --------------------------------------------------------------------------- #
class AssetOperationRequest(BaseModel):
    action: OperationLiteral
    operator: Optional[str] = Field(default=None, max_length=64, description="操作人")
    user_name: Optional[str] = Field(default=None, max_length=64, description="领用时指定使用人")
    location: Optional[str] = Field(default=None, max_length=128, description="可选，同时更新存放位置")
    note: Optional[str] = Field(default=None, max_length=500)

    _clean = field_validator("operator", "user_name", "location", "note", mode="before")(_blank_to_none)


class AssetOperationOut(BaseModel):
    asset: AssetOut
    event: AssetEventOut


# --------------------------------------------------------------------------- #
# 配对
# --------------------------------------------------------------------------- #
class PairRequest(BaseModel):
    monitor_id: int = Field(description="要挂上去的显示器")
    host_id: int = Field(description="挂到哪台主机上")
    operator: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=255)

    _clean = field_validator("operator", "note", mode="before")(_blank_to_none)


class RebindRequest(BaseModel):
    monitor_id: int = Field(description="要换绑的显示器")
    host_id: int = Field(description="要换到哪台主机上")
    operator: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=255)

    _clean = field_validator("operator", "note", mode="before")(_blank_to_none)


class UnbindRequest(BaseModel):
    operator: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=255)

    _clean = field_validator("operator", "note", mode="before")(_blank_to_none)


class RelationOut(BaseModel):
    id: int
    active: bool
    host_id: int
    host: AssetBrief
    monitor_id: int
    monitor: AssetBrief
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    released_by: Optional[str] = None
    released_at: Optional[datetime] = None
    release_note: Optional[str] = None


class HostPairing(BaseModel):
    """配对管理页里的一行：一台主机 + 它挂着的显示器。"""

    host: AssetBrief
    monitors: list[AssetBrief]
    monitor_count: int


class PairingBoard(BaseModel):
    hosts: list[HostPairing]
    unpaired_monitors: list[AssetBrief]
    stats: dict[str, int]
    #: 配对页顶部的提示（比如「还没有显示类设备，先去设备类型里改类别」）
    hints: list[str] = []


class RelationHistoryPage(BaseModel):
    total: int
    items: list[RelationOut]


# --------------------------------------------------------------------------- #
# 盘点
# --------------------------------------------------------------------------- #
class InventoryScope(BaseModel):
    """发起盘点时的筛选条件。留空 = 全盘。"""

    device_type_id: Optional[str] = Field(default=None, description="设备类型 ID，逗号分隔")
    status: Optional[str] = Field(default=None, description="状态，逗号分隔")
    user_name: Optional[str] = None
    location: Optional[str] = None
    keyword: Optional[str] = None
    asset_ids: Optional[list[int]] = Field(default=None, description="指定设备，给了就忽略其他条件")

    _clean = field_validator(
        "device_type_id", "status", "user_name", "location", "keyword", mode="before"
    )(_blank_to_none)


class InventoryTaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(default=None, max_length=500)
    created_by: Optional[str] = Field(default=None, max_length=64)
    scope: InventoryScope = Field(default_factory=InventoryScope)

    _clean = field_validator("name", "note", "created_by", mode="before")(_blank_to_none)


class InventoryTaskOut(BaseModel):
    id: int
    name: str
    status: str
    status_label: str
    note: Optional[str] = None
    scope: Optional[dict[str, Any]] = None
    scope_text: str = ""
    created_by: Optional[str] = None
    created_at: datetime
    closed_by: Optional[str] = None
    closed_at: Optional[datetime] = None
    total: int = 0
    checked: int = 0
    pending: int = 0
    abnormal: int = 0
    progress: float = 0.0


class InventoryItemOut(BaseModel):
    id: int
    task_id: int
    asset_id: int
    asset: AssetOut
    result: str
    result_label: str
    snapshot_user: Optional[str] = None
    snapshot_location: Optional[str] = None
    snapshot_status: Optional[str] = None
    operator: Optional[str] = None
    note: Optional[str] = None
    checked_at: Optional[datetime] = None
    #: 标记为「已盘」之后，账面值被改动过没 —— 帮盘点人发现"盘完又被人动过"
    changed_since: bool = False


class InventoryTaskDetail(InventoryTaskOut):
    items: list[InventoryItemOut] = []
    #: 差异明细：未盘 + 异常
    diff: list[InventoryItemOut] = []


class InventoryMarkRequest(BaseModel):
    asset_id: int = Field(description="要标记的设备")
    result: InventoryResultLiteral
    operator: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=255)

    _clean = field_validator("operator", "note", mode="before")(_blank_to_none)


class InventoryMarkResult(BaseModel):
    item: InventoryItemOut
    task: InventoryTaskOut


class InventoryContext(BaseModel):
    """手机扫码进详情页时，告诉它「这台设备正被哪个盘点任务盯着」。"""

    task_id: int
    task_name: str
    task_status: str
    item_id: int
    result: str
    result_label: str
    operator: Optional[str] = None
    note: Optional[str] = None
    checked_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# 批量导入
# --------------------------------------------------------------------------- #
class ImportRowOut(BaseModel):
    """一行数据的判定结果 —— 预览和导入结果共用同一份结构。"""

    row: int
    asset_code: str = ""
    action: str
    action_label: str
    errors: list[str] = []
    warnings: list[str] = []
    #: 「字段 旧 → 新」的人话列表，更新时才有
    changes: list[str] = []
    #: 库里已有那条的设备描述，用于更新时前后对照
    existing_label: str = ""


class ImportPreview(BaseModel):
    filename: str
    mode: str
    mode_label: str
    total: int = 0
    create: int = 0
    update: int = 0
    unchanged: int = 0
    error: int = 0
    will_write: int = 0
    can_commit: bool = False
    #: 导入时会自动创建的设备类型
    auto_types: list[str] = []
    #: 文件里有、但模板里没有的列（会被忽略）
    unknown_headers: list[str] = []
    #: 模板里有、文件里没有的列
    missing_headers: list[str] = []
    #: 缺了就没法导入的必需列
    missing_required: list[str] = []
    #: 文件只有表头、没有数据行
    empty: bool = False
    rows: list[ImportRowOut] = []
    #: 行数太多时只回前若干行，这个标志告诉前端"底下还有"
    rows_truncated: bool = False


class ImportResultOut(BaseModel):
    filename: str
    mode: str
    mode_label: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    created_types: list[str] = []
    failed_rows: list[ImportRowOut] = []
    #: 写入成功的设备编号（限量返回，避免大文件响应过大）
    asset_codes: list[str] = []


# --------------------------------------------------------------------------- #
# 操作审计
# --------------------------------------------------------------------------- #
class AuditLogOut(BaseModel):
    id: int
    created_at: datetime
    operator: Optional[str] = None
    ip: Optional[str] = None
    action: str
    action_label: str
    action_icon: str = "•"
    target_type: str
    target_type_label: str = ""
    target_id: Optional[int] = None
    target_label: Optional[str] = None
    summary: Optional[str] = None
    status: str
    status_label: str = ""
    ok: bool = True
    detail: Optional[dict[str, Any]] = None


class AuditPage(BaseModel):
    items: list[AuditLogOut]
    total: int
    page: int
    page_size: int


class AuditActionOption(BaseModel):
    value: str
    label: str
    icon: str = "•"


class AuditGroup(BaseModel):
    name: str
    options: list[AuditActionOption] = []


class AuditMeta(BaseModel):
    """审计页筛选下拉的候选值。"""

    groups: list[AuditGroup] = []
    operators: list[str] = []
    ips: list[str] = []
    total: int = 0
    today: int = 0
    failed: int = 0
