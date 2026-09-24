"""手动灌演示数据（10 台示例资产 + 配对 + 初始履历）。

为什么是手动：
    启动时**不写任何业务数据**。删掉 .db 重启后就是一个空库 ——
    表建好了、设备类型有了、一台资产都没有。这一点很重要：
    自动灌种子会让人分不清"库里这些设备是我录的还是系统塞的"，
    误删数据库文件后重启还会凭空"长"出十台假设备。
    想要样例数据，显式跑这个脚本，你自己心里有数。

用法（在 backend 目录下）：
    python seed.py            灌演示数据
    python seed.py --check    只看各表现在有多少条，什么都不写

不会破坏已录入的数据：
    每一步都只在对应表为空时才写。所以对着一个有真实数据的库跑这个脚本，
    资产不会被追加、已有履历不会被插东西。
    想清空演示数据：python reset.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从任意目录执行本文件
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import (  # noqa: E402
    BUSINESS_TABLES,
    DB_FILE,
    DICTIONARY_TABLES,
    SessionLocal,
    ensure_schema,
    table_counts,
)
from app.seed import DEVICE_TYPES, SEED_ASSETS, seed_demo  # noqa: E402


def _show_counts(counts: dict[str, int]) -> None:
    print("  字典表（启动时自动初始化，reset 不会清）：")
    for table in DICTIONARY_TABLES:
        value = counts.get(table, -1)
        shown = "表不存在" if value < 0 else f"{value} 条"
        print(f"    {table:<18} {shown}")
    print("  业务表：")
    for table in BUSINESS_TABLES:
        value = counts.get(table, -1)
        shown = "表不存在" if value < 0 else f"{value} 条"
        print(f"    {table:<18} {shown}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="给 IT 资产管理系统灌入演示数据（启动时不会自动灌）"
    )
    parser.add_argument("--check", action="store_true", help="只看各表条数，不写任何东西")
    args = parser.parse_args()

    print(f"数据库文件：{DB_FILE}")
    print(
        f"备用的演示数据：设备类型 {len(DEVICE_TYPES)} 个 / 示例资产 {len(SEED_ASSETS)} 台"
        "（表里已有数据则跳过）"
    )
    print()

    if args.check:
        counts = table_counts()
        if all(value < 0 for value in counts.values()):
            print("数据库还没有建表。先启动一次服务（或直接跑 python seed.py）即可。")
            return 1
        _show_counts(counts)
        return 0

    # 建表（老库顺带做轻量迁移），这是 seed 的前置条件
    ensure_schema()

    with SessionLocal() as db:
        result = seed_demo(db)
        counts = table_counts()

    print(f"字典数据：设备类型 {result['types_created']} 个" + ("（已有，跳过）" if not result["types_created"] else ""))
    print(f"演示资产：{result['assets_created']} 台" + ("（已有数据，跳过）" if not result["assets_created"] else ""))
    print(
        f"顺带回填：配对 {result['relations_created']} 条 / 履历 {result['events_created']} 条"
        + (
            "（两张表都非空，跳过）"
            if not result["relations_created"] and not result["events_created"]
            else ""
        )
    )
    print()
    print("当前数据量：")
    _show_counts(counts)

    if not result["assets_created"]:
        print()
        print("说明：资产表里已经有数据，本脚本不会往里追加 —— 它只往空表灌。")
        print("      想要干净的演示环境：python reset.py 先清空（会有二次确认）。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
