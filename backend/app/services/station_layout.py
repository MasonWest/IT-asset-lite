"""批量生成点位的排布规则 —— 纯函数，零 `app.*` 依赖。

为什么单独一个模块而不是塞在 router 里：这是**可以被确定性测试的规则**，
而 `routers/workstations.py` 依赖 `app.database`，import 它会在导入那一刻
把 `DB_FILE` 定死（`test_light.py` 的临时库前提门就是被这个坑过）。
放这里就能进 `unittest discover` 跟别的纯函数测试混跑。

朝向常量用字面量、不 import `models.Facing`，理由同上；两边是否一致由
`tests/test_workstations.py` 断言（它 import 两边来对）。
"""

from __future__ import annotations

#: 与 `models.Facing.UP` / `.DOWN` 对齐
FACING_UP = "up"
FACING_DOWN = "down"

#: 合法的 `facing` 入参（`alternate` 是**生成模式**，不是朝向值，不落库）
BULK_MODES: tuple[str, ...] = (FACING_DOWN, FACING_UP, "alternate")


def resolve_bulk_facing(mode: str, row: int) -> str:
    """批量生成时，第 `row` 行的点位该落什么朝向。

    `alternate` = **按行**交替：第 0 行朝下、第 1 行朝上、第 2 行朝下……

    为什么是按行不是按列（2026-10-27 改）：现实里面对面工位的形状是
    「一排坐 N 个人、桌子对拼、另一排面对面坐 N 个人」，所以**一整排同向**。
    按列交替生成出来是同一排里左朝下、右朝上，像锯齿 —— 那种朝向在任何
    真实排布里都不存在。`cols=4` + `count=8` 应当一次得到一组 4+4。

    `row` 从 0 开始、必须非负；`mode` 不认识时退回朝下（与旧行为一致）。
    """
    if row < 0:
        raise ValueError(f"row 必须 >= 0，收到 {row}")
    if mode == "alternate":
        return FACING_DOWN if row % 2 == 0 else FACING_UP
    if mode == FACING_UP:
        return FACING_UP
    return FACING_DOWN
