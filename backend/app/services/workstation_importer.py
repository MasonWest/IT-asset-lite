"""点位批量导入（`workstations-*.json`）。

## 与资产导入的关系

不共用 `importer.py` —— 那里是 Excel 列定义 + 单元格解析，这里是 JSON。
但**共用同一套哲学**（本项目已有的、被验收过的那套）：

  1. 预览与落库跑**同一段代码**（`build_plan` → `apply_plan`），
     不存在"预览说没事、一点确认就报错"；
  2. 结果分四桶：新增 / 更新 / 无变化 / 失败。
     少了「无变化」这一桶，同一份文件导第二遍会谎报"更新了 62 条"——
     用户再也无法判断导入到底有没有生效；
  3. 空值 = **不修改**，不是清空。写 `-` 才是清空；
  4. 报错给**真实行号**（JSON 数组里的序号 + 1），不是内部下标。

## ⚠️ 本文件最重要的一条：必须显式忽略 `workstations[].assets`

原型导出的 JSON 里，几乎每个点位带一个 `assets` 数组，内容是这样的：

    {"id": "a1-W07", "type": "host",    "name": "ThinkPad X1 Carbon (主机)", "sn": "TP-W07-8842"}
    {"id": "a2-W07", "type": "monitor", "name": "Dell 27\\" 4K (显示器)",     "sn": "DL-W07-9018"}

**那是原型侧的假资产**，本系统已有 142 台真实资产的 `asset_code` 形如 `A100-01-00002`，
且真实库里 `serial_number` 全部为空 —— 两边**没有任何可对齐的键**。
（顺带它自己也不自洽：`summary.assigned = 45`，却有 47 个点位带资产。）

所以这里 **`row.pop("assets", None)` 并留注释**，而不是"顺手没读"：
图省事把它写进去，会凭空造出 94 台假设备混进台账、参与统计、被导出读到。

真实资产挂在哪个点位，由 `assets.workstation_id` 决定（方向是反的），
不是由这份 JSON 决定。

## 幂等

以 `code` 为匹配键 upsert。**不是 x/y**（拖一下就变）、**不是 seq**（导出时现算）、
**不是 workstation_id**（前端临时标识，历次格式还变过）。
`code` 是唯一稳定、唯一有意义、且用户可在界面上改的标识 ——
与资产侧拿 `asset_code` 做 upsert 键完全一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Facing, Workstation

#: 认得出是点位导出文件的 schema。升级格式时这里是明确的分岔点，
#: 而不是靠字段名瞎猜。
EXPECTED_SCHEMA = "asset-space-map/workstations@1"

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_UNCHANGED = "unchanged"
ACTION_ERROR = "error"

ACTION_LABELS: dict[str, str] = {
    ACTION_CREATE: "新增",
    ACTION_UPDATE: "更新",
    ACTION_UNCHANGED: "无变化",
    ACTION_ERROR: "错误",
}

#: 空值标记：写这些字符等于「清空该字段」，留空才是「不修改」。
CLEAR_TOKENS = frozenset({"-", "--", "—", "空", "(空)", "（空）", "null", "none", "nil"})

MAX_LENGTHS = {"code": 32, "user_name": 64, "room": 64}


def _is_clear_token(text: str) -> bool:
    return text.strip().lower() in CLEAR_TOKENS


# --------------------------------------------------------------------------- #
# 计划
# --------------------------------------------------------------------------- #
@dataclass
class RowPlan:
    row: int
    code: str = ""
    action: str = ACTION_CREATE
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    workstation_id: Optional[int] = None

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)


@dataclass
class ImportPlan:
    filename: str
    rows: list[RowPlan] = field(default_factory=list)
    schema_ok: bool = True
    schema_note: str = ""
    empty: bool = False
    #: 坐标与库里另一个点位完全重合（疑似"改名后重导"）
    duplicate_positions: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    def _count(self, action: str) -> int:
        return sum(1 for r in self.rows if r.action == action)

    @property
    def create(self) -> int:
        return self._count(ACTION_CREATE)

    @property
    def update(self) -> int:
        return self._count(ACTION_UPDATE)

    @property
    def unchanged(self) -> int:
        return self._count(ACTION_UNCHANGED)

    @property
    def error(self) -> int:
        return self._count(ACTION_ERROR)

    @property
    def will_write(self) -> int:
        return self.create + self.update

    @property
    def can_commit(self) -> bool:
        return bool(self.will_write)


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _clean_text(raw: Any) -> tuple[Optional[str], bool]:
    """返回 (值, 是否显式清空)。空 → (None, False)，即「不修改」。"""
    if raw is None:
        return None, False
    text = str(raw).strip()
    if not text:
        return None, False
    if _is_clear_token(text):
        return None, True
    return text, False


def _parse_int(raw: Any, label: str, errors: list[str]) -> Optional[int]:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        errors.append(f"{label} 不能为空")
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        errors.append(f"{label} 不是数字：{raw!r}")
        return None


def build_plan(
    db: Session,
    content: bytes,
    filename: str,
) -> ImportPlan:
    """解析 + 校验，产出「打算怎么改」的计划。**不写任何数据。**"""
    plan = ImportPlan(filename=filename)

    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except UnicodeDecodeError:
        try:
            payload = json.loads(content.decode("gbk"))
        except Exception:  # noqa: BLE001
            plan.schema_ok = False
            plan.schema_note = "文件编码无法识别，请用 UTF-8 保存的 JSON"
            return plan
    except ValueError as error:
        plan.schema_ok = False
        plan.schema_note = f"不是合法的 JSON：{error}"
        return plan

    if not isinstance(payload, dict):
        plan.schema_ok = False
        plan.schema_note = "JSON 顶层应该是一个对象（含 schema / canvas / workstations）"
        return plan

    schema = str(payload.get("schema") or "").strip()
    if schema != EXPECTED_SCHEMA:
        plan.schema_ok = False
        plan.schema_note = (
            f"文件 schema 是「{schema or '（缺失）'}」，"
            f"本接口只认「{EXPECTED_SCHEMA}」。"
            f"请确认导出的是点位文件，而不是别的 JSON。"
        )
        return plan

    raw_rows = payload.get("workstations")
    if not isinstance(raw_rows, list) or not raw_rows:
        plan.empty = True
        return plan

    # 一次查完库里已有的点位，避免每行一次查询
    existing: dict[str, Workstation] = {}
    for station in db.execute(select(Workstation)).scalars().all():
        existing[station.code] = station

    # 坐标 → 现有点位代号，用来发现"改了名又重导"造成的重叠
    occupied: dict[tuple[int, int], str] = {
        (int(s.x), int(s.y)): s.code for s in existing.values() if s.active
    }

    # 文件内重复 code：先扫一遍，报错时才能指出"和哪一条撞了"
    seen: dict[str, int] = {}
    duplicate_of: dict[int, int] = {}
    for index, item in enumerate(raw_rows):
        if not isinstance(item, dict):
            continue
        code, _ = _clean_text(item.get("code"))
        if not code:
            continue
        if code in seen:
            duplicate_of[index] = seen[code]
        else:
            seen[index] = 0  # 占位，真正的行号在下面填
        seen[code] = index + 1

    for index, item in enumerate(raw_rows):
        plan.rows.append(
            _plan_row(
                row_number=index + 1,
                item=item,
                duplicate_of=duplicate_of.get(index),
                existing=existing,
                occupied=occupied,
            )
        )

    # 坐标重合只是提示，不进 error —— 用户可能就是想把两个格子摆在同一格？
    # 不，正常布局里不可能重合，所以报 warning 让他自己看一眼。
    plan.duplicate_positions = [
        f"第 {r.row} 行 {r.code}：{note}" for r in plan.rows for note in r.warnings
        if "坐标完全相同" in note
    ]

    return plan


def _plan_row(
    *,
    row_number: int,
    item: Any,
    duplicate_of: Optional[int],
    existing: dict[str, Workstation],
    occupied: dict[tuple[int, int], str],
) -> RowPlan:
    row = RowPlan(row=row_number)
    errors = row.errors

    if not isinstance(item, dict):
        errors.append("这一条不是对象，无法解析")
        row.action = ACTION_ERROR
        return row

    # ⚠️ 原型带的假资产列表。**必须显式丢弃** —— 理由见模块开头的长注释。
    # 真实资产归属由 assets.workstation_id 决定，方向是反的。
    item.pop("assets", None)
    # 同理丢弃：前端临时标识（历次格式还变过）、导出时现算的顺序号、已废弃的 user_id
    for ignored in ("workstation_id", "seq", "user_id"):
        item.pop(ignored, None)

    # --- code：匹配键，必填且唯一 ---
    code, _ = _clean_text(item.get("code"))
    if not code:
        errors.append("点位编码不能为空（它是判断新增还是更新的依据）")
        row.action = ACTION_ERROR
        return row
    row.code = code
    if len(code) > MAX_LENGTHS["code"]:
        errors.append(f"点位编码太长（最多 {MAX_LENGTHS['code']} 个字符）")
    if duplicate_of is not None:
        errors.append(f"点位编码「{code}」在文件里重复了（第 {duplicate_of} 条已经用过）")

    # --- 坐标：必填 ---
    x = _parse_int(item.get("x"), "x", errors)
    y = _parse_int(item.get("y"), "y", errors)

    row.payload["x"] = x
    row.payload["y"] = y

    # --- 朝向 ---
    facing_raw, _ = _clean_text(item.get("facing"))
    if facing_raw is None:
        row.payload["facing"] = Facing.DOWN
    elif facing_raw in Facing.ALL:
        row.payload["facing"] = facing_raw
    else:
        row.warnings.append(f"朝向「{facing_raw}」不认识，按「朝下」处理")
        row.payload["facing"] = Facing.DOWN

    # --- 文本字段：空 = 不修改，`-` = 清空 ---
    for key in ("user_name", "room"):
        value, cleared = _clean_text(item.get(key))
        if value is not None:
            limit = MAX_LENGTHS.get(key)
            if limit and len(value) > limit:
                errors.append(f"{key} 太长（{len(value)} 字，最多 {limit} 字）")
                continue
            row.payload[key] = value
        elif cleared:
            row.payload[key] = None

    # --- 与库里比对 ---
    current = existing.get(code)
    if current is not None:
        row.workstation_id = current.id
        row.action = ACTION_UPDATE
        if not errors:
            row.changes = _diff_against(current, row.payload)
            if not row.changes:
                row.action = ACTION_UNCHANGED
    else:
        row.action = ACTION_CREATE if not errors else ACTION_ERROR
        # 坐标与另一个点位完全重合 → 提示"是不是同一个点位改了名"
        if x is not None and y is not None:
            other = occupied.get((x, y))
            if other and other != code:
                row.warnings.append(
                    f"坐标 ({x},{y}) 与现有点位「{other}」完全相同。"
                    f"如果这是同一个点位改了名，请不要导入这一条。"
                )

    if errors:
        row.action = ACTION_ERROR
    return row


def _diff_against(station: Workstation, payload: dict[str, Any]) -> list[str]:
    """产出「字段 旧 → 新」的人话列表，空 = 没变化。"""
    changes: list[str] = []
    for key, new_value in payload.items():
        if new_value is None:
            # None 来自「显式清空」；没传的键根本不在这里
            old = getattr(station, key, None)
            if old in (None, ""):
                continue
            changes.append(f"{_label(key)} {_display(old, key)} → 空")
            continue
        old_value = getattr(station, key, None)
        if _render(old_value) == _render(new_value):
            continue
        changes.append(f"{_label(key)} {_display(old_value, key)} → {_display(new_value, key)}")
    return changes


FIELD_LABELS = {
    "x": "x 坐标",
    "y": "y 坐标",
    "facing": "朝向",
    "user_name": "使用人",
    "room": "房间备注",
}


def _label(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def _render(value: Any) -> str:
    if value is None or value == "":
        return ""
    return str(value)


def _display(value: Any, key: str) -> str:
    if value is None or value == "":
        return "空"
    if key == "facing":
        return Facing.LABELS.get(str(value), str(value))
    return str(value)


# --------------------------------------------------------------------------- #
# 落库
# --------------------------------------------------------------------------- #
@dataclass
class ImportOutcome:
    filename: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    created_codes: list[str] = field(default_factory=list)


def apply_plan(db: Session, plan: ImportPlan) -> ImportOutcome:
    """把计划写进库。只 add / flush，**不 commit** —— 由调用方统一提交。"""
    outcome = ImportOutcome(filename=plan.filename)

    for row in plan.rows:
        if row.action == ACTION_ERROR:
            outcome.failed += 1
            continue
        if row.action == ACTION_UNCHANGED:
            outcome.unchanged += 1
            continue

        if row.action == ACTION_CREATE:
            station = Workstation(
                code=row.code,
                x=row.payload.get("x") or 0,
                y=row.payload.get("y") or 0,
                facing=row.payload.get("facing") or Facing.DOWN,
                user_name=row.payload.get("user_name"),
                room=row.payload.get("room"),
                active=True,
            )
            db.add(station)
            outcome.created += 1
            outcome.created_codes.append(row.code)
            continue

        # 更新
        station = db.get(Workstation, row.workstation_id) if row.workstation_id else None
        if station is None:
            row.errors.append("要更新的点位在提交时已经不存在了（可能被同时删掉了）")
            row.action = ACTION_ERROR
            outcome.failed += 1
            continue
        for key, value in row.payload.items():
            setattr(station, key, value)
        outcome.updated += 1

    db.flush()
    return outcome
