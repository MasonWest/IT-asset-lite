"""字典数据自动初始化 + 演示数据手动灌入。

两条路径分得很清楚，别混：

  ensure_dictionary(db)   设备类型这类字典数据。**启动时自动调用** ——
                          没有设备类型就没法录入资产，它是系统能跑的最小前提。
                          它不含任何资产。
  seed_demo(db)           10 台示例资产 + 由它们推导出的配对 + 初始履历。
                          **只在显式执行 `python seed.py` 时才跑。**

所以删掉 .db 重启后是一个空库：表建好了、设备类型有了、一台资产都没有。
这是预期行为。想看样例数据就手动灌。

所有写入都是幂等的 —— 只在对应表为空时才动手，绝不覆盖你已经录入的数据。
想连设备类型都不自动建：设环境变量 IT_ASSET_SKIP_SEED=1。
"""

from __future__ import annotations

import os
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Asset,
    AssetEvent,
    AssetEventType,
    AssetRelation,
    AssetStatus,
    DeviceCategory,
    DeviceType,
)

#: 字典数据：设备类型。空库时自动建，用户之后可以随意增删改。
DEVICE_TYPES: list[tuple[str, int, str]] = [
    ("主机", 10, DeviceCategory.HOST),
    ("笔记本", 20, DeviceCategory.HOST),
    ("显示器", 30, DeviceCategory.DISPLAY),
    ("键盘", 40, DeviceCategory.OTHER),
    ("鼠标", 50, DeviceCategory.OTHER),
    ("打印机", 60, DeviceCategory.OTHER),
]

