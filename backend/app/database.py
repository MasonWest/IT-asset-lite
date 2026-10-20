"""数据库连接与会话管理。

只用 SQLite + SQLAlchemy ORM，方便以后整体迁移到 MySQL / PostgreSQL：
只要换掉 DATABASE_URL 即可，业务代码不用动。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# backend/ 目录
BASE_DIR = Path(__file__).resolve().parent.parent

# 数据库文件放哪：默认 backend/data/it_assets.db，可用环境变量覆盖
DATA_DIR = Path(os.getenv("IT_ASSET_DATA_DIR") or (BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "it_assets.db"

DATABASE_URL = os.getenv("IT_ASSET_DATABASE_URL") or f"sqlite:///{DB_FILE.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    # SQLite 默认禁止跨线程复用连接，FastAPI 是线程池模型，这里必须放开
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    """打开外键约束 —— SQLite 默认是关的，不开的话删除保护形同虚设。"""
    if not DATABASE_URL.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每个请求一个会话。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）。"""
    from . import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(bind=engine)


#: 业务表：用户自己干活攒出来的数据。`reset.py` 清空的就是这些。
#: 顺序有讲究 —— 必须先删子表，否则 PRAGMA foreign_keys=ON 会拦住删除：
#: inventory_items 引用 inventory_tasks + assets，asset_relations / asset_events 引用 assets，
#: assets 引用 workstations，workstations 引用 floor_maps
#: （所以顺序是 assets → workstations → floor_maps：先删引用方，再删被引用方）。
#: 靠这个顺序而不是"临时关掉外键校验"，是为了让 schema 的问题在重置时就暴露出来 ——
#: 顺序排错的话 reset.py 会当场抛外键错，而不是留下一个残缺的库。
#:
#: 轻资产现在只有两张表 + 一张「当前分配记录」：
#: - `light_movements`（历史）与 `light_holdings`（现在的快照）都引用 `light_categories`，
#:   所以必须排在 categories **之前**（先删引用方）；
#: - `light_holdings` 还**建了外键**指向 `assets` / `workstations`（当前分配记录是
#:   "此刻真的挂在哪"，与历史流水不同，它要求宿主真的存在），所以它必须排在
#:   assets / workstations **之前**。
#: - `light_movements` 引用的是 assets / workstations 的**历史快照而非外键**
#:   （宿主删了历史也成立），所以它与 assets / workstations 之间没有删除顺序约束。
#: （`light_items` 单件档案表已在 v2.9.0 删除 —— 取消单件追踪，不再保留。）
BUSINESS_TABLES: tuple[str, ...] = (
    "inventory_items",
    "asset_relations",
    "asset_events",
    "inventory_tasks",
    "light_movements",  # 引用 light_categories
    "light_holdings",  # 引用 light_categories + assets + workstations（都要排在它后面）
    "light_categories",  # 被上面两张引用，必须排在它们后面
    "assets",
    "relation_exceptions",  # 引用 workstations，必须排在 workstations 前面
    "workstations",  # 被 assets / light_holdings 引用，必须排在它们后面
    "floor_maps",  # 被 workstations 引用，必须排在 workstations 后面
    "audit_logs",  # 无外键，但同属业务数据（审计日志只追加，只有 reset 能清）
)

#: 字典表：系统能跑起来的最小基础数据，**不是**用户的业务数据。
#: 启动时会自动补齐，reset 不动它 —— 清空业务数据后界面不该连"设备类型"都没有。
DICTIONARY_TABLES: tuple[str, ...] = ("device_types",)


def table_counts() -> dict[str, int]:
    """各表当前行数。表不存在记 -1（老库还没升到那一步时会出现）。"""
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for table in BUSINESS_TABLES + DICTIONARY_TABLES:
            if not _table_exists(conn, table):
                counts[table] = -1
                continue
            counts[table] = conn.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
    return counts


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _table_exists(conn, table: str) -> bool:
    row = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).first()
    return row is not None


#: 轻量迁移的记账表。就一张 key/value，用来记住"某个一次性迁移已经做过了"。
#: 没有它的话，每次启动都会重复执行迁移 —— 幂等的 ALTER 无所谓，
#: 但「把时间往后挪 8 小时」这种操作重复执行会把数据改得越来越离谱。
_SCHEMA_META_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_meta ("
    "  key VARCHAR(64) PRIMARY KEY,"
    "  value VARCHAR(255)"
    ")"
)

