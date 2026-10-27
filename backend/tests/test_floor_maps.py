"""平面图：**从零建出第一张图**的接口回归。

## 为什么要有这个文件

v2.3.0 决定「真·空库不塞默认底图」（不把本项目办公室的布局塞给新用户），
但当时只做了"不塞"，没做"给一张白纸"：`floor_maps` 只有 GET 和 PUT，
新 clone 的人进编辑器只会看到 404，**底图永远画不出来**。
2026-10-27 在真实 clone 的空库上实测确认了这条死路，同一天补上
`POST /api/floor-maps`，这个文件把它的边界钉住。

## 库怎么隔离（两个约束同时满足）

1. `app.database.DB_FILE` 在 **import 时刻**就定死了，而同一个 discover 进程里
   第一个 import 它的文件说了算 → 本文件必须在 import `app.*` **之前**把
   `IT_ASSET_DATA_DIR` 指到 `.tmp/` 下，否则同进程后面跑的 `test_light.py`
   那个"拒绝在非临时库上运行"的前提门会当场炸。
2. 但本文件**不碰那个文件库**：测试自己用内存库 + `dependency_overrides[get_db]`，
   所以不需要清理、也不会给别的测试留下脏数据。

（目录名沿用 `light_tests_db` 是历史遗留 —— 现在它其实是"本进程的临时库"。
共用同一个目录而不是各建一个，就是为了上面第 1 条：一个进程只能有一个 DB_FILE。）
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

# --- 必须在 import app.* 之前 ------------------------------------------------ #
_BACKEND = Path(__file__).resolve().parent.parent
_TMP_DIR = (_BACKEND.parent / ".tmp" / "light_tests_db").resolve()
_TMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["IT_ASSET_DATA_DIR"] = str(_TMP_DIR)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models  # noqa: E402,F401  让 metadata 里凑齐所有表
from app.database import Base, DB_FILE, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AuditLog, FloorMap  # noqa: E402

if not str(DB_FILE).startswith(str(_TMP_DIR)):  # pragma: no cover
    raise RuntimeError(f"拒绝在非临时库上运行平面图测试：DB_FILE={DB_FILE}")

#: 内存库。`StaticPool` 让所有连接共用同一个内存实例，否则每个连接各拿到一个空库。
_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


def _override_get_db():
    with Session(_engine) as session:
        yield session


class FloorMapTestBase(unittest.TestCase):
    """每个用例一张全新的内存库：不需要清理，也不会互相看见数据。"""

    def setUp(self) -> None:
        Base.metadata.create_all(_engine)
        app.dependency_overrides[get_db] = _override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(_engine)


class TestEmptyDatabase(FloorMapTestBase):
    """空库：没有图是**正常态**，接口要用 404 表达它，而不是 500。"""

    def test_default_is_404_when_no_map(self):
        res = self.client.get("/api/floor-maps/default")
        self.assertEqual(res.status_code, 404)
        # 文案必须指向真实存在的出路 —— 老文案说"重启后端会重建默认图"，
        # 而按 v2.3.0 的逻辑空库重启、reset 后重启都不会重建（都实测过）。
        self.assertIn("创建空白底图", res.json()["detail"])

    def test_list_is_empty(self):
        body = self.client.get("/api/floor-maps").json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])


class TestCreateFirstMap(FloorMapTestBase):
    def test_creates_blank_canvas(self):
        res = self.client.post("/api/floor-maps", json={})
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()

        self.assertEqual(body["name"], "我的办公室")
        self.assertEqual(body["canvas"], {"width": 960, "height": 1320, "grid": 20})
        self.assertEqual(body["layout"]["shapes"], [], "新建的图必须是空白的")
        self.assertEqual(body["layout"]["schema_version"], 1)
        self.assertTrue(body["is_default"])
        self.assertIsNotNone(body["updated_at"], "前端乐观锁要靠它，不能是 null")

    def test_default_readable_after_create(self):
        created = self.client.post("/api/floor-maps", json={}).json()
        res = self.client.get("/api/floor-maps/default")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["id"], created["id"])
        self.assertEqual(self.client.get("/api/floor-maps").json()["total"], 1)

    def test_custom_size_and_name(self):
        res = self.client.post(
            "/api/floor-maps", json={"name": "研发区", "width": 1200, "height": 800, "grid": 10}
        )
        self.assertEqual(res.status_code, 201, res.text)
        body = res.json()
        self.assertEqual(body["name"], "研发区")
        self.assertEqual(body["canvas"], {"width": 1200, "height": 800, "grid": 10})

    def test_blank_name_falls_back_to_default(self):
        res = self.client.post("/api/floor-maps", json={"name": "   "})
        self.assertEqual(res.status_code, 201, res.text)
        self.assertEqual(res.json()["name"], "我的办公室")

    def test_layout_payload_is_ignored(self):
        """接口**刻意不收 layout** —— 传了也不能生效。

        这是 v2.3.0「不往新用户库里塞别人公司办公室」的护栏：
        一旦有人给 schema 加回 `layout` 字段，这个测试会红。
        """
        res = self.client.post(
            "/api/floor-maps",
            json={
                "layout": {
                    "schema_version": 1,
                    "shapes": [{"id": "s1", "kind": "room", "x": 0, "y": 0, "w": 100, "h": 80}],
                }
            },
        )
        self.assertEqual(res.status_code, 201, res.text)
        self.assertEqual(res.json()["layout"]["shapes"], [])

    def test_name_too_long_rejected(self):
        res = self.client.post("/api/floor-maps", json={"name": "长" * 65})
        self.assertEqual(res.status_code, 422)

    def test_size_out_of_range_rejected(self):
        self.assertEqual(self.client.post("/api/floor-maps", json={"width": 10}).status_code, 422)
        self.assertEqual(self.client.post("/api/floor-maps", json={"height": 99999}).status_code, 422)


class TestSingleMapSemantics(FloorMapTestBase):
    """单图语义：建第二张必须 409。

    不能静默返回旧图（= 点了没反应），也不能真建第二张 ——
    `_default_floor` 按 `is_default desc, id asc` 取图，冒出第二张会让
    "哪张才是地图"变成运气问题。
    """

    def test_second_create_conflicts(self):
        first = self.client.post("/api/floor-maps", json={})
        self.assertEqual(first.status_code, 201)

        res = self.client.post("/api/floor-maps", json={"name": "第二张"})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn(first.json()["name"], res.json()["detail"])

        # 反向断言：库里真的只有一张
        self.assertEqual(self.client.get("/api/floor-maps").json()["total"], 1)

    def test_soft_deleted_map_does_not_block_creating(self):
        """软删的图不该拦住新建 —— 否则用户删图后就再也建不出图了。"""
        created = self.client.post("/api/floor-maps", json={}).json()
        with Session(_engine) as db:
            row = db.get(FloorMap, created["id"])
            row.active = False
            db.commit()

        self.assertEqual(self.client.get("/api/floor-maps/default").status_code, 404)
        res = self.client.post("/api/floor-maps", json={"name": "重建的"})
        self.assertEqual(res.status_code, 201, res.text)


class TestAudit(FloorMapTestBase):
    """建图是一次真实的数据变更，必须留痕（走 MAP_CREATE，不是兜底的"编辑资产"）。"""

    def test_audit_written_once(self):
        self.client.post("/api/floor-maps", json={"name": "研发办公室", "operator": "维护"})
        with Session(_engine) as db:
            rows = db.query(AuditLog).all()
        self.assertEqual(len(rows), 1, "一次建图 = 一条审计")
        entry = rows[0]
        self.assertEqual(entry.action, "map_create")
        self.assertEqual(entry.target_type, "map")
        self.assertEqual(entry.target_label, "研发办公室")
        self.assertEqual(entry.operator, "维护")
        self.assertIn("空白画布", entry.summary or "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
