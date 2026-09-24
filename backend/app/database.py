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
#: inventory_items 引用 inventory_tasks + assets，asset_relations / asset_events 引用 assets。
#: 靠这个顺序而不是"临时关掉外键校验"，是为了让 schema 的问题在重置时就暴露出来。
BUSINESS_TABLES: tuple[str, ...] = (
    "inventory_items",
    "asset_relations",
    "asset_events",
    "inventory_tasks",
    "assets",
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
    ("audit_logs", ("created_at",)),
)

TIMESTAMPS_LOCAL_KEY = "timestamps_local"


def _meta_get(conn, key: str) -> str | None:
    row = conn.exec_driver_sql("SELECT value FROM schema_meta WHERE key = ?", (key,)).first()
    return row[0] if row else None


def _meta_set(conn, key: str, value: str) -> None:
    conn.exec_driver_sql(
        "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


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


def ensure_schema() -> dict[str, object]:
    """建表 + 轻量迁移。返回这次实际做了什么，供启动日志回显。

    三件事，全部幂等：
      1. create_all 建缺的表（新表交给它就行，不用手写 DDL）；
      2. 给第二阶段之前的老库补 device_types.category 列；
      3. 给第三阶段之前的老库把 UTC 时间戳校正成本地时间。

    不引入 Alembic —— 约束里明确要求不提前引依赖，这几处改动不值得把迁移框架搬进来。
    新库走 create_all 时列就齐了，前两步会自动跳过。
    """
    init_db()

    if not DATABASE_URL.startswith("sqlite"):
        return {"category_added": False, "timestamps_shifted": 0, "offset_minutes": 0}

    with engine.begin() as conn:
        conn.exec_driver_sql(_SCHEMA_META_DDL)
        category_added = _add_category_column(conn)
        shifted, offset_minutes = _shift_legacy_utc_timestamps(conn)

    return {
        "category_added": category_added,
        "timestamps_shifted": shifted,
        "offset_minutes": offset_minutes,
    }
