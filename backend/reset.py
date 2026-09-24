"""清空所有业务数据，保留表结构和字典数据。

删掉什么：
    资产、主机-显示器配对、变更履历、盘点任务与明细、操作审计日志。
保留什么：
    表结构本身，以及设备类型这类字典数据（清完界面还能正常用，只是没数据）。

两道保险：
    1. 必须手动输入 RESET 才会执行。不输入、输入别的、或者 stdin 是空的，
       一律中止，什么都不动；
    2. 确认之后、动手之前，自动备份一份带时间戳的数据库（走 SQLite 在线备份接口，
       服务开着也安全）—— 手滑之后还能捞回来。备份失败就不动手。

用法（在 backend 目录下）：
    python reset.py             列出将删除的内容 → 输入 RESET 确认 → 自动备份 → 清空
    python reset.py --check     只看会删掉什么，一个字都不写
    python reset.py --yes       跳过手动确认（给自动化脚本用）
    python reset.py --no-backup 跳过自动备份（不推荐：这一步是唯一的后悔药）

    也接受管道输入，但同样得真的是 RESET：echo RESET | python reset.py

清完之后想再要演示数据：python seed.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# 允许从任意目录执行本文件
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import backup_db  # noqa: E402
from app.config import BACKUP_DIR  # noqa: E402
from app.database import (  # noqa: E402
    BUSINESS_TABLES,
    DB_FILE,
    DICTIONARY_TABLES,
    ensure_schema,
    table_counts,
)

CONFIRM_WORD = "RESET"


def _describe(counts: dict[str, int]) -> tuple[list[str], int]:
    """把「要删的」「要留的」打出来，返回 (要删的表, 总条数)。"""
    print("将要删除：")
    will_delete: list[str] = []
    total = 0
    for table in BUSINESS_TABLES:
        value = counts.get(table, -1)
        if value < 0:
            print(f"  {table:<18} （表不存在，跳过）")
            continue
        total += value
        will_delete.append(table)
        print(f"  {table:<18} {value} 条")

    print()
    print("将要保留：")
    for table in DICTIONARY_TABLES:
        value = counts.get(table, -1)
        shown = "（表不存在）" if value < 0 else f"{value} 条"
        print(f"  {table:<18} {shown}   ← 字典数据，清空后界面还得靠它")
    print("  （表结构本身也不会动）")
    return will_delete, total


def _confirm(total: int) -> bool:
    print()
    print(f"即将删除 {total} 条业务数据，此操作不可撤销（有备份的话可以从备份恢复）。")
    try:
        answer = input(f"输入 {CONFIRM_WORD} 继续，输入其他任何内容都会中止：")
    except EOFError:
        # stdin 是空的 / 关掉的（脚本里直接调、或者被重定向成空）
        print()
        print("[已中止] 读不到你的输入，什么都没动。")
        print("         确实要清空的话，加 --yes 再执行一次。")
        return False

    if answer.strip().upper() != CONFIRM_WORD:
        print()
        print("[已中止] 输入不匹配，什么都没动。")
        return False
    return True


def _wipe(tables: list[str]) -> int:
    """在一个事务里按外键安全顺序清空。要么全删，要么一条不删。

    不需要额外重置自增序列：主键是 `INTEGER PRIMARY KEY` 而不是 `AUTOINCREMENT`
    （见 models.py），删空后新行的 id 会自动从 1 开始。
    但**顺序不能乱** —— 外键是 `ON DELETE RESTRICT`，先删父表会直接被拦住。
    """
    conn = sqlite3.connect(str(DB_FILE), timeout=15)
    try:
        conn.execute("PRAGMA foreign_keys=ON")  # 必须在开事务之前设
        removed = 0
        with conn:  # 退出时提交；抛异常则整体回滚
            for table in tables:
                cursor = conn.execute(f"DELETE FROM {table}")
                removed += cursor.rowcount or 0
        return removed
    finally:
        conn.close()


def _vacuum() -> tuple[int, int]:
    """回收空间，返回 (回收前, 回收后) 字节数。

    DELETE 只是把页面标成"空闲"，文件不会变小；重置后顺手瘦身。
    VACUUM 必须在事务之外执行，所以这里单开一个 autocommit 连接。
    """
    before = DB_FILE.stat().st_size
    conn = sqlite3.connect(str(DB_FILE), timeout=15, isolation_level=None)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return before, DB_FILE.stat().st_size


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清空 IT 资产管理系统的业务数据（保留表结构与设备类型字典）"
    )
    parser.add_argument("--check", action="store_true", help="只看会删掉什么，不写任何东西")
    parser.add_argument("--yes", action="store_true", help="跳过手动确认（自动化用）")
    parser.add_argument("--no-backup", action="store_true", help="跳过自动备份（不推荐）")
    args = parser.parse_args()

    if not DB_FILE.exists():
        print(f"数据库文件不存在：{DB_FILE}")
        print("本来就没有数据，不用清。启动一次服务会自动建库建表。")
        return 1

    ensure_schema()
    counts = table_counts()

    print(f"数据库文件：{DB_FILE}")
    print(f"备份目录  ：{BACKUP_DIR}")
    print()
    will_delete, total = _describe(counts)

    if args.check:
        print()
        print(f"以上是 --check 的结果，共 {total} 条，什么都没删。")
        return 0

    if total == 0:
        print()
        print("业务数据本来就是空的，不用清。")
        return 0

    print()
    if args.no_backup:
        print("注意：已指定 --no-backup，清空后没有任何备份可以恢复。")
    else:
        print(f"执行前会自动备份一份到：{BACKUP_DIR}")

    # 先确认再备份：确认之前不动任何东西 ——
    # 反过来做的话，中途放弃也会留下一堆用不上的备份文件。
    if not args.yes and not _confirm(total):
        return 1

    if not args.no_backup:
        try:
            target, size, assets = backup_db.make_backup(BACKUP_DIR)
        except Exception as error:  # noqa: BLE001  备份失败就别往下走了
            print()
            print(f"[错误] 自动备份失败：{error}")
            print("       为安全起见已中止，数据一条没动。")
            print("       确实要清空可以加 --no-backup（不建议）。")
            return 1
        print()
        print(f"已自动备份：{target}")
        print(f"            {size / 1024:,.1f} KB，含 {assets} 台设备")

    try:
        removed = _wipe(will_delete)
    except sqlite3.Error as error:
        print()
        print(f"[错误] 清空失败：{error}")
        print("       如果提示 database is locked，先跑 stop.bat 停掉服务再试。")
        print("       已备份的文件没有受影响，数据还在。")
        return 1

    size_before, size_after = _vacuum()

    print()
    print(f"[完成] 已删除 {removed} 条业务数据")
    if size_after < size_before:
        print(
            f"       数据库文件 {size_before / 1024:,.1f} KB → {size_after / 1024:,.1f} KB（已回收空闲页）"
        )
    else:
        # 表结构本身（表 + 索引的空 B 树）就占着这些页，删数据不影响它，属于正常情况
        print(f"       数据库文件 {size_after / 1024:,.1f} KB（没有空闲页可回收）")
    print()
    print("当前数据量：")
    final = table_counts()
    for table in DICTIONARY_TABLES:
        print(f"  {table:<18} {final.get(table, -1)} 条   ← 字典数据保留")
    for table in BUSINESS_TABLES:
        print(f"  {table:<18} {final.get(table, -1)} 条")
    print()
    print("想重新灌演示数据：python seed.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
