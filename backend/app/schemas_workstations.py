"""工位（资产空间地图）的请求 / 响应结构。

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
    """新增工位。code 留空则由后端生成 —— 不要交给前端算，并发会撞。"""

    code: Optional[str] = Field(default=None, max_length=32)
    x: int
    y: int
    facing: Optional[FacingLiteral] = None
    user_name: Optional[str] = Field(default=None, max_length=64)
    room: Optional[str] = Field(default=None, max_length=64)
    operator: Optional[str] = Field(default=None, max_length=64)

    _clean = field_validator("code", "user_name", "room", "operator", mode="before")(_blank_to_none)


class WorkstationUpdate(BaseModel):
    """编辑工位。只传要改的字段；未传的保持原值。

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
    """批量生成工位（画一片区域 → 行列排布）。

    **为什么必须是独立接口而不是循环调 `POST /api/workstations`**：
    「一次批量操作 = 1 条审计」是全系统口径（批量导入 30 台设备也是 1 条）。
    循环 24 次会往审计页灌 24 条「新增工位」，把真正的资产操作淹掉。

    ## 参数语义
    - `count` 是**总数**，不是"列数 × 行数"（前端算好了再传，避免两边各算一遍还同意不了）。
    - `cols` 只影响排布（一行几个），`facing` 支持 `alternate` —— 现实里面对面的工位
      朝向是反的，一个"全部朝下"的批量工具做出来的图会假得刺眼。
    - `start_code` / `code_prefix`：**编码建议是给用户的，真相在后端**。
      前端面板显示"将生成 W63~W86"只是预览；真分配时后端在事务里查全量 code 兜底，
      撞了就 409，**绝不静默跳号**（因为已删工位的编码不释放，跳号会让用户对不上账）。
    """

    count: int = Field(ge=1, le=200, description="生成总数量")
    origin_x: int = Field(description="起始 X（左上角第一个工位）")
    origin_y: int = Field(description="起始 Y")
    step_x: int = Field(default=90, ge=20, le=400, description="横向间距")
    step_y: int = Field(default=66, ge=20, le=400, description="纵向间距")
    cols: int = Field(default=6, ge=1, le=30, description="每行几个（决定换行）")
    facing: str = Field(default="down", description="down / up / alternate（交替，面对面）")
    code_prefix: Optional[str] = Field(default="W", max_length=8, description="编码前缀")
    start_code: Optional[str] = Field(
        default=None, max_length=32, description="起始编码（如 W63）。不传则由后端按现有最大值续号"
    )
    room: Optional[str] = Field(default=None, max_length=64, description="房间标签，传给每个工位")
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
    #: 落库后的工位，供前端直接画上去（省一次全量拉取）
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
    #: 工位上挂了几台资产 —— 派生查询，不落库（红线 2）
    asset_count: int = 0


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
    画布上只剩工位（这是有意的降级，好过整页报错）。
    """

    canvas: WorkstationMapCanvas = WorkstationMapCanvas()
    layout: dict = Field(default_factory=dict)
    floor_map_id: Optional[int] = None
    total: int = 0
    assigned: int = 0
    empty: int = 0
    items: list[WorkstationOut] = []


class WorkstationAssetsRequest(BaseModel):
    """把一批资产挪到工位上 / 从工位摘下来。

    **落库方向永远是 assets.workstation_id**，工位表里不存资产列表 ——
    存两处必然对不上。
    """

    asset_ids: list[int] = Field(min_length=1)
    operator: Optional[str] = Field(default=None, max_length=64)


# --------------------------------------------------------------------------- #
# 地图盘点视图
# --------------------------------------------------------------------------- #
class MapInventoryAsset(BaseModel):
    """地图侧边栏里的单台资产 + 它在这个盘点任务里的结果。"""

    asset_id: int
    asset_code: str
    device_type_name: str = ""
    brand: Optional[str] = None
    model: Optional[str] = None
    status: str = ""
    status_label: str = ""
    #: checked / abnormal / pending —— 直接用 InventoryResult，不另立一套枚举
    result: str
    result_label: str = ""
    operator: Optional[str] = None
    checked_at: Optional[datetime] = None
    #: 盘点之后账面又被改过（inventory_items 存了盘点时刻的快照，可比）
    changed_since: bool = False
    snapshot_user: Optional[str] = None
    snapshot_location: Optional[str] = None


class MapInventoryWorkstation(BaseModel):
    """一个工位在这次盘点里的状态。

    state 五种取值：
      checked / abnormal / pending —— 由该工位下**属于本次任务**的资产 result 聚合，不落库
      empty                        —— 这位置一台资产都没有
      not_in_scope                 —— 有资产，但不属于当前选中的这次盘点（本次不盘）

    **empty / not_in_scope 两种中性态只存在于这个视图层**，不会写进 inventory_items ——
    它们是"查不到对应行"推出来的，不是盘点结果。给 InventoryResult 加第四态会污染
    导出、筛选和统计，因为那是**资产**的盘点结果，而空工位不是资产。
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
    #: 该工位下**属于本次任务**的资产明细（empty / not_in_scope 时为空）
    assets: list[MapInventoryAsset] = []
    #: 该工位下资产总数（含不属于本次任务的）—— 用来区分 empty 与 not_in_scope
    asset_count: int = 0


class MapInventoryView(BaseModel):
    """地图盘点视图。一次拿全图状态，避免前端为 62 个工位发 62 次请求。"""

    task: InventoryTaskOut
    canvas: WorkstationMapCanvas = WorkstationMapCanvas()
    workstations: list[MapInventoryWorkstation] = []
    #: 各状态的**工位**计数（含 empty / not_in_scope）
    counts: dict[str, int] = {}
    #: 进度分母用 task.total / task.checked —— 那是**资产**维度。
    #: 盘点的对象是资产：一个工位可能挂 2 台，也可能 0 台，
    #: 拿工位数当分母在任何工位挂 2 台资产时都会失准。
    #: 「没有被任何工位认领」的资产数 —— 盘点时最该被看见的一批（没位置 = 没人管）
    unassigned: int = 0
    #: 有工位、但不属于本次任务的资产数（这些设备本次不盘）
    out_of_scope: int = 0


# --------------------------------------------------------------------------- #
# 工位导入
# --------------------------------------------------------------------------- #
class WorkstationImportRow(BaseModel):
    """一条工位的判定结果 —— 预览与落库结果共用同一份结构。"""

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
