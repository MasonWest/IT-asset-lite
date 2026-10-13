"""点位归属的判定口径 —— **"这个点位有没有人"，后端与前端必须是同一个答案**。

## 为什么要有这个模块

2026-10-08 之前，后端算"已分配"用的是最朴素的判据：

```python
assigned = sum(1 for i in items if (i.user_name or "").strip())   # ← 只看非空
```

而前端 `utils/format.ts::hasOwner()` 是这样的：

```ts
hasOwner(u) => Boolean(u?.trim()) && !['/', '／'].includes(u)      # ← 斜杠算「没归属」
```

**两者的唯一分歧是斜杠。** 系统里 `user_name` 是自由文本（刻意没做人员字典表），
而项目自己早就认定「`/` = 没填」—— 真实库里**资产**表有 15 台写的是 `/`。
所以完全可能出现"资产表里斜杠一堆、点位表里也有几个"的情况，那时：

- 后端把 `/` 算成**已分配** → 顶栏「已分配 46」
- 前端把 `/` 显示成**未填使用人**、格子染成"无人"

**同一页上两个数字打架，而且没有任何报错。** 这就是典型的"两份数据两处存"。

## 口径（与前端 `utils/format.ts` 逐条对齐）

| 输入 | 算「有人」吗 |
| --- | --- |
| `None` / `''` / `'   '` | ❌ 没归属 |
| `'/'` / `'／'`（半角 + 全角） | ❌ 没归属（"没填"不是人名） |
| `'林晓薇'` | ✅ 有 |

**半角和全角都认**：中文输入法下打出 `／` 太正常了。
（与前端 `NO_USER_MARKS = ['/', '／']` 一致 ——改一边必须改另一边，
这是本文件存在的全部意义。）

## ⚠️ 改这里必须同步改前端

本模块和 `frontend/src/utils/format.ts` 的 `isNoUser()` / `hasOwner()` 是**同一口径的两份实现**
（跨语言，没法共用一份源码）。**改判据要两边一起改**，
否则 `e2e-station-relation-1002.js` 的 A5「前后端逐类对账」会红 ——
那条断言存在的意义就是守住这个同步。
"""

from __future__ import annotations

#: 代表「没填」的占位符。半角 + 全角都要认（中文输入法下 `／` 太常见）。
#: 与前端 `utils/format.ts::NO_USER_MARKS` 逐条对应。
NO_USER_MARKS: tuple[str, ...] = ("/", "／")


def is_no_user(user_name: str | None) -> bool:
    """这个使用人是不是**写了「没填」的占位符**。

    注意它**不覆盖空值**：`is_no_user(None)` 与 `is_no_user('')` 都是 `False`
    —— 那是"没写"，不是"写了斜杠"。两个概念要分开，和前端同名函数行为一致。

    判断"到底归不归某人"请用 `has_owner()`，别拿这个。
    """
    name = (user_name or "").strip()
    return bool(name) and name in NO_USER_MARKS


def has_owner(user_name: str | None) -> bool:
    """这个点位/设备**到底归不归某个人**。空值和斜杠都算「没归属」。

    与前端 `utils/format.ts::hasOwner()` 完全同口径。
    """
    name = (user_name or "").strip()
    return bool(name) and name not in NO_USER_MARKS


def count_assigned(user_names: list[str | None]) -> int:
    """一批点位里有几个**有人**。后端统计口径的唯一入口。

    ⚠️ 别再在路由里写 `sum(1 for x in items if (x.user_name or '').strip())`
    —— 那个写法会把斜杠算成已分配，与前端显示不一致（见本模块开头）。
    """
    return sum(1 for name in user_names if has_owner(name))