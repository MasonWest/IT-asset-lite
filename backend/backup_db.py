"""把 SQLite 数据库安全地备份成一个带日期时间的文件。

为什么用 Python 而不是在 backup.bat 里直接 copy：
    服务正在运行时，在操作系统层面复制 .db 有可能拿到「写了一半」的页面 ——
    备份看起来成功了，真要用的时候才发现文件是坏的。
    SQLite 官方给了正确做法：Connection.backup()，它在一个读取事务的快照上
    把数据复制出来，服务在跑也安全。所以 bat 只负责把本脚本叫起来。

用法：
    python backup_db.py                备份到 config.BACKUP_DIR
    python backup_db.py --keep 30      备份后只保留最近 30 份（0 = 不清理）
    python backup_db.py --out D:\\bak   备份到指定目录
    python backup_db.py --list         只列出已有备份，不新建
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 允许从任意目录执行本文件
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import BACKUP_DIR  # noqa: E402
from app.database import DB_FILE  # noqa: E402

PREFIX = "it_assets_"
SUFFIX = ".db"


def list_backups(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{PREFIX}*{SUFFIX}"))


def make_backup(folder: Path) -> tuple[Path, int, int]:
    """做一份一致性快照。返回 (文件, 字节数, 设备台数 / -1 表示读不到)。"""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = folder / f"{PREFIX}{stamp}{SUFFIX}"

    source = sqlite3.connect(str(DB_FILE))
    try:
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)
            dest.commit()
        finally:
            dest.close()
    finally:
        source.close()

    # 校验。少了这一步，「成功」可能只是生成了一个 0 字节的文件。
    check = sqlite3.connect(str(target))
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"备份文件的完整性校验未通过：{integrity}")
        tables = [
            row[0]
            for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        assets = -1
        if "assets" in tables:
            assets = check.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        check.close()

    return target, target.stat().st_size, assets


def main() -> int:
    parser = argparse.ArgumentParser(description="备份 IT 资产管理系统的 SQLite 数据库")
    parser.add_argument("--out", default=None, help="备份目录，默认取 config.BACKUP_DIR")
    parser.add_argument("--keep", type=int, default=30, help="只保留最近 N 份，0 表示不清理")
    parser.add_argument("--list", action="store_true", help="只列出已有备份，不新建")
    args = parser.parse_args()

    folder = Path(args.out).resolve() if args.out else BACKUP_DIR

    if args.list:
        print(f"备份目录：{folder}")
        backups = list_backups(folder)
        if not backups:
            print("  （还没有任何备份）")
        for item in backups:
            print(f"  {item.name}   {item.stat().st_size / 1024:,.1f} KB")
        return 0

    if not DB_FILE.exists():
        print(f"[错误] 数据库文件不存在：{DB_FILE}")
        print("       这套系统还没启动过 —— 数据库是首次启动时自动创建的。")
        return 1

    print(f"数据库文件：{DB_FILE}")
    print(f"备份目录  ：{folder}")
    print()

    try:
        target, size, assets = make_backup(folder)
    except Exception as error:  # noqa: BLE001  这里就是要兜住一切并给人话
        print(f"[错误] 备份失败：{error}")
        return 1

    tail = f"，包含 {assets} 台设备" if assets >= 0 else ""
    print(f"[完成] 已生成备份：{target.name}")
    print(f"       大小 {size / 1024:,.1f} KB{tail}")

    if args.keep and args.keep > 0:
        backups = list_backups(folder)
        removed = 0
        for old in backups[: max(0, len(backups) - args.keep)]:
            try:
                old.unlink()
                removed += 1
            except OSError:
                pass
        if removed:
            print(f"       按 --keep {args.keep} 清理了 {removed} 份更早的备份")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
