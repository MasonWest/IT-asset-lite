"""轻资产 v2 迁移：取消「数量 / 单件」双模式，建立 `light_holdings` 当前分配记录。

对应《设计方案-轻资产管理模块-修订版》§8.3 / §16。做四件事：

1. **建立当前分配记录**：按**有效流水**重放，把"此刻每个品类分布在哪、各多少"落进
   `light_holdings`。**必须按流水的净结果算**，不能拿"有过流转的旧档案"当持有量 ——
   一件 SSD 曾经入库、后来报损，它有流转记录但最终状态是"已退出管理"，
   转成持有量就会凭空造出一件在库的东西（初稿在这里写错过，已改）。
2. **逐条核对旧单件档案**：把每一条 `light_items` 的最终状态与流水重放结果对账，
   生成"清理清单"（从未流转 / 已报损）与"异常清单"（与流水矛盾、宿主已删）。
   **空档案可以清理，但禁止静默删除** —— 报告里逐条打印后再删。
3. **删除 `light_items` 表**。
4. **删除 `light_categories.tracking` 列**（先删它的索引，SQLite 不允许删被索引的列）。

## ⚠️ 为什么必须把 `light_movements.item_id` 一起删掉（与设计初稿不一致，这里说明）

初稿写的是"保留 `item_id` 列但不读不写（物理删列要重建表，收益小于风险）"。
实测发现那个判断**漏算了外键的副作用**：`item_id` 上有 `REFERENCES light_items(id)`，
而 `light_items` 这张表要被删掉 —— 父表不存在之后，**任何一次对 `light_movements`
的写入都会直接报 `OperationalError: no such table: main.light_items`**（新库同理，
因为模型删了、`create_all` 不会再建那张表）。这不是"要不要留一列"的问题，
而是"留着一个指向幽表的约束会让整张表彻底写不进去"。
SQLite 3.35+ 原生支持 `ALTER TABLE ... DROP COLUMN`，所以直接删列，不需要 12 步重建表。

## 纪律（§16 实施约束）

- **默认干跑**，只打印报告；`--apply --confirm` 才写库；
- 写库前**先备份**（复用 `backup_db.py` 的在线备份 + 完整性校验）；
- 默认**拒绝**在非临时库上写（`IT_ASSET_DATA_DIR` 之外），除非显式 `--allow-real-db`；
- 真实库 / 临时库都支持：`IT_ASSET_DATA_DIR=... python migrate_light_v2.py`

用法：
    python migrate_light_v2.py                      # 干跑，只看报告
    python migrate_light_v2.py --json               # 报告以 JSON 输出
    python migrate_light_v2.py --apply --confirm    # 真正执行（先备份）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app.database import DB_FILE, SessionLocal, ensure_schema, engine  # noqa: E402
from app.models import LightCategory, LightHolderKind, LightMovement, LightOrigin  # noqa: E402
from app.services import light as light_service  # noqa: E402
from app.services.light import HolderRef, position_key  # noqa: E402

_LINE = "-" * 66


# --------------------------------------------------------------------------- #
# 读旧结构（旧表 / 旧列都可能已经不存在）
# --------------------------------------------------------------------------- #
def _table_exists(table: str) -> bool:
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).first()
    return row is not None


def _columns(table: str) -> set[str]:
    if not _table_exists(table):
        return set()
    with engine.begin() as conn:
        return {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def _legacy_items() -> list[dict]:
    """旧 `light_items` 表的内容（原样读出来，不依赖已删除的模型）。"""
    if not _table_exists("light_items"):
        return []
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, category_id, serial_number, label, active FROM light_items ORDER BY id"
        ).fetchall()
    return [
        {"id": r[0], "category_id": r[1], "serial_number": r[2], "label": r[3], "active": r[4]}
        for r in rows
    ]


def _legacy_item_final_state(item_id: int) -> tuple[str | None, bool]:
    """一条旧单件的**最终状态**：重放它的有效流水（排除 import），看最后去向。

    返回 `(最终 to_kind, 有没有任何有效流水)`。`to_kind is None` 表示"已报损 / 已注销"。
    """
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT to_kind FROM light_movements "
            "WHERE item_id = ? AND origin IN (?, ?) "
            "ORDER BY occurred_at ASC, id ASC",
            (item_id, LightOrigin.MANUAL, LightOrigin.OPENING),
        ).fetchall()
    if not rows:
        return None, False
    return rows[-1][0], True


def _host_ok(kind: str, ref_id: int | None) -> tuple[bool, str]:
    """持有位置指向的宿主还在不在（holdings 有真外键，宿主不在就写不进去）。"""
    if ref_id is None:
        return False, "缺少宿主 id"
    table = "assets" if kind == LightHolderKind.ASSET else "workstations"
    if not _table_exists(table):
        return False, f"{table} 表不存在"
    with engine.begin() as conn:
        row = conn.exec_driver_sql(f"SELECT 1 FROM {table} WHERE id = ?", (ref_id,)).first()
    if row is None:
        return False, "宿主已删除"
    return True, ""


# --------------------------------------------------------------------------- #
# 规划：算出"迁移后应当有什么"
# --------------------------------------------------------------------------- #
def build_report() -> dict:
    ensure_schema()

    schema = {
        "has_light_items": _table_exists("light_items"),
        "has_tracking_column": "tracking" in _columns("light_categories"),
        "has_item_id_column": "item_id" in _columns("light_movements"),
        "has_holdings_table": _table_exists("light_holdings"),
    }

    report: dict = {
        "db_file": str(DB_FILE),
        "schema": schema,
        "already_migrated": not (schema["has_light_items"] or schema["has_tracking_column"] or schema["has_item_id_column"]),
        "categories": [],
        "cleanup": [],
        "anomalies": [],
        "blocking": [],
        "totals": {"positions": 0, "quantity": 0},
    }

    with SessionLocal() as db:
        categories = list(db.execute(select(LightCategory).order_by(LightCategory.id.asc())).scalars().all())
        for category in categories:
            entry = _plan_category(db, category, report)
            report["categories"].append(entry)

    # 参与 / 排除的流水条数（让人一眼确认口径没跑偏）
    with engine.begin() as conn:
        eff = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM light_movements WHERE origin IN (?, ?)",
            (LightOrigin.MANUAL, LightOrigin.OPENING),
        ).scalar_one()
        skipped = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM light_movements WHERE origin = ?", (LightOrigin.IMPORT,)
        ).scalar_one()
    report["movements"] = {"effective": int(eff), "skipped_import": int(skipped)}
    return report


def _plan_category(db, category: LightCategory, report: dict) -> dict:
    """规划一个品类的迁移：目标分配 = 有效流水重放（**保证守恒式成立**）。"""
    replay = light_service.replay_holdings(db, category.id)
    current = {
        position_key(light_service.holding_ref(h)): h.quantity
        for h in light_service.current_holdings(db, category.id)
    }

    positions: list[dict] = []
    for key, quantity in sorted(replay.items(), key=lambda kv: str(kv[0])):
        kind, ref_id, _unused, who = key
        if quantity <= 0:
            report["anomalies"].append({
                "category": category.name,
                "reason": "流水重放后该位置数量为负或为零",
                "position": _describe_position(db, kind, ref_id, who),
                "quantity": quantity,
            })
            report["blocking"].append(f"品类「{category.name}」：{_describe_position(db, kind, ref_id, who)} 重放结果为 {quantity}")
            continue
        ok, why = (True, "")
        if kind in (LightHolderKind.ASSET, LightHolderKind.WORKSTATION):
            ok, why = _host_ok(kind, ref_id)
            if not ok:
                report["anomalies"].append({
                    "category": category.name,
                    "reason": f"宿主不可用（{why}），需人工确认归属 —— **不自动丢进库存**",
                    "position": _describe_position(db, kind, ref_id, who),
                    "quantity": quantity,
                })
                report["blocking"].append(
                    f"品类「{category.name}」：{_describe_position(db, kind, ref_id, who)} 的宿主{why}"
                )
                continue
        positions.append({
            "kind": kind,
            "ref_id": ref_id,
            "who": who or None,
            "quantity": quantity,
            "before": current.get(key, 0),
            "after": quantity,
            "changed": current.get(key, 0) != quantity,
        })

    report["totals"]["positions"] += len(positions)
    report["totals"]["quantity"] += sum(p["quantity"] for p in positions)
    return {
        "id": category.id,
        "name": category.name,
        "unit": category.unit,
        "before_stock": sum(q for k, q in current.items() if k[0] == LightHolderKind.STOCK),
        "after_stock": sum(p["quantity"] for p in positions if p["kind"] == LightHolderKind.STOCK),
        "before_allocated": sum(q for k, q in current.items() if k[0] in LightHolderKind.HELD),
        "after_allocated": sum(p["quantity"] for p in positions if p["kind"] in LightHolderKind.HELD),
        "positions": positions,
    }


def _describe_position(db, kind: str, ref_id, who) -> str:
    return light_service.describe_ref(
        db, HolderRef(kind=kind, ref_id=ref_id, user=who if kind == LightHolderKind.USER else None,
                      stock_location=who if kind == LightHolderKind.STOCK else None)
    )


def _plan_legacy_items(report: dict) -> None:
    """逐条核对旧单件档案 → 清理清单 / 异常清单（**禁止静默删除**）。"""
    items = _legacy_items()
    if not items:
        return
    with SessionLocal() as db:
        names = {
            c.id: c.name
            for c in db.execute(select(LightCategory)).scalars().all()
        }
    for item in items:
        final_kind, has_flow = _legacy_item_final_state(item["id"])
        label = item["label"] or item["serial_number"] or f"单件 #{item['id']}"
        category = names.get(item["category_id"], f"品类 #{item['category_id']}")
        record = {
            "id": item["id"],
            "category": category,
            "label": label,
            "active": bool(item["active"]),
            "final_kind": final_kind,
            "has_flow": has_flow,
        }
        if item["category_id"] not in names:
            report["anomalies"].append({**record, "reason": "它指向的品类已不存在，不猜归属"})
            continue
        if not has_flow:
            report["cleanup"].append({**record, "reason": "从未流转过（建了档、没入过账）"})
            continue
        if final_kind is None:
            report["cleanup"].append({**record, "reason": "最终状态是已报损 / 已注销（已退出管理）"})
            continue
        if not item["active"]:
            report["anomalies"].append({**record, "reason": "档案已注销，但流水最终状态仍指向某个位置，需人工判断"})
            continue
        # 正常：它的最终位置已经包含在"流水重放"的结果里，不需要额外动作
        report["cleanup"].append({**record, "reason": "有流转、状态正常 —— 已由流水重放覆盖，档案本身可清理"})


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
def _write_holdings(report: dict) -> int:
    """把规划出来的目标分配写进 `light_holdings`（缺失的新建，已有的对齐）。"""
    written = 0
    with SessionLocal() as db:
        for entry in report["categories"]:
            for pos in entry["positions"]:
                if not pos["changed"]:
                    continue
                ref = HolderRef(
                    kind=pos["kind"],
                    ref_id=pos["ref_id"],
                    user=pos["who"] if pos["kind"] == LightHolderKind.USER else None,
                    stock_location=pos["who"] if pos["kind"] == LightHolderKind.STOCK else None,
                )
                delta = pos["after"] - pos["before"]
                light_service.apply_holding_delta(db, category_id=entry["id"], ref=ref, delta=delta)
                written += 1
        db.commit()
    return written


def _drop_indexes_on(conn, table: str, column: str) -> list[str]:
    """把**所有**引用某一列的索引删掉。

    ⚠️ 为什么不能只按名字删：同一列上可能挂着两个索引 —— 一个是列上的
    `index=True`（`ix_<表>_<列>`），一个是 `__table_args__` 里的复合索引
    （`ix_light_movement_item_time`）。只删知道名字的那个，`ALTER TABLE ... DROP COLUMN`
    会报 `unknown column "item_id" in foreign key definition`（真实库上实测踩到，
    合成库恰好只有一个索引所以没暴露）。这里按 `PRAGMA index_info` 的实际列来判，不猜名字。
    """
    dropped: list[str] = []
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?", (table,)
    ).fetchall()
    for (name,) in rows:
        if not name:
            continue
        cols = [
            r[2]
            for r in conn.exec_driver_sql(f'PRAGMA index_info("{name}")').fetchall()
        ]
        if column in cols:
            conn.exec_driver_sql(f'DROP INDEX IF EXISTS "{name}"')
            dropped.append(name)
    return dropped


def _rebuild_movements_without_item_id() -> str:
    """把 `light_movements` 重建成"没有 item_id 那一列"的版本。

    ## 为什么不能直接 `ALTER TABLE ... DROP COLUMN item_id`

    真实库那张表用的是**表级外键**写法（`FOREIGN KEY(item_id) REFERENCES light_items (id)`），
    SQLite 的 `DROP COLUMN` 明确拒绝"被外键约束引用的列"，报
    `unknown column "item_id" in foreign key definition`。
    （合成库当时用的是列内联 `REFERENCES`，那个反而能删 —— 所以第一版脚本在合成库上过了、
    在真实库上炸。这就是"必须拿真实结构复现过才算验证"的教训。）

    所以对表级外键，走 SQLite 官方推荐的「重建表」：建新表 → 拷贝数据 → 删旧表 → 改名 → 重建索引。
    新表的 DDL 直接由 **SQLAlchemy 模型**编译出来，不手写，避免和模型漂移。
    """
    import sqlite3 as _sqlite3

    from sqlalchemy.dialects import sqlite as _sqlite_dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app.models import LightMovement

    dialect = _sqlite_dialect.dialect()
    con = _sqlite3.connect(str(DB_FILE), isolation_level=None)  # autocommit，自己管事务
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        old_cols = [r[1] for r in con.execute("PRAGMA table_info(light_movements)").fetchall()]
        keep = [c for c in old_cols if c != "item_id"]

        new_ddl = str(CreateTable(LightMovement.__table__).compile(dialect=dialect)).replace(
            "CREATE TABLE light_movements", "CREATE TABLE light_movements__new", 1
        )
        stmts = [
            new_ddl,
            f"INSERT INTO light_movements__new ({', '.join(keep)}) "
            f"SELECT {', '.join(keep)} FROM light_movements",
            "DROP TABLE light_movements",
            "ALTER TABLE light_movements__new RENAME TO light_movements",
        ]
        for idx in LightMovement.__table__.indexes:
            stmts.append(str(CreateIndex(idx).compile(dialect=dialect)))

        con.execute("BEGIN IMMEDIATE")
        for statement in stmts:
            con.execute(statement)
        # 重建之后必须再校验一遍外键：这一步不过，说明这次重建把引用关系搞坏了
        violations = con.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            con.execute("ROLLBACK")
            raise RuntimeError(f"重建 light_movements 后外键校验失败：{violations[:3]}")
        con.execute("COMMIT")
        return (
            f"已重建 light_movements（去掉 item_id 与它上面的外键；保留 {len(keep)} 列、"
            f"重建 {len(LightMovement.__table__.indexes)} 个索引，外键校验通过）"
        )
    except Exception:
        try:
            if con.in_transaction:
                con.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        con.close()


def _mark_openings() -> int:
    """把"这个品类做过期初"的**提示标记**补上。

    ⚠️ 这只是界面提示用（"别再催我做期初了"），**不是"禁止再录入"的开关** ——
    期初允许补录 / 重做。老库里那些 `origin='opening'` 的流水就是当年做过的期初，
    迁移时把它们对应到标记上，界面的提示口径才和历史一致。
    """
    from app.database import light_opening_key, meta_set_on  # noqa: E402

    marked = 0
    with SessionLocal() as db:
        rows = db.execute(
            select(LightMovement.category_id)
            .where(LightMovement.origin == LightOrigin.OPENING)
            .distinct()
        ).all()
        for (category_id,) in rows:
            meta_set_on(db, light_opening_key(int(category_id)), "1")
            marked += 1
        if marked:
            db.commit()
    return marked


def _drop_legacy_schema() -> list[str]:
    """删除旧表 / 旧列。返回做过的事，供报告回显。

    ⚠️ 顺序不能反：**先删列，再删表**。`light_movements.item_id` 上的外键指向
    `light_items`，只要那个外键还在，`DROP TABLE light_items` 就会被外键拦住
    （或者更糟：删完表之后 `light_movements` 整张表再也写不进去）。
    """
    done: list[str] = []
    with engine.begin() as conn:
        cat_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(light_categories)").fetchall()}
        if "tracking" in cat_cols:
            idx = _drop_indexes_on(conn, "light_categories", "tracking")
            conn.exec_driver_sql("ALTER TABLE light_categories DROP COLUMN tracking")
            done.append(f"已删除 light_categories.tracking（顺带删掉 {len(idx)} 个索引 {idx}）")

    # `light_movements` 的 item_id 不能走 DROP COLUMN（真实库是表级外键），单独重建。
    with engine.begin() as conn:
        mv_cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(light_movements)").fetchall()}
    if "item_id" in mv_cols:
        done.append(_rebuild_movements_without_item_id())

    with engine.begin() as conn:
        if _table_exists("light_items"):
            conn.exec_driver_sql("DROP TABLE light_items")
            done.append("已删除 light_items 表（单件档案，取消单件追踪）")
    return done


def _print_report(report: dict) -> None:
    print(_LINE)
    print("  轻资产 v2 迁移报告（默认干跑，未写任何数据）")
    print(_LINE)
    print(f"  数据库   : {report['db_file']}")
    print(f"  旧结构   : light_items={report['schema']['has_light_items']} "
          f"tracking列={report['schema']['has_tracking_column']} "
          f"item_id列={report['schema']['has_item_id_column']}")
    print(f"  流水口径 : 参与重放 {report['movements']['effective']} 条 / "
          f"排除 import {report['movements']['skipped_import']} 条")
    if report["already_migrated"]:
        print()
        print("  ✓ 已经是最新结构，无需迁移。")
        print(_LINE)
        return

    print()
    print("  ── 各品类：迁移前 → 迁移后 ──")
    for entry in report["categories"]:
        print(f"  · {entry['name']}（单位 {entry['unit']}）")
        print(f"      库存     {entry['before_stock']} → {entry['after_stock']}")
        print(f"      已分配   {entry['before_allocated']} → {entry['after_allocated']}")
        for pos in entry["positions"]:
            flag = "改成" if pos["changed"] else "不变"
            print(f"      - {pos['kind']}#{pos['ref_id'] or pos['who'] or ''} "
                  f"{pos['before']} → {pos['after']}（{flag}）")

    if report["cleanup"]:
        print()
        print(f"  ── 待清理的旧档案（{len(report['cleanup'])} 条，确认后才会删）──")
        for row in report["cleanup"]:
            print(f"  · #{row['id']} {row['category']} · {row['label']} —— {row['reason']}")
    if report["anomalies"]:
        print()
        print(f"  ⚠ ── 异常清单（{len(report['anomalies'])} 条，需人工判断）──")
        for row in report["anomalies"]:
            print(f"  · {json.dumps(row, ensure_ascii=False)}")
    if report["blocking"]:
        print()
        print("  ⛔ 阻塞项（这些位置无法安全落库）：")
        for line in report["blocking"]:
            print(f"  · {line}")

    print()
    print("  干跑结束。确认无误后执行：python migrate_light_v2.py --apply --confirm")
    print(_LINE)


def main() -> int:
    parser = argparse.ArgumentParser(description="轻资产 v2 迁移（默认干跑）")
    parser.add_argument("--apply", action="store_true", help="真正写库（必须同时给 --confirm）")
    parser.add_argument("--confirm", action="store_true", help="确认执行")
    parser.add_argument("--allow-real-db", action="store_true", help="允许在非临时库上写（危险）")
    parser.add_argument("--force", action="store_true", help="存在阻塞项时仍然继续（会跳过无法落库的位置）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args()

    report = build_report()
    _plan_legacy_items(report)

    if not args.apply:
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_report(report)
        return 0

    if not args.confirm:
        print("拒绝执行：--apply 必须同时给 --confirm（这是写库操作）。", file=sys.stderr)
        return 2

    tmp_dir = os.getenv("IT_ASSET_DATA_DIR")
    in_tmp = bool(tmp_dir) and str(DB_FILE).startswith(str(Path(tmp_dir).resolve()))
    if not in_tmp and not args.allow_real_db:
        print(
            "拒绝执行：当前库不在 IT_ASSET_DATA_DIR 指向的临时目录里。\n"
            "先在临时库上跑通（IT_ASSET_DATA_DIR=... python migrate_light_v2.py --apply --confirm），\n"
            "确认报告没问题后再对真实库加 --allow-real-db 执行。",
            file=sys.stderr,
        )
        return 2

    if report["blocking"] and not args.force:
        print("拒绝执行：报告里有阻塞项（见上面的 ⛔）。请先处理，或加 --force 明确跳过它们。", file=sys.stderr)
        return 2

    # 先备份（在线备份 + 完整性校验）
    import backup_db  # noqa: E402

    backup_path, size, _assets = backup_db.make_backup(backup_db.BACKUP_DIR)
    print(f"已备份：{backup_path}（{size} 字节）")

    written = _write_holdings(report)
    marked = _mark_openings()
    dropped = _drop_legacy_schema()
    print(f"已写入 / 对齐 {written} 条当前分配记录")
    if marked:
        print(f"  · 已给 {marked} 个做过期初的品类补上「期初已登记」提示标记")
    for line in dropped:
        print(f"  · {line}")

    if report["blocking"]:
        print(f"⚠ 有 {len(report['blocking'])} 个位置因阻塞项未落库 —— 迁移不完整，请人工处理后再跑一次体检。")
    print("迁移完成。建议接着跑：python light_check.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