SEED_ASSETS: list[dict] = [
    {
        "asset_code": "IT-2026-0001",
        "finance_code": "FIN-2024-1101",
        "device_type": "主机",
        "brand": "联想",
        "model": "ThinkCentre M720t",
        "serial_number": "PC0L8X2A",
        "status": AssetStatus.IN_USE,
        "user_name": "张伟",
        "location": "总部 3F 研发区 A-01",
        "purchase_date": date(2024, 3, 12),
        "warranty_until": date(2027, 3, 11),
        "notes": "研发主力机，i7-12700 / 32G / 1T SSD。",
    },
    {
        "asset_code": "IT-2026-0002",
        "finance_code": "FIN-2024-1102",
        "device_type": "主机",
        "brand": "戴尔",
        "model": "OptiPlex 7010 MT",
        "serial_number": "8HK2N43",
        "status": AssetStatus.IN_USE,
        "user_name": "李娜",
        "location": "总部 3F 财务室 B-04",
        "purchase_date": date(2024, 6, 8),
        "warranty_until": date(2027, 6, 7),
        "notes": "财务专用，已装税控软件。",
    },
    {
        "asset_code": "IT-2026-0003",
        "finance_code": "FIN-2023-0905",
        "device_type": "显示器",
        "brand": "戴尔",
        "model": "U2723QE 27英寸 4K",
        "serial_number": "CN0P7Q2R",
        "status": AssetStatus.IN_USE,
        "user_name": "张伟",
        "location": "总部 3F 研发区 A-01",
        "purchase_date": date(2023, 9, 20),
        "warranty_until": date(2026, 9, 19),
        "notes": "Type-C 一线连。",
    },
    {
        "asset_code": "IT-2026-0004",
        "finance_code": "FIN-2023-0906",
        "device_type": "显示器",
        "brand": "AOC",
        "model": "24B2XH 23.8英寸",
        "serial_number": "AOC24B2XH0231",
        "status": AssetStatus.IDLE,
        "user_name": None,
        "location": "总部 2F 库房 货架 C2",
        "purchase_date": date(2023, 9, 20),
        "warranty_until": date(2026, 9, 19),
        "notes": "备用机，成色良好。",
    },
    {
        "asset_code": "IT-2026-0005",
        "finance_code": "FIN-2025-0210",
        "device_type": "显示器",
        "brand": "明基",
        "model": "GW2790QT 27英寸 2K",
        "serial_number": "BQM2790QT0044",
        "status": AssetStatus.IN_USE,
        "user_name": "王强",
        "location": "总部 4F 产品部 C-02",
        "purchase_date": date(2025, 2, 18),
        "warranty_until": date(2028, 2, 17),
        "notes": "带人体工学支架。",
    },
    {
        "asset_code": "IT-2026-0006",
        "finance_code": "FIN-2025-0211",
        "device_type": "笔记本",
        "brand": "苹果",
        "model": "MacBook Pro 14 M4 Pro",
        "serial_number": "C02XL9YKMD6T",
        "status": AssetStatus.IN_USE,
        "user_name": "陈静",
        "location": "总部 4F 设计部 D-07",
        "purchase_date": date(2025, 1, 9),
        "warranty_until": date(2028, 1, 8),
        "notes": "设计岗主力机，AppleCare+ 到 2028。",
    },
    {
        "asset_code": "IT-2026-0007",
        "finance_code": "FIN-2022-0333",
        "device_type": "笔记本",
        "brand": "惠普",
        "model": "ProBook 450 G8",
        "serial_number": "5CD1104XY7",
        "status": AssetStatus.REPAIR,
        "user_name": "刘洋",
        "location": "总部 3F IT 维修间",
        "purchase_date": date(2022, 5, 16),
        "warranty_until": date(2025, 5, 15),
        "notes": "电池鼓包，已过保，等配件更换。",
    },
    {
        "asset_code": "IT-2026-0008",
        "finance_code": None,
        "device_type": "键盘",
        "brand": "罗技",
        "model": "K845 机械键盘",
        "serial_number": "2103LZ09A8J8",
        "status": AssetStatus.IN_USE,
        "user_name": "张伟",
        "location": "总部 3F 研发区 A-01",
        "purchase_date": date(2024, 3, 12),
        "warranty_until": None,
        "notes": None,
    },
    {
        "asset_code": "IT-2026-0009",
        "finance_code": None,
        "device_type": "鼠标",
        "brand": "罗技",
        "model": "M720 Triathlon",
        "serial_number": "2204LZ11B2K1",
        "status": AssetStatus.IN_USE,
        "user_name": "李娜",
        "location": "总部 3F 财务室 B-04",
        "purchase_date": date(2024, 6, 8),
        "warranty_until": None,
        "notes": None,
    },
    {
        "asset_code": "IT-2026-0010",
        "finance_code": "FIN-2024-0777",
        "device_type": "打印机",
        "brand": "惠普",
        "model": "LaserJet Pro M404dn",
        "serial_number": "VNC3K12345",
        "status": AssetStatus.IN_USE,
        "user_name": None,
        "location": "总部 3F 打印区",
        "purchase_date": date(2024, 8, 1),
        "warranty_until": date(2027, 7, 31),
        "notes": "部门公用，黑白激光。",
    },
]


def _seed_types(db: Session) -> int:
    """空表时建 6 个基础设备类型。字典数据的唯一入口。"""
    if db.execute(select(func.count(DeviceType.id))).scalar_one():
        return 0
    for name, order, category in DEVICE_TYPES:
        db.add(DeviceType(name=name, sort_order=order, category=category))
    db.commit()
    return len(DEVICE_TYPES)


def _seed_assets(db: Session) -> int:
    """空表时写入 10 台示例资产。**这是演示数据，不是系统必需的数据。**"""
    if db.execute(select(func.count(Asset.id))).scalar_one():
        return 0

    name_to_id = {t.name: t.id for t in db.execute(select(DeviceType)).scalars().all()}
    created = 0
    for row in SEED_ASSETS:
        data = dict(row)
        type_name = data.pop("device_type")
        type_id = name_to_id.get(type_name)
        if type_id is None:
            continue
        db.add(Asset(device_type_id=type_id, **data))
        created += 1
    db.commit()
    return created


