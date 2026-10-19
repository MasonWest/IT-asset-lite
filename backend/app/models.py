"""ORM 模型。

表清单：
- device_types          设备类型（可自定义：主机 / 显示器 / 键盘 …）
- assets                资产台账
- asset_relations       主机 ←→ 显示器 配对关系（一台主机可带多台显示器）
- asset_events          资产变更历史（时间线），所有状态流转 / 配对 / 盘点都往这里落一条
- inventory_tasks       盘点任务
- inventory_items       盘点明细（每个任务 × 每台设备一行）
- workstations          点位（资产空间地图上的一个格子）
- floor_maps            平面图（点位站的建筑图元：地台 / 走廊 / 房间 / 墙体）
- audit_logs            操作审计日志（谁、从哪个 IP、什么时间、干了什么）
- light_categories      轻资产品类（MK270 键鼠 / SSD …），决定追踪粒度
- light_items           轻资产单件条目（**只有单件模式的品类才产生行**）
- light_movements       轻资产流转流水（append-only，**当前状态的唯一真相**）

轻资产（不建正式资产编码、不进 assets 台账的 IT 配件）刻意独立成三张表：
  - **不复用 assets**：它没有 asset_code，混进去会污染台账统计口径与二维码/标签打印；
  - **不复用 asset_events**：那是以 `asset_id` 为 NOT NULL 外键的"设备视角"流水，
    轻资产的历史必须独立记录（配件可能在主机流转前后被拆装、更换，靠宿主日志推断会丢真因）；
  - **不复用 asset_relations**：配对语义是"主机 ↔ 显示器"固定两类，硬塞配件会污染配对页与套装聚合。
  可复用的是**范式**（预览/校验/审计/派生），不是表。

asset_events 和 audit_logs 长得像，但视角不同，别合并：
  asset_events 是「设备视角」—— 挂在某台设备下、给那台设备的事件时间线看；
  audit_logs   是「操作者视角」—— 一次操作一条、不管碰了几台设备，带 IP 和成功与否。
  批量导入一次改了 30 台设备：时间线要 30 条（每台各自看得到自己被改过），
  审计只要 1 条（一次"导入"操作）。合成一张表，两个视角都会瘸。

关于「使用人」「存放位置」：故意不做成独立的表。
筛选下拉里的候选值直接从 assets 现有数据里 distinct 出来即可，
这样用户随手输入一个新使用人就能用，不用先去维护一张人员表。

关于设备类型的 category：类型名字允许用户随便改（"主机"改成"机箱"就废了），
所以「能不能当主机用 / 是不是显示器」不能靠名字猜，得单独存一列。

关于时间字段：**一律用本地时间，不用 UTC。**
SQLAlchemy 的 `server_default=func.now()` 在 SQLite 里编译成 `CURRENT_TIMESTAMP`，
而 SQLite 的 CURRENT_TIMESTAMP 是 **UTC** —— 在东八区的机器上写进去的时间会比实际早 8 小时。
本系统是单办公室局域网工具，用户点开时间线看到的就是墙上时钟，所以统一用 `default=datetime.now`
（Python 侧默认值，走本地时区）。老库里那些被写成 UTC 的时间由 database.ensure_schema()
一次性校正过来。

不用 server_default 的取舍：直接裸 SQL 往表里 INSERT 时没人给默认值了。
但本项目所有写入都走 ORM，这个代价可以接受；换来的是"库里存的就是界面上显示的"，
把 .db 文件丢进任何 SQLite 查看器里看到的都是人话时间。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class AssetStatus:
    """资产状态（固定枚举，不做自定义）。"""

    IN_USE = "in_use"
    IDLE = "idle"
    REPAIR = "repair"
    SCRAPPED = "scrapped"

    LABELS: dict[str, str] = {
        IN_USE: "在用",
        IDLE: "在库",
        REPAIR: "维修中",
        SCRAPPED: "已报废",
    }

    ALL: tuple[str, ...] = (IN_USE, IDLE, REPAIR, SCRAPPED)


class DeviceCategory:
    """设备类别 —— 决定这台设备能不能参与配对。"""

    HOST = "host"
    DISPLAY = "display"
    OTHER = "other"

    LABELS: dict[str, str] = {
        HOST: "主机类",
        DISPLAY: "显示类",
        OTHER: "其他",
    }

    ALL: tuple[str, ...] = (HOST, DISPLAY, OTHER)

    #: 新建类型时按名字猜一个默认值，猜不中就是 other，用户可以随时改
    @classmethod
    def guess(cls, name: str) -> str:
        n = (name or "").strip()
        if not n:
            return cls.OTHER
        if "显示器" in n or "屏幕" in n:
            return cls.DISPLAY
        if any(k in n for k in ("主机", "台式", "笔记本", "服务器", "工作站")):
            return cls.HOST
        return cls.OTHER


class AssetOperation:
    """状态流转动作（对外的「操作」按钮）。

    每个动作 = 目标状态 + 允许的来源状态。所有动作都会往 asset_events 落一条记录。
    """

    CHECKOUT = "checkout"          # 领用（也用于转交他人）
    RETURN = "return"              # 归还入库
    REPAIR = "repair"              # 送修
    REPAIR_DONE = "repair_done"    # 维修完成
    SCRAP = "scrap"                # 报废
    RESTORE = "restore"            # 误报废后恢复入库

    LABELS: dict[str, str] = {
        CHECKOUT: "领用",
        RETURN: "归还",
        REPAIR: "送修",
        REPAIR_DONE: "维修完成",
        SCRAP: "报废",
        RESTORE: "恢复入库",
    }


    ALL: tuple[str, ...] = (CHECKOUT, RETURN, REPAIR, REPAIR_DONE, SCRAP, RESTORE)

    #: 动作执行后的目标状态
    TARGET_STATUS: dict[str, str] = {
        CHECKOUT: AssetStatus.IN_USE,
        RETURN: AssetStatus.IDLE,
        REPAIR: AssetStatus.REPAIR,
        REPAIR_DONE: AssetStatus.IDLE,
        SCRAP: AssetStatus.SCRAPPED,
        RESTORE: AssetStatus.IDLE,
    }

    #: 允许的来源状态；不在集合里就直接 409 挡住
    ALLOWED_FROM: dict[str, tuple[str, ...]] = {
        CHECKOUT: (AssetStatus.IDLE, AssetStatus.IN_USE),
        RETURN: (AssetStatus.IN_USE,),
        REPAIR: (AssetStatus.IDLE, AssetStatus.IN_USE),
        REPAIR_DONE: (AssetStatus.REPAIR,),
        SCRAP: (AssetStatus.IDLE, AssetStatus.IN_USE, AssetStatus.REPAIR),
        RESTORE: (AssetStatus.SCRAPPED,),
    }

    #: 必须填使用人的动作
    REQUIRES_USER: tuple[str, ...] = (CHECKOUT,)

    #: UI 上默认展示的按钮（按业务顺序）
    UI_ORDER: tuple[str, ...] = (CHECKOUT, RETURN, REPAIR, REPAIR_DONE, SCRAP, RESTORE)

    @classmethod
    def actions_for(cls, status: str) -> list[str]:
        """当前状态下还能做的动作，前端按钮直接照着渲染。"""
        return [a for a in cls.UI_ORDER if status in cls.ALLOWED_FROM.get(a, ())]


class AssetEventType:
    """变更历史的事件类型。"""

    CREATE = "create"                # 录入台账
    UPDATE = "update"                # 信息变更
    STATUS = "status"                # 直接编辑表单里改了状态（没走「操作」按钮）
    PAIR = "pair"                    # 绑定配对
    UNPAIR = "unpair"                # 解除配对
    INVENTORY = "inventory"          # 盘点标记
    DELETE = "delete"                # 删除（先记后删，实际不留）
    #: 点位迁移（整点位业务迁移 / 多台资产换点位）。
    #: **与 UPDATE 分开**：迁移会一次动十几台设备，在履历上应该一眼可辨，
    #: 而不是让人从"信息变更：所在点位 空 → W01"里去猜这是不是一次批量迁移。
    RELOCATE = "relocate"
    #: 关系差异被**确认为例外** / 例外被撤销。
    #: 它不是"设备被改了"，而是"关于这台设备的一条业务决定" —— 之所以也写进履历，
    #: 是因为看履历的人有权知道"它当初被认定过是合理的、理由是什么"。
    #: ⚠️ 只有**设备级**的例外写它（点位级没有对应设备，写在点位侧栏的例外表里）。
    #: ⚠️ 例外的**失效**不写事件 —— 失效是算出来的（指纹比对），落库就违反红线 2。
    RELATION_EXCEPTION = "relation_exception"

    LABELS: dict[str, str] = {
        CREATE: "录入台账",
        UPDATE: "信息变更",
        STATUS: "状态变更",
        PAIR: "绑定配对",
        UNPAIR: "解除配对",
        INVENTORY: "盘点标记",
        RELOCATE: "点位迁移",
        RELATION_EXCEPTION: "关系例外",
    }

    @classmethod
    def label(cls, event_type: str) -> str:
        if event_type in cls.LABELS:
            return cls.LABELS[event_type]
        if event_type in AssetOperation.LABELS:
            return AssetOperation.LABELS[event_type]
        return event_type


class InventoryResult:
    """盘点明细结果。"""

    PENDING = "pending"      # 未盘（账上有、还没盘到）
    CHECKED = "checked"      # 已盘
    ABNORMAL = "abnormal"    # 异常

    LABELS: dict[str, str] = {
        PENDING: "未盘",
        CHECKED: "已盘",
        ABNORMAL: "异常",
    }

    ALL: tuple[str, ...] = (PENDING, CHECKED, ABNORMAL)


class InventoryTaskStatus:
    OPEN = "open"
    CLOSED = "closed"

    LABELS: dict[str, str] = {OPEN: "进行中", CLOSED: "已结束"}
    ALL: tuple[str, ...] = (OPEN, CLOSED)


class AuditAction:
    """审计日志的操作类型。

    粒度取「业务动作」而不是「HTTP 请求」——
    用户看审计页想知道的是"谁把设备报废了"，不是"谁 POST 了 /api/assets/7/operations"。
    """

    ASSET_CREATE = "asset_create"
    ASSET_UPDATE = "asset_update"
    ASSET_DELETE = "asset_delete"
    ASSET_OPERATE = "asset_operate"
    ASSET_PAIR = "asset_pair"
    ASSET_UNPAIR = "asset_unpair"
    ASSET_REBIND = "asset_rebind"
    ASSET_IMPORT = "asset_import"
    ASSET_EXPORT = "asset_export"
    #: 点位迁移助手（整个点位 / 多台资产换点位）。与 ASSET_UPDATE 分开：
    #: 一次迁移可能动十几台设备和两个点位，在审计页应能一眼筛出来。
    RELOCATION_APPLY = "relocation_apply"

    #: 关联操作建议（给无归属点位新增使用人时，顺手把勾选的资产使用人对上）。
    #: 与 ASSET_UPDATE 分开的理由同 RELOCATION_APPLY：一次操作可能动多台设备，
    #: 审计页要能一眼筛出"这是一次关联同步"，而不是 N 条散落的编辑。
    ASSET_LINKAGE = "asset_linkage"
    #: 清人场景的姊妹动作：点位使用人被清空时，把仍登记在原使用人名下的设备解除。
    #: 与 ASSET_LINKAGE 分开（而不是用一个带方向的字段）：审计页筛"谁把设备划给某人"
    #: 与"谁把设备从某人名下拿掉"是两件不同的事，方向混在一条里等于要读 summary 才知道。
    ASSET_UNLINK = "asset_unlink"

    #: 关系核对的「例外确认」。**这是一条业务决定，必须留痕** ——
    #: 不是问题状态的开关（问题状态是每次现算的派生结果，不落库）。
    RELATION_EXCEPTION_CONFIRM = "relation_exception_confirm"
    RELATION_EXCEPTION_REVOKE = "relation_exception_revoke"

    INVENTORY_CREATE = "inventory_create"
    INVENTORY_MARK = "inventory_mark"
    INVENTORY_CLOSE = "inventory_close"
    INVENTORY_REOPEN = "inventory_reopen"
    INVENTORY_DELETE = "inventory_delete"

    TYPE_CREATE = "type_create"
    TYPE_UPDATE = "type_update"
    TYPE_DELETE = "type_delete"

    #: 点位（资产空间地图）。move 与 update 刻意分开 ——
    #: 「调整布局」和「改点位归属」是两件事：前者是排版动作，后者改的是业务归属。
    #: 合成一个的话，用户在审计页想筛「谁改过点位归属」会被几十条拖拽记录淹掉。
    WORKSTATION_CREATE = "workstation_create"
    WORKSTATION_UPDATE = "workstation_update"
    WORKSTATION_MOVE = "workstation_move"
    WORKSTATION_DELETE = "workstation_delete"

    #: 平面图（地图编辑器）。与点位分开：
    #: 「画平面图」改的是建筑结构，「摆点位」改的是座位 —— 两件事在审计页要能筛得开。
    #: 一次保存整张图 = 1 条 map_update（图元全在前端一起编辑，见 FloorMap 的说明）。
    MAP_CREATE = "map_create"
    MAP_UPDATE = "map_update"
    MAP_DELETE = "map_delete"

    AUDIT_EXPORT = "audit_export"

    #: 轻资产（不建正式编码的 IT 配件）。粒度与 ASSET_* 保持一致：
    #: 一个 `LIGHT_OPERATE` 覆盖入库/分配/归还/拆卸/转移/报损/调整 ——
    #: 具体是哪种操作写在 summary 与 detail.op 里，不按 op 拆成一堆动作名
    #: （对照 AssetOperation 六种流转也只对应一个 ASSET_OPERATE）。
    LIGHT_CATEGORY_CREATE = "light_category_create"
    LIGHT_CATEGORY_UPDATE = "light_category_update"
    LIGHT_CATEGORY_DELETE = "light_category_delete"
    #: ⚠️ **已废弃（取消单件追踪）：不再有代码产生这三个值**，但枚举值本身
    #: **必须保留** —— 老库的 `audit_logs` 里冻结着它们，删掉枚举会让审计页
    #: 把历史记录显示成原始字符串。审计记录不是文案，是证据，不追改。
    LIGHT_ITEM_CREATE = "light_item_create"
    LIGHT_ITEM_UPDATE = "light_item_update"
    LIGHT_ITEM_DELETE = "light_item_delete"
    LIGHT_OPERATE = "light_operate"
    LIGHT_IMPORT = "light_import"

    LABELS: dict[str, str] = {
        ASSET_CREATE: "新增资产",
        ASSET_UPDATE: "编辑资产",
        ASSET_DELETE: "删除资产",
        ASSET_OPERATE: "状态流转",
        ASSET_PAIR: "绑定显示器",
        ASSET_UNPAIR: "解除配对",
        ASSET_REBIND: "更换配对",
        ASSET_IMPORT: "批量导入",
        ASSET_EXPORT: "导出资产",
        RELOCATION_APPLY: "点位迁移",
        ASSET_LINKAGE: "关联使用人",
        ASSET_UNLINK: "解除使用人",
        RELATION_EXCEPTION_CONFIRM: "确认关系例外",
        RELATION_EXCEPTION_REVOKE: "撤销关系例外",
        INVENTORY_CREATE: "发起盘点",
        INVENTORY_MARK: "盘点标记",
        INVENTORY_CLOSE: "结束盘点",
        INVENTORY_REOPEN: "重开盘点",
        INVENTORY_DELETE: "删除盘点",
        TYPE_CREATE: "新增设备类型",
        TYPE_UPDATE: "编辑设备类型",
        TYPE_DELETE: "删除设备类型",
        WORKSTATION_CREATE: "新增点位",
        WORKSTATION_UPDATE: "编辑点位",
        WORKSTATION_MOVE: "调整点位布局",
        WORKSTATION_DELETE: "删除点位",
        MAP_CREATE: "新建平面图",
        MAP_UPDATE: "编辑平面图",
        MAP_DELETE: "删除平面图",
        LIGHT_CATEGORY_CREATE: "新增轻资产品类",
        LIGHT_CATEGORY_UPDATE: "编辑轻资产品类",
        LIGHT_CATEGORY_DELETE: "删除轻资产品类",
        LIGHT_ITEM_CREATE: "新增轻资产单件",
        LIGHT_ITEM_UPDATE: "编辑轻资产单件",
        LIGHT_ITEM_DELETE: "注销轻资产单件",
        LIGHT_OPERATE: "轻资产流转",
        LIGHT_IMPORT: "导入轻资产流水",
        AUDIT_EXPORT: "导出审计日志",
    }

    #: 审计动作 → **图标标识**（前端 `utils/icons.ts::ICON_PATHS` 的 key）。
    #:
    #: ⚠️ **这里返回的是标识，不是图标字符**（2026-10-19 改）。
    #: 原来存的是 emoji，于是 emoji 事实上成了接口的展示协议 ——
    #: 前端收到一个 emoji 字符串却没法保证它在别的系统字体下长什么样，
    #: 而且前端当时根本没用它（自己另有一份 18 项的映射，38 个动作里有 20 个没图标）。
    #: 现在后端只给"这是哪个图标"，怎么画由前端那张唯一路径表决定。
    ICONS: dict[str, str] = {
        ASSET_CREATE: "sparkles",
        ASSET_UPDATE: "edit",
        ASSET_DELETE: "trash",
        ASSET_OPERATE: "refresh",
        ASSET_PAIR: "link",
        ASSET_UNPAIR: "scissors",
        ASSET_REBIND: "shuffle",
        ASSET_IMPORT: "download",
        ASSET_EXPORT: "upload",
        RELOCATION_APPLY: "truck",
        ASSET_LINKAGE: "handshake",
        ASSET_UNLINK: "unlock",
        RELATION_EXCEPTION_CONFIRM: "handshake",
        RELATION_EXCEPTION_REVOKE: "undo",
        INVENTORY_CREATE: "compass",
        INVENTORY_MARK: "clipboard",
        INVENTORY_CLOSE: "flag",
        INVENTORY_REOPEN: "undo",
        INVENTORY_DELETE: "trash",
        TYPE_CREATE: "tag",
        TYPE_UPDATE: "tag",
        TYPE_DELETE: "tag",
        WORKSTATION_CREATE: "chair",
        WORKSTATION_UPDATE: "chair",
        WORKSTATION_MOVE: "compass",
        WORKSTATION_DELETE: "trash",
        MAP_CREATE: "map",
        MAP_UPDATE: "map",
        MAP_DELETE: "map",
        LIGHT_CATEGORY_CREATE: "box",
        LIGHT_CATEGORY_UPDATE: "box",
        LIGHT_CATEGORY_DELETE: "box",
        LIGHT_ITEM_CREATE: "chip",
        LIGHT_ITEM_UPDATE: "chip",
        LIGHT_ITEM_DELETE: "chip",
        LIGHT_OPERATE: "refresh",
        LIGHT_IMPORT: "download",
        AUDIT_EXPORT: "upload",
    }

    #: 筛选下拉里的分组顺序，前端直接照这个渲染
    GROUPS: dict[str, tuple[str, ...]] = {
        "资产台账": (ASSET_CREATE, ASSET_UPDATE, ASSET_DELETE, ASSET_OPERATE),
        "主机 / 显示器配对": (ASSET_PAIR, ASSET_UNPAIR, ASSET_REBIND),
        "盘点": (INVENTORY_CREATE, INVENTORY_MARK, INVENTORY_CLOSE, INVENTORY_REOPEN, INVENTORY_DELETE),
        "点位与地图": (
            WORKSTATION_CREATE,
            WORKSTATION_UPDATE,
            WORKSTATION_MOVE,
            WORKSTATION_DELETE,
            RELOCATION_APPLY,
            ASSET_LINKAGE,
            ASSET_UNLINK,
            RELATION_EXCEPTION_CONFIRM,
            RELATION_EXCEPTION_REVOKE,
            MAP_CREATE,
            MAP_UPDATE,
            MAP_DELETE,
        ),
        "导入导出": (ASSET_IMPORT, ASSET_EXPORT, LIGHT_IMPORT, AUDIT_EXPORT),
        "轻资产": (
            LIGHT_CATEGORY_CREATE,
            LIGHT_CATEGORY_UPDATE,
            LIGHT_CATEGORY_DELETE,
            LIGHT_ITEM_CREATE,
            LIGHT_ITEM_UPDATE,
            LIGHT_ITEM_DELETE,
            LIGHT_OPERATE,
        ),
        "设备类型": (TYPE_CREATE, TYPE_UPDATE, TYPE_DELETE),
    }

    ALL: tuple[str, ...] = tuple(k for keys in GROUPS.values() for k in keys)

    @classmethod
    def label(cls, action: str) -> str:
        return cls.LABELS.get(action, action)

    @classmethod
    def icon(cls, action: str) -> str:
        return cls.ICONS.get(action, "dot")


class AuditTarget:
    """审计记录指向的对象类型 —— 决定前端"操作对象"那一列怎么跳转。"""

    ASSET = "asset"
    INVENTORY = "inventory"
    DEVICE_TYPE = "device_type"
    WORKSTATION = "workstation"
    MAP = "map"
    FILE = "file"
    SYSTEM = "system"
    #: 轻资产。品类与单件分开 —— 审计页「操作对象」那一列的跳转目标不同
    #: （一个跳到品类详情，一个跳到单件所在页面）。
    LIGHT_CATEGORY = "light_category"
    #: ⚠️ **已废弃（取消单件追踪）**：新代码不再产生它，但老库的 `audit_logs` 里
    #: 冻结着这个值，删掉会让审计页把历史记录显示成原始字符串。保留。
    LIGHT_ITEM = "light_item"

    LABELS: dict[str, str] = {
        ASSET: "设备",
        INVENTORY: "盘点任务",
        DEVICE_TYPE: "设备类型",
        WORKSTATION: "点位",
        MAP: "平面图",
        FILE: "文件",
        SYSTEM: "系统",
        LIGHT_CATEGORY: "轻资产品类",
        LIGHT_ITEM: "轻资产单件",
    }


class AuditStatus:
    """审计结果。失败也要留痕 —— 「谁试过但没干成」同样是审计要回答的问题。"""

    SUCCESS = "success"
    FAILED = "failed"

    LABELS: dict[str, str] = {SUCCESS: "成功", FAILED: "失败"}


# --------------------------------------------------------------------------- #
# 设备类型
# --------------------------------------------------------------------------- #
class DeviceType(Base):
    """设备类型。"""

    __tablename__ = "device_types"
    __table_args__ = (UniqueConstraint("name", name="uq_device_type_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: host / display / other —— 老库通过 ensure_schema() 补列并回填
    category: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeviceCategory.OTHER, server_default=DeviceCategory.OTHER
    )
    #: 界面图标标识（`frontend/src/utils/icons.ts::ICON_PATHS` 的 key，如 `tower`）。
    #:
    #: **刻意存标识而不是 emoji、也不是 SVG 源码**：图标换一套视觉时不必动库，
    #: 前端也只需要一张路径表。允许为空 —— 空值表示"用户还没确认过图标"，
    #: 界面据此按名称推断一个**建议**图标（只显示、不写库，见 format.ts::typeIcon）。
    #:
    #: ⚠️ 与 `category` 同样的理由：**图标不按名字自动判断**。
    #: 用户完全可以把「主机」改名成「工控机」，靠名字猜图标同样会一改就废。
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    assets: Mapped[list["Asset"]] = relationship(back_populates="device_type")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeviceType {self.id} {self.name} ({self.category})>"


# --------------------------------------------------------------------------- #
# 资产台账
# --------------------------------------------------------------------------- #
class Asset(Base):
    """资产台账。"""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    asset_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    finance_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    device_type_id: Mapped[int] = mapped_column(
        ForeignKey("device_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AssetStatus.IN_USE, index=True)

    user_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    #: 挂在哪个点位上（资产空间地图）。绝大多数资产不在点位上，所以可空。
    #: RESTRICT：点位上还挂着资产时，数据库层面不允许把点位硬删掉 ——
    #: 这是最后一道网，面向用户的拦截应该是应用层的 409 + 人话文案。
    #: 点位正常退役走的是软删除（active=False），那条路径根本不触发外键。
    workstation_id: Mapped[int | None] = mapped_column(
        ForeignKey("workstations.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    device_type: Mapped[DeviceType] = relationship(back_populates="assets", lazy="joined")
    workstation: Mapped["Workstation | None"] = relationship(
        back_populates="assets", lazy="joined"
    )

    #: 作为「主机」侧的配对关系
    host_relations: Mapped[list["AssetRelation"]] = relationship(
        back_populates="host",
        foreign_keys="AssetRelation.host_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    #: 作为「显示器」侧的配对关系
    monitor_relations: Mapped[list["AssetRelation"]] = relationship(
        back_populates="monitor",
        foreign_keys="AssetRelation.monitor_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    events: Mapped[list["AssetEvent"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssetEvent.created_at.desc(), AssetEvent.id.desc()",
    )
    inventory_items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def category(self) -> str:
        return self.device_type.category if self.device_type else DeviceCategory.OTHER

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset {self.id} {self.asset_code}>"


# --------------------------------------------------------------------------- #
# 主机 ←→ 显示器 配对
# --------------------------------------------------------------------------- #
class AssetRelation(Base):
    """主机与显示器的配对关系。

    解绑不做物理删除 —— 把 active 置 0 并记下解绑时间/操作人，
    这样「换绑」的完整轨迹（谁从哪台主机换到哪台）能一直查得到。
    """

    __tablename__ = "asset_relations"
    #: 「一台显示器同一时刻只能挂一台主机」用应用层校验保证，不建唯一索引。
    #: 原因：跨库的「部分唯一索引」写法不通用（MySQL 根本不支持），
    #: 建普通唯一索引又会让解绑后的历史记录互相冲突，把「换绑」直接锁死。
    __table_args__ = (
        Index("ix_asset_relation_monitor_active", "monitor_id", "active"),
        Index("ix_asset_relation_host_active", "host_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    host_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    monitor_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    released_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    release_note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    host: Mapped[Asset] = relationship(
        back_populates="host_relations", foreign_keys=[host_id], lazy="joined"
    )
    monitor: Mapped[Asset] = relationship(
        back_populates="monitor_relations", foreign_keys=[monitor_id], lazy="joined"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssetRelation {self.id} host={self.host_id} monitor={self.monitor_id} active={self.active}>"


# --------------------------------------------------------------------------- #
# 变更历史
# --------------------------------------------------------------------------- #
class AssetEvent(Base):
    """资产变更历史 —— 每台设备的时间线。"""

    __tablename__ = "asset_events"
    __table_args__ = (Index("ix_asset_event_asset_created", "asset_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    event_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)

    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: 关联对象 / 变更明细，JSON 文本。JSON 列在各数据库行为不一致，用 Text 最稳
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, index=True
    )

    asset: Mapped[Asset] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssetEvent {self.id} asset={self.asset_id} {self.event_type}>"


# --------------------------------------------------------------------------- #
# 盘点
# --------------------------------------------------------------------------- #
class InventoryTask(Base):
    """一次盘点任务。"""

    __tablename__ = "inventory_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=InventoryTaskStatus.OPEN, index=True
    )

    #: 发起时的筛选条件快照（JSON），用于回显「这次盘的是哪些设备」
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)

    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InventoryTask {self.id} {self.name} {self.status}>"


class InventoryItem(Base):
    """盘点明细：一个任务 × 一台设备。"""

    __tablename__ = "inventory_items"
    __table_args__ = (UniqueConstraint("task_id", "asset_id", name="uq_inventory_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    task_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    result: Mapped[str] = mapped_column(
        String(16), nullable=False, default=InventoryResult.PENDING, index=True
    )

    #: 盘点时刻的账面值快照 —— 用来发现「账上在人手上、实际不在」这类差异
    snapshot_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    snapshot_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped[InventoryTask] = relationship(back_populates="items")
    asset: Mapped[Asset] = relationship(back_populates="inventory_items", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InventoryItem {self.id} task={self.task_id} asset={self.asset_id} {self.result}>"


# --------------------------------------------------------------------------- #
# 操作审计
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    """操作审计日志。

    刻意不建外键 —— 审计必须比业务数据活得久：
    资产被删了，那条「谁删的」的记录还得在；盘点任务删了，「谁发起的」也不能跟着没。
    所以 target_id 只是个数字，不挂约束，也不跟随级联删除。

    同样刻意不提供任何写 / 改 / 删接口。这张表只有两个动作：追加、查询。
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_log_created", "created_at"),
        Index("ix_audit_log_action_created", "action", "created_at"),
        Index("ix_audit_log_operator_created", "operator", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, index=True
    )

    #: 系统没有账号体系（约束里明确不做登录），所以操作人允许为空 ——
    #: 空值只说明"这台机器的这个浏览器没设名字"，IP 仍然能区分来源。
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    #: 客户端 IP。局域网里 NAT 之后可能多人共用一个出口 IP，所以它是辅助证据不是身份
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    target_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AuditTarget.SYSTEM, server_default=AuditTarget.SYSTEM
    )
    #: 业务对象 id；对象已删也留着，不做外键
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 给页面直接显示的对象名，例如「IT-2026-0001 · 联想 ThinkCentre M720t」
    target_label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    #: 一句话说清干了什么，例如「状态 在库 → 维修中」
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: success / failed
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AuditStatus.SUCCESS, server_default=AuditStatus.SUCCESS, index=True
    )

    #: 结构化补充信息（变更明细、导入批次统计、失败原因…），JSON 文本
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def ok(self) -> bool:
        return self.status != AuditStatus.FAILED

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.id} {self.action} by={self.operator} ip={self.ip}>"


