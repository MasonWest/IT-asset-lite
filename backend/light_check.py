"""轻资产一致性体检：**只检查，绝不自动修**（设计 §4.2 ②）。

对每个品类，把**有效流水**重放一遍算出应有的 `light_holdings`，与实际表里的逐条比对，
报告差异：

- 数量不一致（表里多少 vs 重放算出多少）；
- 表里有、重放算不出来的"幽灵持有"；
- 重放算得出来、表里却没有的"漏记持有"；
- 任何位置出现负数；
- 守恒式不成立；
- 流水里引用了已被删除的资产 / 点位（不算错，但要列出来 —— 历史本来就允许宿主先走）。

## 行为约束（决策 1 硬性要求）

- **默认且唯一的常规模式就是"只检查"**，输出报告，**绝不自动改数据**；
- 脚本**不提供 `--fix`** —— 修复动作与检查动作分离，走 `light_rebuild.py` 的
  "先出差异报告、备份、显式确认"两段式。
  体检是**发现**问题的，不是**允许**问题的。

用法：
    python light_check.py                # 打印报告（退出码：0 = 一致，1 = 有差异）
    python light_check.py --json         # JSON 输出（给 CI / 脚本用）
    IT_ASSET_DATA_DIR=... python light_check.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select  # noqa: E402

from app.database import DB_FILE, SessionLocal, ensure_schema, engine  # noqa: E402
from app.models import LightCategory, LightHolderKind, LightMovement, LightOrigin  # noqa: E402
from app.services import light as light_service  # noqa: E402
from app.services.light import HolderRef, position_key  # noqa: E402

_LINE = "-" * 66


def _describe(kind: str, ref_id, who) -> str:
    if kind == LightHolderKind.STOCK:
        return f"库存（{who}）" if who else "库存"
    if kind == LightHolderKind.USER:
        return f"使用人「{who}」"
    return f"{LightHolderKind.LABELS.get(kind, kind)} #{ref_id}"


def check() -> dict:
    ensure_schema()
    report: dict = {"db_file": str(DB_FILE), "categories": [], "ok": True}

    with SessionLocal() as db:
        categories = list(db.execute(select(LightCategory).order_by(LightCategory.id.asc())).scalars().all())

        for category in categories:
            actual = {
                position_key(light_service.holding_ref(h)): h.quantity
                for h in light_service.current_holdings(db, category.id)
            }
            replayed = light_service.replay_holdings(db, category.id)

            issues: list[dict] = []
            for key in sorted(set(actual) | set(replayed), key=str):
                kind, ref_id, _u, who = key
                a = actual.get(key, 0)
                b = replayed.get(key, 0)
                if a == b:
                    continue
                label = _describe(kind, ref_id, who)
                if a > 0 and b == 0:
                    issues.append({"type": "ghost", "position": label, "table": a, "replay": b,
                                   "message": f"幽灵持有：表里 {a}，重放算不出来"})
                elif b > 0 and a == 0:
                    issues.append({"type": "missing", "position": label, "table": a, "replay": b,
                                   "message": f"漏记持有：重放应为 {b}，表里没有"})
                else:
                    issues.append({"type": "mismatch", "position": label, "table": a, "replay": b,
                                   "message": f"数量不一致：表里 {a}，重放 {b}"})

            for key, qty in actual.items():
                if qty < 0:
                    kind, ref_id, _u, who = key
                    issues.append({"type": "negative", "position": _describe(kind, ref_id, who),
                                   "table": qty, "replay": None,
                                   "message": f"表里出现了负数：{qty}"})

            holdings_total = sum(actual.values())
            net = light_service.net_by_movements(db, category.id)
            if holdings_total != net:
                issues.append({
                    "type": "conservation", "position": "（整品类）",
                    "table": holdings_total, "replay": net,
                    "message": f"守恒式不成立：Σholdings={holdings_total}，流水净存在量={net}",
                })

            # 流水引用了已删除的宿主 —— 不算错（历史允许宿主先走），但要列出来
            dangling: list[str] = []
            rows = db.execute(
                select(LightMovement)
                .where(LightMovement.category_id == category.id)
                .where(LightMovement.origin.in_(LightOrigin.EFFECTIVE))
            ).scalars().all()
            seen: set[str] = set()
            for m in rows:
                for side in ("from", "to"):
                    kind = getattr(m, f"{side}_kind")
                    if kind not in (LightHolderKind.ASSET, LightHolderKind.WORKSTATION):
                        continue
                    ref_id = getattr(m, f"{side}_ref_id")
                    code = getattr(m, f"{side}_ref_code") or f"#{ref_id}"
                    alive = False
                    if ref_id is not None:
                        table = "assets" if kind == LightHolderKind.ASSET else "workstations"
                        with engine.begin() as conn:
                            alive = conn.exec_driver_sql(
                                f"SELECT 1 FROM {table} WHERE id = ?", (ref_id,)
                            ).first() is not None
                    if not alive and code not in seen:
                        seen.add(code)
                        dangling.append(f"{LightHolderKind.LABELS.get(kind, kind)} {code}")

            if issues:
                report["ok"] = False
            report["categories"].append({
                "id": category.id,
                "name": category.name,
                "unit": category.unit,
                "table_positions": len(actual),
                "table_total": holdings_total,
                "movement_net": net,
                "issues": issues,
                "dangling_refs": dangling,
            })

    return report


def _print_report(report: dict) -> None:
    print(_LINE)
    print("  轻资产一致性体检（只检查，不修复）")
    print(_LINE)
    print(f"  数据库：{report['db_file']}")
    if not report["categories"]:
        print("  （还没有任何轻资产品类）")
    for entry in report["categories"]:
        head = "✓" if not entry["issues"] else "✗"
        print()
        print(f"  {head} {entry['name']}（单位 {entry['unit']}）"
              f"  表里 {entry['table_positions']} 处 / 合计 {entry['table_total']}"
              f"   流水净存在量 {entry['movement_net']}")
        for issue in entry["issues"]:
            print(f"      · {issue['message']}")
        for ref in entry["dangling_refs"]:
            print(f"      ~ 流水里引用了已删除的宿主：{ref}（历史仍成立，仅提示）")
    print()
    if report["ok"]:
        print("  ✓ 台账与流水一致。")
    else:
        print("  ✗ 发现差异。**本脚本不自动修复** —— 请先跑 light_rebuild.py 看差异报告，")
        print("    确认后再用 `light_rebuild.py --apply --confirm`（会先备份）。")
    print(_LINE)


def main() -> int:
    parser = argparse.ArgumentParser(description="轻资产一致性体检（只读）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    report = check()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