#: 曾被 server_default=func.now() 写成 UTC 的列。
#: 注意这里**只列 created_at / updated_at** ——
#: released_at、closed_at、checked_at 从一开始就是 datetime.now() 写的本地时间，
#: 把它们也往后挪 8 小时等于把好数据改坏。
_LEGACY_UTC_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("device_types", ("created_at",)),
    ("assets", ("created_at", "updated_at")),
    ("asset_relations", ("created_at",)),
    ("asset_events", ("created_at",)),
    ("inventory_tasks", ("created_at",)),
    ("workstations", ("created_at", "updated_at")),
    ("floor_maps", ("created_at", "updated_at")),
    ("audit_logs", ("created_at",)),
)

TIMESTAMPS_LOCAL_KEY = "timestamps_local"

#: 轻资产「本地时间校正」的记账 key（v2.9.8）。
#: 与 `timestamps_local` 分开记 —— 那次修的是 `server_default=func.now()` 写出的 UTC，
#: 这次修的是**浏览器 `toISOString()` 写出的 UTC**，两批数据的判据完全不同。
LIGHT_TIMES_LOCAL_KEY = "light_times_local"

#: (表, 列, 额外 WHERE 片段) —— 被浏览器按 UTC 写进库的轻资产时间。
_LIGHT_TIME_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # 历史导入（origin='import'）的日期来自 Excel 单元格，是用户手填的墙钟日期，
    # 不经过浏览器那条 `toISOString()` 路径，**不平移**。
    ("light_movements", "occurred_at", " AND COALESCE(origin, '') <> 'import'"),
    # holdings.since 是"东西从什么时候开始在这儿"，由写它的那条流水决定，同一批偏移。
    ("light_holdings", "since", ""),
)

#: 「默认平面图已认领」的记账 key。
#: 没有它的话，老库每次启动都会重复插一行默认图 —— 而且第二步的 UPDATE
#: 只会作用于 floor_map_id IS NULL 的行，看起来"幂等"，实际上 floor_maps 会越攒越多。
DEFAULT_FLOOR_MAP_KEY = "default_floor_map_id"


def _meta_get(conn, key: str) -> str | None:
    row = conn.exec_driver_sql("SELECT value FROM schema_meta WHERE key = ?", (key,)).first()
    return row[0] if row else None


