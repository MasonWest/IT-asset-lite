"""把「一个人 ↔ 一台主机 + 一台显示器」的组合，自动落到工位上。

背景：工位导出 JSON 里的人名和资产系统是对应的，但**只有一部分人**是
「恰好一台主机 + 一台显示器」的干净结构。这个脚本只处理这部分，
其余的一律不碰 —— 硬关联出来的错数据，比没有数据更难发现。

## 干的事（两步，都在一个事务里）

1. **人 → 工位**：工位上的使用人本来就来自 JSON（由 `import_workstations.py` 灌入），
   这里只做校验，不改它。
2. **资产 → 工位**：把这个人的那台主机和那台显示器，落到他的工位上
   （写 `assets.workstation_id`）。

## 判据（必须**全部**满足，缺一不可）

- 工位存在、且 `user_name` 非空（空格子不处理）
- 该姓名在资产里**恰好 1 台 `category=host`** 且**恰好 1 台 `category=display`**
  —— 0 台或 2 台都算"不符合"，跳过
- 那两台资产**当前没有工位归属**（已经有的一律不覆盖，说明是人工放过的）
- 那两台资产**状态不是已报废**（报废设备不该出现在工位上）

不符合的会在干跑里逐条列出**原因**，由使用者自己到系统里处理。

## 为什么按 `category` 而不是按设备类型名判「主机 / 显示器」

类型名用户可以随便改（"主机"改成"机箱"就废了），`category` 才是决定
能不能参与配对的列 —— 见 `models.py` 里 `DeviceCategory` 的注释。
另外 `笔记本电脑`/`一体机` 的 category 是 `other`，所以它们**不会**被误当成主机，
正符合"只处理干净组合"的意图。

## 幂等

重复运行是安全的：已经关联过的资产会被归到"已有工位"并跳过，不会重复写。
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import backup_db  # noqa: E402
from app.config import BACKUP_DIR  # noqa: E402
from app.database import SessionLocal, ensure_schema  # noqa: E402
from app.models import (  # noqa: E402
    Asset,
    AssetEventType,
    AssetStatus,
    AuditAction,
    AuditTarget,
    DeviceCategory,
    Workstation,
)
from app.services.audit import record_audit  # noqa: E402
from app.services.events import record_event  # noqa: E402


def collect(db):
    """算出「能自动关联的」和「不符合的（附原因）」。只读。"""
    stations = db.query(Workstation).filter(Workstation.active.is_(True)).all()
    station_by_name: dict[str, Workstation] = {}
    for s in stations:
        name = (s.user_name or "").strip()
        if name:
            station_by_name.setdefault(name, s)

    # 资产按使用人分组，分别收集 host / display
    hosts: dict[str, list[Asset]] = defaultdict(list)
    displays: dict[str, list[Asset]] = defaultdict(list)
    for asset in db.query(Asset).all():
        name = (asset.user_name or "").strip()
        if not name:
            continue
        category = asset.device_type.category if asset.device_type else DeviceCategory.OTHER
        if category == DeviceCategory.HOST:
            hosts[name].append(asset)
        elif category == DeviceCategory.DISPLAY:
            displays[name].append(asset)

    ready: list[dict] = []
    skipped: list[tuple[str, str]] = []

    for name, station in sorted(station_by_name.items()):
        h = hosts.get(name, [])
        d = displays.get(name, [])

        if len(h) != 1 or len(d) != 1:
            detail = []
            if not h and not d:
                detail.append("名下没有主机和显示器")
            else:
                detail.append(f"主机 {len(h)} 台、显示器 {len(d)} 台")
            if len(h) > 1:
                detail.append("主机：" + "、".join(a.asset_code for a in h))
            if len(d) > 1:
                detail.append("显示器：" + "、".join(a.asset_code for a in d))
            if len(h) == 0 and len(d) == 1:
                detail.append(f"只有显示器 {d[0].asset_code}")
            skipped.append((name, f"工位 {station.code} · " + "；".join(detail)))
            continue

        host, monitor = h[0], d[0]
        problems = []
        if host.status == AssetStatus.SCRAPPED:
            problems.append(f"主机 {host.asset_code} 已报废")
        if monitor.status == AssetStatus.SCRAPPED:
            problems.append(f"显示器 {monitor.asset_code} 已报废")
        if host.workstation_id is not None:
            problems.append(f"主机 {host.asset_code} 已有工位")
        if monitor.workstation_id is not None:
            problems.append(f"显示器 {monitor.asset_code} 已有工位")
        if problems:
            skipped.append((name, f"工位 {station.code} · " + "；".join(problems)))
            continue

        ready.append({"name": name, "station": station, "host": host, "monitor": monitor})

    # 有设备、但地图上没有工位的人 —— 不是错误，只是提示
    no_desk = []
    for name in sorted(set(hosts) | set(displays)):
        if name not in station_by_name:
            h, d = hosts.get(name, []), displays.get(name, [])
            no_desk.append((name, len(h), len(d)))

    return ready, skipped, no_desk


def describe(ready, skipped, no_desk) -> None:
    print("将要自动关联（一个人 = 一台主机 + 一台显示器）：")
    if not ready:
        print("  （没有符合条件的组合）")
    for item in ready:
        print(
            f"  {item['name']:<8} 工位 {item['station'].code:<5}"
            f" 主机 {item['host'].asset_code:<18} 显示器 {item['monitor'].asset_code}"
        )
    print(f"  小计：{len(ready)} 人 / {len(ready) * 2} 台资产")

    print()
    print("跳过：不符合「1 主机 + 1 显示器」的（请自行处理）")
    if not skipped:
        print("  （无）")
    for name, reason in skipped:
        print(f"  {name:<8} {reason}")

    if no_desk:
        print()
        print("提示：资产里有设备、但地图上没有对应工位的人（本次无法关联）")
        for name, nh, nd in no_desk:
            print(f"  {name:<8} 主机 {nh} 台、显示器 {nd} 台")

    print()
    print("说明：以上只写「资产 → 工位」的归属。设备之间的主机↔显示器配对关系")
    print("      **不在本脚本范围内**（那件事由系统里的配对功能负责）。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把「一个人 = 1 主机 + 1 显示器」的组合自动关联到工位（默认干跑）"
    )
    parser.add_argument("--yes", action="store_true", help="真的写入（不加就是干跑）")
    parser.add_argument("--no-backup", action="store_true", help="跳过自动备份（不推荐）")
    parser.add_argument("--operator", default="工位自动关联", help="写进审计的操作人")
    args = parser.parse_args()

    ensure_schema()
    with SessionLocal() as db:
        station_count = db.query(Workstation).filter(Workstation.active.is_(True)).count()
        if station_count == 0:
            print("[错误] 数据库里一个工位都没有。先跑一次导入：")
            print("       python import_workstations.py --yes")
            return 1

        ready, skipped, no_desk = collect(db)
        describe(ready, skipped, no_desk)

        if not ready:
            print()
            print("没有需要写入的内容。")
            return 0

        if not args.yes:
            print()
            print("以上是干跑结果，什么都没写。确认无误后加 --yes 真正关联。")
            return 0

        if not args.no_backup:
            try:
                target, size, assets = backup_db.make_backup(BACKUP_DIR)
            except Exception as error:  # noqa: BLE001
                print(f"[错误] 自动备份失败：{error}")
                print("       为安全起见已中止，数据一条没动。确实要写可以加 --no-backup（不建议）。")
                return 1
            print(f"\n已自动备份：{target.name}（{size / 1024:,.1f} KB，含 {assets} 台设备）")

        touched_assets = 0
        for item in ready:
            station = item["station"]
            label = station.code
            for asset in (item["host"], item["monitor"]):
                asset.workstation_id = station.id
                touched_assets += 1
                # 每台资产各写 1 条时间线 —— 履历要各自完整。
                # 这是「这台机器搬到哪去了」的答案，不能省。
                record_event(
                    db,
                    asset,
                    AssetEventType.UPDATE,
                    operator=args.operator,
                    note=f"所在工位 空 → {label}（按使用人自动关联）",
                    detail={
                        "changes": {"workstation_id": {"from": "", "to": label}},
                        "auto_linked_by": args.operator,
                        "person": item["name"],
                    },
                )

        # 一次批量操作 = 1 条审计（不是 82 条），与批量导入同口径
        record_audit(
            db,
            AuditAction.WORKSTATION_UPDATE,
            target_type=AuditTarget.WORKSTATION,
            target_id=None,
            target_label=f"工位自动关联（{len(ready)} 人）",
            summary=(
                f"按使用人自动关联工位：{len(ready)} 人 / {touched_assets} 台资产"
                f"（每人恰好 1 主机 + 1 显示器）；"
                f"跳过不符合条件 {len(skipped)} 人"
            ),
            operator=args.operator,
            detail={
                "people": [i["name"] for i in ready],
                "stations": [i["station"].code for i in ready],
                "assets_touched": touched_assets,
                "skipped": [{"name": n, "reason": r} for n, r in skipped],
            },
        )
        db.commit()

    print()
    print(f"[完成] 已关联 {len(ready)} 人 / {touched_assets} 台资产")
    print(f"       跳过 {len(skipped)} 人（不符合条件，留给你自己处理）")
    print()
    print("想核对效果：打开资产台账看这几台的「所在工位」，或打开空间地图看工位的资产清单。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
