#!/usr/bin/env python
"""点位排布规则的测试 —— 纯函数，不起服务、不连库、不占端口。

跑法（在 backend/ 下）：
    py -3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import station_layout as sl  # noqa: E402


class TestResolveBulkFacing(unittest.TestCase):
    """`alternate` 必须产出「整排同向、两排相对」。"""

    def test_alternate_is_per_row_not_per_column(self):
        # cols=4, count=8 → 两行；第 0 行 4 个朝下、第 1 行 4 个朝上
        rows = [i // 4 for i in range(8)]
        got = [sl.resolve_bulk_facing("alternate", r) for r in rows]
        self.assertEqual(
            got,
            ["down", "down", "down", "down", "up", "up", "up", "up"],
        )

    def test_alternate_one_row_is_all_down(self):
        # count <= cols 时只有一行，不应该出现"同排里有人朝上"
        got = [sl.resolve_bulk_facing("alternate", 0) for _ in range(6)]
        self.assertEqual(set(got), {"down"})

    def test_alternate_four_rows_alternates(self):
        got = [sl.resolve_bulk_facing("alternate", r) for r in range(4)]
        self.assertEqual(got, ["down", "up", "down", "up"])

    def test_fixed_modes_ignore_row(self):
        for row in range(5):
            self.assertEqual(sl.resolve_bulk_facing("up", row), "up")
            self.assertEqual(sl.resolve_bulk_facing("down", row), "down")

    def test_unknown_mode_falls_back_to_down(self):
        # 入参合法性由 pydantic 拦，这里只保证纯函数不会抛
        self.assertEqual(sl.resolve_bulk_facing("sideways", 0), "down")

    def test_negative_row_rejected(self):
        with self.assertRaises(ValueError):
            sl.resolve_bulk_facing("alternate", -1)


class TestFacingConstantsMatchModel(unittest.TestCase):
    """纯函数里的字面量必须和 `models.Facing` 对齐（故意不 import models，见模块 docstring）。"""

    def test_values(self):
        from app.models import Facing

        self.assertEqual(sl.FACING_UP, Facing.UP)
        self.assertEqual(sl.FACING_DOWN, Facing.DOWN)
        self.assertEqual(set(sl.BULK_MODES), set(Facing.ALL) | {"alternate"})


if __name__ == "__main__":
    unittest.main()
