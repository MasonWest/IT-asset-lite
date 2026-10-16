"""轻资产（v2：holdings + movements 双写模型）：核心逻辑与接口的回归测试。

## ⚠️ 只在临时库上跑

本文件在 import 任何 `app.*` 之前**强制**把 `IT_ASSET_DATA_DIR` 指向 `.tmp/light_tests_db`，
并在 import 后断言数据库文件确实落在临时目录 —— 不满足就直接炸，
绝不允许"以为在临时库、其实在打真实库"（这个项目为此吃过亏）。

单独跑（别丢进 discover 里跟别的用例混进程）：
    cd backend && python -m unittest tests.test_light -v
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# --- 必须在 import app.* 之前把库指到临时目录 -------------------------------- #
_BACKEND = Path(__file__).resolve().parent.parent
_TMP_DIR = (_BACKEND.parent / ".tmp" / "light_tests_db").resolve()
os.environ["IT_ASSET_DATA_DIR"] = str(_TMP_DIR)

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import light_check  # noqa: E402
import light_rebuild  # noqa: E402
from app.database import DB_FILE, SessionLocal, engine, ensure_schema  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Asset,
    AssetStatus,
    DeviceCategory,
    DeviceType,
    LightCategory,
    LightHolderKind,
    LightHolding,
    LightMovement,
    LightOp,
    LightOrigin,
    Workstation,
)
from app.services import light as light_service  # noqa: E402
from app.services.light import HolderRef  # noqa: E402

# 前提门：不是临时库就别跑
if not str(DB_FILE).startswith(str(_TMP_DIR)):  # pragma: no cover
    raise RuntimeError(f"拒绝在非临时库上运行轻资产测试：DB_FILE={DB_FILE}")

#: 用例之间要清空的表（按外键依赖顺序，先删引用方）。
#: ⚠️ **不要每个用例都 rmtree + create_all 重建库** ——
#: Windows 上删一个还开着的 sqlite 文件极慢（实测 4 个用例要 35 秒），
#: 整轮跑下来直接撞工具超时被杀。建表只做一次，用例之间清数据即可。
_CLEAR_ORDER: tuple[str, ...] = (
    "light_holdings",
    "light_movements",
    "light_categories",
    "asset_events",
    "asset_relations",
    "inventory_items",
    "inventory_tasks",
    "relation_exceptions",
    "assets",
    "device_types",
    "workstations",
    "floor_maps",
    "audit_logs",
)

_TMP_DIR.mkdir(parents=True, exist_ok=True)
ensure_schema()  # 建表只做一次


def _reset_db() -> None:
    engine.dispose()
    with engine.begin() as conn:
        for table in _CLEAR_ORDER:
            conn.exec_driver_sql(f'DELETE FROM "{table}"')
        conn.exec_driver_sql("DELETE FROM schema_meta WHERE key LIKE 'light_opening:%'")


class LightTestBase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_db()
        self.db = SessionLocal()
        self.device_type = DeviceType(name="主机", sort_order=1, category=DeviceCategory.HOST)
        self.db.add(self.device_type)
        self.db.commit()

    def tearDown(self) -> None:
        try:
            self.db.close()
        except Exception:  # pragma: no cover
            pass

    # --- 造数据的小工具 --- #
    def make_category(self, name: str, *, unit: str = "套") -> LightCategory:
        category = LightCategory(name=name, unit=unit)
        self.db.add(category)
        self.db.commit()
        return category

    def make_asset(self, code: str, user: str | None = None, *, status: str = AssetStatus.IN_USE) -> Asset:
        asset = Asset(
            asset_code=code,
            device_type_id=self.device_type.id,
            status=status,
            user_name=user,
            brand="联想",
            model="ThinkCentre M720t",
        )
        self.db.add(asset)
        self.db.commit()
        return asset

    def make_station(self, code: str, *, room: str | None = None, user: str | None = None) -> Workstation:
        station = Workstation(code=code, x=0, y=0, room=room, user_name=user)
        self.db.add(station)
        self.db.commit()
        return station

    def move(self, category: LightCategory, op: str, *, frm: HolderRef | None = None,
             to: HolderRef | None = None, quantity: int = 1, origin: str = LightOrigin.MANUAL,
             operator: str | None = "tester", request_id: str | None = None,
             note: str | None = None) -> LightMovement:
        movement = light_service.record_operation(
            self.db, category=category, op=op,
            from_ref=frm or HolderRef(), to_ref=to or HolderRef(),
            quantity=quantity, origin=origin, operator=operator,
            request_id=request_id, note=note,
        )
        self.db.commit()
        return movement

    def qty(self, category: LightCategory, ref: HolderRef) -> int:
        return light_service.quantity_at(self.db, category.id, ref)

    def stock(self, category: LightCategory) -> int:
        return light_service.stock_quantity(self.db, category.id)

    def allocated(self, category: LightCategory) -> int:
        return light_service.allocated_quantity(self.db, category.id)

    def expect_http(self, code: int, fn, *args, **kwargs) -> str:
        try:
            fn(*args, **kwargs)
        except HTTPException as exc:
            assert exc.status_code == code, f"期望 {code}，实际 {exc.status_code}: {exc.detail}"
            return str(exc.detail)
        raise AssertionError(f"期望抛出 HTTP {code}，但没有抛")


STOCK = lambda: HolderRef(kind=LightHolderKind.STOCK)  # noqa: E731


class TestQuantityModel(LightTestBase):
    """分配 / 部分归还 / 转移 / 报损 —— 统一模型（一次操作 = 两个持有记录上的加减）。"""

    def test_stock_in_then_allocate(self):
        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=3)
        self.assertEqual(self.stock(cat), 3)
        self.assertEqual(light_service.net_by_movements(self.db, cat.id), 3)

        self.move(cat, LightOp.ALLOCATE, frm=STOCK(), to=HolderRef(kind=LightHolderKind.USER, user="张三"), quantity=3)
        self.assertEqual(self.stock(cat), 0)
        self.assertEqual(self.allocated(cat), 3)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="张三")), 3)

    def test_partial_return_same_code_path(self):
        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=3)
        self.move(cat, LightOp.ALLOCATE, frm=STOCK(), to=HolderRef(kind=LightHolderKind.USER, user="张三"), quantity=3)
        # 还 1 套（部分归还）→ 只更新数量；再还 2 套 → 记录被删
        self.move(cat, LightOp.RETURN, frm=HolderRef(kind=LightHolderKind.USER, user="张三"), to=STOCK(), quantity=1)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="张三")), 2)
        self.assertEqual(self.stock(cat), 1)
        self.move(cat, LightOp.RETURN, frm=HolderRef(kind=LightHolderKind.USER, user="张三"), to=STOCK(), quantity=2)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="张三")), 0)
        rows = [h for h in light_service.current_holdings(self.db, cat.id)
                if h.holder_kind == LightHolderKind.USER]
        self.assertEqual(rows, [], "减到 0 就该删掉那条记录，而不是留一条 0 件的持有")

    def test_transfer_user_to_user_is_one_movement(self):
        """验收场景 2：一次换手只有**一条**流水，不是归还 + 分配两条。"""
        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=3)
        self.move(cat, LightOp.ALLOCATE, frm=STOCK(), to=HolderRef(kind=LightHolderKind.USER, user="张三"), quantity=3)
        before = self.db.query(LightMovement).count()
        self.move(cat, LightOp.TRANSFER,
                  frm=HolderRef(kind=LightHolderKind.USER, user="张三"),
                  to=HolderRef(kind=LightHolderKind.USER, user="李四"), quantity=2)
        after = self.db.query(LightMovement).count()
        self.assertEqual(after - before, 1, "换手应当只写一条流水")
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="张三")), 1)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="李四")), 2)

    def test_transfer_machine_to_machine_and_station_to_station(self):
        """机器搬机器（B022 → B023）、工位挪工位（W23 → W25）——**一条**流水。"""
        cat = self.make_category("内存", unit="条")
        a1 = self.make_asset("B022-03-00001")
        a2 = self.make_asset("B023-03-00002")
        s1 = self.make_station("W23")
        s2 = self.make_station("W25")

        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=2)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=a1.id), quantity=2)
        before = self.db.query(LightMovement).count()
        self.move(cat, LightOp.TRANSFER,
                  frm=HolderRef(kind=LightHolderKind.ASSET, ref_id=a1.id),
                  to=HolderRef(kind=LightHolderKind.ASSET, ref_id=a2.id), quantity=2)
        self.assertEqual(self.db.query(LightMovement).count() - before, 1)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.ASSET, ref_id=a1.id)), 0)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.ASSET, ref_id=a2.id)), 2)

        # 点位 → 点位：先挪回库存再放过去也太绕，直接换位置只要一条
        self.move(cat, LightOp.DETACH, frm=HolderRef(kind=LightHolderKind.ASSET, ref_id=a2.id), to=STOCK(), quantity=1)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=s1.id), quantity=1)
        self.move(cat, LightOp.TRANSFER,
                  frm=HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=s1.id),
                  to=HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=s2.id), quantity=1)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=s1.id)), 0)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=s2.id)), 1)
        light_service.assert_conservation(self.db, cat.id)

    def test_scrap_reduces_total(self):
        cat = self.make_category("转接头", unit="个")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=5)
        self.move(cat, LightOp.SCRAP, frm=STOCK(), to=HolderRef(), quantity=2)
        self.assertEqual(self.stock(cat), 3)
        self.assertEqual(light_service.net_by_movements(self.db, cat.id), 3)

    def test_insufficient_stock_is_409_and_nothing_written(self):
        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=2)
        detail = self.expect_http(
            409, self.move, cat, LightOp.ALLOCATE, frm=STOCK(),
            to=HolderRef(kind=LightHolderKind.USER, user="张三"), quantity=3,
        )
        self.assertIn("2", detail)
        self.assertEqual(self.stock(cat), 2, "被拒绝的操作不能改动任何持有记录")
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="张三")), 0)

    def test_holder_cannot_return_what_it_never_had(self):
        """验收 #12 的老洞：从"什么都没持有的人"归还，凭空把库存加上去。"""
        cat = self.make_category("键鼠套装")
        self.expect_http(
            409, self.move, cat, LightOp.RETURN,
            frm=HolderRef(kind=LightHolderKind.USER, user="张三"), to=STOCK(), quantity=3,
        )
        self.assertEqual(self.stock(cat), 0)

    def test_placeholder_user_rejected(self):
        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=1)
        self.expect_http(
            400, self.move, cat, LightOp.ALLOCATE, frm=STOCK(),
            to=HolderRef(kind=LightHolderKind.USER, user="/"), quantity=1,
        )

    def test_stock_split_across_locations(self):
        """验收 #14：库存能分放两处，同一位置不会出现两条。"""
        cat = self.make_category("转接头", unit="个")
        self.move(cat, LightOp.STOCK_IN, to=HolderRef(kind=LightHolderKind.STOCK, stock_location="库房A"), quantity=3)
        self.move(cat, LightOp.STOCK_IN, to=HolderRef(kind=LightHolderKind.STOCK, stock_location="前台抽屉"), quantity=2)
        self.assertEqual(self.stock(cat), 5)
        rows = [h for h in light_service.current_holdings(self.db, cat.id)
                if h.holder_kind == LightHolderKind.STOCK]
        self.assertEqual(len(rows), 2)
        # 同一位置再来一次 → 合并到同一条，而不是冒第二条
        self.move(cat, LightOp.STOCK_IN, to=HolderRef(kind=LightHolderKind.STOCK, stock_location="库房A"), quantity=1)
        rows = [h for h in light_service.current_holdings(self.db, cat.id)
                if h.holder_kind == LightHolderKind.STOCK]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            self.qty(cat, HolderRef(kind=LightHolderKind.STOCK, stock_location="库房A")), 4
        )

    def test_ambiguous_stock_source_is_409(self):
        """库存分放多处又不说从哪一处发 → **不猜**，409 说清楚。"""
        cat = self.make_category("转接头", unit="个")
        self.move(cat, LightOp.STOCK_IN, to=HolderRef(kind=LightHolderKind.STOCK, stock_location="库房A"), quantity=3)
        self.move(cat, LightOp.STOCK_IN, to=HolderRef(kind=LightHolderKind.STOCK, stock_location="前台抽屉"), quantity=2)
        detail = self.expect_http(
            409, self.move, cat, LightOp.ALLOCATE, frm=STOCK(),
            to=HolderRef(kind=LightHolderKind.USER, user="张三"), quantity=1,
        )
        self.assertIn("库房A", detail)


