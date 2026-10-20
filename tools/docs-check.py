#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档一致性体检 —— 把这个仓库"靠记得住"的文档规则变成可执行断言。

用法：
    py -3 tools/docs-check.py            # 常规：ERROR 才失败，WARN 只提示
    py -3 tools/docs-check.py --strict   # 体积阈值也当 ERROR（迁移完成后的验收口径）
    py -3 tools/docs-check.py --json     # 机器可读输出

设计原则：
1. **只用标准库**，不需要装任何东西（这个仓库刻意不建 venv）。
2. **只读**：不改任何文件；敏感词只告警，绝不自动替换。
3. **不硬编码业务数字**：表数从 models.py 数，版本号从代码读，姓名从库里反查。
   凡是"抄进脚本就会漂"的东西，一律现算。
4. 文档结构还没落位时（docs/ 不存在、CS 还没拆），相关检查报 SKIP 而不是 ERROR ——
   否则迁移做到一半永远红，人就不看了。

退出码：0 = 全部 ERROR 通过；1 = 有 ERROR；2 = 前提不成立（仓库路径不对）。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 体积阈值（行数）。判据是"人还愿意读完"，不是"越小越好" ----
SIZE_LIMITS = {
    "README.md": 200,
    "CURRENT_STATE.md": 300,
}
# 单份设计稿超过这个体积就提示（不是错，只是提醒"它可能该分卷了"）
DESIGN_WARN_KB = 100

# 四个版本号来源（标题 / 路径 / 提取方式）
VERSION_SOURCES = [
    ("backend/app/__init__.py", r'__version__\s*=\s*"([^"]+)"'),
    ("backend/app/config.py", r'APP_VERSION\s*=\s*"([^"]+)"'),
    ("frontend/package.json", r'"version"\s*:\s*"([^"]+)"'),
]

# 视为"已脱敏占位符"的姓名模式：不进敏感词报告
PLACEHOLDER_PAT = re.compile(r"^(员工[A-Z]|张三|李四|王五|赵六|某某|测试.*|demo)$")

results: list[dict] = []


def add(level: str, check: str, msg: str, detail: str = "") -> None:
    results.append({"level": level, "check": check, "msg": msg, "detail": detail})


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


def strip_code(text: str) -> str:
    """去掉围栏代码块与行内代码 —— 里面的链接/表格是"示例"，不该被当真。

    **必须保留行号**：逐行处理、被去掉的行留空串，否则报错位置对不上原文件。
    """
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
            continue
        out.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def is_current(p: Path) -> bool:
    """只有这些文件声称"当前事实"，才校验里面的数字与代码一致。

    设计稿 / 归档是**时点快照**（"当时有 7 张表"是正确记录），拿它们去比今天代码
    只会得到假红 —— 判据是"这份文件会不会被当成今天的真相来读"。
    """
    rel = p.relative_to(ROOT).as_posix()
    return rel in ("README.md", "CURRENT_STATE.md") or rel.startswith(("docs/dev/", "docs/user/"))


