"""
本地时间（naive datetime）口径的唯一实现。

本系统的约定是：**库里所有时间都是"墙上时钟"，没有时区**（见 `models.py` 顶部说明）。
用户是在局域网里用这个工具，看到的必须是本机时间；一旦某个入口把带时区的
datetime 直接写进去，SQLite 的 DateTime 列会把 tzinfo 丢掉 —— 存进去的就变成 UTC，
页面上就差 8 小时（东八区），而且和审计日志（`datetime.now()` 写的本地时间）对不上。

事故现场（v2.9.8）：前端「发生日期 + 当前时分」拼完后调了
`new Date(...).toISOString()`，那是 **UTC**；轻资产「当前分配」显示
「起始于 2026/10/20 06:18」，而同一笔操作在审计日志里是 14:18。

所以客户端传来的时间一律先过 `to_local_naive()`：**带时区的先换算到本机时区，
再去掉 tzinfo**。裸 datetime 原样通过（前端按本地口径拼的字符串就落在这里）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator

__all__ = ["LocalNaiveDatetime", "to_local_naive"]


def to_local_naive(value: datetime) -> datetime:
    """把带时区的 datetime 转成本机时区的 naive datetime；naive 原样返回。

    ⚠️ `datetime.astimezone()` 不带参数就是"转到本机时区"，这正是要的语义：
    不管客户端从哪个时区发来（`Z`、`+00:00`、`+08:00`），落库的都是同一面墙上的时间。
    """
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


#: 客户端可写的时间字段一律用它，别直接写 `datetime`。
LocalNaiveDatetime = Annotated[datetime, AfterValidator(to_local_naive)]