def _meta_set(conn, key: str, value: str) -> None:
    conn.exec_driver_sql(
        "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def meta_get_on(db, key: str) -> str | None:
    """在**既有会话**里读记账值 —— 与 `meta_get` 的区别是不另开连接。

    ⚠️ 为什么必须有这一对 `_on` 版本：SQLite 在写事务未提交时不让**同一个进程的另一个
    连接**写库（`database is locked`）。服务层在写事务里要读/写 `schema_meta` 时，
    必须走同一个会话，否则一提交就炸。
    """
    from sqlalchemy import text as _text

    try:
        row = db.execute(_text("SELECT value FROM schema_meta WHERE key = :k"), {"k": key}).first()
    except Exception:  # noqa: BLE001 —— 表还没建好时不该把请求打挂
        return None
    return row[0] if row else None


def meta_set_on(db, key: str, value: str) -> None:
    """在**既有会话 / 事务**里写记账值（表不存在时先建）。"""
    from sqlalchemy import text as _text

    db.execute(_text(_SCHEMA_META_DDL))
    db.execute(
        _text(
            "INSERT INTO schema_meta (key, value) VALUES (:k, :v) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        ),
        {"k": key, "v": value},
    )


def light_opening_key(category_id: int) -> str:
    """「这个品类做过期初确认了吗」的记账键。

    ⚠️ **它只是"提示用"**（界面据此收起"去登记期初"的提示），
    **不是"禁止再录入"的开关** —— 期初确认允许补录 / 重做（设计 §6.1）。
    """
    return f"light_opening:{category_id}"


def _add_category_column(conn) -> bool:
    """第二阶段：给 device_types 补 category 列。返回是否真的补了。"""
    if "category" in _existing_columns(conn, "device_types"):
        return False

    conn.exec_driver_sql(
        "ALTER TABLE device_types ADD COLUMN category VARCHAR(16) NOT NULL DEFAULT 'other'"
    )

    # 只在「刚补上这一列」的时候回填，之后再也不动它 ——
    # 否则用户手动把某个类型改成「其他」，下次重启又被名字规则改回去，这谁受得了。
    # 回填顺序：先认显示类，再认主机类（第二步只看还是 other 的，不会覆盖上一步）。
    # 名字两头都沾时按显示类处理更安全 —— 显示器绝不能被当主机去配别的显示器。
    conn.exec_driver_sql(
        "UPDATE device_types SET category = 'display' "
        "WHERE name LIKE '%显示器%' OR name LIKE '%屏幕%'"
    )
    conn.exec_driver_sql(
        "UPDATE device_types SET category = 'host' "
        "WHERE category = 'other' AND (name LIKE '%主机%' OR name LIKE '%台式%' "
        "OR name LIKE '%笔记本%' OR name LIKE '%服务器%' OR name LIKE '%工作站%')"
    )
    return True


def _add_icon_column(conn, table: str) -> bool:
    """给 `device_types` / `light_categories` 补 `icon` 列（图标标识）。返回是否真的补了。

    **为什么必须写这段代码**：`create_all()` 只建缺的**表**，不会给已存在的表补**列**
    （同 `_add_workstation_id_column` 的说明）。

    **为什么不需要回填**：这一列全 NULL 就是正确语义 —— NULL = "用户还没确认过图标"，
    界面会按名称推断一个**建议**图标显示出来（不写库）。
    ⚠️ **刻意不回填**：把推断结果写进库，等于替用户做了他没做过的决定；
    用户哪天把「主机」改名成「工控机」，一个已写死的推断图标就成了错的配置。
    想批量确认走界面上的「套用建议图标」按钮（用户点了才写）。

    因此这里没有 UPDATE、也不用记账：ALTER 本身幂等（列已存在就直接返回 False）。
    """
    if "icon" in _existing_columns(conn, table):
        return False

    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN icon VARCHAR(32)")
    return True


def _add_workstation_id_column(conn) -> bool:
    """第四阶段：给 assets 补 workstation_id 列（点位关联）。返回是否真的补了。

    **为什么必须写这段代码**：`Base.metadata.create_all()` 只建缺的**表**，
    绝不会给一个已存在的表补**列**。新库走 create_all 时列就是齐的、这里直接跳过，
    但已经跑起来的老库（142 台资产那个）必须靠这个函数才能拿到新列。

    与第一阶段补 device_types.category 的区别：这一列**不需要回填** ——
    补上之后就是全 NULL，而 NULL 的语义正好是正确的（现有资产都还没指定点位）。
    所以这里没有 UPDATE，也不用记账（ALTER 本身幂等：列已存在就返回 False）。

    SQLite 的 ALTER TABLE ADD COLUMN 支持 REFERENCES（可空列可用），
    但不支持在同一句里建索引，所以索引单独建 —— 建索引用 IF NOT EXISTS，
    重复启动不会报错。
    """
    if "workstation_id" in _existing_columns(conn, "assets"):
        return False

    conn.exec_driver_sql(
        "ALTER TABLE assets ADD COLUMN workstation_id INTEGER "
        "REFERENCES workstations(id) ON DELETE RESTRICT"
    )
    # 地图要按点位反查资产清单，这个索引是必须的。
    # 注意：模型里的 index=True 只对 create_all 新建的表生效，
    # 对已存在的 assets 表得在这里显式建。
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_assets_workstation_id ON assets (workstation_id)"
    )
    return True


def _add_floor_map_id_column(conn) -> bool:
    """第五阶段：给 workstations 补 floor_map_id 列（属于哪张平面图）。返回是否真的补了。

    与 `_add_workstation_id_column` 的关键区别：**这一列必须回填** ——
    补上之后是 NULL，但 NULL 不是正确语义（每个点位都该属于某张图）。
    回填在 `_recognize_default_floor_map()` 里做，所以**顺序不能反**：
    先补列 → 再建默认图 → 最后回填。
    """
    if "floor_map_id" in _existing_columns(conn, "workstations"):
        return False

    conn.exec_driver_sql(
        "ALTER TABLE workstations ADD COLUMN floor_map_id INTEGER "
        "REFERENCES floor_maps(id) ON DELETE RESTRICT"
    )
    # 按图查点位会走这个索引（单图阶段用不上，但它是"以后会痛"的那类遗漏）。
    # 模型里的 index=True 只对 create_all 新建的表生效，老库得在这里显式建。
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_workstations_floor_map_id ON workstations (floor_map_id)"
    )
    return True


