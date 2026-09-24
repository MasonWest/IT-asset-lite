"""按编号后缀批量建立「主机 ←→ 显示器」配对。

## 解决什么问题

批量导入资产时，显示器编号常常是「对应主机编号 + 一个后缀」，
比如主机 `B022-03-00001` 配的显示器叫 `B022-03-00001-M`。
一台一台点「绑定」不现实（几十上百台），本脚本按这个命名规则一次性关联。

规则（默认后缀 `-M`，可用 --suffix 改）：

    B022-03-00001-M  →  主机 B022-03-00001

## 安全性设计

1. **默认只分析不写库**。必须显式加 --yes 才会落库 ——
   跟 reset.py 反过来：那边默认要确认词，这边默认连确认都不给，直接干跑。
2. **落库前自动备份**（SQLite 在线备份接口，服务开着也安全）。备份失败就不动手。
3. **写库走项目自己的代码路径**：调 `services/pairing.py::bind()`，
   所以校验、状态机、`asset_events` 时间线全部和界面点按钮完全一致；
   审计按项目口径记**一条**（一次批量操作 = 一条），明细放结构化 detail。
4. **只做加法**。显示器已经挂在别的主机上时**跳过并列出**，不擅自换绑 ——
   换绑会抹掉「它以前在哪台机器上」，这种决定必须人来做。
5. **要么全成，要么全不写**：任何一条失败整批回滚。

用法（在 backend 目录下）：

    python link_monitors.py                分析并列出配对计划，一个字都不写
    python link_monitors.py --yes          备份 → 实际写入
    python link_monitors.py --suffix=_M    换一个后缀（下划线形式）
    python link_monitors.py --operator=张翔  指定审计里的操作人
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# 允许从任意目录执行本文件
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

import backup_db  # noqa: E402
from app.config import BACKUP_DIR  # noqa: E402
from app.database import DB_FILE, SessionLocal, ensure_schema  # noqa: E402
from app.models import (  # noqa: E402
    Asset,
    AssetRelation,
    AuditAction,
    AuditTarget,
    DeviceCategory,
)
from app.services.audit import record_audit  # noqa: E402
from app.services.pairing import active_host_relation_of, bind  # noqa: E402

DEFAULT_SUFFIX = "-M"
DEFAULT_OPERATOR = "系统批量配对"


@dataclass
class Item:
    """一条待建立的配对。"""

    monitor: Asset
    host: Asset


@dataclass
class Issues:
    """没能进配对计划的东西，分类列出来让人一眼看懂为什么。"""

    #: (显示器编号, 期望的主机编号)
    missing_host: list[tuple[str, str]] = field(default_factory=list)
    #: (显示器编号, 主机编号, 主机实际类型名)
    bad_host_type: list[tuple[str, str, str]] = field(default_factory=list)
    #: (显示器编号, 当前挂的主机编号)
    pending_unbind: list[tuple[str, str]] = field(default_factory=list)
    #: 已经是目标状态，不用动
    already_bound: list[tuple[str, str]] = field(default_factory=list)
    #: 编号撞车之类的意外
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.missing_host)
            + len(self.bad_host_type)
            + len(self.pending_unbind)
            + len(self.already_bound)
        )


def _category_of(asset: Asset) -> str:
    dtype = asset.device_type
    return dtype.category if dtype is not None else DeviceCategory.OTHER


def _type_name(asset: Asset) -> str:
    dtype = asset.device_type
    return dtype.name if dtype is not None else "（无类型）"


def scan(db, suffix: str) -> tuple[list[Item], Issues]:
    """扫全库，算出「能配对」和「配不上」两堆。不写任何东西。"""
    assets = list(db.execute(select(Asset).order_by(Asset.id.asc())).scalars().all())

    # 编号唯一是库层面的约束，但大小写可能不一致，这里统一 fold 一遍找对端
    by_code: dict[str, Asset] = {}
    for asset in assets:
        key = asset.asset_code.strip().casefold()
        if key in by_code:
            by_code[key] = None  # type: ignore[assignment]  撞车标记，用不到
        else:
            by_code[key] = asset

    marker = suffix.casefold()
    plan: list[Item] = []
    issues = Issues()

    for asset in assets:
        code = asset.asset_code.strip()
        if not code.casefold().endswith(marker):
            continue
        if len(code) <= len(suffix):
            issues.warnings.append(f"「{code}」只有后缀没有主体，跳过")
            continue
        if _category_of(asset) != DeviceCategory.DISPLAY:
            issues.warnings.append(
                f"「{code}」带后缀但类型是「{_type_name(asset)}」（非显示类），跳过"
            )
            continue

        base = code[: -len(suffix)]
        host = by_code.get(base.strip().casefold())

        if host is None:
            issues.missing_host.append((code, base))
            continue
        if _category_of(host) != DeviceCategory.HOST:
            issues.bad_host_type.append((code, host.asset_code, _type_name(host)))
            continue

        current = active_host_relation_of(db, asset.id)
        if current is not None:
            if current.host_id == host.id:
                issues.already_bound.append((code, host.asset_code))
            else:
                old = db.get(Asset, current.host_id)
                issues.pending_unbind.append((code, old.asset_code if old else str(current.host_id)))
            continue

        plan.append(Item(monitor=asset, host=host))

    return plan, issues


def _report(plan: list[Item], issues: Issues, suffix: str) -> None:
    print(f"配对规则：显示器编号去掉后缀「{suffix}」= 对应主机编号")
    print()
    print(f"【可以配对】{len(plan)} 台")
    for item in plan:
        print(f"  {item.host.asset_code:<18}  ←  {item.monitor.asset_code}")

    if issues.total or issues.warnings:
        print()
        print(f"【不参与本次配对】{issues.total + len(issues.warnings)} 项")
        for code, base in issues.missing_host:
            print(f"  [缺主机]   {code}  找不到对应主机「{base}」")
        for code, host_code, type_name in issues.bad_host_type:
            print(f"  [类型不符] {code}  对端「{host_code}」是「{type_name}」，不是主机类")
        for code, host_code in issues.pending_unbind:
            print(f"  [已挂别处] {code}  当前挂在「{host_code}」上，需人工确认后再处理")
        for code, host_code in issues.already_bound:
            print(f"  [已配好]   {code}  ↔  {host_code}，无需重复绑定")
        for text in issues.warnings:
            print(f"  [跳过]     {text}")


def _summarize_plan(plan: list[Item]) -> dict:
    return {
        "suffix": None,  # 由调用方填
        "pairs": [
            {
                "host_id": item.host.id,
                "host_code": item.host.asset_code,
                "monitor_id": item.monitor.id,
                "monitor_code": item.monitor.asset_code,
            }
            for item in plan
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按编号后缀批量建立主机 ←→ 显示器配对（默认只分析，--yes 才写库）"
    )
    parser.add_argument("--suffix", default=DEFAULT_SUFFIX, help=f"显示器编号后缀，默认 {DEFAULT_SUFFIX}")
    parser.add_argument("--operator", default=DEFAULT_OPERATOR, help="审计记录里的操作人")
    parser.add_argument("--yes", action="store_true", help="确认写入（不加就是干跑，不碰数据）")
    parser.add_argument("--no-backup", action="store_true", help="跳过自动备份（不推荐）")
    args = parser.parse_args()

    suffix = args.suffix.strip()
    operator = args.operator.strip() or DEFAULT_OPERATOR

    if not DB_FILE.exists():
        print(f"数据库文件不存在：{DB_FILE}")
        print("启动一次服务会自动建库建表，或者先跑 python seed.py 灌演示数据。")
        return 1

    ensure_schema()

    with SessionLocal() as db:
        plan, issues = scan(db, suffix)
        print(f"数据库文件：{DB_FILE}")
        print()
        _report(plan, issues, suffix)

        if not plan:
            print()
            print("没有需要配对的显示器，收工。")
            return 0

        if not args.yes:
            print()
            print(f"以上是干跑结果，共 {len(plan)} 对，一个字都没写。")
            print("确实要写入，加 --yes 再执行一次：")
            print(f"    python link_monitors.py --suffix={suffix} --yes")
            return 0

        if args.no_backup:
            print()
            print("注意：已指定 --no-backup，写错了没有备份可恢复。")
        else:
            try:
                target, size, assets = backup_db.make_backup(BACKUP_DIR)
            except Exception as error:  # noqa: BLE001  备份失败就别往下走
                print()
                print(f"[错误] 自动备份失败：{error}")
                print(f"       为安全起见已中止，数据一条没动。备份目录：{BACKUP_DIR}")
                return 1
            print()
            print(f"已自动备份：{target}")
            print(f"            {size / 1024:,.1f} KB，含 {assets} 台设备")

        note = f"批量配对（按编号后缀「{suffix}」自动关联）"
        written = 0
        try:
            for item in plan:
                bind(
                    db,
                    host=item.host,
                    monitor=item.monitor,
                    operator=operator,
                    note=note,
                )
                written += 1

            detail = _summarize_plan(plan)
            detail["suffix"] = suffix
            record_audit(
                db,
                AuditAction.ASSET_PAIR,
                target_type=AuditTarget.ASSET,
                target_label=f"{written} 台显示器（批量）",
                summary=f"批量配对：按编号后缀「{suffix}」自动关联 {written} 台显示器到对应主机",
                operator=operator,
                ip="127.0.0.1",
                detail=detail,
            )
            db.commit()
        except Exception as error:  # noqa: BLE001  要么全成，要么全不写
            db.rollback()
            print()
            print(f"[错误] 第 {written + 1} 对写入失败，整批已回滚：{error}")
            print("       数据库保持原样，可以从上面的备份再比对一次。")
            return 1

    print()
    print(f"[完成] 已建立 {written} 对配对关系")
    print(f"       同时写入 {written * 2} 条变更履历（主机 + 显示器各一条）和 1 条审计日志")
    print()
    print("想核对：打开网页 → 设备列表按「显示器」筛 → 卡片上会显示所属主机编号")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