def md_files() -> list[Path]:
    """仓库内所有参与文档体系的 md（排除 node_modules / dist / backups / .tmp）。"""
    skip = {"node_modules", "dist", ".git", "backups", ".tmp", ".workbuddy", "2026-09-28-72debbad"}
    out = []
    for p in ROOT.rglob("*.md"):
        if any(part in skip for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


# ---------------------------------------------------------------- 1. 版本号
def check_version() -> None:
    found: dict[str, str] = {}
    for rel, pat in VERSION_SOURCES:
        p = ROOT / rel
        if not p.exists():
            add("ERROR", "版本号", f"找不到 {rel}")
            continue
        m = re.search(pat, read(p))
        if not m:
            add("ERROR", "版本号", f"{rel} 里提取不到版本号")
            continue
        found[rel] = m.group(1)

    # package-lock.json：根节点两处（"name"/"version" 与 packages[""]）
    lock = ROOT / "frontend/package-lock.json"
    if lock.exists():
        raw = read(lock)
        m = re.search(r'^\s{2}"version": "([^"]+)"', raw, re.M)
        if m:
            found["frontend/package-lock.json"] = m.group(1)

    uniq = set(found.values())
    if len(uniq) > 1:
        add("ERROR", "版本号", "四处版本号不一致", json.dumps(found, ensure_ascii=False))
    elif uniq:
        add("OK", "版本号", f"{len(found)} 处一致：{uniq.pop()}")

    # CS 头部必须一致
    cs = ROOT / "CURRENT_STATE.md"
    if cs.exists():
        head = "\n".join(read(cs).splitlines()[:20])
        m = re.search(r"版本基线\s*\|\s*\**(v[\d.]+)", head)
        if not m:
            add("WARN", "版本号", "CURRENT_STATE 头部没找到「版本基线 | vX.Y.Z」")
        elif found and m.group(1).lstrip("v") != list(found.values())[0].lstrip("v"):
            add("ERROR", "版本号", f"CURRENT_STATE 头部写 {m.group(1)}，代码是 {list(found.values())[0]}")


# ------------------------------------------------------- 2. 页脚不得另写版本
def check_footer() -> None:
    cs = ROOT / "CURRENT_STATE.md"
    if not cs.exists():
        return
    tail = read(cs).splitlines()[-6:]
    text = "\n".join(tail)
    m = re.findall(r"v\d+\.\d+\.\d+", text)
    if m:
        add("ERROR", "页脚版本", f"页脚又写了版本号 {m}（版本只许出现在头部）", text.strip()[:120])
    else:
        add("OK", "页脚版本", "页脚没有重复写版本号")


# --------------------------------------------------- 3. 写进文档的表数要等于代码
def check_table_count() -> None:
    models = ROOT / "backend/app/models.py"
    if not models.exists():
        add("SKIP", "表数", "找不到 models.py")
        return
    real = len(re.findall(r"__tablename__\s*=", read(models)))
    bad = []
    for p in md_files():
        if not is_current(p):
            continue
        for i, line in enumerate(strip_code(read(p)).splitlines(), 1):
            # 只抓"总数声明"，不抓"这次只加了 1 张表"这类增量描述
            claims = re.findall(r"(?:共|合计|现为|总共有?)\s*(\d+)\s*张(?:业务)?表", line)
            claims += re.findall(r"(\d+)\s*张业务表", line)
            for n in {int(x) for x in claims}:
                # schema_meta 是记账表，允许 "N 张业务表 + 一张记账表" 的写法
                if n not in (real, real - 1):
                    bad.append(f"{p.relative_to(ROOT)}:{i} 写 {n} 张，实际 {real}")
    if bad:
        add("ERROR", "表数", f"文档写的表数 ≠ models.py 的 {real} 张", "; ".join(bad[:5]))
    else:
        add("OK", "表数", f"文档里的表数与 models.py 一致（{real} 张）")


# --------------------------------------------------------- 4. 相对链接可达
def check_links() -> None:
    broken, total = [], 0
    for p in md_files():
        for i, line in enumerate(strip_code(read(p)).splitlines(), 1):
            for m in re.finditer(r"\]\(([^)\s]+)\)", line):
                target = m.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = target.split("#")[0]
                if not target:
                    continue
                # 归档快照是"原样留底的复印件"，里面的相对路径按原位置解析，
                # 挪进 docs/archive/ 之后必然对不上。它们不是给人点的，跳过。
                if p.relative_to(ROOT).as_posix().startswith("docs/archive/"):
                    continue
                total += 1
                if not (p.parent / target).exists():
                    broken.append(f"{p.relative_to(ROOT)}:{i} -> {target}")
    if broken:
        add("ERROR", "链接", f"{len(broken)} 条相对链接指向不存在的文件", "; ".join(broken[:6]))
    else:
        add("OK", "链接", f"{total} 条相对链接全部可达")


# ------------------------------------------------------------- 5. 无孤儿文档
def git_ignored(paths: list[Path]) -> set[Path]:
    """哪些文件被 .gitignore 排除。

    被排除的文件**不算入链目标**：它们在仓库里根本不存在，clone 下来的人看不到，
    用"能不能从 README 走到"去要求它们，只会逼出"给一个 clone 后就 404 的链接"。
    """
    if not paths:
        return set()
    rel = [p.relative_to(ROOT).as_posix() for p in paths]
    # 注意：必须传 bytes。用 text=True 时 Windows 会把 "\n" 翻成 "\r\n"，
    # git 收到的路径尾部带 \r，一个都匹配不上（表现为"全都没被忽略"）。
    r = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=ROOT, input="\n".join(rel).encode("utf-8"), capture_output=True,
    )
    if r.returncode not in (0, 1):  # 0=有命中 1=全没命中
        return set()
    out = r.stdout.decode("utf-8", "replace")
    return {(ROOT / line.strip()).resolve() for line in out.splitlines() if line.strip()}