#: 默认平面图的初始图元。**从 SpaceMapView.vue 原先写死的 14 个图元逐字搬过来**
#: （2 地台 / 走廊 + 文字 / 8 房间 / 3 竖墙 / 1 前厅），外加原型里有、
#: 但接入时被漏掉的 3 个结构图元：大楼入口 + 2 条横向分隔条。
#:
#: ⚠️ 这份数据是 E0「只做数据化、不改视觉」铁律的一部分：
#: 它必须与改造前模板里的几何**逐字段完全相等** —— e2e-floor-legacy-parity.js 会逐项对账。
#: 不要在这里"顺手优化"任何一个坐标。
DEFAULT_FLOOR_LAYOUT: dict[str, object] = {
    "schema_version": 1,
    "shapes": [
        {"id": "z1", "kind": "zone", "x": 40, "y": 186, "w": 384, "h": 800},
        {"id": "z2", "kind": "zone", "x": 524, "y": 186, "w": 384, "h": 262},
        {"id": "c1", "kind": "corr", "x": 424, "y": 186, "w": 100, "h": 800},
        {"id": "c2", "kind": "corrtext", "x": 470, "y": 520, "text": "中央走道"},
        {
            "id": "r1",
            "kind": "room",
            "room": "exec",
            "x": 44,
            "y": 44,
            "w": 427,
            "h": 118,
            "name": "总经理办公室",
            "sub": "独立行政办公",
        },
        {"id": "w1", "kind": "wall", "x": 471, "y": 44, "h": 118},
        {
            "id": "r2",
            "kind": "room",
            "room": "meet",
            "x": 477,
            "y": 44,
            "w": 427,
            "h": 118,
            "name": "01 室",
            "sub": "多功能研讨室",
        },
        {"id": "r3", "kind": "room", "room": "meet", "x": 524, "y": 466, "w": 380, "h": 160, "name": "小会议室"},
        {"id": "d1", "kind": "divider", "x": 524, "y": 626, "w": 380},
        {"id": "r4", "kind": "room", "room": "meet", "x": 524, "y": 642, "w": 380, "h": 150, "name": "洽谈室"},
        {"id": "d2", "kind": "divider", "x": 524, "y": 792, "w": 380},
        {"id": "r5", "kind": "room", "room": "live", "x": 524, "y": 808, "w": 380, "h": 170, "name": "直播间"},
        {"id": "r6", "kind": "room", "room": "exec", "x": 44, "y": 1010, "w": 180, "h": 150, "name": "机房"},
        {"id": "w2", "kind": "wall", "x": 224, "y": 1010, "h": 150},
        {"id": "r7", "kind": "room", "room": "front", "x": 224, "y": 1010, "w": 140, "h": 150, "name": "前台"},
        {"id": "w3", "kind": "wall", "x": 364, "y": 1010, "h": 150},
        {"id": "r8", "kind": "room", "room": "meet", "x": 364, "y": 1010, "w": 540, "h": 150, "name": "大会议室"},
        {"id": "l1", "kind": "lobby", "x": 334, "y": 1190, "w": 280, "h": 96},
        {"id": "e1", "kind": "entry", "x": 442, "y": 1212, "text": "大楼入口"},
    ],
}

#: 默认平面图的名字与画布尺寸（与既有的 960×1320 一致）
DEFAULT_FLOOR_NAME = "主办公室"
DEFAULT_FLOOR_WIDTH = 960
DEFAULT_FLOOR_HEIGHT = 1320


def _database_is_completely_empty(conn) -> bool:
    """这个库是不是**一张业务数据都没有**（所有业务表 + 字典表全空）。

    为什么单独抽一个函数：v2.3.0 起"空库不塞默认底图"要判断的就是这件事，
    而**它不能看 floor_maps 空不空** —— 那是结果不是原因，见 `_recognize_default_floor_map`
    里的注释。判据必须是"整个库没有一条用户数据"。

    `floor_maps` / `workstations` / `assets` 全空时返回 True。
    查不到表（老库还没升到那一步）一律当"不空" —— 宁可多建一张图，
    也不能在"其实有数据只是表名对不上"的库上跳过回填（那会让老用户的点位丢掉 floor_map_id）。
    """
    for table in ("workstations", "assets", "floor_maps"):
        if not _table_exists(conn, table):
            return False
        if (conn.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one() or 0) > 0:
            return False
    return True


