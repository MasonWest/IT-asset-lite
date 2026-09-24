"""ORM 模型。

表清单：
- device_types          设备类型（可自定义：主机 / 显示器 / 键盘 …）
- assets                资产台账
- asset_relations       主机 ←→ 显示器 配对关系（一台主机可带多台显示器）
- asset_events          资产变更历史（时间线），所有状态流转 / 配对 / 盘点都往这里落一条
- inventory_tasks       盘点任务
- inventory_items       盘点明细（每个任务 × 每台设备一行）
- audit_logs            操作审计日志（谁、从哪个 IP、什么时间、干了什么）

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

    ICONS: dict[str, str] = {
        CHECKOUT: "📤",
        RETURN: "📥",
        REPAIR: "🔧",
        REPAIR_DONE: "✅",
        SCRAP: "🗑️",
        RESTORE: "♻️",
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

    LABELS: dict[str, str] = {
        CREATE: "录入台账",
        UPDATE: "信息变更",
        STATUS: "状态变更",
        PAIR: "绑定配对",
        UNPAIR: "解除配对",
        INVENTORY: "盘点标记",
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

    INVENTORY_CREATE = "inventory_create"
    INVENTORY_MARK = "inventory_mark"
    INVENTORY_CLOSE = "inventory_close"
    INVENTORY_REOPEN = "inventory_reopen"
    INVENTORY_DELETE = "inventory_delete"

    TYPE_CREATE = "type_create"
    TYPE_UPDATE = "type_update"
    TYPE_DELETE = "type_delete"

    AUDIT_EXPORT = "audit_export"

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
        INVENTORY_CREATE: "发起盘点",
        INVENTORY_MARK: "盘点标记",
        INVENTORY_CLOSE: "结束盘点",
        INVENTORY_REOPEN: "重开盘点",
        INVENTORY_DELETE: "删除盘点",
        TYPE_CREATE: "新增设备类型",
        TYPE_UPDATE: "编辑设备类型",
        TYPE_DELETE: "删除设备类型",
        AUDIT_EXPORT: "导出审计日志",
    }

    ICONS: dict[str, str] = {
        ASSET_CREATE: "✨",
        ASSET_UPDATE: "✏️",
        ASSET_DELETE: "🗑️",
        ASSET_OPERATE: "🔁",
        ASSET_PAIR: "🔗",
        ASSET_UNPAIR: "✂️",
        ASSET_REBIND: "🔀",
        ASSET_IMPORT: "📥",
        ASSET_EXPORT: "📤",
        INVENTORY_CREATE: "🧭",
        INVENTORY_MARK: "📋",
        INVENTORY_CLOSE: "🏁",
        INVENTORY_REOPEN: "↩️",
        INVENTORY_DELETE: "🗑️",
        TYPE_CREATE: "🏷️",
        TYPE_UPDATE: "🏷️",
        TYPE_DELETE: "🏷️",
        AUDIT_EXPORT: "📤",
    }

    #: 筛选下拉里的分组顺序，前端直接照这个渲染
    GROUPS: dict[str, tuple[str, ...]] = {
        "资产台账": (ASSET_CREATE, ASSET_UPDATE, ASSET_DELETE, ASSET_OPERATE),
        "主机 / 显示器配对": (ASSET_PAIR, ASSET_UNPAIR, ASSET_REBIND),
        "盘点": (INVENTORY_CREATE, INVENTORY_MARK, INVENTORY_CLOSE, INVENTORY_REOPEN, INVENTORY_DELETE),
        "导入导出": (ASSET_IMPORT, ASSET_EXPORT, AUDIT_EXPORT),
        "设备类型": (TYPE_CREATE, TYPE_UPDATE, TYPE_DELETE),
    }

    ALL: tuple[str, ...] = tuple(k for keys in GROUPS.values() for k in keys)

    @classmethod
    def label(cls, action: str) -> str:
        return cls.LABELS.get(action, action)

    @classmethod
    def icon(cls, action: str) -> str:
        return cls.ICONS.get(action, "•")


class AuditTarget:
    """审计记录指向的对象类型 —— 决定前端"操作对象"那一列怎么跳转。"""

    ASSET = "asset"
    INVENTORY = "inventory"
    DEVICE_TYPE = "device_type"
    FILE = "file"
    SYSTEM = "system"

    LABELS: dict[str, str] = {
        ASSET: "设备",
        INVENTORY: "盘点任务",
        DEVICE_TYPE: "设备类型",
        FILE: "文件",
        SYSTEM: "系统",
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

    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )

    device_type: Mapped[DeviceType] = relationship(back_populates="assets", lazy="joined")

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
