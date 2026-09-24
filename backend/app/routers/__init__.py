"""API 路由包。

新增路由模块时记得在这里也 import 一下 ——
不 import 的话 app.routers.__all__ 里没有它，
将来有人写 from app.routers import * 会静默漏掉整个模块。
"""

from . import (  # noqa: F401
    assets,
    audit,
    device_types,
    inventory,
    meta,
    relations,
    system,
    transfer,
)

__all__ = [
    "assets",
    "audit",
    "device_types",
    "inventory",
    "meta",
    "relations",
    "system",
    "transfer",
]