def _recognize_default_floor_map(conn) -> dict[str, object]:
    """第五阶段：建默认平面图，并把现有点位挂上去。

    返回 `{"id": int|None, "created": bool, "attached": int}`：
    - `created` 为 True 表示这次真的插了新图（幂等性靠它区分）
    - `attached` 是这次挂到默认图上的点位数（无图时为 0）

    **这一步漏了会怎样**：升级后 floor_maps 空表、点位 floor_map_id 全是 NULL，
    /map 找不到图 → 画布上只剩 62 个孤零零的格子，走廊和房间全没了。
    用户看到的是"我的地图被清空了"，而且他会以为是升级弄丢了数据。

    与补 device_types.category 那次的区别：**那次不需要回填，这次必须回填** ——
    所以要走记账（DEFAULT_FLOOR_MAP_KEY），保证「插图 + 回填」只发生一次。
    ALTER 本身幂等可以不管，但 INSERT 一条没有唯一键约束的行是不幂等的。

    三步的顺序不能反：先补列 → 再插图 → 最后回填（回填要有目标 id 才能填）。

    ## v2.3.0：真·空库不再塞默认底图

    **判据是「整个库一条业务数据都没有」，不是「floor_maps 没有图」。**
    这个区别很要紧 —— `floor_maps` 空是**结果**，而我们真正想问的是**原因**：
    是"老库升级该补图"（要补）还是"新用户刚起步"（不该塞）？
    两种情况在 `floor_maps` 这一行上长得**一模一样**。

    **所以这个函数绝不能删**，它同时是老库 `floor_map_id` 回填的唯一路径 ——
    删了老用户升级后会掉进"点位还在、但不属于任何图"的状态。
    只能在这里加一个前置短路：真·空库 → 什么都不做，把空白画布留给用户。

    为什么不让新用户看到一张别人的地图：`DEFAULT_FLOOR_LAYOUT` 里
    「总经理办公室 / 直播间 / 大会议室 / 中央走道」是**本项目办公室的布局**，
    包含本项目真实的组织结构。塞给每个新用户 = 假装知道对方公司长什么样，
    而 `floor_maps` **没有删除接口**（只有 `PUT`）——
    新人唯一出路是进编辑器把 19 个图元逐个 Delete 掉再重画。
    空白画布 + 一句"还没画底图"是**诚实的空态**，也是唯一不出错的默认。
    """
    # v2.3.0：真·空库 → 不建图。这是"别往新用户库里塞别人办公室"的那道闸。
    # 判空放在最前面，连 floor_maps 表都不去查 —— 反正没有数据可回填。
    if _database_is_completely_empty(conn):
        # ⚠️ 刻意**不写** DEFAULT_FLOOR_MAP_KEY 记账：那把钥匙的意思是
        # "默认图已认领"，而这里的事实是"从未建过图"。写了它，将来用户
        # 自己建了图之后会被这段的记账分支误判成"被 reset 清过"而不再认领。
        return {"id": None, "created": False, "attached": 0}

    # 已经有图了 → 认领过，直接返回默认图（或最早那张）
    if _table_exists(conn, "floor_maps"):
        row = conn.exec_driver_sql(
            "SELECT id FROM floor_maps WHERE is_default = 1 AND active = 1 ORDER BY id LIMIT 1"
        ).first()
        if row is None:
            row = conn.exec_driver_sql(
                "SELECT id FROM floor_maps WHERE active = 1 ORDER BY id LIMIT 1"
            ).first()
        if row is not None:
            _meta_set(conn, DEFAULT_FLOOR_MAP_KEY, str(row[0]))
            return {"id": int(row[0]), "created": False, "attached": 0}
    else:
        return {"id": None, "created": False, "attached": 0}

    recorded = _meta_get(conn, DEFAULT_FLOOR_MAP_KEY)
    if recorded is not None:
        # 记账说有过，但表里查不到 → 被 reset 清过。此时不该重建：
        # reset 的语义就是"清空业务数据"，重启后又长出来一张图会让人以为没清干净。
        return {"id": None, "created": False, "attached": 0}

    import json

    layout_json = json.dumps(DEFAULT_FLOOR_LAYOUT, ensure_ascii=False)
    conn.exec_driver_sql(
        "INSERT INTO floor_maps (name, width, height, grid, layout, is_default, active, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)",
        (
            DEFAULT_FLOOR_NAME,
            DEFAULT_FLOOR_WIDTH,
            DEFAULT_FLOOR_HEIGHT,
            20,
            layout_json,
            datetime.now(),
            datetime.now(),
        ),
    )
    map_id = conn.exec_driver_sql("SELECT last_insert_rowid()").scalar_one()

    # 回填：所有还没挂图的点位都挂到默认图上。
    # 用 IS NULL 而不是无条件 UPDATE —— 万一将来有多图，这里不会把别的图上的点位抢过来。
    attached = conn.exec_driver_sql(
        "UPDATE workstations SET floor_map_id = ? WHERE floor_map_id IS NULL", (map_id,)
    ).rowcount or 0

    _meta_set(conn, DEFAULT_FLOOR_MAP_KEY, str(map_id))
    return {"id": int(map_id), "created": True, "attached": int(attached)}