class TestAssetAndStation(LightTestBase):
    """验收场景 3 / 4 / 5：装到资产、放到点位、主机换人后配件跟着变。"""

    def test_attach_to_asset_and_by_asset(self):
        cat = self.make_category("内存", unit="条")
        asset = self.make_asset("B022-03-00001", user="张三")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=2)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), quantity=2)

        rows = light_service.holdings_by_asset(self.db, asset.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantity, 2)
        self.assertEqual(self.allocated(cat), 2)
        self.assertEqual(self.stock(cat), 0)

    def test_station_holder_does_not_adopt_station_user(self):
        """验收场景 4：放 W23 工位上，**不因为 W23 有使用人就改记到人名下**。"""
        cat = self.make_category("键鼠套装")
        station = self.make_station("W23", user="李四")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=1)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=station.id), quantity=1)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="李四")), 0)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=station.id)), 1)

    def test_station_groups_direct_and_via_assets(self):
        """验收场景：点位详情两组 —— 直接关联 + 随主机关联，且**不重复计算**。"""
        cat_a = self.make_category("键鼠套装")
        cat_b = self.make_category("内存", unit="条")
        station = self.make_station("W23")
        asset = self.make_asset("B022-03-00001", user="张三")
        asset.workstation_id = station.id
        self.db.commit()

        self.move(cat_a, LightOp.STOCK_IN, to=STOCK(), quantity=1)
        self.move(cat_a, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=station.id), quantity=1)
        self.move(cat_b, LightOp.STOCK_IN, to=STOCK(), quantity=2)
        self.move(cat_b, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), quantity=2)

        grouped = light_service.holdings_by_workstation(self.db, station.id)
        self.assertEqual([h.category_id for h in grouped["direct"]], [cat_a.id])
        self.assertEqual([h.category_id for h in grouped["via_assets"]], [cat_b.id])

        # 主机换点位 → 随主机那一组自动跟着变，**配件上不另存点位**
        station2 = self.make_station("W25")
        asset.workstation_id = station2.id
        self.db.commit()
        grouped_now = light_service.holdings_by_workstation(self.db, station.id)
        self.assertEqual(grouped_now["via_assets"], [])
        self.assertEqual(len(light_service.holdings_by_workstation(self.db, station2.id)["via_assets"]), 1)

    def test_asset_menu_owner_is_read_through(self):
        """验收场景 5：主机换人 → 配件"使用环境"跟着变（现读），历史不变。"""
        cat = self.make_category("SSD", unit="块")
        asset = self.make_asset("B022-03-00001", user="张三")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=1)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), quantity=1)

        asset.user_name = "李四"
        self.db.commit()
        from app.services import light_serialize

        holding = light_service.holdings_by_asset(self.db, asset.id)[0]
        out = light_serialize.to_holding_out(self.db, holding)
        self.assertEqual(out.host_user, "李四", "使用人必须现读宿主，不是写流水时的副本")