# --------------------------------------------------------------------------- #
# 点位（资产空间地图）
# --------------------------------------------------------------------------- #
class Facing:
    """点位朝向。桌子朝哪边摆 —— 它是家具的物理属性，与它在画布第几行无关。

    为什么必须落库而不是按行列推导：**「调整位置」是这个模块的核心交互**，
    用户可以任意拖动格子。一旦某个点位被拖到别的行，「按行交替」的规则就不再成立，
    而朝向显然不应该因为拖动而改变。
    """

    UP = "up"
    DOWN = "down"

    LABELS: dict[str, str] = {UP: "朝上", DOWN: "朝下"}
    ALL: tuple[str, ...] = (UP, DOWN)


class FloorMap(Base):
    """平面图 —— 一张地图上的建筑图元（地台 / 走廊 / 房间 / 墙体 / 前厅 / 入口）。

    为什么现在才有这张表（推翻了一期「不建 floor_maps」的结论）：
    一期的判断是「只有一张画布、尺寸是渲染常量、用户改不了」—— 三条前提当时都成立。
    但用户要**自己画平面图**之后，前提全没了：
      - 尺寸成了用户数据（他的办公室可能是 1200×800）
      - 图元成了用户数据（他没有直播间，但有两个仓库）
      - "第二张图"从想象中的通用性变成了第二个办公室的真实需求

    为什么图元存 `layout` JSON 而**不建 map_shapes 表**：
      - 图元是**纯几何 + 一个标签**，没有任何东西引用它 —— 没有身份的实体不值得一张表；
      - 整张图永远是一起编辑、一起保存 → 一次 UPDATE，天然对上「1 次保存 = 1 条审计」；
      - 撤销 / 重做 / 复制地图全在前端文档层完成，零后端配合。
    对照：`asset_events.detail` 用 Text 不用 JSON 列、`InventoryScope.scope` 存 JSON —— 同一取舍。

    为什么**点位不在这里**：点位有 `code` 唯一约束、被 `assets.workstation_id` 引用、
    还要参与盘点聚合 —— 它有身份、有引用者，必须是表。两者形态不同不是不一致，
    是它们本来就是两种东西。
    """

    __tablename__ = "floor_maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(64), nullable=False)

    #: 画布尺寸。用户可改 —— 这正是它从"渲染常量"变成"业务数据"的地方。
    #: 与既有约定一致：坐标存的就是画布坐标，将来叠底图时按同样尺寸画进同一坐标系即可。
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=960)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1320)
    grid: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    #: 图元 JSON：{"schema_version": 1, "shapes": [...]}，存 Text 不存 JSON 列。
    #:
    #: ⚠️ `schema_version` 不是"为未来扩展预留"，是 JSON 文档最基本的可演进性：
    #: 半年后 room 的字段一变，库里这些 layout 就分不清是哪一版格式了。
    #: 读取**一律经** frontend/src/utils/floorMap.ts::parseLayout 做 normalize，
    #: 组件不许直接 JSON.parse。未知版本号要拒绝加载并提示，不能静默渲染出错位的图。
    layout: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    #: 默认图。单图阶段恒为 True 且只有一行；
    #: 多图之后表示"从入口进来默认打开的那张"。
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    #: 软删除 —— 与 workstations / asset_relations 同一设计。
    #: 图上还有点位时不允许硬删（RESTRICT + 应用层 409），
    #: 因为删图连带的是那些点位上的资产归属，因果上不成立。
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    workstations: Mapped[list["Workstation"]] = relationship(back_populates="floor_map")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FloorMap {self.id} {self.name} {self.width}x{self.height} default={self.is_default}>"


