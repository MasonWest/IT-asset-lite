# 维护约定

> 提交前该做什么、版本号在哪几处、哪些坑踩过。

## 14.1 改动税分级：**什么改动才动哪份文档**

> 这张表是本节存在的意义。**默认值是"什么都不用改"** ——
> 大多数改动（改文案、调样式、重构内部、加一个不改变口径的函数）**一份文档都不用动**。
> 确认要动，再按下面两列执行。（v2.3.1 起）

| 你改了什么 | 要动文档吗 | 为什么 |
| --- | --- | --- |
| 内部重构 / 调样式 / 改文案措辞 / 修注释 | **都不动** | 用户看不见、行为没变 |
| 加一个不改变口径的函数、修一个 bug（行为本来就对） | **都不动** | 口径没变，记下来反而是噪音 |
| 新增/调整**用户可见的能力** | [user/](../user/) 对应那一篇 + 根 README 的能力表 | 用户要知道自己多了什么、怎么用 |
| 新增/改变**设计口径**（"什么算 X"、"为什么用 A 不用 B"） | [decisions/](decisions/README.md) 对应册**加一条**（编号取最大 +1，索引同步） | 这是"半年后有人接手"最需要的东西 |
| 动了**数据模型 / 工程红线 / 术语** | [data-model.md](data-model.md) / [invariants.md](invariants.md) / [glossary.md](glossary.md) | 红线是"不可违背"，变了必须留痕 |
| 新增了一张表 / 改了端口这类"现状" | [CURRENT_STATE.md](../../CURRENT_STATE.md) 的那一句摘要 | CS 只留结论，细节在这里 |
| 版本号 | 四处 + CHANGELOG | 见 14.3 |

**⚠️ 别把"加一条决策"当成每次改动的标配。** 决策的判据是
**"这条口径将来会被别人误读吗？会误读就写一条"** ——
纯粹的"我今天加了个按钮"不配进决策表。
**决策表应该是慢的：** 判据不是"多少条"，而是"新加一条要不要改代码里的口径"。条数是结果，不是指标。

## 14.2 五类文档各管什么（别越界写）

| 文件 / 目录 | 写什么 | **不写什么** |
| --- | --- | --- |
| 根 `README.md` | 门面：这是什么、能干什么、3 步跑起来、文档入口 | 操作步骤详解、表结构、决策理由 |
| 根 `CURRENT_STATE.md` | **状态入口**：约束、现状一句话、红线索引、决策索引、"还没做的" | 实施历史、实测数字、操作步骤、决策全文 |
| 根 `CHANGELOG.md` | **每次版本改了什么**、为什么、踩了什么 | 长期有效的口径（那些属于 docs/ 或决策册） |
| `docs/user/` | 用户视角：怎么用、怎么排错 | 表结构、SQL、内部决策 |
| `docs/dev/` | 架构 / 模型 / 红线 / 决策全文 / 验收 / 维护 | "怎么点按钮" |
| `docs/design/` | 设计稿（**时点快照**，带 `> 状态：` 横幅） | 当前口径（那些该进 docs/dev） |
| `docs/archive/` | 原文快照、搬迁对照表 | 任何"现行的"内容 —— **只进不出，不修** |

> **为什么 CS 里没有"功能现状"这一节了**（2026-10-20 重构）：它原来记的是"现在有什么能力"，
> 但很快混进了大量"2026-09-30 那天做了什么、当时踩了什么" —— 那些 **CHANGELOG 已经逐条记着**
> （它就是干这个的），在这里再抄一遍等于**每次改动都要多考虑一处要不要同步**，而绝大多数时候
> 答案永远是"不用动"。判据只有一句：**半年后有人接手时，现在这条还有用吗？**
> 有用 → 进 `docs/dev/`；只是"当初为什么这么改" → 进 CHANGELOG。

> **两条判据**：
> ① 一条信息出现"2026-XX-XX 加了…"这种带日期的叙述 → 属于 CHANGELOG，不属于 CS。
> ② 一句话如果"半年后有人接手时已经没用了" → 它属于 CHANGELOG；还是有用 → 它属于 `docs/dev/`。

## 14.3 提交前的自查

改代码后按这个顺序，**跳步的代价是"看着跑通了其实没验"**：

0. `py -3 tools/docs-check.py` —— 文档没被你改坏（版本号、表数、链接、锚点、决策索引）
1. `python backend\check_env.py` —— 环境没坏
2. `cd frontend && npx vue-tsc --noEmit` —— 类型干净
3. `npx vite build` —— 前端能构建
4. 跑该改动相关的验收脚本（**只读的可以打真实库，含写操作的必须对临时库** —— 见 [acceptance.md](acceptance.md)）

## 14.4 容易踩的坑

（`bat` 编码、路由前缀顺序两条已升级为决策 11 / 8，见 [decisions/process.md](decisions/process.md) 与 [decisions/import-export.md](decisions/import-export.md)，此处不重复）

- **前端构建失败先看 `NODE_ENV` / `npm omit`**。本机环境里 `NODE_ENV=production`
  且 `npm omit=dev`，导致 `npm install` **会跳过 devDependencies** —— vite、vue-tsc 都不装，
  于是 `npm run build` 和 `npx vue-tsc` 全报错（`npx` 还会去拉一个版本不匹配的缓存副本，
  错误信息指向 `ERR_PACKAGE_PATH_NOT_EXPORTED './lib/tsc'`，看着像代码问题，其实不是）。
  正确做法：`npm install --include=dev`，然后用 `./node_modules/.bin/vite` 调用本地二进制。
  这跟"库里有数据但界面空"是同一类坑：**报错指向的地方不是原因所在。**
- **改了后端依赖要同步改 `backend/check_env.py` 的清单**。`requirements.txt` 管"装什么"，
  `check_env.py` 管"装完能不能 import"，两边必须一起动。
- **改了表结构要同步 `database.py` 的 `BUSINESS_TABLES` / `DICTIONARY_TABLES`**，
  否则 `reset.py` 和 `seed.py --check` 会漏表。**`workstations` 必须排在 `assets` 之后** ——
  它被 `assets` 引用，删除顺序错了 `reset.py` 会当场抛外键错（这是有意的：
  让 schema 问题在重置时暴露，而不是留下一个残缺的库）。
- **`create_all` 不会给已存在的表补列**。给 `assets` 加 `workstation_id` 走的是
  `ensure_schema()` 里的 `_add_workstation_id_column()`（照 `device_types.category` 那次）。
  它顺带显式建索引 —— 模型里的 `index=True` 只对 `create_all` 新建的表生效。
  改完**必须拿一份老库副本启动一次**验证，空库验不出问题。
- **加列之后还要改两处人工名单，否则功能静默失效**：
  ① `routers/assets.py` 的 `_TRACKED_FIELDS`（漏了 → 改点位不进时间线）；
  ② `services/events.py` 的 `FIELD_LABELS`（漏了 → 时间线显示英文键名）。
  两处都不会报错，只会"看起来没生效"。
- **新增写路由要同步 `main.py` 的 `_FAILED_ROUTES`**，否则失败请求会落到兜底分支，
  被记成「编辑资产 / 操作对象：系统」—— 审计里多出一条根本不存在的操作，比少记更糟。
- **改行为别改 `seed.py` 与 `main.py` 的分工**：字典走启动，演示数据走命令行。
  点位数据既不是字典也不是演示数据 → 走 `import_workstations.py` 显式导入。
- **`dist` 被 gitignore**：改前端源码必须本地 `npx vite build`，否则用户重启也看不到。