def _shift_legacy_utc_timestamps(conn) -> tuple[int, int]:
    """第三阶段：把老库里被写成 UTC 的时间校正成本地时间。

    第一阶段起所有 created_at / updated_at 都用 `server_default=func.now()`，
    它在 SQLite 下编译成 CURRENT_TIMESTAMP —— **那是 UTC**。
    东八区的机器上，真实下午 5 点录入的设备在库里写着上午 9 点，
    时间线和审计页都会显示错的时间。这里按本机当前时区偏移整体平移一次。

    为什么敢直接平移：CURRENT_TIMESTAMP 的偏移量是确定的（永远是 0），
    所以"库里这些值都是 UTC"是已知事实，平移就是准确的还原，不是猜测。
    用记账表保证只做一次；时区偏移为 0 的机器上直接跳过。

    返回 (平移的行数, 偏移分钟数)。
    """
    if _meta_get(conn, TIMESTAMPS_LOCAL_KEY) == "local":
        return 0, 0

    offset_seconds = int(datetime.now().astimezone().utcoffset().total_seconds())
    shifted = 0

    if offset_seconds:
        modifier = f"{offset_seconds // 60:+d} minutes"
        for table, columns in _LEGACY_UTC_COLUMNS:
            if not _table_exists(conn, table):
                continue
            existing = _existing_columns(conn, table)
            for column in columns:
                if column not in existing:
                    continue
                result = conn.exec_driver_sql(
                    f"UPDATE {table} SET {column} = datetime({column}, ?) WHERE {column} IS NOT NULL",
                    (modifier,),
                )
                shifted += result.rowcount or 0

    _meta_set(conn, TIMESTAMPS_LOCAL_KEY, "local")
    return shifted, offset_seconds // 60


def _shift_light_utc_timestamps(conn) -> tuple[int, int]:
    """把轻资产里被写成 UTC 的 `since` / `occurred_at` 校正成本地时间（v2.9.8）。

    现场：轻资产「当前分配」写着「起始于 2026/10/20 06:18」，而同一笔操作在
    审计日志里是 14:18 —— 差 8 小时，正好是东八区。

    原因：前端把「发生日期 + 当前时分」拼成本地时间后调了
    `new Date(...).toISOString()`。**`toISOString()` 输出的是 UTC**（带 `Z`），
    pydantic 解析成带时区的 datetime，而 SQLite 的 DateTime 列会把 tzinfo 丢掉 ——
    "本地 14:18" 落库成 "06:18"。现在写入口已经改成发本地 naive 字符串
    （`local_time.LocalNaiveDatetime` 兜底），这里负责把历史数据改回来。

    怎么认出"哪几行是这么写进来的"（判据是写入路径，不是猜数值）：
      · 前端拼的是 `YYYY-MM-DDTHH:mm`（**分钟精度**）→ 落库后微秒部分恰好是 0；
      · 后端自己写的 `datetime.now()` 一定带微秒；
      · 历史导入的日期来自 Excel 单元格，不经过浏览器，排除（见 `_LIGHT_TIME_COLUMNS`）。
    所以判据 = 「微秒为 0 且不是 import」。⚠️ 别把 `datetime('now')` 之类当基准去比对，
    那会把"用户故意补登到上个月"的记录误判成"没偏移"。

    幂等：`schema_meta` 记账，只做一次；时区偏移为 0 的机器上直接跳过。
    """
    if _meta_get(conn, LIGHT_TIMES_LOCAL_KEY) == "local":
        return 0, 0

    offset_seconds = int(datetime.now().astimezone().utcoffset().total_seconds())
    shifted = 0

    if offset_seconds:
        modifier = f"{offset_seconds // 60:+d} minutes"
        for table, column, extra in _LIGHT_TIME_COLUMNS:
            if not _table_exists(conn, table):
                continue
            if column not in _existing_columns(conn, table):
                continue
            # LIKE '%.000000' = 微秒为 0，即"分钟精度"的那批（前端发来的）
            result = conn.exec_driver_sql(
                f"UPDATE {table} SET {column} = datetime({column}, ?) "
                f"WHERE {column} IS NOT NULL AND {column} LIKE '%.000000'{extra}",
                (modifier,),
            )
            shifted += result.rowcount or 0

    _meta_set(conn, LIGHT_TIMES_LOCAL_KEY, "local")
    return shifted, offset_seconds // 60


