"""轻资产重建：按流水重新算出 `light_holdings` 并覆盖（设计 §4.2 ③）。

它比体检**危险** —— 会真的改数据。所以流程被拆成两步：

1. **`--report`（默认）**：只输出"重建后会发生什么"的差异报告 ——
   哪些记录会被新增、哪些会被改、哪些会被删，逐条列出**改前 → 改后**。
2. **`--apply --confirm`**：在**先备份库**（复用 `backup_db.py`，含完整性校验）之后
   才真正写入；没有显式确认参数一律不写。

这样避免"重建工具把当前正确的状态覆盖成错误状态"——因为人在覆盖前已经看过差异。

## 重建必须知道哪些流水有效（§4.2 ④）

**绝不能把全部流水简单相加。** 三类来源的参与规则不同，严格复用
`LightOrigin.EFFECTIVE`（manual / opening 参与，`import` **绝不参与**）：

- `import`（历史 Excel）只有分配、没有对应入库，算进来会得到负库存或凭空出现的持有量；
- 冲正流水（`corrects_id` 不为空）**参与** —— 它本身就是一次真实的归属变动，
  按时间顺序把原记录与冲正记录都算进去，结果自然抵消；
- 排序必须稳定：`occurred_at ASC, id ASC`。

用法：
    python light_rebuild.py                       # 默认只出差异报告
    python light_rebuild.py --apply --confirm     # 真正重建（先备份）
    python light_rebuild.py --apply --confirm --allow-real-db   # 在非临时库上写（危险）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app.database import DB_FILE, SessionLocal, ensure_schema  # noqa: E402
from app.models import LightCategory, LightHolderKind  # noqa: E402
from app.services import light as light_service  # noqa: E402
from app.services.light import HolderRef, position_key  # noqa: E402

_LINE = "-" * 66


def _describe(kind: str, ref_id, who) -> str:
    if kind == LightHolderKind.STOCK:
        return f"库存（{who}）" if who else "库存"
    if kind == LightHolderKind.USER:
        return f"使用人「{who}」"
    return f"{LightHolderKind.LABELS.get(kind, kind)} #{ref_id}"


def _ref_of(kind: str, ref_id, who) -> HolderRef:
    return HolderRef(
        kind=kind,
        ref_id=ref_id,
        user=who if kind == LightHolderKind.USER else None,
        stock_location=who if kind == LightHolderKind.STOCK else None,
    )


def build_plan() -> dict:
    """算出"重建后会发生什么"。**只读**，不改任何数据。"""
    ensure_schema()
    plan: dict = {"db_file": str(DB_FILE), "categories": [], "blocking": []}

    with SessionLocal() as db:
        categories = list(db.execute(select(LightCategory).order_by(LightCategory.id.asc())).scalars().all())
        for category in categories:
            current = {
                position_key(light_service.holding_ref(h)): h.quantity
                for h in light_service.current_holdings(db, category.id)
            }
            target = light_service.replay_holdings(db, category.id)

            creates: list[dict] = []
            updates: list[dict] = []
            deletes: list[dict] = []
            for key in sorted(set(current) | set(target), key=str):
                kind, ref_id, _u, who = key
                before = current.get(key, 0)
                after = target.get(key, 0)
                if before == after:
                    continue
                row = {
                    "kind": kind,
                    "ref_id": ref_id,
                    "who": who,
                    "position": _describe(kind, ref_id, who),
                    "before": before,
                    "after": after,
                }
                if after <= 0:
                    # 重放结果不是正数 —— 不允许写进去（写进去要么是幽灵、要么是负数）
                    if after < 0:
                        plan["blocking"].append(
                            f"品类「{category.name}」：{row['position']} 重放结果为 {after}，"
                            f"不会写入负数（请先修流水或数据）"
                        )
                    deletes.append(row)
                    continue
                if before == 0:
                    creates.append(row)
                elif after == 0:
                    deletes.append(row)
                else:
                    updates.append(row)

            plan["categories"].append({
                "id": category.id,
                "name": category.name,
                "unit": category.unit,
                "creates": creates,
                "updates": updates,
                "deletes": deletes,
            })
    return plan


def _print_plan(plan: dict) -> None:
    print(_LINE)
    print("  轻资产重建 · 差异报告（默认模式，未写任何数据）")
    print(_LINE)
    print(f"  数据库：{plan['db_file']}")
    total = 0
    for entry in plan["categories"]:
        n = len(entry["creates"]) + len(entry["updates"]) + len(entry["deletes"])
        total += n
        if not n:
            continue
        print()
        print(f"  · {entry['name']}（单位 {entry['unit']}）")
        for row in entry["creates"]:
            print(f"      + 新增 {row['position']}：无 → {row['after']}")
        for row in entry["updates"]:
            print(f"      ~ 修改 {row['position']}：{row['before']} → {row['after']}")
        for row in entry["deletes"]:
            print(f"      - 删除 {row['position']}：{row['before']} → 无")
    print()
    if not total:
        print("  ✓ 台账与流水已经一致，重建不会改变任何东西。")
    else:
        print(f"  共 {total} 处会发生改变。确认无误后执行：")
        print("      python light_rebuild.py --apply --confirm")
    if plan["blocking"]:
        print()
        print("  ⛔ 阻塞项：")
        for line in plan["blocking"]:
            print(f"  · {line}")
    print(_LINE)


def _apply(plan: dict) -> int:
    """按差异报告写库。新增 / 修改补差额，删除扣掉原有量。"""
    changed = 0
    with SessionLocal() as db:
        for entry in plan["categories"]:
            for row in entry["creates"] + entry["updates"]:
                light_service.apply_holding_delta(
                    db,
                    category_id=entry["id"],
                    ref=_ref_of(row["kind"], row["ref_id"], row["who"]),
                    delta=row["after"] - row["before"],
                )
                changed += 1
            for row in entry["deletes"]:
                if row["before"] <= 0:
                    continue
                light_service.apply_holding_delta(
                    db,
                    category_id=entry["id"],
                    ref=_ref_of(row["kind"], row["ref_id"], row["who"]),
                    delta=-row["before"],
                )
                changed += 1
        db.commit()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="按流水重建 light_holdings（默认只出报告）")
    parser.add_argument("--apply", action="store_true", help="真正写库（必须同时给 --confirm）")
    parser.add_argument("--confirm", action="store_true", help="确认执行")
    parser.add_argument("--allow-real-db", action="store_true", help="允许在非临时库上写（危险）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    parser.add_argument("--report", action="store_true", help="（默认行为）只输出差异报告")
    args = parser.parse_args()

    plan = build_plan()

    if not args.apply:
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            _print_plan(plan)
        return 0

    if not args.confirm:
        print("拒绝执行：--apply 必须同时给 --confirm（这是写库操作）。", file=sys.stderr)
        return 2

    tmp_dir = os.getenv("IT_ASSET_DATA_DIR")
    in_tmp = bool(tmp_dir) and str(DB_FILE).startswith(str(Path(tmp_dir).resolve()))
    if not in_tmp and not args.allow_real_db:
        print(
            "拒绝执行：当前库不在 IT_ASSET_DATA_DIR 指向的临时目录里。\n"
            "先在临时库上验一遍，确认报告没问题后再对真实库加 --allow-real-db。",
            file=sys.stderr,
        )
        return 2

    if plan["blocking"]:
        print("拒绝执行：报告里有阻塞项（重放结果出现负数）。请先修数据。", file=sys.stderr)
        return 2

    import backup_db  # noqa: E402

    backup_path, size, _assets = backup_db.make_backup(backup_db.BACKUP_DIR)
    print(f"已备份：{backup_path}（{size} 字节）")

    changed = _apply(plan)
    print(f"已按流水重建 {changed} 处持有记录")
    print("建议接着跑：python light_check.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