def _backfill_events(db: Session) -> int:
    """老库没有变更历史时，给每台设备补一条「录入台账」。

    created_at 用资产自己的创建时间，不要用 now() ——
    否则十台设备的履历会全部挤在「今天」，时间线一眼假。
    """
    if db.execute(select(func.count(AssetEvent.id))).scalar_one():
        return 0

    assets = db.execute(select(Asset)).scalars().all()
    for asset in assets:
        db.add(
            AssetEvent(
                asset_id=asset.id,
                event_type=AssetEventType.CREATE,
                to_status=asset.status,
                operator="系统",
                note=f"录入台账：{asset.asset_code}",
                created_at=asset.created_at or datetime.now(),
            )
        )
    db.commit()
    return len(assets)


def _backfill_relations(db: Session) -> int:
    """配对表为空时，按使用人 + 位置把显示器挂到主机上。

    规则不用硬编码资产编号，而是从数据本身推：
      「同一使用人、同一位置」的主机和显示器，大概率就是一套。
    推不出来的（比如库房里的备用显示器）就留在未配对池子里，
    让用户到「配对管理」页自己去挂 —— 总比瞎猜好。
    """
    if db.execute(select(func.count(AssetRelation.id))).scalar_one():
        return 0

    hosts = db.execute(
        select(Asset).where(Asset.device_type.has(category=DeviceCategory.HOST))
    ).scalars().all()
    monitors = db.execute(
        select(Asset).where(Asset.device_type.has(category=DeviceCategory.DISPLAY))
    ).scalars().all()
    if not hosts or not monitors:
        return 0

    # 主机按 (使用人, 位置) 建索引，显示器拿着同样的键去找宿主
    host_index: dict[tuple[str, str], Asset] = {}
    for host in hosts:
        if host.user_name and host.location:
            host_index.setdefault((host.user_name, host.location), host)

    created = 0
    for monitor in monitors:
        if not monitor.user_name or not monitor.location:
            continue
        host = host_index.get((monitor.user_name, monitor.location))
        if host is None or host.id == monitor.id:
            continue

        db.add(
            AssetRelation(
                host_id=host.id,
                monitor_id=monitor.id,
                active=True,
                note="按使用人 / 位置自动匹配",
                created_by="系统",
            )
        )
        created += 1
    db.commit()
    return created


def ensure_dictionary(db: Session) -> int:
    """补齐字典数据（设备类型），返回新增条数；表非空时返回 0。

    启动时自动跑这一支。它是系统可用的最小前提，而且**不含任何测试资产** ——
    所以「删掉 .db 重启后只看得见字典数据」是预期行为，不是 bug。
    """
    if os.getenv("IT_ASSET_SKIP_SEED") == "1":
        return 0
    return _seed_types(db)


def seed_demo(db: Session) -> dict[str, int]:
    """灌演示数据。**只在显式调用时执行**（命令行入口：`python seed.py`）。

    四步各自幂等，返回实际写入条数；全为 0 说明表里已经有数据、整体跳过了。

    两个"顺带回填"要说明一下，它们看着像写假数据，其实是老库升级路径：
      - 配对表为空 → 按「使用人 + 位置」把显示器挂到主机上；
      - 履历表为空 → 给每台设备补一条「录入台账」，让时间线不是空的。
    两者都只在表**完全为空**时才动手，不会往已有履历里插东西。
    """
    types_created = ensure_dictionary(db)
    assets_created = _seed_assets(db)
    # 顺序不能换：配对和履历都是从 assets 推导出来的，资产没有就没什么可回填
    relations_created = _backfill_relations(db)
    events_created = _backfill_events(db)

    return {
        "types_created": types_created,
        "assets_created": assets_created,
        "relations_created": relations_created,
        "events_created": events_created,
    }