def check_orphans() -> None:
    files = md_files()
    ignored = git_ignored(files)
    files = [p for p in files if p.resolve() not in ignored]
    index = {p.resolve(): p for p in files}
    edges: dict[Path, set[Path]] = {p.resolve(): set() for p in files}
    for p in files:
        for m in re.finditer(r"\]\(([^)\s]+\.md)(?:#[^)]*)?\)", read(p)):
            tgt = (p.parent / m.group(1)).resolve()
            if tgt in index:
                edges[p.resolve()].add(tgt)

    roots = [p.resolve() for p in files if p.relative_to(ROOT).as_posix() in ("README.md", "docs/README.md")]
    if not roots:
        add("SKIP", "孤儿", "没有 README.md / docs/README.md 作为入口")
        return
    seen, stack = set(roots), list(roots)
    while stack:
        cur = stack.pop()
        for nxt in edges.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    orphans = sorted(index[k].relative_to(ROOT).as_posix() for k in index if k not in seen)
    if orphans:
        add("WARN" if not (ROOT / "docs").exists() else "ERROR", "孤儿",
            f"{len(orphans)} 份 md 从入口不可达", ", ".join(orphans[:8]))
    else:
        add("OK", "孤儿", f"{len(files)} 份 md 全部从入口可达")


# ------------------------------------------------- 6. 表格没被空行截断
def slug(text: str) -> str:
    """GitHub 风格的标题锚点：小写、去标点、空格变连字符、保留中文。"""
    s = text.strip().lower()
    s = re.sub(r"`", "", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fa5 \-_]", "", s)
    s = s.replace(" ", "-")
    return s


def anchors_of(path: Path) -> set[str]:
    out = set()
    for line in strip_code(read(path)).splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            out.add(slug(m.group(2)))
    return out


def check_anchors() -> None:
    """`](某.md#某锚点)` 里的锚点必须真存在 —— 标题一改，深链就静默失效。"""
    bad, total = [], 0
    cache: dict[Path, set[str]] = {}
    for p in md_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith("docs/archive/"):
            continue
        for i, line in enumerate(strip_code(read(p)).splitlines(), 1):
            for m in re.finditer(r"\]\(([^)\s]+?\.md)#([^)\s]+)\)", line):
                target = (p.parent / m.group(1)).resolve()
                if not target.exists():
                    continue  # 文件不存在由 check_links 报
                total += 1
                if target not in cache:
                    cache[target] = anchors_of(target)
                if m.group(2) not in cache[target]:
                    bad.append(f"{rel}:{i} -> {m.group(1)}#{m.group(2)}")
    if bad:
        add("ERROR", "锚点", f"{len(bad)} 条深链指向不存在的标题", "; ".join(bad[:6]))
    else:
        add("OK", "锚点", f"{total} 条带锚点链接全部命中")


def check_table_break() -> None:
    bad = []
    for p in md_files():
        lines = read(p).splitlines()
        for i in range(1, len(lines) - 1):
            if lines[i].strip() == "" and lines[i - 1].lstrip().startswith("|") and lines[i + 1].lstrip().startswith("|"):
                bad.append(f"{p.relative_to(ROOT)}:{i + 1}")
    if bad:
        add("ERROR", "表格", f"{len(bad)} 处表格被空行截断（会渲染成两张表）", ", ".join(bad[:8]))
    else:
        add("OK", "表格", "没有表格被空行截断")


# ------------------------------------------------------------- 7. 体积阈值
def check_size(strict: bool) -> None:
    level_soft = "ERROR" if strict else "WARN"
    for rel, limit in SIZE_LIMITS.items():
        p = ROOT / rel
        if not p.exists():
            continue
        n = len(read(p).splitlines())
        if n > limit:
            add(level_soft, "体积", f"{rel} {n} 行 > {limit} 行")
        else:
            add("OK", "体积", f"{rel} {n} 行 ≤ {limit}")
    for p in md_files():
        kb = p.stat().st_size // 1024
        if kb > DESIGN_WARN_KB:
            add("WARN", "体积", f"{p.relative_to(ROOT)} {kb} KB > {DESIGN_WARN_KB} KB（考虑分卷）")


