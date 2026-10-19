"""点位（资产空间地图）的请求 / 响应结构。

单独一个文件而不是往 schemas.py 里塞：schemas.py 已经 590 多行，
再插 200 行会让那个文件继续膨胀。这里只依赖 schemas.py 的公共件，
**不反向 import 具体模型**，`InventoryTaskOut` 那个引用是延迟到运行时才解析的。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import FacingLiteral, InventoryTaskOut, _blank_to_none


# --------------------------------------------------------------------------- #
# 地图渲染
# --------------------------------------------------------------------------- #
class WorkstationCreate(BaseModel):
    """新增点位。code 留空则由后端生成 —— 不要交给前端算，并发会撞。"""

    code: Optional[str] = Field(default=None, max_length=32)
    x: int
    y: int
    facing: Optional[FacingLiteral] = None
    user_name: Optional[str] = Field(default=None, max_length=64)
    room: Optional[str] = Field(default=None, max_length=64)
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("code", "user_name", "room", "operator", mode="before")(_blank_to_none)


class WorkstationUpdate(BaseModel):
    """编辑点位。只传要改的字段；未传的保持原值。

    x / y 也能从这儿单改，但界面上的拖动应当走 `/layout` 攒批提交 ——
    逐次提交会让审计被几十次布局微调刷屏。
    """

    code: Optional[str] = Field(default=None, max_length=32)
    x: Optional[int] = None
    y: Optional[int] = None
    facing: Optional[FacingLiteral] = None
    user_name: Optional[str] = Field(default=None, max_length=64)
    room: Optional[str] = Field(default=None, max_length=64)
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("user_name", "room", "operator", mode="before")(_blank_to_none)


class WorkstationMove(BaseModel):
    id: int
    x: int
    y: int


class WorkstationLayoutRequest(BaseModel):
    """一次布局调整。整体提交，后端算完差异只写 1 条审计。"""

    items: list[WorkstationMove] = Field(min_length=1)
    operator: Optional[str] = Field(default=None, max_length=64)


class WorkstationBulkGenerate(BaseModel):
    """批量生成点位（画一片区域 → 行列排布）。

    **为什么必须是独立接口而不是循环调 `POST /api/workstations`**：
    「一次批量操作 = 1 条审计」是全系统口径（批量导入 30 台设备也是 1 条）。
    循环 24 次会往审计页灌 24 条「新增点位」，把真正的资产操作淹掉。

    ## 参数语义
    - `count` 是**总数**，不是"列数 × 行数"（前端算好了再传，避免两边各算一遍还同意不了）。
    - `cols` 只影响排布（一行几个），`facing` 支持 `alternate` —— 现实里面对面的点位
      朝向是反的，一个"全部朝下"的批量工具做出来的图会假得刺眼。
    - `start_code` / `code_prefix`：**编码建议是给用户的，真相在后端**。
      前端面板显示"将生成 W63~W86"只是预览；真分配时后端在事务里查全量 code 兜底，
      撞了就 409，**绝不静默跳号**（因为已删点位的编码不释放，跳号会让用户对不上账）。
    """

    count: int = Field(ge=1, le=200, description="生成总数量")
    origin_x: int = Field(description="起始 X（左上角第一个点位）")
    origin_y: int = Field(description="起始 Y")
    step_x: int = Field(default=90, ge=20, le=400, description="横向间距")
    step_y: int = Field(default=66, ge=20, le=400, description="纵向间距")
    cols: int = Field(default=6, ge=1, le=30, description="每行几个（决定换行）")
    facing: str = Field(default="down", description="down / up / alternate（交替，面对面）")
    code_prefix: Optional[str] = Field(default="W", max_length=8, description="编码前缀")
    start_code: Optional[str] = Field(
        default=None, max_length=32, description="起始编码（如 W63）。不传则由后端按现有最大值续号"
    )
    room: Optional[str] = Field(default=None, max_length=64, description="房间标签，传给每个点位")
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("code_prefix", "start_code", "room", "operator", mode="before")(_blank_to_none)

    @field_validator("facing")
    @classmethod
    def _check_facing(cls, v: str) -> str:
        allowed = {"up", "down", "alternate"}
        if v not in allowed:
            raise ValueError(f"facing 只能是 {sorted(allowed)} 之一")
        return v

    @field_validator("code_prefix")
    @classmethod
    def _check_prefix(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.isalpha():
            raise ValueError("编码前缀只能是字母")
        return v


class WorkstationBulkResult(BaseModel):
    """批量生成结果。"""

    created: int = 0
    #: 实际分配到的编码，按生成顺序。前端拿它做"生成了哪些"的确认。
    codes: list[str] = []
    #: 第一个 / 最后一个编码，给 toast 用
    first_code: Optional[str] = None
    last_code: Optional[str] = None
    #: 落库后的点位，供前端直接画上去（省一次全量拉取）
    items: list["WorkstationOut"] = []


class WorkstationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    x: int
    y: int
    facing: str = "down"
    user_name: Optional[str] = None
    room: Optional[str] = None
    active: bool = True
    created_at: datetime
    updated_at: datetime
    #: 点位上挂了几台资产 —— 派生查询，不落库（红线 2）
    asset_count: int = 0
    #: 点位上「看得见的」轻资产件数（Σquantity）—— 派生查询，不落库（红线 2）。
    #:
    #: 与 `asset_count` 同一套路：列表接口一次查完下发，前端不再为此打接口。
    #:
    #: ⚠️ **分两组、不发合计**：地图徽标数字 = `light_direct + light_via_assets`，
    #: 由消费方自己相加 —— 多一个 `total` 字段就多一个"两处算法不一致"的机会。
    #: 两个数字与点位侧栏「轻资产（N）」用的是**同一个** `_station_light_rows`
    #: （`services/light.py`），所以侧栏那个 N 必然等于这两个相加。
    #:
    #: ⚠️ **不含库存**：`holder_kind='stock'` 的东西还没分配到任何点位，
    #: 即使 `stock_location` 自由文本里写了点位编码也不算（设计 §5.5）。
    light_direct: int = 0
    #: 装在这个点位上的主机里的配件（`holder_kind='asset'`，按宿主现算）
    light_via_assets: int = 0


class WorkstationMapCanvas(BaseModel):
    """画布的基准尺寸。前端不用再硬编码，将来换底图也只改这一处。"""

    width: int = 960
    height: int = 1320
    #: 网格对齐用。与前端视觉网格必须一致 ——
    #: 视觉网格 20px 而吸附网格 4px 的话，用户肉眼看不出"对齐了"。
    grid: int = 20


class WorkstationMap(BaseModel):
    """地图渲染用的一次性数据。

    `layout` 是平面图的建筑图元（地台/走廊/房间/墙体/前厅），
    由后端从默认平面图带出来 —— 前端不再写死。没有图时是空 layout，
    画布上只剩点位（这是有意的降级，好过整页报错）。
    """

    canvas: WorkstationMapCanvas = WorkstationMapCanvas()
    layout: dict = Field(default_factory=dict)
    floor_map_id: Optional[int] = None
    total: int = 0
    #: **有人**的点位数。斜杠 `/` 不算有人（与前端 `format.ts::hasOwner()` 同口径，
    #: 见 `services/ownership.py`）。它和下面的 `without_asset` 是**两个正交轴**。
    assigned: int = 0
    #: **一台设备都没挂**的点位数 —— 资产轴，**不是**"没人用"。
    #:
    #: ⚠️ **这个字段名字曾经叫 `empty`，是个陷阱**（2026-10-08 改名）：
    #: 它算的是「无资产」，而**前端的「空闲点位」算的是「无人」**（`total - assigned`）——
    #: **两个字段都叫"空闲"，指的是完全不同的事**，同一页上两个数字对不上，
    #: 而且没有任何报错（跟斜杠那个 bug 同一类病）。
    #:
    #: 改成 `without_asset` 之后名字自己说清是什么，不再依赖读文档：
    #: "没有人"要减一下算（`total - assigned`），"没设备"直接就是这个字段。
    without_asset: int = 0
    items: list[WorkstationOut] = []


class WorkstationAssetsRequest(BaseModel):
    """把一批资产挪到点位上 / 从点位摘下来。

    **落库方向永远是 assets.workstation_id**，点位表里不存资产列表 ——
    存两处必然对不上。
    """

    asset_ids: list[int] = Field(min_length=1)
    operator: Optional[str] = Field(default=None, max_length=64)


class WorkstationLinkageRequest(BaseModel):
    """关联操作建议的执行请求：给无归属点位新增使用人后，把勾选的资产使用人对上。

    `asset_ids` 允许为空 = 用户选了「暂不处理」，回包只报告不写库。
    `user_name` 必须是**非空**的使用人 —— 这个接口只做"填人"，不做"清人"。
    """

    user_name: str = Field(min_length=1, max_length=64)
    asset_ids: list[int] = Field(default_factory=list)
    operator: Optional[str] = Field(default=None, max_length=64)

    @field_validator("user_name")
    @classmethod
    def _user_not_placeholder(cls, v: str) -> str:
        name = v.strip()
        if not name:
            raise ValueError("使用人不能为空")
        if name in ("/", "／"):
            raise ValueError("使用人不能是占位符（/ 等于没填）")
        return name


class WorkstationUnlinkRequest(BaseModel):
    """清人建议的执行请求：点位使用人被清空时，把勾选的设备解除使用人。

    `asset_ids` 允许为空 = 用户选了「都保留」，回包只报告不写库。
    **没有 `user_name` 字段** —— 清谁由点位**当前**使用人决定（服务端读库），
    不接受客户端传人名，免得清错人。
    """

    asset_ids: list[int] = Field(default_factory=list)
    operator: Optional[str] = Field(default=None, max_length=64)


# --------------------------------------------------------------------------- #
# 地图盘点视图
# --------------------------------------------------------------------------- #
class MapInventoryAsset(BaseModel):
    """地图侧边栏里的单台资产 + 它在这个盘点任务里的结果。"""

    asset_id: int
    asset_code: str
    device_type_name: str = ""
    #: 已确认的图标标识（为空 = 未确认，前端按名称推断）
    device_type_icon: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    status: str = ""
    status_label: str = ""
    #: checked / abnormal / pending —— 直接用 InventoryResult，不另立一套枚举
    result: str
    result_label: str = ""
    operator: Optional[str] = None
    #: 盘点备注。**必须带上** —— 地图侧栏的「标记异常」会采集备注，
    #: 不带回来就变成"填了看不见"；反过来，从盘点列表填的备注也要能在地图上回显。
    #: 与 InventoryItemOut.note 是同一列（inventory_items.note），不是副本。
    note: Optional[str] = None
    checked_at: Optional[datetime] = None
    #: 盘点之后账面又被改过（inventory_items 存了盘点时刻的快照，可比）
    changed_since: bool = False
    snapshot_user: Optional[str] = None
    snapshot_location: Optional[str] = None


class MapInventoryWorkstation(BaseModel):
    """一个点位在这次盘点里的状态。

    state 五种取值：
      checked / abnormal / pending —— 由该点位下**属于本次任务**的资产 result 聚合，不落库
      empty                        —— 这位置一台资产都没有
      not_in_scope                 —— 有资产，但不属于当前选中的这次盘点（本次不盘）

    **empty / not_in_scope 两种中性态只存在于这个视图层**，不会写进 inventory_items ——
    它们是"查不到对应行"推出来的，不是盘点结果。给 InventoryResult 加第四态会污染
    导出、筛选和统计，因为那是**资产**的盘点结果，而空点位不是资产。
    """

    workstation_id: int
    code: str
    x: int
    y: int
    facing: str = "down"
    user_name: Optional[str] = None
    room: Optional[str] = None
    #: checked / abnormal / pending / empty / not_in_scope
    state: str
    state_label: str = ""
    #: 该点位下**属于本次任务**的资产明细（empty / not_in_scope 时为空）
    assets: list[MapInventoryAsset] = []
    #: 该点位下资产总数（含不属于本次任务的）—— 用来区分 empty 与 not_in_scope
    asset_count: int = 0


class MapInventoryView(BaseModel):
    """地图盘点视图。一次拿全图状态，避免前端为 62 个点位发 62 次请求。"""

    task: InventoryTaskOut
    canvas: WorkstationMapCanvas = WorkstationMapCanvas()
    workstations: list[MapInventoryWorkstation] = []
    #: 各状态的**点位**计数（含 empty / not_in_scope）
    counts: dict[str, int] = {}
    #: 进度分母用 task.total / task.checked —— 那是**资产**维度。
    #: 盘点的对象是资产：一个点位可能挂 2 台，也可能 0 台，
    #: 拿点位数当分母在任何点位挂 2 台资产时都会失准。
    #: 「没有被任何点位认领」的资产数 —— 盘点时最该被看见的一批（没位置 = 没人管）
    unassigned: int = 0
    #: 有点位、但不属于本次任务的资产数（这些设备本次不盘）
    out_of_scope: int = 0


# --------------------------------------------------------------------------- #
# 点位导入
# --------------------------------------------------------------------------- #
class WorkstationImportRow(BaseModel):
    """一条点位的判定结果 —— 预览与落库结果共用同一份结构。"""

    row: int
    code: str = ""
    action: str
    action_label: str = ""
    errors: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []


class WorkstationImportPreview(BaseModel):
    """导入预览。**不写任何数据**，只回"打算怎么改"。"""

    filename: str
    total: int = 0
    create: int = 0
    update: int = 0
    unchanged: int = 0
    error: int = 0
    will_write: int = 0
    can_commit: bool = False
    empty: bool = False
    #: schema 闸门。挡住"把别的 JSON 传进来"这类误操作，
    #: 将来格式升级成 @2 时这里也是明确的分岔点，不靠字段名瞎猜。
    schema_ok: bool = True
    schema_note: str = ""
    rows: list[WorkstationImportRow] = []
    rows_truncated: bool = False


class WorkstationImportResult(WorkstationImportPreview):
    """落库结果。结构同预览 —— 两边跑的是同一段 `build_plan`，
    所以不存在"预览说没事、一点确认就报错"。"""