def ensure_schema() -> dict[str, object]:
    """建表 + 轻量迁移。返回这次实际做了什么，供启动日志回显。

    四件事，全部幂等：
      1. create_all 建缺的表（新表交给它就行，不用手写 DDL）；
      2. 给第二阶段之前的老库补 device_types.category 列；
      3. 给第四阶段之前的老库补 assets.workstation_id 列；
      4. 给第五阶段之前的老库补 workstations.floor_map_id 列，
         并建默认平面图、把现有点位回填上去；
      5. 给第三阶段之前的老库把 UTC 时间戳校正成本地时间；
      6. 给图标改造之前的老库补 device_types.icon / light_categories.icon 列
         （**不回填** —— NULL 就是"用户还没确认过图标"的正确语义）。
    不引入 Alembic —— 约束里明确要求不提前引依赖，这几处改动不值得把迁移框架搬进来。
    新库走 create_all 时列就齐了，前几步会自动跳过。

    第 3、4 步的**顺序要求**：必须在 init_db() 之后跑 ——
    ALTER 语句里 REFERENCES workstations(id) / floor_maps(id) 需要那些表已经存在。
    第 4 步内部还有一层顺序：补列 → 建默认图 → 回填（回填要有目标 id）。
    """
    init_db()

    if not DATABASE_URL.startswith("sqlite"):
        return {
            "category_added": False,
            "workstation_column_added": False,
            "floor_map_column_added": False,
            "device_type_icon_added": False,
            "light_category_icon_added": False,
            "default_floor_map_id": None,
            "default_floor_map_created": False,
            "default_floor_map_name": DEFAULT_FLOOR_NAME,
            "workstations_attached": 0,
            "timestamps_shifted": 0,
            "offset_minutes": 0,
            "light_times_shifted": 0,
        }

    with engine.begin() as conn:
        conn.exec_driver_sql(_SCHEMA_META_DDL)
        category_added = _add_category_column(conn)
        workstation_column_added = _add_workstation_id_column(conn)
        # ⚠️ 顺序：补列 → 建默认图 → 回填。三者都在同一个事务里，任一步失败整批回滚。
        floor_map_column_added = _add_floor_map_id_column(conn)
        floor_map = _recognize_default_floor_map(conn)
        shifted, offset_minutes = _shift_legacy_utc_timestamps(conn)
        # 轻资产那批（浏览器 toISOString 写进来的 UTC）—— 判据不同，单独一次、单独记账
        light_shifted, _ = _shift_light_utc_timestamps(conn)
        # 图标：纯展示属性，不加约束、不回填，放在最后（与前面的顺序没有依赖）
        device_type_icon_added = _add_icon_column(conn, "device_types")
        light_category_icon_added = _add_icon_column(conn, "light_categories")

    return {
        "category_added": category_added,
        "workstation_column_added": workstation_column_added,
        "floor_map_column_added": floor_map_column_added,
        "device_type_icon_added": device_type_icon_added,
        "light_category_icon_added": light_category_icon_added,
        "default_floor_map_id": floor_map["id"],
        "default_floor_map_created": floor_map["created"],
        "default_floor_map_name": DEFAULT_FLOOR_NAME,
        "workstations_attached": floor_map["attached"],
        "timestamps_shifted": shifted,
        "offset_minutes": offset_minutes,
        "light_times_shifted": light_shifted,
    }
