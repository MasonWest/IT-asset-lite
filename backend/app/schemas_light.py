"""轻资产的请求 / 响应模型（Pydantic v2）。

单独一个文件，理由同 `schemas_workstations.py` —— 不让 `schemas.py` 继续膨胀。

⚠️ 这一版按《设计方案-轻资产管理模块-修订版》**取消**了：
  - `tracking`（数量 / 单件双模式）；
  - 整套 `LightItem*`（单件档案）DTO。
新增了 `light_holdings`（当前分配记录）相关 DTO，以及按资产 / 按点位查配件、
期初确认、历史导入的请求响应。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 手工登记允许的来源。**没有 `import`** —— 历史导入只能走导入接口。
LightOriginLiteral = Literal["manual", "opening"]
#: 引用类型。`"none"` 表示"系统外部 / 已注销"（入库的来源、报损的去向）——
#: 前端传 `"none"`，服务层翻成真正的 `None`（Pydantic 的 Literal 表达不了 None 语义）。
LightHolderKindLiteral = Literal["stock", "user", "asset", "workstation", "none"]
LightOpLiteral = Literal[
    "stock_in", "allocate", "return", "attach", "detach", "transfer", "scrap", "adjust"
]


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip() or None
    return value


# --------------------------------------------------------------------------- #
# 引用（from / to / 持有位置）
# --------------------------------------------------------------------------- #
class HolderRefIn(BaseModel):
    """流水的来源 / 去向，也是"一处持有位置"。

    - `kind='stock'` → 可带 `stock_location`（同一品类可分放几处库房）；
    - `kind='user'` → 传 `user`；
    - `kind ∈ {asset, workstation}` → 传 `ref_id`。
    服务层会校验，并在写流水时快照编码与名称。
    """

    kind: LightHolderKindLiteral
    ref_id: Optional[int] = None
    user: Optional[str] = Field(default=None, max_length=64)
    #: 仅 kind='stock' 有意义：存放位置描述（"3 楼库房A柜" / "前台抽屉" / 某个点位编码）
    stock_location: Optional[str] = Field(default=None, max_length=128)

    _clean = field_validator("user", "stock_location", mode="before")(_blank_to_none)


class HolderOut(BaseModel):
    """一处引用 / 一个持有位置在界面上的样子。"""

    kind: Optional[str] = None
    kind_label: str = ""
    ref_id: Optional[int] = None
    ref_code: Optional[str] = None
    ref_label: Optional[str] = None
    user: Optional[str] = None
    #: 一句现成的展示文案（"库存" / "张三" / "B022-03-00001"）
    display: str = ""
    #: 引用的宿主是否还在（资产 / 点位被删后为 False，提示"已删除"）
    alive: bool = True
    #: 一行简短说明（如 "随 B022-03-00001"、"在某人手上，未记录固定位置"）
    hint: str = ""

    #: 以下四项**只在 kind='asset' 时填**：宿主**当前**的使用人 / 点位 / 状态。
    #: 现读正式资产表（红线 2：不复制会过期的数据）—— 主机换人之后，
    #: "这块 SSD 现在跟着谁用"必须自动跟着变，而不是显示一个历史副本。
    host_user: Optional[str] = None
    host_workstation_code: Optional[str] = None
    host_status: Optional[str] = None
    host_status_label: Optional[str] = None

    #: 仅 kind='stock'：存放位置描述，以及"位置文本恰好是某个活着的点位编码"时的点位 id
    #: （前端据此把它渲染成可点击的链接；**不做外键**，见 models.LightHolding 的说明）
    stock_location: Optional[str] = None
    stock_location_station_id: Optional[int] = None


class HoldingOut(HolderOut):
    """一条当前分配记录（"现在在哪、有几件"）。

    ⚠️ **必须带品类信息**：`HolderOut` 那半边回答的是"在哪"，光有它，界面上就只能显示
    "装在 B022-03-00038 上 —— ×1"，**看不出装的是什么东西**（2026-10-16 用户实测报障：
    点位侧栏与资产详情页都只有宿主编号，得点开「登记配件变动」才知道是块固态硬盘）。
    品类永远在，所以是接口的责任，不能指望调用方自己去 join。
    """

    id: int
    category_id: int
    #: 品类主名（如「M.2固态 宏碁N3500」）—— 一行里最先要看到的那个词
    category_name: str = ""
    category_brand: Optional[str] = None
    category_spec: Optional[str] = None
    category_unit: str = ""
    #: 「名称 · 品牌 规格」单行版，给只有一行位置的地方（title / 窄屏）
    category_label: str = ""
    quantity: int = 0
    since: Optional[datetime] = None
    note: Optional[str] = None


class StationHoldingsOut(BaseModel):
    """某点位的关联轻资产 —— **两组分开返回，前端不做拼装判断**（设计 §5.4）。"""

    workstation_id: int
    code: str = ""
    #: 固定在此工位（`holder_kind='workstation'` 且 `workstation_id=本点位`）
    direct: list[HoldingOut] = []
    #: 随主机上的（`holder_kind='asset'` 且该资产当前 `workstation_id=本点位`）
    via_assets: list[HoldingOut] = []


class AssetHoldingsOut(BaseModel):
    """某资产的关联配件（资产详情页"关联轻资产"区块）。"""

    asset_id: int
    asset_code: str = ""
    items: list[HoldingOut] = []
    total: int = 0


# --------------------------------------------------------------------------- #
# 品类
# --------------------------------------------------------------------------- #
class LightCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    brand: Optional[str] = Field(default=None, max_length=64)
    spec: Optional[str] = Field(default=None, max_length=128)
    unit: str = Field(default="件", min_length=1, max_length=16)
    notes: Optional[str] = None
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("name", "brand", "spec", "unit", "notes", mode="before")(_blank_to_none)


class LightCategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    brand: Optional[str] = Field(default=None, max_length=64)
    spec: Optional[str] = Field(default=None, max_length=128)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=16)
    notes: Optional[str] = None
    active: Optional[bool] = None
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("name", "brand", "spec", "unit", "notes", mode="before")(_blank_to_none)


class LightCategoryStat(BaseModel):
    """台账每一行的派生数字 —— 全部只读 `light_holdings`（结构上不可能对不上）。"""

    stock: int = 0
    allocated: int = 0
    net: int = 0
    imported_count: int = 0
    #: 有历史流水、当前账却是空的（典型：只导入了分配流水、没做期初）→ 界面说"未登记"，不猜数字
    pending_opening: bool = False
    holdings: list[HoldingOut] = []


class LightCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    brand: Optional[str] = None
    spec: Optional[str] = None
    unit: str = "件"
    notes: Optional[str] = None
    active: bool = True
    created_at: datetime
    updated_at: datetime
    #: 做过期初确认没有（**只是提示用**，不阻止再次登记 —— 设计 §6.1）
    opening_done: bool = False
    stat: LightCategoryStat = LightCategoryStat()


class LightCategoryPage(BaseModel):
    items: list[LightCategoryOut]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- #
# 流水
# --------------------------------------------------------------------------- #
class LightMovementCreate(BaseModel):
    """登记一次流转。**唯一写入口** —— op 决定语义，from/to 决定物料移动。"""

    category_id: int
    op: LightOpLiteral
    # 用 default_factory 而不是直接塞一个模型实例 —— Pydantic 会共享同一个默认对象，
    # 一旦哪里改了它，后面的请求就跟着错（经典的"可变默认值"陷阱）。
    from_ref: HolderRefIn = Field(default_factory=lambda: HolderRefIn(kind="stock"))
    to_ref: HolderRefIn = Field(default_factory=lambda: HolderRefIn(kind="stock"))
    quantity: int = Field(default=1, ge=1, le=100000)
    occurred_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=255)
    operator: Optional[str] = Field(default=None, max_length=64)
    #: manual（默认）/ opening（期初确认，必须带操作人）
    origin: LightOriginLiteral = "manual"
    #: 前端每次提交带一个 UUID，防重复提交
    request_id: Optional[str] = Field(default=None, max_length=64)
    #: 更正哪一条（仅追溯）
    corrects_id: Optional[int] = None

    _clean = field_validator("note", "operator", "request_id", mode="before")(_blank_to_none)


class LightReplaceRequest(BaseModel):
    """**更换配件**：一条业务动作 = 报损 + 安装两条流水（同事务）。"""

    category_id: int
    #: 哪台机器上的配件要换
    asset_id: int
    quantity: int = Field(default=1, ge=1, le=100000)
    occurred_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=255)
    operator: Optional[str] = Field(default=None, max_length=64)
    request_id: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("note", "operator", "request_id", mode="before")(_blank_to_none)


class LightMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    category_name: str = ""
    op: str
    op_label: str = ""
    quantity: int = 1
    from_holder: HolderOut = HolderOut()
    to_holder: HolderOut = HolderOut()
    operator: Optional[str] = None
    occurred_at: datetime
    created_at: datetime
    note: Optional[str] = None
    origin: str = "manual"
    origin_label: str = ""
    #: 是否计入当前状态（历史导入为 False）
    effective: bool = True
    import_batch: Optional[str] = None
    corrects_id: Optional[int] = None
    #: **这条流水能不能机械冲正**。后端按 `LightOp.reverse_op()` 算好给前端 ——
    #: "报损 / 安装（外部→资产）"这类没有机械反向的操作，前端不该渲染一个点了就报错的按钮。
    #: 历史导入恒为 False（它只用于追溯，本来就不该被冲正）。
    can_reverse: bool = False


class LightMovementPage(BaseModel):
    items: list[LightMovementOut]
    total: int
    page: int
    page_size: int


class LightMovementResult(BaseModel):
    #: 一次业务动作产生的流水。**更换是两条**，所以这里是列表（绝大多数情况只有一条）。
    movements: list[LightMovementOut]
    #: 这条操作引起的当前状态变化（给前端弹提示 / 刷新用）
    stat: LightCategoryStat


# --------------------------------------------------------------------------- #
# 期初确认
# --------------------------------------------------------------------------- #
class OpeningEntry(BaseModel):
    holder: HolderRefIn
    quantity: int = Field(ge=1, le=100000)


class OpeningRequest(BaseModel):
    category_id: int
    entries: list[OpeningEntry] = Field(min_length=1)
    operator: Optional[str] = Field(default=None, max_length=64)
    note: Optional[str] = Field(default=None, max_length=255)
    occurred_at: Optional[datetime] = None

    _clean = field_validator("note", "operator", mode="before")(_blank_to_none)


# --------------------------------------------------------------------------- #
# 历史导入（只写流水，绝不碰 holdings）
# --------------------------------------------------------------------------- #
class ImportRowOut(BaseModel):
    row: int
    action: str
    action_label: str = ""
    message: str = ""
    summary: str = ""


class ImportPreview(BaseModel):
    filename: str = ""
    total: int = 0
    will_import: int = 0
    duplicate: int = 0
    error: int = 0
    rows: list[ImportRowOut] = []
    rows_truncated: bool = False
    #: 文件层面的问题（表头缺列 / 空文件 / 解不开）—— 非空时整份文件都不落库
    schema_note: Optional[str] = None

    @property
    def can_commit(self) -> bool:  # pragma: no cover - 仅内部用
        return self.schema_note is None and self.will_import > 0


class ImportResult(ImportPreview):
    pass


# --------------------------------------------------------------------------- #
# 其它
# --------------------------------------------------------------------------- #
class LightHolderCandidate(BaseModel):
    """流转对话框里选「正式资产 / 点位」时的一个候选。"""

    id: int
    #: 资产编码 / 点位编码（快照进流水用的就是这个）
    code: str
    #: 主文案：设备类型名 / 点位名
    label: str = ""
    #: 副提示：资产当前使用人 / 点位当前使用人（现读，仅展示）
    hint: str = ""
    kind: str = "asset"


class LightFilterOptions(BaseModel):
    """轻资产页面的下拉候选（一次拿全，别让前端逐项查）。

    ⚠️ 这里**没有**资产 / 点位列表 —— 那两份各有 150 / 69 条，全塞进来纯浪费。
    选宿主走 `/light/holders` 搜索端点，要什么取什么。
    """

    categories: list[dict[str, Any]] = []
    users: list[str] = []
    ops: list[dict[str, str]] = []
    #: 已经用过的库存存放位置（自由文本的候选，见设计 §14.9）
    stock_locations: list[str] = []
