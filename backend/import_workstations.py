"""把点位导出 JSON 灌进数据库（一次性 bootstrap / 灾难恢复）。

用法（在 backend 目录下）：

    python import_workstations.py                        # 干跑：只打印计划，一个字不写
    python import_workstations.py --yes                  # 备份后写入
    python import_workstations.py --file other.json      # 指定别的文件
    python import_workstations.py --file x.json --yes --no-backup

**默认干跑，`--yes` 才写** —— 这是 `link_monitors.py` 已经跑通过的那套交互
（干跑 46 对 → --yes 一次写入 → 重跑全部落到「已配好」分支、零重复写入）。

⚠️ 关于重导：本脚本以 `code` 为匹配键 upsert。如果点位编码已被人工改过
（比如 W23 改成了「销售-01」），重导**会新建一个 W23 而不是更新那个点位** ——
因为库里已经没有 code = W23 的行了。日常调整请走界面，不要重跑本脚本。
脚本会检测"坐标与另一个点位完全重合"并给出提示，那种情况基本就是这个坑。

⚠️ 关于 JSON 里的 `assets` 字段：那是**原型侧的假资产**，本脚本显式忽略
（细节见 services/workstation_importer.py 开头的长注释）。
它和真实 assets 表没有任何可对齐的键，读进来会凭空造出一批假设备。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许从任意目录执行本文件
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import backup_db  # noqa: E402
from app.config import BACKUP_DIR  # noqa: E402
from app.database import SessionLocal, ensure_schema, table_counts  # noqa: E402
from app.models import Asset, AuditAction, AuditTarget, Workstation  # noqa: E402
from app.services import workstation_importer  # noqa: E402
from app.services.audit import record_audit  # noqa: E402

DEFAULT_FILE = BACKEND_DIR.parent / "workstations-20260930-1145.json"


def _resolve(path_text: str | None) -> Path:
    if path_text:
        return Path(path_text).expanduser().resolve()
    return DEFAULT_FILE


def _describe(plan: workstation_importer.ImportPlan, *, verbose: bool) -> None:
    print("导入计划：")
    print(f"  文件      {plan.filename}")
    print(f"  总条数    {plan.total}")
    print(f"  新增      {plan.create}")
    print(f"  更新      {plan.update}")
    print(f"  无变化    {plan.unchanged}")
    print(f"  失败      {plan.error}")
    print()

    if plan.duplicate_positions:
        print("⚠️  坐标重合提示（可能是同一个点位改了编码后被重导）：")
        for line in plan.duplicate_positions:
            print(f"    {line}")
        print()

    problems = [r for r in plan.rows if r.errors]
    if problems:
        print(f"有问题的行（{len(problems)} 条）：")
        for row in problems[:20]:
            print(f"  第 {row.row} 条  {row.code or '（无编码）'}")
            for message in row.errors:
                print(f"      ✗ {message}")
        if len(problems) > 20:
            print(f"    … 还有 {len(problems) - 20} 条")
        print()

    warns = [r for r in plan.rows if r.warnings]
    if warns:
        print(f"提示（{len(warns)} 条）：")
        for row in warns[:10]:
            print(f"  第 {row.row} 条  {row.code or '（无编码）'}")
            for message in row.warnings:
                print(f"      ! {message}")
        print()

    if verbose:
        print("逐条明细：")
        for row in plan.rows:
            mark = {"create": "+", "update": "~", "unchanged": "=", "error": "✗"}[row.action]
            extra = f"  {row.action_label}"
            if row.action == "update":
                extra += "：" + "；".join(row.changes)
            print(f"  {mark} 第 {row.row:>3} 条  {row.code:<10}{extra}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把点位导出 JSON 灌进数据库（默认干跑，--yes 才写）"
    )
    parser.add_argument("--file", default=None, help=f"点位 JSON，默认 {DEFAULT_FILE}")
    parser.add_argument("--yes", action="store_true", help="真的写入（不加就是干跑）")
    parser.add_argument("--no-backup", action="store_true", help="跳过自动备份（不推荐）")
    parser.add_argument("--quiet", action="store_true", help="不打印逐条明细")
    args = parser.parse_args()

    path = _resolve(args.file)
    if not path.is_file():
        print(f"[错误] 找不到文件：{path}")
        return 1

    ensure_schema()

    content = path.read_bytes()
    with SessionLocal() as db:
        plan = workstation_importer.build_plan(db, content, path.name)

        if not plan.schema_ok:
            print(f"[错误] {plan.schema_note}")
            return 1
        if plan.empty:
            print("[错误] 这份文件里一个点位都没有（workstations 为空）")
            return 1

        _describe(plan, verbose=not args.quiet)

        if not args.yes:
            print("以上是干跑结果，什么都没写。确认无误后加 --yes 真正导入。")
            return 0

        if not plan.will_write:
            print("没有任何需要写入的变化（全部是「无变化」或「失败」），不用写。")
            return 0

        if not args.no_backup:
            try:
                target, size, assets = backup_db.make_backup(BACKUP_DIR)
            except Exception as error:  # noqa: BLE001  备份失败就别往下走
                print(f"[错误] 自动备份失败：{error}")
                print("       为安全起见已中止，数据一条没动。确实要导可以加 --no-backup（不建议）。")
                return 1
            print(f"已自动备份：{target.name}（{size / 1024:,.1f} KB，含 {assets} 台设备）")

        before_assets = db.query(Asset).count()
        outcome = workstation_importer.apply_plan(db, plan)

        # 一次导入 = 1 条审计（不是 62 条）。与「批量导入 30 台设备 = 1 条」同口径。
        # 点位变动**不写 asset_events** —— 没碰任何资产，硬写就得挑一台"代表资产"
        # 来挂，那是在为满足表结构而编造事实。
        if outcome.created or outcome.updated:
            record_audit(
                db,
                AuditAction.WORKSTATION_CREATE,
                target_type=AuditTarget.WORKSTATION,
                target_id=None,
                target_label=f"点位导入：{path.name}",
                summary=f"（命令行）导入点位文件《{path.name}》："
                        f"新增 {outcome.created} / 更新 {outcome.updated}"
                        f" / 无变化 {outcome.unchanged} / 失败 {outcome.failed}",
                detail={
                    "filename": path.name,
                    "source": "cli",
                    "created": outcome.created,
                    "updated": outcome.updated,
                    "unchanged": outcome.unchanged,
                    "failed": outcome.failed,
                    "created_codes": outcome.created_codes[:50],
                },
            )

        db.commit()
        after_assets = db.query(Asset).count()

        print()
        print(f"[完成] 新增 {outcome.created} / 更新 {outcome.updated} "
              f"/ 无变化 {outcome.unchanged} / 失败 {outcome.failed}")

        total = db.query(Workstation).filter(Workstation.active.is_(True)).count()
        print(f"       当前有效点位：{total} 个")

        # 判定性断言：JSON 里的假资产必须一台都没进库
        if after_assets != before_assets:
            print()
            print(f"[!] 警告：assets 表行数变了（{before_assets} → {after_assets}）。")
            print("    本脚本不该碰资产。请检查是否有人把 JSON 里的 assets 字段写了进去。")
        else:
            print(f"       assets 表未变动（{after_assets} 台）—— 种子 JSON 里的假资产已被忽略 ✓")

        print()
        print("建议再跑一次本命令：全部应该是「无变化」，那是幂等的证据。")
        print(f"当前各表行数：{ {k: v for k, v in table_counts().items()} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