class TestStationLightCounts(LightTestBase):
    """地图轻资产徽标的取数（设计 §5.5 第一期）。

    ⚠️ 这里测的**不是"角标好不好看"**，而是它和点位侧栏是不是同一个口径 ——
    两处数字对不上时，用户没有任何办法判断该信哪个（"地图说有 3 件、点进去一件没有"）。
    所以地图徽标与侧栏共用 `_station_light_rows`，本类把这条约束钉住。
    """

    def test_badge_equals_sidebar_groups_sum(self):
        """徽标 = 侧栏两组数量之和；批量查询与逐个点位查询结果一致（防 N+1 改成另一个口径）。"""
        kb = self.make_category("键鼠套装")
        ram = self.make_category("内存", unit="条")
        w23 = self.make_station("W23")
        w25 = self.make_station("W25")  # 什么都没有的点位
        host = self.make_asset("B022-03-00001")
        host.workstation_id = w23.id
        self.db.commit()

        self.move(kb, LightOp.STOCK_IN, to=STOCK(), quantity=5)
        self.move(kb, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=w23.id), quantity=2)
        self.move(ram, LightOp.STOCK_IN, to=STOCK(), quantity=3)
        self.move(ram, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=host.id), quantity=1)

        counts = light_service.station_light_counts(self.db, [w23.id, w25.id])
        self.assertEqual(counts[w23.id], {"direct": 2, "via_assets": 1})
        self.assertEqual(counts[w25.id], {"direct": 0, "via_assets": 0})

        for station in (w23, w25):
            grouped = light_service.holdings_by_workstation(self.db, station.id)
            self.assertEqual(
                counts[station.id],
                {
                    "direct": sum(h.quantity for h in grouped["direct"]),
                    "via_assets": sum(h.quantity for h in grouped["via_assets"]),
                },
                "徽标数字与点位侧栏两组之和必须一致",
            )

    def test_stock_is_not_counted_even_when_location_looks_like_a_station_code(self):
        """D3：`stock_location` 是**自由文本**，写成 "W23" 也只是人写的备注，不是位置关系。

        乐观地把它算进 W23 的角标，就会出现"地图上显示有货、点进去一件没有"，
        而那张卡上明明写着"还没分配出去"。
        """
        kb = self.make_category("键鼠套装")
        w23 = self.make_station("W23")
        self.move(
            kb, LightOp.STOCK_IN,
            to=HolderRef(kind=LightHolderKind.STOCK, stock_location="W23"),
            quantity=5,
        )

        counts = light_service.station_light_counts(self.db, [w23.id])
        self.assertEqual(counts[w23.id], {"direct": 0, "via_assets": 0})
        self.assertEqual(self.stock(kb), 5, "库存本身照常记着，只是不属于任何点位")

    def test_user_held_has_no_station_and_zero_rows_do_not_count(self):
        """发给人的配件不落在任何点位上；数量归零的记录也不代表"这里有东西"。"""
        kb = self.make_category("键鼠套装")
        w23 = self.make_station("W23")
        w99 = self.make_station("W99")
        self.move(kb, LightOp.STOCK_IN, to=STOCK(), quantity=4)
        self.move(kb, LightOp.ALLOCATE, frm=STOCK(), to=HolderRef(kind=LightHolderKind.USER, user="张三"), quantity=2)
        self.move(kb, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.WORKSTATION, ref_id=w23.id), quantity=1)

        counts = light_service.station_light_counts(self.db, [w23.id])
        self.assertEqual(counts[w23.id], {"direct": 1, "via_assets": 0}, "人手上的 2 套不该落到任何点位上")

        # 手工塞一条数量为 0 的记录：`apply_holding_delta` 正常不会让它存在，
        # 但"表里没有"是它的性质、**不是本函数的假设** —— 查询必须自己挡掉。
        # 本库主键会复用、重建脚本也直接写表，所以这里不假设上游永远干净。
        self.db.add(
            LightHolding(
                category_id=kb.id,
                holder_kind=LightHolderKind.WORKSTATION,
                workstation_id=w99.id,
                quantity=0,
            )
        )
        self.db.commit()
        counts = light_service.station_light_counts(self.db, [w99.id])
        self.assertEqual(counts[w99.id], {"direct": 0, "via_assets": 0})

    def test_scrapped_or_reassigned_assets_drop_out_of_the_badge(self):
        """主机换点位后，随主机那一组自动跟着走 —— 点位这一层不落任何数据。"""
        ram = self.make_category("内存", unit="条")
        w23 = self.make_station("W23")
        w25 = self.make_station("W25")
        host = self.make_asset("B022-03-00001")
        host.workstation_id = w23.id
        self.db.commit()
        self.move(ram, LightOp.STOCK_IN, to=STOCK(), quantity=2)
        self.move(ram, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=host.id), quantity=2)

        self.assertEqual(light_service.station_light_counts(self.db, [w23.id])[w23.id]["via_assets"], 2)

        host.workstation_id = w25.id
        self.db.commit()
        counts = light_service.station_light_counts(self.db, [w23.id, w25.id])
        self.assertEqual(counts[w23.id]["via_assets"], 0)
        self.assertEqual(counts[w25.id]["via_assets"], 2)


