"""平面图（floor map）的读写契约。

## 为什么图元走 JSON，而点位走表

点位与图元长得像，形态却不同：

- 点位有 `code` 唯一约束、被 `assets.workstation_id` 引用、能单独增删改 →
  必须是一张表，否则约束和引用都没地方放。
- 图元（地台/走廊/房间/墙体/前厅）**没有任何东西引用它们**，永远是"整张图
  一起编辑一起保存" → 存成一个 JSON 文档就够了，建 `map_shapes` 表只会
  换来一堆"删图元要先删关联"的伪问题。

这不是不一致，是**两种数据本来就不一样**。

## schema_version 是 JSON 文档的基本盘

`layout` 里第一个键必须是 `schema_version`。它不是数据库版本历史，
是"将来改图元字段时，老图还能被读出来"的唯一依据 —— 成本几乎为零。
读写的唯一入口是 `services/floor_map_layout.py::parse_layout`，
任何组件都不许直接 `json.loads(layout)`。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FloorMapCanvas(BaseModel):
    """画布基准尺寸。与点位地图共用同一套坐标语义（960×1320，网格 20）。"""

    width: int = 960
    height: int = 1320
    grid: int = 20


class FloorMapOut(BaseModel):
    """单张平面图。`layout` 是解析后的对象，不是字符串 —— 前端拿到就能画。"""

    id: int
    name: str
    canvas: FloorMapCanvas = FloorMapCanvas()
    layout: dict = Field(default_factory=dict)
    is_default: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class FloorMapList(BaseModel):
    """列表。**当前只有一张**，字段做成 list 只是为了将来扩展时契约不变。

    ⚠️ 前端红线：只能取 `items[0]`，禁止渲染选择器 / `?map=` / 多图 Tab。
    单图心智模型在后端可以有 `map` 概念，在前端只能有"这一张图"的概念。
    """

    total: int = 0
    items: list[FloorMapOut] = []


class FloorMapSave(BaseModel):
    """保存平面图（整张覆盖）。

    `expected_updated_at` 是乐观锁：前端带上它打开页面时看到的版本，
    与库里对不上就 409 —— 两个人同时开着一个地图编辑器是迟早的事，
    没有这个就会出现"后保存的人静默覆盖前一个人"。
    """

    name: Optional[str] = Field(default=None, max_length=128)
    layout: Optional[dict] = Field(default=None, description="整张图的图元文档（含 schema_version）")
    expected_updated_at: Optional[str] = Field(
        default=None, description="乐观锁：打开页面时看到的 updated_at；不带则跳过检查"
    )
    operator: Optional[str] = Field(default=None, max_length=64)
