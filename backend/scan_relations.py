#!/usr/bin/env python
"""关系与数据质量**只读**扫描 —— 先出清单，绝不改任何数据。

跑法（在 backend/ 下）：
    py -3 scan_relations.py                     # 扫默认库，打印报告
    py -3 scan_relations.py --json .tmp/report.json
    py -3 scan_relations.py --db 别的库.db

## 三条纪律（任务书要求）

1. **只读**：用 SQLite 的 `mode=ro` 打开，物理上写不进去。跑在真实库上也安全。
2. **不批量改生产数据**：本脚本一个 UPDATE 都没有。发现问题 → 出清单 → 人确认。
3. **复用同一套规则**：直接调 `services.relation_check`，**不在这里重写判断** ——
   扫描结果才有资格反过来检验规则（自己抄自己，抄错两边一起错）。

## 分类口径（要求：区分真实错误 / 合理差异 / 历史遗留 / 规则误报）

| 分类 | 判据 |
| --- | --- |
| `合理差异` | 点位无人但有设备（公共设备 / 备用机）、点位有人但设备没填 —— 业务上说得通 |
| `历史遗留` | 点位使用人写的是地点名（会议室 / 前台…）而不是人名 → 数据录入口径问题 |
| `规则误报` | 两侧都是像样的名字却判成差异，但点位名明显不是人 → 规则该改，不是数据该改 |
| `待人工判定` | 两侧都是像样的人名却不一致 —— **这才是真需要人去核对的** |

分类**只影响报告怎么分组**，不影响核对结论本身。
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import importer  # noqa: E402
from app.services import relation_check as rc  # noqa: E402
from app.services.ownership import NO_USER_MARKS, is_no_user  # noqa: E402

#: 点位使用人里出现这些词，说明填的是**地点**不是人 ——
#: 注意它只是"报告分类"用的启发式，**不参与核对判定**（判定永远只看 has_owner）。
_PLACE_HINTS = (
    "会议室", "办公室", "前台", "机房", "仓库", "库房", "大厅", "走道", "走廊",
    "楼梯", "打印区", "茶水", "接待", "储物", "直播", "洽谈", "研讨",
)

#: 这些词是「状态」不是「人名」—— 写在使用人里是在说"这台机器是公用的"。
#: 路线图 Phase 0 点名过这一类（占位符、房间名之外的第三种）。
#:
#: ⚠️ **只在数据卫生层认它，不动 `ownership.has_owner`。**
#: 引擎判"有没有人"的口径（空值 / `/` / `／` = 没人）是决策 76 定的，
#: 改它会牵动前后端两份实现、P0 卡片与既有 e2e —— 那是另一件事。
#: 所以：`公用` 在**引擎**眼里仍是一个"使用人"（可能产生待核对），
#: 在**清洗清单**眼里是"要清掉的非人名值"。先清数据，口径问题另议。
_NON_PERSON_WORDS = (
    "公用", "公共", "共用", "共享", "备用", "待分配", "未分配", "待定", "库存",
)


def looks_like_place(name: Optional[str]) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if is_no_user(n):
        return True
    if any(h in n for h in _PLACE_HINTS):
        return True
    if any(w in n for w in _NON_PERSON_WORDS):
        return True
    # 末尾一个"室 / 区 / 台 / 间"也基本不是人名
    return n.endswith(("室", "区", "台", "间", "位"))


# --------------------------------------------------------------------------- #
# Phase 0：使用人「值清单」与清洗方案
#
# 这两个函数**只产出清单，不写库**。真正落库走既有的批量导入流程
# （`POST /api/transfer/import`，空单元格 = 不修改、写 `-` = 清空，见 importer.CLEAR_TOKENS）。
# 之所以在这儿生成清单而不是手搓一份表格：值是按 SQL 分组现算的，
# 换个库、数据变了清单跟着变，不会拿着一份过期的名单去改数据。
# --------------------------------------------------------------------------- #
def _asset_column_header(key: str) -> str:
    """按 key 取导入模板里的列名。**不写死中文列名** —— 列名改了这里要立刻炸。"""
    for column in importer.ASSET_COLUMNS:
        if column.key == key:
            return column.header
    raise KeyError(f"导入模板里没有这一列：{key}")


def _classify_user_value(value: Optional[str]) -> tuple[str, str]:
    """一个 `user_name` 值是什么性质、该怎么处理。返回 (性质, 建议)。"""
    v = (value or "").strip()
    if not v:
        return "空值", "不用动"
    if is_no_user(v):
        return "占位符", "清空使用人"
    if looks_like_place(v):
        return "像地点", "清空使用人；把地点填进「存放位置」"
    return "人名", "保留"


def _user_value_census(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """两张表的 `user_name` 各有哪些值、各几台、怎么处理。"""
    out: dict[str, list[dict[str, Any]]] = {}
    for table in ("assets", "workstations"):
        rows = conn.execute(
            f"SELECT COALESCE(user_name, '') AS v, COUNT(*) AS n FROM {table} "
            f"GROUP BY v ORDER BY n DESC, v ASC"
        ).fetchall()
        items: list[dict[str, Any]] = []
        for r in rows:
            kind, suggestion = _classify_user_value(r["v"])
            items.append(
                {"value": (r["v"] or "").strip(), "rows": int(r["n"]), "kind": kind, "suggestion": suggestion}
            )
        out[table] = items
    return out


def _clean_plan(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """把"要改哪一行、哪个字段、从什么改成什么、为什么"逐行列出来 —— **审核清单**。

    规则（与路线图 Phase 0 一致，不自行扩大）：
      · 使用人写的是占位符（`/`、`／`）→ 清空；
      · 使用人写的是地点（会议室…）→ 清空，**并把地点搬进「存放位置」**（那个字段本来就是干这个的）；
      · 已经是空的 → 不用动；是人名 → 保留。
    """
    plans: list[dict[str, Any]] = []
    for r in conn.execute(
        "SELECT id, asset_code, COALESCE(user_name,'') AS u, COALESCE(location,'') AS loc "
        "FROM assets ORDER BY asset_code"
    ).fetchall():
        kind, _ = _classify_user_value(r["u"])
        if kind not in ("占位符", "像地点"):
            continue
        plans.append(
            {
                "table": "assets",
                "id": int(r["id"]),
                "asset_code": r["asset_code"],
                "field": _asset_column_header("user_name"),
                "from": r["u"].strip(),
                "to": None,
                "why": "「使用人」里写的是占位符，不是人名" if kind == "占位符" else f"「使用人」里写的是地点「{r['u'].strip()}」，不是人名",
            }
        )
        if kind == "像地点" and not (r["loc"] or "").strip():
            plans.append(
                {
                    "table": "assets",
                    "id": int(r["id"]),
                    "asset_code": r["asset_code"],
                    "field": _asset_column_header("location"),
                    "from": "",
                    "to": r["u"].strip(),
                    "why": "把地点从「使用人」搬到它该在的字段「存放位置」",
                }
            )
    for r in conn.execute(
        "SELECT id, code, COALESCE(user_name,'') AS u FROM workstations WHERE active = 1 ORDER BY code"
    ).fetchall():
        kind, _ = _classify_user_value(r["u"])
        if kind not in ("占位符", "像地点"):
            continue
        plans.append(
            {
                "table": "workstations",
                "id": int(r["id"]),
                "asset_code": r["code"],
                "field": "user_name",
                "from": r["u"].strip(),
                "to": None,
                "why": "点位的「使用人」写的是占位符 / 地点，不是人名",
            }
        )
    return plans


def write_clean_csv(plans: list[dict[str, Any]], headers: list[str], path: Path) -> None:
    """把清洗方案写成**能直接喂给批量导入的 CSV**。

    ⚠️ 两条约定（`importer` 里定的，别在这里另立一套）：
      · **空单元格 = 不修改**，所以只填要改的那几列；
      · **要清空的字段写 `-`**，不能靠留空 —— 留空是"不动"。
    """
    code_h = _asset_column_header("asset_code")
    user_h = _asset_column_header("user_name")
    loc_h = _asset_column_header("location")
    per_asset: dict[str, dict[str, str]] = {}
    for p in plans:
        if p["table"] != "assets":
            continue  # 点位的使用人不在资产导入模板里，另行处理
        row = per_asset.setdefault(p["asset_code"], {code_h: p["asset_code"]})
        if p["field"] == user_h:
            row[user_h] = "-"
        elif p["field"] == loc_h:
            row[loc_h] = p["to"] or ""
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for code in sorted(per_asset):
            writer.writerow(per_asset[code])


def _connect_readonly(db_file: Path) -> sqlite3.Connection:
    uri = f"file:{db_file.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _load(conn: sqlite3.Connection) -> tuple[list[dict], dict[int, list[dict]]]:
    stations = [
        dict(r)
        for r in conn.execute(
            "SELECT id, code, user_name, active FROM workstations WHERE active = 1 "
            "ORDER BY id"
        ).fetchall()
    ]
    assets_by_station: dict[int, list[dict]] = {}
    rows = conn.execute(
        "SELECT id, asset_code, user_name, workstation_id FROM assets "
        "WHERE workstation_id IS NOT NULL ORDER BY asset_code"
    ).fetchall()
    for r in rows:
        assets_by_station.setdefault(int(r["workstation_id"]), []).append(dict(r))
    return stations, assets_by_station


def _classify(issue: rc.Issue, station_user: Optional[str]) -> str:
    if issue.status == rc.CheckStatus.MISMATCH:
        if looks_like_place(station_user):
            return "规则误报"
        return "待人工判定"
    if issue.status == rc.CheckStatus.UNREGISTERED:
        if looks_like_place(station_user) or not (station_user or "").strip():
            return "合理差异"
        return "历史遗留"
    return "其他"


def scan(db_file: Path) -> dict[str, Any]:
    conn = _connect_readonly(db_file)
    try:
        stations, assets_by_station = _load(conn)
        orphan_assets = [
            dict(r)
            for r in conn.execute(
                "SELECT a.id, a.asset_code, a.workstation_id FROM assets a "
                "LEFT JOIN workstations w ON w.id = a.workstation_id "
                "WHERE a.workstation_id IS NOT NULL AND (w.id IS NULL OR w.active = 0)"
            ).fetchall()
        ]
        placeholder_assets = int(
            conn.execute(
                "SELECT COUNT(*) FROM assets WHERE user_name IN (?, ?)",
                NO_USER_MARKS,
            ).fetchone()[0]
        )
        # Phase 0：使用人值清单 + 逐行清洗方案（都只读，落库走既有批量导入）
        user_values = _user_value_census(conn)
        clean_plan = _clean_plan(conn)
    finally:
        conn.close()

    findings: list[dict[str, Any]] = []
    counters = {
        "stations": len(stations),
        "applicable": 0,
        "consistent": 0,
        "mismatch": 0,
        "unregistered": 0,
        "undetermined": 0,
    }
    by_class: dict[str, int] = {}

    for row in stations:
        assets = assets_by_station.get(int(row["id"]), [])
        st = rc.StationInput(id=row["id"], code=row["code"], user_name=row["user_name"])
        inputs = [
            rc.AssetInput(id=a["id"], code=a["asset_code"], user_name=a["user_name"])
            for a in assets
        ]
        result = rc.check_station(st, inputs)
        if not result.applicable:
            continue
        counters["applicable"] += 1
        counters[result.verdict] = counters.get(result.verdict, 0) + 1

        for issue in result.issues:
            klass = _classify(issue, row["user_name"])
            by_class[klass] = by_class.get(klass, 0) + 1
            findings.append(
                {
                    "class": klass,
                    "status": issue.status,
                    "severity": issue.severity,
                    "scope": issue.scope,
                    "workstation_id": row["id"],
                    "workstation_code": row["code"],
                    "workstation_user": row["user_name"],
                    "asset_id": issue.asset_id,
                    "title": issue.title,
                    "detail": issue.detail,
                    "fingerprint": issue.fingerprint,
                }
            )

    # 规则反向检验：如果点位名"像地点"，这类差异基本是规则误报而不是真差异
    place_like_stations = [
        r for r in stations if looks_like_place(r["user_name"])
    ]

    return {
        "db_file": str(db_file),
        "counters": counters,
        "classified": by_class,
        "findings": findings,
        "user_values": user_values,
        "clean_plan": clean_plan,
        "adjacent": {
            "orphan_assets": orphan_assets,
            "asset_placeholder_users": placeholder_assets,
            "place_like_station_users": [
                {"id": r["id"], "code": r["code"], "user_name": r["user_name"]}
                for r in place_like_stations
            ],
        },
    }


def _print_report(report: dict[str, Any]) -> None:
    c = report["counters"]
    print("=" * 68)
    print("  关系与数据质量扫描（只读）")
    print("=" * 68)
    print(f"  数据库：{report['db_file']}")
    print()
    print(f"  点位数            {c['stations']}")
    print(f"    其中有设备的     {c['applicable']}")
    print(f"    结论一致         {c.get('consistent', 0)}")
    print(f"    结论差异         {c.get('mismatch', 0)}")
    print(f"    未登记           {c.get('unregistered', 0)}")
    print(f"    无法判断         {c.get('undetermined', 0)}")
    print()
    print("  问题分类（只影响分组，不影响核对结论）")
    for klass in ("待人工判定", "历史遗留", "合理差异", "规则误报", "其他"):
        if report["classified"].get(klass):
            print(f"    {klass:<8} {report['classified'][klass]}")
    print()
    if report["findings"]:
        print("  明细（最多 40 条）")
        for f in report["findings"][:40]:
            who = f["asset_id"] if f["asset_id"] else "点位级"
            print(f"    [{f['class']}] {f['workstation_code']} · {who} — {f['title']}")
        if len(report["findings"]) > 40:
            print(f"    … 还有 {len(report['findings']) - 40} 条，用 --json 看全量")
    else:
        print("  没有发现需要处理的关系问题。")
    print()
    adj = report["adjacent"]
    print("  相邻检查")
    print(f"    点位名像地点（非人名）的点位   {len(adj['place_like_station_users'])}")
    print(f"    使用人写成占位符的设备         {adj['asset_placeholder_users']}")
    print(f"    挂在已删点位上的设备（应为 0） {len(adj['orphan_assets'])}")
    print()

    # ---- Phase 0：使用人值清单 ----
    values = report.get("user_values") or {}
    if values:
        print("  「使用人」值清单（Phase 0 清洗的直接输入）")
        for table, label in (("assets", "资产"), ("workstations", "点位")):
            rows = values.get(table) or []
            dirty = [r for r in rows if r["kind"] in ("占位符", "像地点")]
            print(f"    {label}：共 {len(rows)} 种取值，其中 {len(dirty)} 种不是人名")
            for r in rows:
                if r["kind"] == "人名":
                    continue
                shown = r["value"] if r["value"] else "（空）"
                print(f"      {shown:<12} {r['rows']:>4} 行   {r['kind']}  → {r['suggestion']}")
        print()

    plan = report.get("clean_plan") or []
    if plan:
        print(f"  清洗方案（逐行，共 {len(plan)} 处改动；**本脚本一个字都没写**）")
        print(f"    其中资产 {sum(1 for p in plan if p['table'] == 'assets')} 处、"
              f"点位 {sum(1 for p in plan if p['table'] == 'workstations')} 处")
        for p in plan[:20]:
            fro = p["from"] or "（空）"
            to = p["to"] or "（清空）"
            print(f"      {p['table']:<12} {p['asset_code']:<18} {p['field']}  {fro} → {to}")
        if len(plan) > 20:
            print(f"      … 还有 {len(plan) - 20} 处，用 --clean-plan 写出完整清单")
        print()

    print("  ⚠️ 本脚本只读，没有修改任何数据。要处理请走界面或明确确认的批量脚本。")


def main() -> int:
    parser = argparse.ArgumentParser(description="关系与数据质量只读扫描（不改数据）")
    parser.add_argument("--db", default=None, help="数据库文件，默认 backend/data/it_assets.db")
    parser.add_argument("--json", default=None, help="把完整结果写到这个 JSON 文件")
    parser.add_argument(
        "--clean-plan",
        default=None,
        help="把 Phase 0 清洗方案写到这个目录：clean-plan.json（审核清单）+ clean-plan.csv（可直接喂批量导入）",
    )
    args = parser.parse_args()

    db_file = Path(args.db) if args.db else (BACKEND_DIR / "data" / "it_assets.db")
    if not db_file.is_file():
        print(f"数据库文件不存在：{db_file}")
        print("先启动一次服务（会自动建库），或确认路径。")
        return 2

    report = scan(db_file)
    _print_report(report)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  完整结果已写入：{out}")
        print()

    if args.clean_plan:
        out_dir = Path(args.clean_plan)
        out_dir.mkdir(parents=True, exist_ok=True)
        plan = report.get("clean_plan") or []
        (out_dir / "clean-plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        headers = [c.header for c in importer.ASSET_COLUMNS]
        write_clean_csv(plan, headers, out_dir / "clean-plan.csv")
        n_assets = len({p["asset_code"] for p in plan if p["table"] == "assets"})
        print(f"  清洗清单已写出：{out_dir / 'clean-plan.json'}（{len(plan)} 处改动）")
        print(f"  可导入的 CSV ：{out_dir / 'clean-plan.csv'}（{n_assets} 台资产）")
        print("    ⚠️ 空单元格 = 不修改；要清空的字段写的是 `-`（importer.CLEAR_TOKENS）")
        print()

    # 退出码：有待人工判定 / 历史遗留 → 1（好让 CI 或批处理能感知），否则 0
    actionable = report["classified"].get("待人工判定", 0) + report["classified"].get("历史遗留", 0)
    return 1 if actionable else 0


if __name__ == "__main__":
    raise SystemExit(main())