class TestReplaceAndScrap(LightTestBase):
    """验收 #6 / #7：更换 = 两条流水同事务；报废前提示。"""

    def test_replace_writes_two_movements_in_one_transaction(self):
        cat = self.make_category("内存", unit="条")
        asset = self.make_asset("B022-03-00001", user="张三")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=1)   # 备件
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=1)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), quantity=1)
        # 此时：B022 上 1 条（旧件），库存 1 条（新件）
        movements = light_service.replace_asset_part(
            self.db, category=cat, asset_ref=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id),
            quantity=1, operator="tester",
        )
        self.db.commit()
        self.assertEqual([m.op for m in movements], [LightOp.SCRAP, LightOp.ATTACH])
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id)), 1)
        self.assertEqual(self.stock(cat), 0)
        self.assertEqual(light_service.net_by_movements(self.db, cat.id), 1)
        light_service.assert_conservation(self.db, cat.id)

    def test_replace_without_stock_is_409(self):
        cat = self.make_category("内存", unit="条")
        asset = self.make_asset("B022-03-00001")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=1)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), quantity=1)
        self.expect_http(
            409, light_service.replace_asset_part, self.db, category=cat,
            asset_ref=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), quantity=1,
        )

    def test_scrap_precheck_lists_light_assets(self):
        """验收 #7：报废前能看到这台机器上还挂着哪些配件（**不自动改归属**）。"""
        cat = self.make_category("内存", unit="条")
        asset = self.make_asset("B022-03-00001")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=2)
        self.move(cat, LightOp.ATTACH, frm=STOCK(), to=HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), quantity=2)

        from app.routers import light as light_router

        impact = light_router.light_impact_for_asset(self.db, asset.id)
        self.assertEqual(impact["total"], 2)
        # 预检本身**不改任何归属**
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id)), 2)