class Workstation(Base):
    """点位 —— 地图上的一个格子，资产可以挂到它上面。

    只存「摆在哪、朝哪、归谁用」。**资产挂在哪个点位不在这里存** ——
    那是 `assets.workstation_id` 反着指向它。理由有三条：
     1. 一台资产同一时刻只在一个点位上（多对一），一条外键就够；
     2. 点位下有几台资产是派生查询，不落库（红线 2：一份数据两处存，迟早对不上）；
     3. 资产在点位间流转的轨迹由 asset_events 记，不需要在这里再存一份列表。

    关于 code：**可编辑，但不释放**。用户能把 W23 改成「销售-01」（编码不承载语义，
    这样部门调整时不用重编号）；但点位软删除后 code 仍被占用，不会再被新建的点位拿去。
    这样审计里两条指向"W23"的记录永远说的是同一个位置。

    关于坐标：**存的是画布坐标**（基准画布 960×1320），不是物理尺寸。
    将来要叠真实平面图时，按同样的 960×1320 画进同一坐标系即可，坐标一个都不用改。
    """

    __tablename__ = "workstations"
    __table_args__ = (UniqueConstraint("code", name="uq_workstation_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    facing: Mapped[str] = mapped_column(
        String(8), nullable=False, default=Facing.DOWN, server_default=Facing.DOWN
    )

    #: 与 assets.user_name 同口径的裸字符串。系统没有用户表（红线：不做登录），
    #: 所以这里是自由文本，不做字典、不做外键。
    #: 注意：它**不是** assets.user_name 的副本 ——
    #: 「W23 这个位置归林晓薇」和「这台主机现在归林晓薇」是两个不同的事实，
    #: 两者不一致本身就是盘点要抓的信号，不做强制同步。
    user_name: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    #: 房间备注。自由文本，只有「前台」「总经理办公室」这类特殊点位才填。
    room: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: 软删除 —— 与 asset_relations 解绑不删行是同一个设计。
    #: 物理删掉会让「这个位置曾经存在过」这段历史消失，而且 code 被释放后
    #: 新点位可以复用同一个编码，审计里的记录就再也说不清是哪个位置了。
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    #: 属于哪张平面图（floor_maps）。**可空**，理由与 assets.workstation_id 同类：
    #: 单图阶段这一列总是有值（迁移时回填默认图），但落库语义上"没挂图"是合法状态，
    #: 强行 NOT NULL 会让将来"新建图但还没放点位"这种中间态无处安放。
    #:
    #: ⚠️ 这一列是**关系预留，不是功能预留**：它让"加第二张图"将来只是一次数据搬迁，
    #: 而**不代表前端现在支持多图**。前端必须保持"只有这一张图"的心智模型 ——
    #: 不许出现地图选择器、`?map=` 状态、`maps[]` 之类的东西。
    #: 否则会滑进"数据库支持多图 / API 半支持 / 前端假装支持 / 业务还是单图"的假状态。
    floor_map_id: Mapped[int | None] = mapped_column(
        ForeignKey("floor_maps.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    assets: Mapped[list["Asset"]] = relationship(back_populates="workstation")
    floor_map: Mapped["FloorMap | None"] = relationship(back_populates="workstations")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Workstation {self.id} {self.code} ({self.x},{self.y}) active={self.active}>"


# --------------------------------------------------------------------------- #
# 关系核对 · 例外确认
# --------------------------------------------------------------------------- #
class RelationException(Base):
    """「这条关系差异我确认过，是正常的」—— **业务决定，不是问题状态**。

    ## 为什么它必须落库，而「问题列表」不必

    关系核对的结果（谁和谁不一致）是**派生数据**：每次由
    `services/relation_check.check_station()` 现算，所以不建「问题状态表」——
    建了就会过期，资产一挂一摘、人一改，那张表立刻开始说谎（红线 2）。

    但**例外确认是另一回事**：它是人做的一个判断（"这个位置是共用的，
    不一致是有意的"）。判断本身是事实，不落库就丢了，下次还会重复烦人。
    所以这里只存"决定"，不存"结论"。

    ## 失效机制：指纹，而不是状态字段

    `fingerprint` 记录**确认那一刻的关系指纹**（点位使用人 + 相关设备的使用人，
    由 `relation_check` 生成）。核对时拿当前指纹比一次：

    - 一致 → 例外仍然有效，这条差异不再提示；
    - 不一致 → 关系已经变了（换了人 / 加了设备 / 设备换了人），
      旧确认**自动作废**，差异重新冒出来。

    这正是要求里那句「相关关系发生变化后，旧例外确认不会继续掩盖新差异」。
    **不要**改成"有个 `resolved` 字段由代码去置位" —— 那又变成了人工维护的状态。

    ## 为什么 asset_id 不用外键、且拿 0 当「点位级」

    - 唯一约束 `(rule, workstation_id, asset_id)` 要能挡住"同一件事确认两次"。
      SQLite 里 `NULL != NULL`，`asset_id` 若可空，点位级的例外能被插无限多条 ——
      所以用 `0` 当哨兵值（资产主键从 1 起，0 永远不会撞）。
    - 但 `0` 不是合法资产 id，挂外键会被约束拒绝，所以这一列**刻意不建外键**
      （与 `AuditLog.target_id` 同一取舍）。资产被删掉时留下的行是安全的：
      它既不会被核对过程读出来（没有对应的问题），指纹里的编码也对不上。
    """

    __tablename__ = "relation_exceptions"
    __table_args__ = (
        UniqueConstraint("rule", "workstation_id", "asset_id", name="uq_relation_exception"),
        Index("ix_relation_exception_station", "workstation_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: 哪条规则。见 `services.relation_check.RuleKey`
    rule: Mapped[str] = mapped_column(String(48), nullable=False)

    #: 点位。点位被删（软删不会触发；硬删极少见）时级联清掉这张表里的行。
    workstation_id: Mapped[int] = mapped_column(
        ForeignKey("workstations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 单台设备级的例外记设备 id；**点位级记 0**（见类说明）。
    asset_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: 确认理由。**必填** —— 没有理由的例外等于把提示直接关掉，明天就没人说得清为什么。
    reason: Mapped[str] = mapped_column(String(255), nullable=False)

    #: 确认那一刻的关系指纹。关系一变就对不上 → 例外自动失效。
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)

    #: 撤销用（软删）。撤销后不会被读出来，但记录留着 ——
    #: 「当初谁批的、后来又为什么取消」是要查得到的历史。
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    #: 最近一次核对 / 重新确认时间
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RelationException {self.id} rule={self.rule} "
            f"ws={self.workstation_id} asset={self.asset_id} active={self.active}>"
        )


# --------------------------------------------------------------------------- #
# 轻资产
# --------------------------------------------------------------------------- #
#: 轻资产**不再有**追踪粒度（`tracking`）这个概念 —— 上一版那套「按数量 / 按单件」双模式
#: 已被取消：所有品类统一按「品类 + 数量」管理（见《设计方案-轻资产管理模块-修订版》§1.1）。
#: 这里刻意**保留一段占位注释**而不是彻底删干净，是因为历史流水里冻结着老库的
#: `light_categories.tracking` 语义；将来若有人翻到旧 CHANGELOG，这段注释能解释
#: "为什么这里本来有个枚举，现在没了"。


class LightOrigin:
    """流水来源 —— **"这条记录从哪来"决定它算不算数**。

    这是本模块最容易做错的地方：历史 Excel 里的分配流水**不代表当前库存**，
    它只是"曾经发生过"的追溯材料（用户明确要求：不得据这些记录推断真实总库存）。

    - `manual`  系统内正常业务操作（入库 / 分配 / 归还 / 拆卸 …）→ **计入**当前状态
    - `opening` 期初初始化（管理员盘点确认后建立的真实基线）        → **计入**当前状态
    - `import`  历史导入（仅追溯）                                → **不计入**当前状态

    ⚠️ 判断"这条流水算不算数"**只用 `EFFECTIVE`**，不要在各处手写 `== 'manual'`
    （漏掉 opening 会让期初基线凭空消失，且没有任何报错）。
    """

    MANUAL = "manual"
    OPENING = "opening"
    IMPORT = "import"

    LABELS: dict[str, str] = {MANUAL: "业务操作", OPENING: "期初初始化", IMPORT: "历史导入"}
    ALL: tuple[str, ...] = (MANUAL, OPENING, IMPORT)
    #: 计入当前状态（库存 / 持有关系）的来源。**import 被刻意排除。**
    EFFECTIVE: tuple[str, ...] = (MANUAL, OPENING)


class LightHolderKind:
    """一条流水的「从哪来 / 到哪去」。

    库里只存 kind + 引用，**不存副本**：当前使用人 / 点位一律现读正式资产或点位表
    （红线 2）。但引用对象会消失、主键还会被复用（本库无 AUTOINCREMENT），
    所以每条流水同时**快照当时的编码与名称**（`*_ref_code` / `*_ref_label`）。
    """

    STOCK = "stock"
    USER = "user"
    ASSET = "asset"
    WORKSTATION = "workstation"

    LABELS: dict[str, str] = {
        STOCK: "库存",
        USER: "使用人",
        ASSET: "正式资产",
        WORKSTATION: "点位",
    }
    ALL: tuple[str, ...] = (STOCK, USER, ASSET, WORKSTATION)
    #: 算作"已被持有"的种类（库存不算）。
    HELD: tuple[str, ...] = (USER, ASSET, WORKSTATION)


class LightOp:
    """轻资产操作类型。

    每条流水都是一次**物料移动**：`quantity` 件从 `from` 去往 `to`。
    统一成 from→to 之后，"库存怎么变 / 持有关系怎么变"就只有一个算法
    （见 services/light.py 的派生公式），加新操作不用再改统计代码。
    """

    STOCK_IN = "stock_in"     # 入库      None → stock
    ALLOCATE = "allocate"     # 分配      stock → user（**给人**）
    RETURN = "return"         # 归还      user / asset / workstation → stock
    ATTACH = "attach"         # 安装/落位 stock|None → asset / workstation（**给物/给位置**）
    DETACH = "detach"         # 拆卸      asset / workstation → stock
    TRANSFER = "transfer"     # 转移      持有方 → 另一持有方
    SCRAP = "scrap"           # 报损      stock|持有方 → None（注销）
    ADJUST = "adjust"         # 盘盈盘亏  stock ↔ None（数量校正）
    #: 更换 —— **不是一次物料移动**，而是"旧件报损 + 新件装上"两条流水的组合动作。
    #: 它不在 FLOWS 里被当成普通 op 校验（见 services/light.py::replace_asset_part），
    #: 放在这里只是为了让"业务动作 → op"的映射表有一个正式的名字。
    REPLACE = "replace"

    LABELS: dict[str, str] = {
        STOCK_IN: "入库",
        ALLOCATE: "分配",
        RETURN: "归还",
        ATTACH: "安装",
        DETACH: "拆卸",
        TRANSFER: "转移",
        SCRAP: "报损",
        ADJUST: "调整",
        REPLACE: "更换配件",
    }

    ALL: tuple[str, ...] = (
        STOCK_IN, ALLOCATE, RETURN, ATTACH, DETACH, TRANSFER, SCRAP, ADJUST, REPLACE,
    )

    #: 每种操作允许的 `(from_kind, to_kind)` 组合。`None` 表示"系统外部 / 注销"。
    #: 校验时逐条比对，不在表里的组合直接 400 —— 与其让它写进去再靠统计去猜，不如当场挡住。
    #:
    #: ⚠️ **按"意图"划分，刻意不重叠**：
    #:   - 「分配」只对人（`user`）—— "把这件东西给张三"；
    #:   - 「安装」只对物（`asset` / `workstation`）—— "把这件东西装到 / 放到某处"。
    #: 两条都允许 `stock → X` 的话（第一版就是），会出现"分配一台主机"这种
    #: 语义上讲不通、但数据库照收的记录，将来对账没人说得清那是安装还是分配。
    #:
    #: 字面量写成 `"stock"` 而不是 `LightHolderKind.STOCK`：这张表在类体内定义，
    #: 引用外部类是循环依赖；反正它只在校验里用，值本身就是契约。
    FLOWS: dict[str, tuple[tuple[str | None, str | None], ...]] = {
        STOCK_IN: ((None, "stock"),),
        ALLOCATE: (("stock", "user"),),
        RETURN: (("user", "stock"), ("asset", "stock"), ("workstation", "stock")),
        ATTACH: (("stock", "asset"), ("stock", "workstation"), (None, "asset"), (None, "workstation")),
        DETACH: (("asset", "stock"), ("workstation", "stock")),
        #: `user → user` = "这批东西本来在张三那儿，现在归李四"。
        #: 没有它，这个再日常不过的意图只能绕「归还 + 分配」两步 ——
        #: 而那条路中间会经过"库存"，一次换手被记成两次入出库，时间线上看不出
        #: 它其实是同一批东西直接换了人；绕路还正好踩过当时的幽灵归还漏洞。
        #: ⚠️ 必须包含 `asset → asset`（B022 → B023）与 `workstation → workstation`
        #: （W23 → W25）—— "机器与机器的转移、点位与点位的转移"是真实业务动作，
        #: 漏了它们就只能绕"先拆卸回库存、再安装过去"，一次挪位被记成两次出入库。
        TRANSFER: (
            ("user", "user"),
            ("user", "asset"), ("user", "workstation"),
            ("asset", "user"), ("asset", "asset"), ("asset", "workstation"),
            ("workstation", "user"), ("workstation", "asset"), ("workstation", "workstation"),
        ),
        SCRAP: (("stock", None), ("user", None), ("asset", None), ("workstation", None)),
        ADJUST: ((None, "stock"), ("stock", None)),
    }

    @classmethod
    def allows(cls, op: str, from_kind: str | None, to_kind: str | None) -> bool:
        return (from_kind, to_kind) in cls.FLOWS.get(op, ())

    #: 冲正时按顺序尝试的候选操作。**不是简单地"把 op 原样反过来"** ——
    #: 反向操作落在哪个 op 上取决于原来的 from：同样是「归还」，
    #: 从人还回去要写成「分配」，从资产还回去要写成「安装」。
    #: 空元组 = 这条操作没有机械的反向（报损想撤销必须由人判断走哪条路），
    #: 强行"恢复"会凭空把它塞回库存，那是编造事实。
    REVERSE_CANDIDATES: dict[str, tuple[str, ...]] = {
        STOCK_IN: (ADJUST,),
        ALLOCATE: (RETURN,),
        RETURN: (ALLOCATE, ATTACH),
        ATTACH: (DETACH,),
        DETACH: (ATTACH,),
        TRANSFER: (TRANSFER,),
        ADJUST: (ADJUST,),
        SCRAP: (),
    }

    @classmethod
    def reverse_op(cls, op: str, from_kind: str | None, to_kind: str | None) -> str | None:
        """冲正一条流水该用哪个操作。挑不出合法组合就返回 None。

        参数是**原流水**的 from / to；内部会反过来找能与之匹配的操作。
        """
        swapped = (to_kind, from_kind)
        for candidate in cls.REVERSE_CANDIDATES.get(op, ()):
            if cls.allows(candidate, *swapped):
                return candidate
        return None

    @classmethod
    def label(cls, op: str) -> str:
        return cls.LABELS.get(op, op)


# --------------------------------------------------------------------------- #
# 轻资产 · 品类
# --------------------------------------------------------------------------- #
class LightCategory(Base):
    """轻资产品类 —— 回答"这是什么"。

    **一张字典表**：名称、品牌（选填）、规格（选填）、单位。
    它**不记数量、不记位置、不记追踪模式**（《修订版设计》§1.1）。

    品牌与规格**允许为空**：真实流水里只给了"设备名"一行字，硬拆只会在库里
    造出一堆凭空的字段值（不编造数据）。名称为空时前端按"品类 #id"兜底显示。
    """

    __tablename__ = "light_categories"
    __table_args__ = (UniqueConstraint("name", name="uq_light_category_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spec: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #: 计量单位：套 / 块 / 条。只是给人看的文案，不参与任何计算。
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="件", server_default="件")

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 界面图标标识（`frontend/src/utils/icons.ts::ICON_PATHS` 的 key，如 `keyboardMouse`）。
    #:
    #: **刻意存标识** —— 不存 emoji、也不存 SVG 源码：换一套视觉时不必动库，
    #: 前端也只有一张路径表。允许为空：空值表示"用户还没确认过图标"，
    #: 界面据此按品类名给一个**建议**图标（只显示、不写库，见
    #: `utils/light.ts::lightCategoryIcon`）。
    #:
    #: ⚠️ **绝不能靠名称正则去猜**（第一版就是那样，`catIcon()` 拿
    #: `/键鼠|键盘|鼠标|套装/` 去匹配名称 + 规格）：用户把品类改个名、或者规格里
    #: 恰好带了"套装"二字，图标就悄悄变了；而且同一个品类在列表页和资产详情页
    #: 用了两套判断，键鼠套装在一边显示键盘、另一边显示芯片。
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)

    #: 软删 —— 与 workstations / floor_maps 同一设计（编码/名称不释放）。
    #: 被流水引用时不允许硬删（应用层 409 + 人话；流程走软删）。
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    #: 现在的分布（快照）—— 列表页 / 详情页直接读它，不重放历史。
    holdings: Mapped[list["LightHolding"]] = relationship(
        back_populates="category", cascade="all, delete-orphan", passive_deletes=True
    )
    #: 完整轨迹（append-only）—— "这条 SSD 去年装在哪台机器上" 这类问题由它回答。
    movements: Mapped[list["LightMovement"]] = relationship(
        back_populates="category", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def display_name(self) -> str:
        return self.name or f"品类 #{self.id}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<LightCategory {self.id} {self.name}>"


# --------------------------------------------------------------------------- #
# 轻资产 · 当前分配记录
# --------------------------------------------------------------------------- #
class LightHolding(Base):
    """轻资产**当前分配记录** —— 回答"现在在哪、有几件"（《修订版设计》§1.2 / §2）。

    ## 一条记录 = 一个持有位置 + 该位置上的数量

    `holder_kind` 四选一，**一条记录只能有一个持有位置**：

    | kind          | 什么时候用        | 现实例子                    |
    | ------------- | ------------- | ----------------------- |
    | `asset`       | 装在某台正式资产上的配件  | 2 条内存插在 `B022-03-00001` 上 |
    | `workstation` | 固定放在某个工位用的    | 1 套键鼠放在 `W23` 工位上        |
    | `user`        | 专门发给某人、没有固定位置 | 3 套键鼠发给张三               |
    | `stock`       | 还没分配出去，在库房    | 5 个转接头备着                |

    **使用人不是第五种位置，而是展示信息**：持有位置是 `asset` / `workstation` 时，
    使用人一律**现读宿主**（`assets.user_name` / `workstations.user_name`），本表
    不另存一份；持有位置是 `user` 时，记录上**不带任何点位**，界面就老实说
    "在某人手上，未记录固定位置"——绝不因为张三名下有工位就推断这套键鼠在 W23。

    ## 与 `LightMovement` 的分工：现在 vs 过去

    - 本表 = **现在的快照**，给列表页 / 详情页直接读（快、简单）；
    - `light_movements` = **完整轨迹**（append-only），给"去年装在哪台机器上"这种问题。

    两者**同事务双写**，改 holdings、追加流水、写审计一起 commit（§4.2）。
    holdings 只允许被 `services/light.py::apply_holding_delta()` 修改，
    并可从流水完整重建（`light_check.py` 体检、`light_rebuild.py` 重建）。

    ## 为什么不落"使用人 + 点位都记"

    那会立刻产生一个无法回答的问题：**张三调去别的工位了，这条配件跟着走还是留下？**
    配件跟着人走 → 记 `user`；配件固定在工位 → 记 `workstation`。二者只能选一个。
    """

    __tablename__ = "light_holdings"
    __table_args__ = (
        # 第二层防线：部分唯一索引（SQLite / PostgreSQL 支持；MySQL 不支持 —— 项目明确不打算支持）。
        # ⚠️ 与 `asset_relations` 的取舍**故意不同**：配对表要保留"解绑"的历史行（active=0 不能互相冲突），
        # 所以不建唯一索引；而本表**只存当前状态、不存历史**（历史在流水里），
        # 表里没有软删行，所以部分唯一索引不会误伤任何合法数据。
        Index(
            "uq_light_hold_asset", "category_id", "asset_id",
            unique=True, sqlite_where=text("asset_id IS NOT NULL"),
        ),
        Index(
            "uq_light_hold_station", "category_id", "workstation_id",
            unique=True, sqlite_where=text("workstation_id IS NOT NULL"),
        ),
        Index(
            "uq_light_hold_user", "category_id", "holder_user",
            unique=True, sqlite_where=text("holder_user IS NOT NULL"),
        ),
        # 库存按"存放位置"而不是按品类唯一：同一个品类可以分放几处库房（库房A 3 个、前台抽屉 2 个）。
        # `COALESCE(...,'')` 是为了让"没填位置"本身也成为一个可唯一的取值 ——
        # 否则 SQLite / SQL 里 NULL 互不相等，两条"没填位置"的库存记录能同时存在。
        Index(
            "uq_light_hold_stock", "category_id", text("COALESCE(stock_location, '')"),
            unique=True, sqlite_where=text("holder_kind = 'stock'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    category_id: Mapped[int] = mapped_column(
        ForeignKey("light_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    #: stock / user / asset / workstation，见 LightHolderKind
    holder_kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: kind=asset 时必填（其余为空）
    asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: kind=workstation 时必填（其余为空）
    workstation_id: Mapped[int | None] = mapped_column(
        ForeignKey("workstations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: kind=user 时必填（自由文本，走 ownership.has_owner 校验）
    holder_user: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: kind=stock 时的存放位置描述（自由文本，也可以是某个点位的编码，见设计 §2.4）。
    #: **不建外键**：现实里备件常放在不是工位的地方（库房、机柜、行政抽屉），
    #: 强行要求先在地图上建点位会把"库房"这种空间硬塞进点位表，污染地图。
    stock_location: Mapped[str | None] = mapped_column(String(128), nullable=True)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    #: 这一条是从什么时候开始的（业务时间，非 created_at）
    since: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    category: Mapped[LightCategory] = relationship(back_populates="holdings", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LightHolding {self.id} cat={self.category_id} {self.holder_kind} "
            f"x{self.quantity} asset={self.asset_id} ws={self.workstation_id} user={self.holder_user}>"
        )


# --------------------------------------------------------------------------- #
# 轻资产 · 流转流水
# --------------------------------------------------------------------------- #
class LightMovement(Base):
    """轻资产流转流水 —— **append-only 的历史**（"怎么变成现在这样的"，设计 §1.3）。

    ## 与 `LightHolding` 的分工：过去 vs 现在

    - `LightHolding` = **现在的快照**，列表页 / 详情页直接读（快、简单）；
    - 本表 = **完整轨迹**，给"这条 SSD 去年装在哪台机器上"这种问题（全、可审计）；
    - 两者**同事务双写**，本表同时是**可重建快照的唯一依据**
      （`light_check.py` 体检、`light_rebuild.py` 重建都按它重放）。

    ## 一条流水就是一次物料移动

    `quantity` 件从 `from_*` 去往 `to_*`。`to_kind` 为 NULL 表示"离开系统"（报损）。

    ## 重放口径（唯一口径，实现见 services/light.py）

    ```
    Δ(某个位置) = quantity × [ +1 若 to 命中该位置  −1 若 from 命中该位置 ]
    ```
    只累计 `origin ∈ LightOrigin.EFFECTIVE` 的流水 —— **历史导入 `import` 绝不参与重放**
    （Excel 里只有分配、没有对应入库，算进来会得到负库存或凭空出现的持有量）。
    排序必须稳定：`occurred_at ASC, id ASC`（同一天多条时用 id 兜底）。

    ## 为什么不原地改

    "改错了一条分配" = 追加两条更正流水（反向 + 重记），不是 UPDATE。
    已确认的历史是证据，篡改它等于让时间线说谎。`corrects_id` 只做**追溯链接**
    （"这条是在更正 #123"），不参与任何计算。

    ## 引用正式资产 / 点位为什么不建外键

    与 `audit_logs.target_id`、`relation_exceptions.asset_id` 同一取舍：
    关联是**历史事实**，宿主删了它也成立（"它当初确实装在 B022-03-00001 上"）。
    且本库主键无 AUTOINCREMENT、**id 会被复用** —— 只存 id 会让旧记录指到一台新设备上。
    所以同时快照 `*_ref_id` + `*_ref_code`（当时的编码）+ `*_ref_label`（当时的名称/型号），
    展示时优先查活的宿主，查不到就回落到快照 +「已删除」。
    """

    __tablename__ = "light_movements"
    __table_args__ = (
        Index("ix_light_movement_category_time", "category_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    category_id: Mapped[int] = mapped_column(
        ForeignKey("light_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # ⚠️ **这里曾经有一个 `item_id` 外键指向 `light_items`（单件档案表）。**
    # 取消单件追踪后那张表被删除，于是出现两个致命后果，必须把这一列连外键一起拿掉：
    #   1. **新库**：模型删了 → create_all 不再建 `light_items` → `light_movements` 里
    #      那条 `REFERENCES light_items(id)` 指向一张不存在的表 → 任何一次流水写入
    #      都直接 `OperationalError: no such table: main.light_items`（实测确认）；
    #   2. **老库**：`DROP TABLE light_items` 之后，同样的"外键指向不存在的表"会立刻生效。
    # 初稿的设计写的是"保留列但不读不写（SQLite 物理删列要重建表，风险大于收益）"——
    # 那个判断**漏算了外键这个副作用**：不是"要不要留着一列"的问题，
    # 而是"留着一个指向幽表的约束会让整张表彻底写不进去"。
    # SQLite 3.35+ 原生支持 `ALTER TABLE ... DROP COLUMN`（本机 3.45），
    # 所以迁移脚本直接删列即可，不需要 12 步重建表。

    op: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    #: --- 从哪来 ---
    from_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    from_ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_ref_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_ref_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    from_user: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: --- 到哪去 ---
    to_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    to_ref_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_ref_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    to_user: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: **业务发生时间**（历史导入可回填过去的时间），与 created_at 不是一个概念。
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, index=True
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: manual / opening / import，见 LightOrigin
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LightOrigin.MANUAL,
        server_default=LightOrigin.MANUAL, index=True,
    )
    #: 一次导入一个批次号，导入结果可追溯（哪一批进来的）
    import_batch: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    #: 前端每次提交带一个 UUID，唯一索引挡住"手抖点两次 / 网络重试"。
    #: SQLite 里 NULL 之间互不冲突，所以只有真正传了才参与去重。
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    #: 这条流水是在更正哪一条（仅追溯，不参与计算，**不建外键**）
    corrects_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    category: Mapped[LightCategory] = relationship(back_populates="movements", lazy="joined")

    @property
    def effective(self) -> bool:
        """这条流水算不算进当前状态。"""
        return self.origin in LightOrigin.EFFECTIVE

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LightMovement {self.id} cat={self.category_id} {self.op} x{self.quantity} "
            f"{self.from_kind}→{self.to_kind} origin={self.origin}>"
        )

