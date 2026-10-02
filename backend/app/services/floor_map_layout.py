"""平面图 layout 的唯一解析入口。

**为什么要有这一层**：`layout` 是用户编辑出来的 JSON，读的人可能有三四个
（地图渲染、编辑器、对账脚本、导出）。如果每处各自 `json.loads` + 自己兜底，
就会出现"地图画出来了、编辑器打开是空的"这种两边理解不一致的鬼故事。

所以规矩是：**谁要 layout，都走这里**。组件里不许出现 `json.loads(row.layout)`。
"""

from __future__ import annotations

import json
from typing import Any

#: 当前支持的 layout 版本。写入时用它，读取时按它做兼容。
CURRENT_LAYOUT_VERSION = 1

#: 八种图元。竖墙（wall）与横分隔条（divider）是两个 kind ——
#: 原型里把横条也写成了 `wall-v` + inline width，只靠宽高区分会一路踩坑，
#: 拆成两个语义明确的 kind 后，"这条是墙还是隔断"一眼可辨。
SHAPE_KINDS = ("zone", "corr", "corrtext", "room", "wall", "divider", "lobby", "entry")

#: 每种 kind 各自的整数字段（几何）。校验时只放行这些键，其余字段透传不管
#: —— 上游将来加字段不该让老后端直接 500。
_GEOMETRY_FIELDS = ("x", "y", "w", "h")


def empty_layout() -> dict[str, Any]:
    """一张没有图元的空图。默认值不许写成 `{}` —— 那样所有读的人都要判 None。"""
    return {"schema_version": CURRENT_LAYOUT_VERSION, "shapes": []}


def parse_layout(raw: Any) -> dict[str, Any]:
    """把库里的 layout（字符串 / dict / None）解析成规范对象。

    **宽容读，严格写**：读到坏数据时退化成空图而不是抛异常 ——
    一张读不出来的图不该让整个地图页白屏。坏的原始值留在库里不删，
    用户下次保存会覆盖掉它。
    """
    if raw is None:
        return empty_layout()

    if isinstance(raw, (str, bytes, bytearray)):
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        text = text.strip()
        if not text:
            return empty_layout()
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return empty_layout()
    elif isinstance(raw, dict):
        data = raw
    else:
        return empty_layout()

    if not isinstance(data, dict):
        return empty_layout()

    shapes = data.get("shapes")
    if not isinstance(shapes, list):
        shapes = []

    version = data.get("schema_version")
    if not isinstance(version, int) or version < 1:
        version = CURRENT_LAYOUT_VERSION

    # 就地规范化每个图元：缺 id / kind 的直接丢（画不出来），
    # 数字字段转 int（前端传过 "120" 这种字符串，留着会在渲染时静默失败）。
    clean: list[dict[str, Any]] = []
    for shape in shapes:
        if not isinstance(shape, dict):
            continue
        if not shape.get("id") or not shape.get("kind"):
            continue
        item = dict(shape)
        item["id"] = str(item["id"])
        item["kind"] = str(item["kind"])
        for field in _GEOMETRY_FIELDS:
            if field in item and item[field] is not None:
                try:
                    item[field] = int(item[field])
                except (ValueError, TypeError):
                    item[field] = 0
        clean.append(item)

    return {"schema_version": version, "shapes": clean}


def dump_layout(layout: dict[str, Any]) -> str:
    """序列化成入库字符串。**不排序键** —— 保序能让 diff 更小、更好读。"""
    return json.dumps(layout, ensure_ascii=False)


def layout_shape_ids(layout: dict[str, Any]) -> list[str]:
    """图元 id 列表，按文档顺序。对账脚本和测试用。"""
    return [str(s.get("id")) for s in layout.get("shapes", []) if s.get("id")]
