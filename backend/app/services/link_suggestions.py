"""关联操作建议 —— **操作之前先帮你想一步**。

## 定位

问题中心（`services/issues.py`）是"**事后**派生视图"：数据里已经出现了差异，它负责列出来。
本模块是"**事前**建议"：用户正要把一个无归属的点位交给某人，系统根据该点位下
资产的现状，逐台给出"要不要顺手把设备使用人也对上"的建议。

两者共用同一套归属口径（`services/ownership.py::has_owner()`），
**本模块不自写判空逻辑** —— 那个写法曾经把 `/` 当成真人（CS 决策 82 的复发形态）。

## 建议规则（第一期：仅「无归属点位 → 新增使用人」一个场景）

| 资产现状 | suggestion | 默认勾选 | 界面文案方向 |
| --- | --- | --- | --- |
| 没有使用人（空 / `/`） | `fill` | ✅ | "未登记，建议填入新使用人" |
| 已是新使用人 | `match` | — | **不进建议列表**（只提示有意义的变化） |
| 有其他使用人 | `conflict` | ❌ | "已登记为 X，勾选将覆盖" |
| 公用 / 共享占位（`SHARED_MARKS`） | `keep` | ❌ | "公用 / 共享设备，可保留现状" |

## 不做什么

- **不落库**：建议是算出来的临时结果（红线 2）。没有建议表、没有建议状态字段。
- **不下结论**：`conflict` 只是提示冲突，**不会替用户决定**——"点位使用人变了，
  所有资产必须跟着变"这条规则被刻意排除，理由与 MapStationPanel 里那句
  「强行联动会把差异抹掉」一致。
- **不覆盖真人**：`fill` 是唯一会默认勾选的动作，且它只发生在资产"本来没有人"时。
  拿"空"去覆盖别人不是同步，是丢归属。

## 执行侧的纪律（`routers/workstations.py::apply_linkage`）

执行前**重新核对**：弹窗打开期间资产关系可能已经变了，过时的检查结果
不能直接批量覆盖 —— 每台资产在写库前重算一次 suggestion，不再是 `fill` 的跳过。
"""

from __future__ import annotations

from app.services.ownership import has_owner

#: 代表「公用 / 共享，不该绑到个人」的占位符。
#: 只在本模块的**建议层**认它 —— 不动 `ownership.has_owner()` 的全局口径
#: （那是独立的一轮决策）。
SHARED_MARKS: tuple[str, ...] = ("公用", "公共", "共用", "备用", "公用的")

SUGGESTION_META: dict[str, dict[str, str]] = {
    "fill": {"label": "建议填入", "hint": "该设备尚未登记使用人"},
    "conflict": {"label": "冲突", "hint": "该设备已登记为其他人，勾选将覆盖"},
    "keep": {"label": "可保留", "hint": "公用 / 共享设备，可以不绑到个人"},
}


def _clean(value: str | None) -> str:
    return (value or "").strip()


def is_shared(value: str | None) -> bool:
    """这个使用人是不是「公用 / 共享」类占位。仅建议层使用。"""
    return _clean(value) in SHARED_MARKS


def suggest_for_asset(current_user: str | None, new_user: str) -> str:
    """单台资产的建议结论：`fill` / `conflict` / `keep`。

    `match`（已一致）在 `build_link_suggestions` 里就被排除，不进建议列表。
    `new_user` 必须是**已 trim** 的非空值 —— 调用方保证。
    """
    if not has_owner(current_user):
        return "fill"
    if is_shared(current_user):
        return "keep"
    return "conflict"


def _type_name(asset) -> str:
    """设备类型名：测试替身给 `device_type_name`，ORM 给 relationship `.device_type.name`。"""
    direct = getattr(asset, "device_type_name", None)
    if direct is not None:
        return direct
    dt = getattr(asset, "device_type", None)
    return getattr(dt, "name", "") or ""