class TestOpeningAndImport(LightTestBase):
    """验收 #8 / #9 / #15 / #16 / #19 / #20。"""

    def test_opening_builds_holdings_and_is_repeatable(self):
        cat = self.make_category("键鼠套装")
        asset = self.make_asset("B022-03-00001")
        light_service.apply_opening(
            self.db, category=cat, operator="tester",
            entries=[(STOCK(), 5), (HolderRef(kind=LightHolderKind.USER, user="张三"), 2),
                     (HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id), 1)],
        )
        self.db.commit()
        self.assertEqual(self.stock(cat), 5)
        self.assertEqual(self.allocated(cat), 3)
        self.assertEqual(light_service.net_by_movements(self.db, cat.id), 8)
        light_service.assert_conservation(self.db, cat.id)

        # 期初**允许重做 / 补录**（不永久封锁）
        light_service.apply_opening(
            self.db, category=cat, operator="tester",
            entries=[(STOCK(), 1)],
        )
        self.db.commit()
        self.assertEqual(self.stock(cat), 6)

    def test_opening_requires_operator(self):
        cat = self.make_category("键鼠套装")
        self.expect_http(
            400, light_service.apply_opening, self.db, category=cat, operator="  ",
            entries=[(STOCK(), 1)],
        )

    def test_adjust_win_loss_has_audit_and_ignores_opening_flag(self):
        """验收 #15：盘点发现数量不对 → 走 adjust，留审计，不受"已初始化"影响。"""
        cat = self.make_category("转接头", unit="个")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=5)
        # 盘亏 2
        self.move(cat, LightOp.ADJUST, frm=STOCK(), to=HolderRef(), quantity=2)
        self.assertEqual(self.stock(cat), 3)
        # 盘盈 4
        self.move(cat, LightOp.ADJUST, frm=HolderRef(), to=STOCK(), quantity=4)
        self.assertEqual(self.stock(cat), 7)
        light_service.assert_conservation(self.db, cat.id)

    def test_history_import_does_not_touch_current_state(self):
        """验收 #9 / #19 / #20：导入只补历史，当前账不变；再导一遍 0 新增。"""
        from app.services import light_import

        cat = self.make_category("键鼠套装")

        class FakeUpload:
            filename = "history.csv"

        content = (
            "品类,操作,数量,来源类型,来源,去向类型,去向,发生日期,备注\n"
            f"{cat.name},分配,2,库存,,使用人,张三,2024-03-12,去年发的\n"
            f"{cat.name},分配,1,库存,,使用人,李四,2024-05-01,\n"
        ).encode("utf-8-sig")

        plan = light_import.build_plan(self.db, content, "history.csv")
        self.assertIsNone(plan.schema_note)
        self.assertEqual(plan.count(light_import.ACTION_IMPORT), 2)

        outcome = light_import.apply_plan(self.db, plan, operator="tester")
        self.db.commit()
        self.assertEqual(outcome.imported, 2)
        # 当前账**一个字没动**
        self.assertEqual(self.stock(cat), 0)
        self.assertEqual(self.allocated(cat), 0)
        self.assertEqual(light_service.current_holdings(self.db, cat.id), [])

        # 幂等：再导一遍 0 新增
        outcome2 = light_import.apply_plan(self.db, light_import.build_plan(self.db, content, "history.csv"))
        self.db.commit()
        self.assertEqual(outcome2.imported, 0)
        self.assertEqual(outcome2.duplicate, 2)

        # 打印口径：参与重建的流水 0 条，被排除的 import 2 条
        self.assertEqual(light_service.net_by_movements(self.db, cat.id), 0)
        self.assertEqual(light_service.imported_movement_count(self.db, cat.id), 2)

    def test_pending_opening_when_only_imported(self):
        from app.services import light_import

        cat = self.make_category("键鼠套装")
        content = (
            "品类,操作,数量,来源类型,来源,去向类型,去向,发生日期\n"
            f"{cat.name},分配,2,库存,,使用人,张三,2024-03-12\n"
        ).encode("utf-8-sig")
        light_import.apply_plan(self.db, light_import.build_plan(self.db, content, "h.csv"))
        self.db.commit()
        summary = light_service.category_summary(self.db, cat)
        self.assertTrue(summary["pending_opening"], "有历史流水但当前账为空 → 提示去做期初，而不是猜数字")
        self.assertEqual(summary["stock"], 0)