# --------------------------------------------- 8. 设计稿要有且只有一行状态横幅
#
# 横幅的合法前缀（顺序即匹配优先级）。允许"前缀 + 括号补充"，但不允许自由发挥 ——
# 否则半年后会出现「状态：大概做了一半」这种既不能筛也不能搜的值。
BANNER_PREFIXES = ("已取代", "已实施", "部分实施", "设计", "素材", "已废弃", "索引")


def check_banners() -> None:
    design = ROOT / "docs/design"
    if not design.exists():
        add("SKIP", "横幅", "docs/design/ 还不存在（Phase 5 之前正常）")
        return
    bad, weird = [], []
    for p in sorted(design.rglob("*.md")):
        head = "\n".join(read(p).splitlines()[:5])
        found = re.findall(r"^>\s*状态：(.+)$", head, re.M)
        if len(found) != 1:
            bad.append(f"{p.relative_to(ROOT)} 有 {len(found)} 行")
        elif not found[0].lstrip("*_ ").startswith(BANNER_PREFIXES):
            weird.append(f"{p.relative_to(ROOT)} = {found[0][:24]}")
    if bad:
        add("ERROR", "横幅", f"{len(bad)} 份设计稿状态横幅不是恰好 1 行", "; ".join(bad[:6]))
    elif weird:
        add("ERROR", "横幅", f"{len(weird)} 份状态值不在约定取值内", "; ".join(weird[:6]))
    else:
        add("OK", "横幅", f"docs/design/ 下 {len(list(design.rglob('*.md')))} 份都有且只有一行横幅")


# ------------------------------------------------ 9. 敏感词（只告警，不改）
def check_decisions() -> None:
    """决策分册的三条硬约束：索引覆盖 = 册内条数、编号不重复、约束型两份逐字一致。"""
    d = ROOT / "docs/dev/decisions"
    if not d.exists():
        add("SKIP", "决策", "docs/dev/decisions/ 还不存在")
        return

    rows: dict[int, tuple[str, str]] = {}
    dup = []
    for f in sorted(d.glob("*.md")):
        if f.name == "README.md":
            continue
        for line in read(f).splitlines():
            m = re.match(r"^\| (\d+) \|", line)
            if m:
                n = int(m.group(1))
                if n in rows:
                    dup.append(f"{n} 同时在 {rows[n][0]} 与 {f.name}")
                rows[n] = (f.name, line.rstrip())

    idx_nums = set()
    idx_file = d / "README.md"
    if idx_file.exists():
        idx_nums = {int(m) for m in re.findall(r"^\| (\d+) \|", read(idx_file), re.M)}

    problems = []
    if dup:
        problems.append("编号重复：" + "; ".join(dup[:4]))
    if idx_nums and idx_nums != set(rows):
        only_idx = sorted(idx_nums - set(rows))[:5]
        only_book = sorted(set(rows) - idx_nums)[:5]
        problems.append(f"索引与册不一致（索引多 {only_idx} / 册多 {only_book}）")
    # 编号应连续（允许已知空缺 79，但不许悄悄多出新洞）
    if rows:
        expect = set(range(min(rows), max(rows) + 1)) - {79}
        holes = sorted(expect - set(rows))
        if holes:
            problems.append(f"册内编号有空缺 {holes}")

    inv = ROOT / "docs/dev/invariants.md"
    if inv.exists():
        inv_rows = {}
        for line in read(inv).splitlines():
            m = re.match(r"^\| (\d+) \|", line)
            if m:
                inv_rows[int(m.group(1))] = line.rstrip()
        diff = [n for n, line in inv_rows.items() if n in rows and rows[n][1] != line]
        if diff:
            problems.append(f"约束型两份不一致（改了册子别忘了同一份全文）: {sorted(diff)[:6]}")

    if problems:
        add("ERROR", "决策", f"{len(problems)} 个问题", " | ".join(problems))
    else:
        n_inv = len([l for l in read(inv).splitlines() if re.match(r"^\| \d+ \|", l)]) if inv.exists() else 0
        add("OK", "决策", f"{len(rows)} 条分在 {len(list(d.glob('*.md'))) - 1} 册，索引一致，约束型 {n_inv} 条两份逐字相同")


