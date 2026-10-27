#!/usr/bin/env python
"""恢复一个被软删除的点位（把 `active` 从 0 改回 1）。

## 为什么要有这个脚本

点位是**软删除**的（决策 32：编码不释放，避免审计里两个同名位置）。
软删除对"这个点位以后不用了"是对的，但**误删就没有回头路** ——
2026-10-12 真出过一件事：自动化验收脚本误往真实库建了个点位（W67），
撤它的时候走了软删除，于是用户下一次新增拿到的是 W68，**编码断档**。
用户的原话：「你软删除了我的 w67 导致我现在新增出来就是 w68 这不连贯了」。

所以这里补一条恢复的路。**它不是常用功能**，是误删之后的补救。

## ⚠️ 它不改编码分配规则

恢复一个已删除的点位**不会**让下一个新增回到那个编码 ——
`W67` 恢复之后，下一个新增仍然是 `W70`（因为 W67/W68/W69 都已"用过"）。
这是决策 32 的有意设计：**编码是一次性消耗品**，回收它会让审计记录里
两条同名记录分不清是哪一条。想要编码连续，正确做法是**恢复它**，不是回收。

## 口径（与 link_monitors.py / import_workstations.py 一致）

- **默认干跑**：不加 `--yes` 一个字都不写，只告诉你"会恢复什么"。
- 写库前**自动备份**，备份失败就不动手。
- 恢复是**一条审计**（`workstation_update`），summary 里写明原因 ——
  审计记录不是文案，是证据，绕过服务层直接改库也必须留痕。
- **只做这一件事**：改 `active`。位置、使用人、编码一律不碰
  （位置你自己去地图上拖，那才是"编辑点位"该走的路径）。

跑法：
    python backend/restore_station.py W67            # 干跑
    python backend/restore_station.py W67 --yes      # 真的恢复
    python backend/restore_station.py W67 --reason "当时误删，现在要用回来"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import func  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models import AuditAction, AuditLog, AuditStatus, AuditTarget, Workstation  # noqa: E402


def find_station(db, code: str) -> Workstation | None:
    """按编码找点位。**大小写不敏感**（W67 / w67 都认）。"""
    want = code.strip().upper()
    return (
        db.query(Workstation)
        .filter(func.upper(Workstation.code) == want)
        .order_by(Workstation.id.desc())
        .first()
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="恢复一个被软删除的点位（默认干跑，--yes 才写）",
    )
    parser.add_argument("code", help="点位编码，如 W67")
    parser.add_argument(
        "--reason",
        default="",
        help="恢复原因，会写进审计日志。不填则用一句通用说明",
    )
    parser.add_argument("--yes", action="store_true", help="真的写入（不加就是干跑）")
    args = parser.parse_args()

    reason = args.reason.strip() or "恢复一个此前软删除的点位"

    with SessionLocal() as db:
        station = find_station(db, args.code)
        if station is None:
            print(f"[错误] 找不到编码为 {args.code} 的点位。")
            print("       注意：软删除的点位**也在表里**，所以找不到通常是真的没这个编码。")
            return 1

        print(f"点位      : {station.code}（id={station.id}）")
        print(f"位置      : ({station.x}, {station.y})")
        print(f"当前状态  : {'✅ 已启用' if station.active else '🗑 已软删除'}")
        print(f"恢复原因  : {reason}")

        if station.active:
            print()
            print("[无需操作] 这个点位本来就是启用状态，不用恢复。")
            return 0

        if not args.yes:
            print()
            print("（干跑）加 --yes 才会真的写入。将要执行：")
            print(f"  1. 备份数据库到 backups/")
            print(f"  2. UPDATE workstations SET active = 1 WHERE id = {station.id}")
            print(f"  3. 记一条审计：恢复点位 {station.code}")
            return 0

        # ── 写库前备份（失败就不动手）────────────────────────────────
        print()
        print("正在备份数据库…")
        try:
            from backup_db import make_backup  # noqa: E402

            from app.config import BACKUP_DIR  # noqa: E402

            backup_path, size_before, size_after = make_backup(BACKUP_DIR)
            print(f"  已备份：{backup_path.name}（{size_before / 1024:.1f} KB）")
        except Exception as exc:  # pragma: no cover - 备份失败是异常路径
            print(f"[错误] 备份失败，不动手：{exc}")
            return 1

        # ── 恢复 + 审计，同一个事务 ─────────────────────────────────
        station.active = True
        db.add(
            AuditLog(
                action=AuditAction.WORKSTATION_UPDATE,
                target_type=AuditTarget.WORKSTATION,
                target_id=station.id,
                target_label=station.code,
                summary=f"恢复点位 {station.code}（此前被软删除）：{reason}",
                operator="restore_station.py",
                status=AuditStatus.SUCCESS,
            )
        )
        db.commit()

        print()
        print(f"[完成] 点位 {station.code} 已恢复到界面上。")
        print()
        print("⚠️ 两点提醒：")
        print(f"  1. **编码不会回收** —— 下一个新增仍然是接下来的号，不会是 {station.code}。")
        print("     这是有意的（决策 32）：编码一次性消耗，回收会让审计里两条同名记录分不清。")
        print("  2. 位置保持原样。去地图上「编辑点位」把它拖到该去的地方。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())