class TestConsistency(LightTestBase):
    """验收 #10：体检只报告不修；重建先出报告、确认后才改。"""

    def test_check_reports_mismatch_and_rebuild_fixes(self):
        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=3)
        # 故意把表里的数量改错
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE light_holdings SET quantity = 8 WHERE category_id = ?", (cat.id,)
            )

        report = light_check.check()
        entry = next(c for c in report["categories"] if c["id"] == cat.id)
        self.assertFalse(report["ok"])
        self.assertTrue(any(i["type"] == "mismatch" for i in entry["issues"]))

        plan = light_rebuild.build_plan()
        entry_plan = next(c for c in plan["categories"] if c["id"] == cat.id)
        self.assertEqual(len(entry_plan["updates"]), 1)
        self.assertEqual(entry_plan["updates"][0]["before"], 8)
        self.assertEqual(entry_plan["updates"][0]["after"], 3)

        light_rebuild._apply(plan)
        self.db.expire_all()
        self.assertEqual(self.stock(cat), 3)
        self.assertTrue(light_check.check()["ok"])

    def test_import_is_excluded_from_replay(self):
        """验收 #13：重建/体检时 import 记录**不参与计算**，结果不等于"全部相加"。"""
        from app.services import light_import

        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=2)
        content = (
            "品类,操作,数量,来源类型,来源,去向类型,去向,发生日期\n"
            f"{cat.name},分配,5,库存,,使用人,张三,2024-03-12\n"
        ).encode("utf-8-sig")
        outcome = light_import.apply_plan(self.db, light_import.build_plan(self.db, content, "h.csv"))
        self.db.commit()
        self.assertEqual(outcome.imported, 1)

        replayed = light_service.replay_holdings(self.db, cat.id)
        total = sum(replayed.values())
        self.assertEqual(total, 2, "import 不参与重放：合计就是 2，不是 2+5")
        self.assertTrue(light_check.check()["ok"])