def build_link_suggestions(
    assets: list,
    new_user: str,
) -> dict:
    """为「无归属点位 → 新增使用人」算一份逐台建议。

    Args:
        assets: 该点位下挂的资产 ORM 对象（要用 `user_name` / `asset_code` /
            `device_type_name` / `id`）。
        new_user: 新点位使用人（调用方已 trim 非空）。

    Returns:
        dict，直接喂接口响应：
        - `suggestions`: 需要**用户看一眼**的行（不含 `match`），
          排序 fill → keep → conflict（先给"顺理成章"的，冲突压后）；
        - `matched_count`: 已经一致、无需提示的台数（回包带上，前端好说
          "另有 N 台已是本人，无需调整"）；
        - `total_assets`: 该点位资产总数（对账用）。
    """
    rows: list[dict] = []
    matched = 0
    order = {"fill": 0, "keep": 1, "conflict": 2}
    for asset in assets:
        current = _clean(asset.user_name)
        if current == new_user:
            matched += 1
            continue
        suggestion = suggest_for_asset(asset.user_name, new_user)
        meta = SUGGESTION_META[suggestion]
        rows.append(
            {
                "asset_id": asset.id,
                "asset_code": asset.asset_code,
                "device_type_name": _type_name(asset),
                "current_user": current if has_owner(current) else None,
                "suggestion": suggestion,
                "label": meta["label"],
                "hint": meta["hint"],
                "checked": suggestion == "fill",
            }
        )
    rows.sort(key=lambda r: (order[r["suggestion"]], r["asset_code"]))
    return {
        "suggestions": rows,
        "matched_count": matched,
        "total_assets": len(assets),
    }


# --------------------------------------------------------------------------- #
# 清人（第二期）—— 「有人 → 无人」方向
#
# ⚠️ **与迁移彻底解耦**（用户口径："各干各的"）：本函数只服务「用户在点位侧栏
# 把使用人清空」这一条路径。`services/relocation.py` 里那套 `move_user`
# 有它自己的预览与令牌，**不许调用这里**，这里的逻辑也不许伸手去改迁移。
# 解耦由 `tests/test_link_suggestions.py::TestDecoupledFromRelocation` 用源码断言守住。
# --------------------------------------------------------------------------- #

CLEAR_META: dict[str, dict[str, str]] = {
    "clear": {"label": "建议清除", "hint": "勾选后这台设备会变成「未登记使用人」"},
}


def suggest_clear_for_asset(current_user: str | None, old_user: str) -> str | None:
    """单台资产的清人建议：使用人**仍登记为被清的那位**才给建议，否则 `None`。

    条件刻意收得很紧：只提示"名字还挂在他身上"的设备。
    使用人本来就为空、或已经是别人的 —— **一个字都不说**（与填人场景同一条原则：
    只提示真正有意义的变化）。
    """
    current = _clean(current_user)
    if not has_owner(current):
        return None
    if current != _clean(old_user):
        return None
    return "clear"


def build_clear_suggestions(assets: list, old_user: str) -> dict:
    """为「点位使用人 有人 → 无人」算一份逐台建议。

    **默认一个都不勾** —— 清人是"丢归属"方向，且设备很可能属于岗位而非个人
    （人走了、机器留给下一任）。勾了才清，不勾就是保留。

    Returns:
        - `suggestions`: 仍登记为 `old_user` 的设备（`checked` 恒为 `False`）；
        - `other_count`: 使用人不是他 / 本来没登记的设备数（导语里说"另有 N 台不受影响”）；
        - `total_assets`: 该点位资产总数（对账用）。
    """
    rows: list[dict] = []
    other = 0
    for asset in assets:
        suggestion = suggest_clear_for_asset(asset.user_name, old_user)
        if suggestion is None:
            other += 1
            continue
        meta = CLEAR_META[suggestion]
        rows.append(
            {
                "asset_id": asset.id,
                "asset_code": asset.asset_code,
                "device_type_name": _type_name(asset),
                "current_user": _clean(asset.user_name),
                "suggestion": suggestion,
                "label": meta["label"],
                "hint": meta["hint"],
                "checked": False,
            }
        )
    rows.sort(key=lambda r: r["asset_code"])
    return {
        "suggestions": rows,
        "other_count": other,
        "total_assets": len(assets),
    }
