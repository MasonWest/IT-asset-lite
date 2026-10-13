#!/usr/bin/env python
"""前后端"什么算有人"的口径对账（2026-10-08）。

## 为什么是独立脚本而不是 e2e

`e2e-ownership-parity-1008.js` 里原本想用 Node 的 `execFileSync` 去调 python，
结果撞上 `spawnSync EBUSY -4082` —— **本机python.exe 正被正在跑的服务实例占着**，
起不来第二个进程（同一个坑在 npm/esbuild 首次安装时也踩过，见 MEMORY）。

所以对账拆成两半，各用各的天然主场：

- **本脚本（Python）**：验后端实现自己 —— 六个边界用例 + 跨语言字面量比对。
  读`frontend/src/utils/format.ts` 的**真实源码**，抠出 `NO_USER_MARKS` 字面量，
  与后端的逐条比对。**不是在浏览器里复刻一遍前端逻辑**（自己抄自己，抄错一起错）。
- **e2e（浏览器）**：只验"接口 `assigned` 与前端逐点数一致"这个端到端结果。

跑法（在 `backend/` 下）：
    python backend/check_ownership_parity.py

⚠️ 本脚本**只读**，不改任何数据、不需要起服务、不占用任何端口。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.ownership import (  # noqa: E402
    NO_USER_MARKS,
    count_assigned,
    has_owner,
    is_no_user,
)

FRONTEND_FORMAT_TS = BACKEND_DIR.parent / "frontend" / "src" / "utils" / "format.ts"

#: 与前端 `hasOwner` / `isNoUser` 逐条对齐的边界用例
CASES: list[str | None] = [None, "", "   ", "/", "／", "林晓薇"]

_pass = 0
_fail = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{f'   -> {extra}' if extra else ''}")


def read_frontend_marks() -> list[str] | None:
    """从前端**真实源码**里抠出 NO_USER_MARKS 的字面量。"""
    if not FRONTEND_FORMAT_TS.is_file():
        return None
    src = FRONTEND_FORMAT_TS.read_text(encoding="utf-8")
    m = re.search(r"NO_USER_MARKS\s*=\s*\[([^\]]*)\]", src)
    if not m:
        return None
    return [
        s.strip().strip("'\"")
        for s in m.group(1).split(",")
        if s.strip()
    ]


def main() -> int:
    print("A. 前后端 NO_USER_MARKS 字面量必须逐条一致")
    fe = read_frontend_marks()
    if fe is None:
        print(f"  FAIL  读不到前端 {FRONTEND_FORMAT_TS}")
        return 2
    be = list(NO_USER_MARKS)
    print(f"        前端 format.ts : {json.dumps(fe, ensure_ascii=False)}")
    print(f"        后端 ownership : {json.dumps(be, ensure_ascii=False)}")
    check("A0 两个列表完全一致", fe == be, f"前端 {fe} vs 后端 {be}")
    check("A1 半角「/」两边都在", "/" in fe and "/" in be)
    check("A2 全角「／」两边都在", "／" in fe and "／" in be)
    check("A3 没有多余项（斜杠之外的占位符没被悄悄加进来）", len(fe) == 2 and len(be) == 2)

    print()
    print("B. 边界用例的判定（前端侧按已验证的字面量复算）")
    rows = []
    for c in CASES:
        name = (c or "").strip()
        fe_no = bool(name) and name in fe
        fe_owner = bool(name) and name not in fe
        be_no = is_no_user(c)
        be_owner = has_owner(c)
        rows.append((c, fe_no, fe_owner, be_no, be_owner))
        print(
            f"        {json.dumps(c, ensure_ascii=False):>10}"
            f"  前端 isNoUser={str(fe_no):5} hasOwner={str(fe_owner):5}"
            f"  后端一致={'✅' if (fe_no, fe_owner) == (be_no, be_owner) else '❌'}"
        )
        check(f"B {json.dumps(c, ensure_ascii=False)} 两侧结论一致",
              (fe_no, fe_owner) == (be_no, be_owner),
              f"前端({fe_no},{fe_owner}) 后端({be_no},{be_owner})")

    print()
    print("C. count_assigned 本身没写错")
    fe_count = sum(1 for r in rows if r[2])
    check("C1 等于「hasOwner 为真的个数」", count_assigned(CASES) == fe_count,
          f"{count_assigned(CASES)} vs {fe_count}")
    check("C2 斜杠一个都不算「有人」",
          not has_owner("/") and not has_owner("／"))
    check("C3 空值/ 空白都不算", not has_owner(None) and not has_owner("") and not has_owner("   "))
    check("C4 正常名字算有", has_owner("林晓薇"))
    check("C5 is_no_user 不覆盖空值（那是「没写」不是「写了斜杠」）",
          not is_no_user(None) and not is_no_user("") and not is_no_user("   "))

    print()
    print(f"结果：{_pass} PASS / {_fail} FAIL")
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(main())