def check_secrets() -> None:
    db = ROOT / "backend/data/it_assets.db"
    if not db.exists():
        add("SKIP", "敏感词", "找不到数据库，跳过姓名反查")
        return
    names: set[str] = set()
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        for table in ("assets", "workstations"):
            try:
                for (v,) in con.execute(f"select distinct user_name from {table} where user_name is not null and user_name <> ''"):
                    if v and len(v.strip()) >= 2 and not PLACEHOLDER_PAT.match(v.strip()):
                        names.add(v.strip())
            except sqlite3.Error:
                pass
        con.close()
    except sqlite3.Error as exc:
        add("SKIP", "敏感词", f"数据库打不开：{exc}")
        return

    hits = []
    for p in md_files():
        text = read(p)
        for n in names:
            if n in text:
                hits.append(f"{p.relative_to(ROOT)}:{n}")
    # 真实编号前缀：形如 B0xx-03-xxxxx（B022/B023 是真实办公区），合成前缀是 A100/A101
    for p in md_files():
        for m in re.finditer(r"\bB0\d{2}-\d{2}-\d{5}", read(p)):
            hits.append(f"{p.relative_to(ROOT)}:编号 {m.group(0)}")
    if hits:
        add("ERROR", "敏感词", f"{len(hits)} 处疑似真实姓名/编号（只告警，请人工确认）", ", ".join(hits[:8]))
    else:
        add("OK", "敏感词", f"反查库内 {len(names)} 个真实姓名 + 真实编号前缀：0 命中")


# ------------------------------------------------------------------ main
# -------------------------------------------------------- 9. 标题不许重复
def check_dup_heading() -> None:
    """同一份文件里出现两个一模一样的标题 → 大概率是改写时粘贴重复了。

    危害不只是难看：GitHub/Pages 的锚点按标题生成，重复标题会让 `#xxx` 只跳到第一个，
    于是"我明明写了小节链接"却跳错地方（F7 那类表格断裂的同族问题）。
    """
    bad = []
    for p in md_files():
        rel = p.relative_to(ROOT).as_posix()
        # 只管"每天要读的那批"（README / CS / docs/dev / docs/user）。
        # CHANGELOG 每个版本条目都有各自的「现场 / 变更 / 验证」，是刻意的模板；
        # 设计稿与归档是时点快照，原样保留 —— 都不修，也就不用查。
        if rel == "CHANGELOG.md" or not is_current(p):
            continue
        seen: dict[str, int] = {}
        for i, line in enumerate(read(p).splitlines(), 1):
            if not line.startswith("#"):
                continue
            key = line.strip()
            if key in seen:
                bad.append(f"{p.relative_to(ROOT)}:{i} 与 :{seen[key]} 都是「{key[:24]}」")
            else:
                seen[key] = i
    if bad:
        add("ERROR", "标题", f"{len(bad)} 处标题重复", "; ".join(bad[:6]))
    else:
        add("OK", "标题", "没有重复标题")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="体积阈值也当错误")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if not (ROOT / "backend").exists():
        print(f"前提不成立：{ROOT} 看起来不是本项目根目录", file=sys.stderr)
        return 2

    check_version()
    check_footer()
    check_table_count()
    check_links()
    check_orphans()
    check_table_break()
    check_anchors()
    check_size(args.strict)
    check_banners()
    check_decisions()
    check_secrets()
    check_dup_heading()

    errors = [r for r in results if r["level"] == "ERROR"]
    warns = [r for r in results if r["level"] == "WARN"]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        icon = {"OK": "✓", "WARN": "!", "ERROR": "✗", "SKIP": "-"}
        for r in results:
            line = f"  {icon[r['level']]} [{r['check']}] {r['msg']}"
            print(line)
            if r["detail"]:
                print(f"      {r['detail']}")
        print()
        print(f"{len(results) - len(errors) - len(warns)} 通过 / {len(warns)} 警告 / {len(errors)} 错误")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