class TestSchemasAndPlaceholders(LightTestBase):
    def test_no_tracking_column_and_no_light_items(self):
        """取消双模式：老结构必须彻底消失（新库里压根不该有这两样）。"""
        with engine.begin() as conn:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(light_categories)").fetchall()}
            tables = {r[0] for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertNotIn("tracking", cols)
        self.assertNotIn("light_items", tables)
        with engine.begin() as conn:
            mcols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(light_movements)").fetchall()}
        self.assertNotIn("item_id", mcols)

    def test_holding_fk_blocks_duplicate_position(self):
        """部分唯一索引是第二层防线：同一 (品类, 资产) 不能有两行。"""
        cat = self.make_category("内存", unit="条")
        asset = self.make_asset("B022-03-00001")
        self.db.add(LightHolding(category_id=cat.id, holder_kind=LightHolderKind.ASSET,
                                 asset_id=asset.id, quantity=1, since=datetime.now()))
        self.db.commit()
        self.db.add(LightHolding(category_id=cat.id, holder_kind=LightHolderKind.ASSET,
                                 asset_id=asset.id, quantity=1, since=datetime.now()))
        with self.assertRaises(Exception):
            self.db.commit()
        self.db.rollback()

    def test_reverse_appends_and_keeps_original(self):
        cat = self.make_category("键鼠套装")
        self.move(cat, LightOp.STOCK_IN, to=STOCK(), quantity=1)  # 入库没有机械反向（走 adjust）
        m = self.move(cat, LightOp.ALLOCATE, frm=STOCK(), to=HolderRef(kind=LightHolderKind.USER, user="张三"), quantity=1)
        self.assertIsNotNone(LightOp.reverse_op(m.op, m.from_kind, m.to_kind))

        from app.services import light_serialize

        # 手工模拟路由里的冲正
        reverse_op = LightOp.reverse_op(m.op, m.from_kind, m.to_kind)
        light_service.record_operation(
            self.db, category=cat, op=reverse_op,
            from_ref=HolderRef(kind=m.to_kind, user=m.to_user),
            to_ref=HolderRef(kind=m.from_kind),
            quantity=m.quantity, operator="tester", corrects_id=m.id,
        )
        self.db.commit()
        rows = self.db.query(LightMovement).order_by(LightMovement.id).all()
        self.assertEqual(len(rows), 3, "原记录必须留着，只追加")
        self.assertEqual(rows[-1].corrects_id, m.id)
        self.assertEqual(self.qty(cat, HolderRef(kind=LightHolderKind.USER, user="张三")), 0)
        self.assertEqual(self.stock(cat), 1)


class TestApi(LightTestBase):
    """接口层：唯一写入口、两组返回、期初预览、导入、路由顺序。"""

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app)

    def test_create_category_and_movement(self):
        res = self.client.post("/api/light/categories", json={"name": "键鼠套装", "unit": "套"})
        self.assertEqual(res.status_code, 201, res.text)
        cid = res.json()["id"]
        self.assertNotIn("tracking", res.json())

        res = self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in",
            "from_ref": {"kind": "none"}, "to_ref": {"kind": "stock"}, "quantity": 3,
        })
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["stat"]["stock"], 3)
        self.assertEqual(len(body["movements"]), 1)

    def test_replace_endpoint_returns_two_movements(self):
        cid = self.client.post("/api/light/categories", json={"name": "内存", "unit": "条"}).json()["id"]
        asset = self.make_asset("B022-03-00001")
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in", "from_ref": {"kind": "none"},
            "to_ref": {"kind": "stock"}, "quantity": 2,
        })
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "attach", "from_ref": {"kind": "stock"},
            "to_ref": {"kind": "asset", "ref_id": asset.id}, "quantity": 1,
        })
        res = self.client.post("/api/light/replace", json={
            "category_id": cid, "asset_id": asset.id, "quantity": 1,
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([m["op"] for m in res.json()["movements"]], ["scrap", "attach"])

    def test_by_asset_and_by_workstation_endpoints(self):
        cid = self.client.post("/api/light/categories", json={"name": "内存", "unit": "条"}).json()["id"]
        asset = self.make_asset("B022-03-00001")
        station = self.make_station("W23")
        asset.workstation_id = station.id
        self.db.commit()
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in", "from_ref": {"kind": "none"},
            "to_ref": {"kind": "stock"}, "quantity": 2,
        })
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "attach", "from_ref": {"kind": "stock"},
            "to_ref": {"kind": "asset", "ref_id": asset.id}, "quantity": 2,
        })

        by_asset = self.client.get(f"/api/light/by-asset/{asset.id}").json()
        self.assertEqual(by_asset["total"], 2)
        self.assertEqual(by_asset["items"][0]["display"], "B022-03-00001")

        by_station = self.client.get(f"/api/light/by-workstation/{station.id}").json()
        self.assertEqual(by_station["direct"], [])
        self.assertEqual(len(by_station["via_assets"]), 1)
        self.assertEqual(by_station["via_assets"][0]["host_user"], None)

    def test_workstations_list_carries_light_counts(self):
        """地图徽标的**数据来源**：点位列表一次下发 `light_direct` / `light_via_assets`。

        ⚠️ 三个字段层面的约定，缺一个地图就会画错：
         1. 两个字段都要在（前端相加得到角标数字）；
         2. **不发合计** —— 多一个 `total` 就多一处口径能漂；
         3. 单点位接口也必须是真的查过的值，不能静默给 0
            （"这个点位有 3 件"是老实的 0 还是没查的 0，从响应里看不出来）。
        """
        cid = self.client.post("/api/light/categories", json={"name": "键鼠套装", "unit": "套"}).json()["id"]
        w23 = self.make_station("W23")
        w25 = self.make_station("W25")
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in", "from_ref": {"kind": "none"},
            "to_ref": {"kind": "stock"}, "quantity": 3,
        })
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "attach", "from_ref": {"kind": "stock"},
            "to_ref": {"kind": "workstation", "ref_id": w23.id}, "quantity": 3,
        })

        rows = {row["code"]: row for row in self.client.get("/api/workstations").json()["items"]}
        self.assertEqual(rows["W23"]["light_direct"], 3)
        self.assertEqual(rows["W23"]["light_via_assets"], 0)
        self.assertEqual(rows["W25"]["light_direct"], 0)
        self.assertNotIn("light_total", rows["W23"], "刻意不发合计 —— 由前端相加")

        one = self.client.get(f"/api/workstations/{w23.id}").json()
        self.assertEqual(one["light_direct"], 3, "单点位接口也要真查，不能落到默认 0")
        self.assertEqual(one["light_via_assets"], 0)

    def test_opening_preview_and_apply(self):
        cid = self.client.post("/api/light/categories", json={"name": "键鼠套装", "unit": "套"}).json()["id"]
        payload = {
            "category_id": cid,
            "entries": [{"holder": {"kind": "stock"}, "quantity": 5},
                        {"holder": {"kind": "user", "user": "张三"}, "quantity": 2}],
            "operator": "tester",
        }
        preview = self.client.post("/api/light/opening/preview", json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(body["stock"], {"before": 0, "after": 5})
        self.assertEqual(body["allocated"], {"before": 0, "after": 2})
        # 预览不写任何数据
        self.assertEqual(self.client.get(f"/api/light/categories/{cid}").json()["stat"]["stock"], 0)

        applied = self.client.post("/api/light/opening", json=payload)
        self.assertEqual(applied.status_code, 200, applied.text)
        detail = self.client.get(f"/api/light/categories/{cid}").json()
        self.assertEqual(detail["stat"]["stock"], 5)
        self.assertEqual(detail["stat"]["allocated"], 2)
        self.assertTrue(detail["opening_done"])

    def test_opening_without_operator_is_400(self):
        cid = self.client.post("/api/light/categories", json={"name": "键鼠套装"}).json()["id"]
        res = self.client.post("/api/light/opening", json={
            "category_id": cid, "entries": [{"holder": {"kind": "stock"}, "quantity": 1}],
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("操作人", res.json()["detail"])

    def test_import_preview_and_apply_via_api(self):
        cid = self.client.post("/api/light/categories", json={"name": "键鼠套装", "unit": "套"}).json()["id"]
        csv = (
            "品类,操作,数量,来源类型,来源,去向类型,去向,发生日期\n"
            "键鼠套装,分配,2,库存,,使用人,张三,2024-03-12\n"
            "键鼠套装,分配,9,库存,,使用人,李四,2024-04-01\n"
        ).encode("utf-8-sig")
        res = self.client.post("/api/light/import/preview",
                               files={"file": ("h.csv", csv, "text/csv")})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["will_import"], 2)

        res = self.client.post("/api/light/import", files={"file": ("h.csv", csv, "text/csv")})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["will_import"], 2)
        # 当前账不变
        self.assertEqual(self.client.get(f"/api/light/categories/{cid}").json()["stat"]["allocated"], 0)

    def test_literal_routes_not_swallowed(self):
        """路由顺序：/summary /holdings /by-asset 不能被 /{id} 吃掉。"""
        for path in ("/api/light/summary", "/api/light/holdings", "/api/light/filters"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, f"{path} → {res.status_code}")

    def test_scrap_asset_blocked_until_confirmed(self):
        """验收 #7：B022 要报废但上面还有配件 → 被拦下、列出配件；**不自动改归属**。"""
        cid = self.client.post("/api/light/categories", json={"name": "内存", "unit": "条"}).json()["id"]
        asset = self.make_asset("B022-03-00001")
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in", "from_ref": {"kind": "none"},
            "to_ref": {"kind": "stock"}, "quantity": 2,
        })
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "attach", "from_ref": {"kind": "stock"},
            "to_ref": {"kind": "asset", "ref_id": asset.id}, "quantity": 2,
        })

        res = self.client.post(f"/api/assets/{asset.id}/operations", json={"action": "scrap"})
        self.assertEqual(res.status_code, 409, res.text)
        detail = res.json()["detail"]
        self.assertIn("内存", detail)
        self.assertIn("×2", detail)
        # 被拦下时配件归属一个字没动
        self.assertEqual(self.qty(self.db.get(LightCategory, cid), HolderRef(kind=LightHolderKind.ASSET, ref_id=asset.id)), 2)

        # 点头「随设备一起报废」→ 配件同一次确认里报损
        res = self.client.post(f"/api/assets/{asset.id}/operations", json={
            "action": "scrap", "acknowledge_warnings": True, "scrap_light_assets": True,
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.db.expire_all()
        self.assertEqual(light_service.holdings_by_asset(self.db, asset.id), [])
        self.assertEqual(self.db.query(LightMovement).filter_by(op="scrap").count(), 1)

    def test_delete_station_blocked_by_direct_light_assets(self):
        """验收 #17：删点位时固定放在那儿的东西**硬拦**；随主机的只提示、不拦。"""
        cid = self.client.post("/api/light/categories", json={"name": "键鼠套装", "unit": "套"}).json()["id"]
        station = self.make_station("W23")
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in", "from_ref": {"kind": "none"},
            "to_ref": {"kind": "stock"}, "quantity": 1,
        })
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "attach", "from_ref": {"kind": "stock"},
            "to_ref": {"kind": "workstation", "ref_id": station.id}, "quantity": 1,
        })

        res = self.client.delete(f"/api/workstations/{station.id}")
        self.assertEqual(res.status_code, 409, res.text)
        self.assertIn("固定放着轻资产", res.json()["detail"])
        # **force 也过不去**：直接关联是真的会失去位置
        res = self.client.delete(f"/api/workstations/{station.id}?force=true")
        self.assertEqual(res.status_code, 409)
        self.assertTrue(self.db.get(Workstation, station.id).active)

    def test_delete_station_allows_via_asset_only(self):
        """验收 #18：只有随主机关联时允许删（清空主机点位即可），配件仍归属那台主机。"""
        cid = self.client.post("/api/light/categories", json={"name": "内存", "unit": "条"}).json()["id"]
        station = self.make_station("W23")
        asset = self.make_asset("B022-03-00001")
        asset.workstation_id = station.id
        self.db.commit()
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in", "from_ref": {"kind": "none"},
            "to_ref": {"kind": "stock"}, "quantity": 1,
        })
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "attach", "from_ref": {"kind": "stock"},
            "to_ref": {"kind": "asset", "ref_id": asset.id}, "quantity": 1,
        })

        res = self.client.delete(f"/api/workstations/{station.id}?force=true")
        self.assertEqual(res.status_code, 200, res.text)
        self.db.expire_all()
        self.assertFalse(self.db.get(Workstation, station.id).active)
        # 配件仍然正确地"装在 B022 上"
        self.assertEqual(len(light_service.holdings_by_asset(self.db, asset.id)), 1)

    def test_export_returns_xlsx(self):
        cid = self.client.post("/api/light/categories", json={"name": "转接头", "unit": "个"}).json()["id"]
        self.client.post("/api/light/movements", json={
            "category_id": cid, "op": "stock_in", "from_ref": {"kind": "none"},
            "to_ref": {"kind": "stock"}, "quantity": 2,
        })
        res = self.client.get("/api/light/export")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content.startswith(b"PK"))  # xlsx 就是 zip


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
