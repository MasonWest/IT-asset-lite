# 轻资产管理模块 — 第一阶段：代码审查与设计方案

> 状态：已取代 → [light-assets-v2-revised.md](light-assets-v2-revised.md) · 2026-10-16

> ## ⛔ 本文档已被取代，请勿据此实施
>
> **取代者：[`light-assets-v2-revised.md`](light-assets-v2-revised.md)（原 `设计方案-轻资产管理模块-修订版.md`）**
>
> 本文的以下核心设计已在 2026-10-16 被推翻，两份口径并存会误导实施：
>
> | 本文的设计                         | 现行口径（修订版）                              |
> | ----------------------------- | -------------------------------------- |
> | 品类分「按数量 / 按单件」双模式（`tracking`） | **取消双模式**，统一按品类 + 数量管理                 |
> | 单件独立建档（`light_items`）         | **删除该表**；轻资产不追 SN                      |
> | 当前状态 100% 由流水实时重放推导           | **`light_holdings` 快照 + 流水留痕，同事务双写**   |
> | 无"当前分配记录"表                    | 新增 `light_holdings`（四种互斥归属 + 库存可带存放位置） |
>
> **仍然有效的部分**：§1 的现有代码审查结果（表结构、关联方式、迁移机制、可复用件）
>   
> 经过复核仍然准确，修订版直接沿用了这些结论。
>   
> 但**设计方案与结论请以修订版为准**。

> 原文状态（已失效）：**待确认（未改任何代码）**
>   
> 产出时间：2026-10-15 ｜ 基线版本 v2.7.2
>   
> 本文只回答"现有代码长什么样 / 轻资产该怎么接进去"，**不动一个字节的业务代码**。
>   
> 确认后进入第二阶段实施。

---

## 0. 阶段一交付说明

| 项      | 结论                                                                                                             |
| ------ | -------------------------------------------------------------------------------------------------------------- |
| 是否修改代码 | **没有。** 本阶段只读代码 + 只写本文档                                                                                        |
| 工作区    | `E:\MMMason\IT_snipe`（单体：FastAPI + SQLAlchemy + SQLite ｜ Vue3 + TS + Vite + Element Plus）                      |
| Git 分支 | `main`，HEAD = `780a420 feat(v2.7.2)`                                                                           |
| 未提交改动  | **仅 1 个未跟踪文件**：`Phase3-统一问题中心-落地清单.md`（**按项目约定刻意保持未跟踪**，实施阶段不要 `git add` 它，也**禁用 `git add -A`**）               |
| 真实库现状  | `backend/data/it_assets.db`：assets 150 / workstations 69 / asset_events 539 / audit_logs 211 / device_types 12 |

---

## 1. 现有架构审查结果

对应任务书第六节要求核实的六个问题，逐条给证据。

### 1.1 正式资产表的实际结构及主键 ✅

`assets`（`backend/app/models.py::Asset`，真实库 DDL 已核对）：

| 列                                                                          | 类型                              | 说明                                                                |
| -------------------------------------------------------------------------- | ------------------------------- | ----------------------------------------------------------------- |
| `id`                                                                       | INTEGER PK                      | **注意：不是 AUTOINCREMENT**（库内无 `sqlite_sequence` 表）→ **删行后 id 会被复用** |
| `asset_code`                                                               | VARCHAR(64) UNIQUE              | 正式资产编码。**真实库形如 `A100-03-00001`**，不是任务书场景里的 `PC-0089`              |
| `finance_code`                                                             | VARCHAR(64)                     | 财务编码                                                              |
| `device_type_id`                                                           | FK device_types RESTRICT        | 设备类型                                                              |
| `brand` / `model` / `serial_number`                                        | VARCHAR                         | **真实库 150 台 `serial_number` 全为空**                                 |
| `status`                                                                   | VARCHAR(16)                     | `in_use / idle / repair / scrapped`                               |
| `user_name`                                                                | VARCHAR(64)                     | **自由文本，不是人员表外键**（真实库 50 个去重值，含 `/`、会议室名等占位符）                      |
| `location`                                                                 | VARCHAR(128)                    | 存放位置（自由文本）                                                        |
| `workstation_id`                                                           | FK workstations RESTRICT **可空** | 资产挂在哪个点位（126/150 台有值）                                             |
| `purchase_date` / `warranty_until` / `notes` / `created_at` / `updated_at` |                                 |                                                                   |

**结论**：轻资产**不新增正式资产编码、不进 `assets` 表**。任务书"不生成正式编码、不混入台账统计口径"可直接满足。

### 1.2 资产点位与正式资产的实际关联方式 ✅

- 点位表 `workstations`（`models.py::Workstation`）：`id / code(UNIQUE) / x / y / facing / user_name(自由文本) / room / active(软删) / floor_map_id`。
- **资产 ↔ 点位是"反着指"的**：只有 `assets.workstation_id` 一处真相（CS 决策 31，红线 2）。点位表里**不存资产列表**；点位上挂了哪些资产是**派生查询**。
- 一个点位可挂多台资产（多对一）；查询走 `GET /api/workstations/{id}/assets`。

**结论**：轻资产要"关联点位"，只需在轻资产侧存一个 `workstation_id`（**正着指**），并在展示时读 `workstations.code`。**禁止**另建房间/工位/位置模型。

### 1.3 使用人变更、点位变更及资产流转日志的实现方式 ✅

- **使用人**：`assets.user_name` 裸字符串，没有人员表。判"到底归不归某人"有**唯一实现** `services/ownership.py::has_owner()`（空值与 `/`、`／` 都算没归属）——必须复用，**不许再写一次 `(x or '').strip()`**（CS 决策 76/82）。
- **点位变更**：改 `assets.workstation_id`；`workstation_id` 在 `_TRACKED_FIELDS` 里，所以会进时间线。
- **状态流转**：`POST /api/assets/{id}/operations`（`routers/assets.py::run_operation`），动作枚举 `AssetOperation`，自带 `ALLOWED_FROM` 状态机校验，非法 → **409 + 人话文案**。
- **流转日志两张表，视角不同，别合并**（红线 1）：
  - `asset_events`（**设备视角**）：`asset_id` **NOT NULL 外键**，`event_type / from_status / to_status / operator / note / detail(Text JSON) / created_at`。**每条事件必须挂在某台 assets 上**。
  - `audit_logs`（**操作者视角**）：**无外键**，`action / target_type / target_id / target_label / summary / status / detail`，只追加 + 查询，**没有任何写/改/删接口**。
- 写入机制：`services/events.py::record_event()` 与 `services/audit.py::record_audit()` 都**只 `add` 不 `commit`**，由路由连同业务改动一起提交（同事务，红线 1）。

**⚠️ 关键结论（本设计最重要的一条）**：`asset_events.asset_id` 是 NOT NULL 外键，**承载不了轻资产流水**；而任务书第三节明确要求"**轻资产与正式资产的关联历史必须独立记录，不能只依靠正式资产自身的流转日志推断**"。所以**必须新建轻资产自己的流水表**，不能复用 `asset_events`。

### 1.4 现有数据库迁移机制 ✅

- 入口：`database.py::ensure_schema()`（启动时 `lifespan` 调用）：
  1. `init_db()` → `Base.metadata.create_all()` **建缺的表**；
  2. 逐个 `_add_xxx_column()`（`PRAGMA table_info` + `ALTER TABLE`）给老库补列；
  3. `_recognize_default_floor_map()` 等一次性回填，用 `schema_meta` 记账保证幂等。
- **不引 Alembic**（项目约束：不提前引依赖）。
- **加的若是"新表"：什么都不用写** —— `create_all` 会自动建（老库也一样，实测过）。**只有加"列"才要 `PRAGMA` + `ALTER`**。
- **红线 7**：新增/删除表必须同步 `database.py` 的 `BUSINESS_TABLES`（`reset.py` 会清）或 `DICTIONARY_TABLES`（自动补、不被清），**且删除顺序要按外键依赖排**（先删引用方）。

**结论**：轻资产 3 张全新表 → `create_all` 直接建，**不需要写任何 ALTER/迁移代码**；只需把表名按依赖顺序加进 `BUSINESS_TABLES`。

### 1.5 前后端接口、页面路由和权限校验方式 ✅

- **接口**：`/api/*`，路由模块集中在 `app/routers/`。**FastAPI 按声明顺序匹配**：字面量路径必须排在 `/{id}` 之前（`/assets/grouped`、`/floor-maps/default` 都有专门注释）——新模块必须遵守。
- **前端路由**（`frontend/src/router/index.ts`，**是复数**）：`/` `/insights` `/asset/:id` `/map` `/map/editor` `/pairings` `/inventory` `/inventory/:id` `/labels` `/import` `/audit`。顶栏导航写在 `App.vue` 的 `<nav>`。
- **前端接口封装**：`src/api/index.ts` 按模块导出（`assetApi` / `workstationApi` / `transferApi` …），`src/types.ts` 放 TS 类型。
- **权限校验**：**没有账号体系，也没有任何权限校验**（红线 5：明确不做登录）。"操作人"由用户在顶栏设置里手填，存 `localStorage['it-asset.operator']`，随请求带 `operator`（body 或 query）。失败请求由 `main.py` 的 `audit_middleware` + `_FAILED_ROUTES` 表补一条审计。
- **前端生效方式**：改 `.py` **必须重启进程**；改前端要 `npm run build` 出 `dist`（红线 8/11）。

**结论**：轻资产模块**不做权限层**（与全站一致）。新路由要①登记进 `routers/__init__.py` 与 `main.py`，②新写操作要加进 `_FAILED_ROUTES` 表，③前端路由加进 `router/index.ts` 与 `App.vue` 导航。

### 1.6 是否已有可复用的库存 / 关联 / 流水能力 ✅

| 现有能力                                                                 | 能否直接复用          | 说明                                                                                                                    |
| -------------------------------------------------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------- |
| `services/spreadsheet.py`                                            | **直接复用**        | `read_rows()` 解析 xlsx/csv（Excel 真实行号、BOM、GBK 兜底）、`build_csv/build_xlsx`、`content_disposition` 中文文件名。**历史流水导入的解析直接用。** |
| `services/importer.py` 的**流程骨架**                                     | **照抄结构，换数据源**   | "预览与落库跑同一段 `build_plan()`"、"新增/更新/无变化/错误四桶"、"空单元格=不改、`-`=清空"、"给 Excel 真实行号"——**整个交互范式照搬**，只是列定义换成轻资产的。                |
| `services/audit.py`                                                  | **直接复用**        | `record_audit()` 同事务写；`write_audit_standalone()` 失败请求独立写。只需新增 `AuditAction` / `AuditTarget` 枚举值。                      |
| `services/ownership.py::has_owner()`                                 | **必须复用**        | "有没有人"的唯一判据。                                                                                                          |
| `routers/workstations.py` 的 **`link-suggestions` + `apply-linkage`** | **借用模式**        | "预检（干跑、只读）→ 用户勾选 → 执行接口写库前逐台重检"——这正是轻资产**关联/迁移**要的形状。                                                                 |
| `services/relocation.py` 的**预览令牌**                                   | **借用模式**        | `sha1(目标快照 + 选中对象快照)`，预览到执行之间有人改动即 409。轻资产"更换/转移"可复用同一手法防并发。                                                          |
| `services/filters.py::build_conditions()`                            | **不能直接复用**      | 它构造的是 `assets` 表的 WHERE 条件（字段绑死 assets）。轻资产筛选要自己写，但**照它的形状**（多值走逗号分隔字符串）。                                             |
| `asset_events`（流水）                                                   | **不能复用**（见 1.3） | `asset_id` NOT NULL 外键。                                                                                               |
| `inventory_*`（盘点）                                                    | **不能复用**        | 盘点对象是 assets；与轻资产无关。                                                                                                  |
| `asset_relations`（配对）                                                | **不能复用**        | 语义是"主机↔显示器"固定两类，硬塞配件会污染配对页与套装聚合。                                                                                      |

**净结论**：**可复用的是"范式与公共件"，不是"表"**。既有表里没有一张能装轻资产，必须新建；但导入、审计、预览、防并发的实现全都有现成模板可抄。

### 1.7 三条"踩雷预警"（来自本项目历史教训，实施时必看）

1. **主键 id 会被复用**（1.1 已确认无 AUTOINCREMENT）。所以轻资产流水里引用正式资产/点位时，**必须同时快照编码**（`asset_code` / `workstation.code`），不能只存 id —— 否则删一台设备后新设备复用 id，历史会指错对象（CS 决策 80 的原话："只记 id 会让旧例外挂到一台新设备上"）。
2. **`user_name` 是自由文本**。真实库有 `/`、`会议室一/二`、空串。轻资产里凡是"名称 = 人名"的判断**一律走 `has_owner()`**，展示走 `utils/format.ts`。
3. **`asset_events` 不是通用流水表**（1.3）。别想着"加个 target_type 就能装轻资产"——那会破坏"设备视角"的语义，且外键根本挂不上。

---

## 2. 最小可行设计

### 2.1 设计原则（逐条对齐既有约束）

| # | 原则                                                          | 依据                        |
| - | ----------------------------------------------------------- | ------------------------- |
| 1 | **轻资产独立建表，不复用也不污染 assets / asset_events / asset_relations** | 任务书核心原则 1                 |
| 2 | **"当前状态"全部派生，不落库；唯一落库的是"流水"与"业务决定"**                        | 红线 2 / CS 决策 71 / 80 / 83 |
| 3 | **流水 append-only，永不原地修改**；改错用反向流水更正                         | 任务书核心规则 3                 |
| 4 | **正式资产/点位只存引用 + 编码快照，使用人/点位一律现读**                           | 任务书核心规则 4/5               |
| 5 | **按品类决定追踪粒度**：`quantity` 不建档、`serial` 才建条目                  | 任务书核心原则 2                 |
| 6 | **历史导入只写流水、不改当前状态；且天然去重**                                   | 任务书核心规则 10                |
| 7 | 复用 `ownership` / `audit` / `spreadsheet` / `importer` 范式    | 1.6                       |

> **为什么"当前状态派生"在这里是安全的**（与 CS 决策 71 的区别要讲清楚）：
>   
> 决策 71 拒绝把"点位四态"落库，是因为它由**别的表**（assets）推出来，资产一挂一摘就过期。
>   
> 轻资产的当前状态由**它自己的 append-only 流水**推出来——流水写了就不会变，**推导永远精确、不会过期**。
>   
> 所以这里派生是安全的，落库反而是两处存真相、迟早对不上。

### 2.2 数据模型（新增 3 张表）

#### T1 `light_categories` — 轻资产品类

| 列                           | 类型                               | 说明                                            |
| --------------------------- | -------------------------------- | --------------------------------------------- |
| `id`                        | INTEGER PK                       |                                               |
| `name`                      | VARCHAR(80) **UNIQUE**           | 品类名，如「罗技 MK270 键鼠套装」。导入时用它做匹配键                |
| `brand`                     | VARCHAR(64) NULL                 | 品牌，选填（真实流水只给了"设备名"，允许为空，**不编造**）              |
| `spec`                      | VARCHAR(128) NULL                | 规格，如「2.4G 无线」「256GB」                          |
| `unit`                      | VARCHAR(16) NOT NULL DEFAULT '件' | 计量单位：套 / 块 / 条                                |
| `tracking`                  | VARCHAR(16) NOT NULL             | `quantity`（数量管理）/ `serial`（单件管理）——**由品类决定粒度** |
| `notes`                     | TEXT NULL                        |                                               |
| `active`                    | BOOLEAN NOT NULL DEFAULT 1       | 软删（与 workstations 同口径，**编码/名称不释放**）           |
| `created_at` / `updated_at` | DATETIME NOT NULL                | `default=datetime.now`（红线 3：本地时间）             |

#### T2 `light_items` — 单件条目（**仅 `tracking='serial'` 的品类才产生行**）

| 列                           | 类型                             | 说明                                         |
| --------------------------- | ------------------------------ | ------------------------------------------ |
| `id`                        | INTEGER PK                     |                                            |
| `category_id`               | FK `light_categories` RESTRICT |                                            |
| `serial_number`             | VARCHAR(128) NULL, index       | 序列号。**允许为空**（历史数据缺 SN，不得编造）                |
| `label`                     | VARCHAR(64) NULL               | 便于识别的短标签（如「SSD-01」），可空；为空时前端按 `品类名 #id` 显示 |
| `notes`                     | TEXT NULL                      |                                            |
| `active`                    | BOOLEAN NOT NULL DEFAULT 1     | 注销/报废软删（历史留着）                              |
| `created_at` / `updated_at` | DATETIME NOT NULL              |                                            |

> **⚠️ 这里刻意不存 `current_user` / `current_asset_id` 等当前状态** —— 由流水派生（原则 2）。
>   
> 数量管理品类**不产生 `light_items` 行**（"不需要为每套生成编码"）。

#### T3 `light_movements` — 轻资产流转流水（append-only，**系统的唯一真相**）

| 列               | 类型                                    | 说明                                                           |
| --------------- | ------------------------------------- | ------------------------------------------------------------ |
| `id`            | INTEGER PK                            |                                                              |
| `category_id`   | FK `light_categories` RESTRICT, index | 必填                                                           |
| `item_id`       | FK `light_items` RESTRICT NULL, index | 单件模式必填；数量模式为 NULL                                            |
| `op`            | VARCHAR(24) NOT NULL, index           | 操作类型，见 2.4                                                   |
| `quantity`      | INTEGER NOT NULL DEFAULT 1            | ≥1；单件模式恒 1                                                   |
| `from_kind`     | VARCHAR(16) NULL                      | `stock` / `user` / `asset` / `workstation`；NULL = 来自系统外部（入库） |
| `from_ref_id`   | INTEGER NULL                          | kind=asset/workstation 时存对应 id（**无外键**，见下）                   |
| `from_ref_code` | VARCHAR(64) NULL                      | **编码快照**（asset_code / workstation.code），防 id 复用              |
| `from_user`     | VARCHAR(64) NULL                      | kind=user 时的自由文本人名                                           |
| `to_kind`       | VARCHAR(16) NULL                      | 同上；**NULL = 注销/消失**（报损）                                      |
| `to_ref_id`     | INTEGER NULL, index                   |                                                              |
| `to_ref_code`   | VARCHAR(64) NULL                      |                                                              |
| `to_user`       | VARCHAR(64) NULL, index               |                                                              |
| `operator`      | VARCHAR(64) NULL                      | 操作人（走 localStorage，同全站口径）                                    |
| `occurred_at`   | DATETIME NOT NULL, index              | **业务发生时间**（历史导入可回填过去时间）                                      |
| `note`          | VARCHAR(255) NULL                     |                                                              |
| `origin`        | VARCHAR(16) NOT NULL DEFAULT 'manual' | `manual` / `import`（历史导入）——**规则 10 的落地**                     |
| `import_batch`  | VARCHAR(64) NULL                      | 一次导入一个批次号，导入结果可追溯（P0.8）                                      |
| `request_id`    | VARCHAR(64) NULL **UNIQUE**           | 前端每次提交一个 UUID；唯一索引挡重复提交（P1.5）                                |
| `created_at`    | DATETIME NOT NULL                     | 记录写入时间（与 `occurred_at` 分开——"什么时候发生的"和"什么时候记的"是两件事）           |


**为什么 asset/workstation 的引用不建外键**：与 `audit_logs.target_id`、`relation_exceptions.asset_id` 同一取舍（CS 决策 80）——
历史在没有宿主之后仍然成立（"它当初确实装在 A100-03-00001 上"）。建了外键，删设备要么被 RESTRICT 挡住、要么级联删掉历史，两者都错。用"id + 编码快照"，展示时先按 id 查活的资产，查不到就显示编码 +「已删除」。

**索引**：`(category_id, occurred_at)`、`(item_id, occurred_at)`、`(to_ref_id)`、`(to_user)`、`request_id` UNIQUE。

### 2.3 哪些信息从现有正式资产 / 点位**读取**（不重复存）

| 轻资产要展示的 | 从哪读 | 说明 |
| --- | --- | --- |
| 关联主机的**当前使用人** | `assets.user_name`（经 `has_owner()`/`userLabel()`） | **不复制**到轻资产表（规则 5） |
| 关联主机的**当前点位** | `assets.workstation_id` → `workstations.code` | 不复制 |
| 关联主机的**状态** | `assets.status` | 主机报废/维修时能一眼看到 |
| 点位信息（编码/房间） | `workstations.code` / `workstations.room` | 直接读 |
| 独立分配给某人的配件 | 轻资产自己的 `to_user` | 规则 4 的"独立分配"分支 |

> 关联**历史**（含已拆卸）靠 `light_movements` 自己的 `from_*` 记录——**不从 `asset_events` 推断**（任务书核心原则 3 末段）。

### 2.4 状态流转（`op` 语义表）

`from_kind → to_kind` 是同一套表达，于是"每一次操作"都是流水里的一次"物料移动"：

| op | 中文 | from → to | 适用模式 | 批次 |
| --- | --- | --- | --- | --- |
| `stock_in` | 入库 | `NULL` → `stock` | 数量 | P0 |
| `allocate` | 分配 | `stock` → `user` \| `asset` \| `workstation` | 两种 | P0 |
| `return` | 归还 | `user` \| `asset` \| `workstation` → `stock` | 两种 | P0 |
| `attach` | 安装/关联 | `stock` \| `user` → `asset` \| `workstation` | 单件 | P0 |
| `detach` | 拆卸 | `asset` \| `workstation` → `stock` | 单件 | P0 |
| `transfer` | 转移 | 持有方 → 另一持有方 | 单件 | P1 |
| `replace` | 更换 | 持有方 → 持有方（旧件报废 + 新件顶替） | 两种 | P1 |
| `scrap` | 报损 | `stock` 或 持有方 → `NULL` | 两种 | P1 |
| `adjust` | 盘盈盘亏调整 | `stock` ↔ `NULL`（±q） | 数量 | P1 |

**校验（写库前，同一事务内）**：

1. **数量校验**：数量模式 `allocate` 前，该品类 `库存 ≥ quantity`，否则 **409 + 人话**（"库里只有 2 套，不能分配 3 套"）。`origin='import'` 的流水**豁免**（历史导入时入库侧本就缺失，见 2.6）。
2. **单件校验**：`item_id` 必须存在且属于同一品类；单件必须处于该操作要求的 from 状态。
3. **状态机**：`op` 的 from/to 组合要合法（如 `stock_in` 的 from 必须为 NULL）。
4. **原子性**：`record_movement()` 只 `add`；路由连同审计一起 `commit`；任何一步抛错整体回滚（红线 1）。
5. **并发**：SQLite 单写者 + 显式事务即可，无需额外锁（同全站口径）。
6. **防重复提交**：`request_id` 唯一索引；撞了返回 **409「这次登记已经提交过了」**（幂等语义），前端同时 disable 按钮。

### 2.5 库存口径与守恒（数量模式）

全部由流水聚合**派生**：

```
库存(品类)   = Σ_q [ (to_kind='stock') − (from_kind='stock') ]        （仅 origin='manual'）
已分配(品类) = Σ_q [ (to_kind∈{user,asset,workstation}) − (from_kind∈{user,asset,workstation}) ]
            （origin='manual' + 'import' 都算——导入的历史分配就是当前持有）
```

- **守恒**：`库存 + 已分配 = Σ入库 − Σ报损`。单测断言这条恒等式。
- **不允许负数**：manual 操作的校验保证 `库存 ≥ 0`；`origin='import'` 不进库存口径，所以也不会把库存拉成负。
- 若某品类**从未登记入库**却有分配（历史导入的常态），库存显示为「**未登记入库**」而不是 0，并给一条中性提示——**不推断真实总库存**（任务书第三节）。

### 2.6 历史流水导入后如何计算当前状态（规则 10 的落地）

**导入流程**（照抄 `importer.py` 范式）：

1. `POST /api/light/import/preview` → **只解析校验，不写库**，产出逐行预览（Excel 真实行号、四桶统计、告警）。
2. `POST /api/light/import` → 与预览**跑同一段 `build_plan()`**，`commit` 时写流水 + 一条 `light_import` 审计。

**每一行的处理**：

- **品类匹配**：按 `name` 精确匹配（trim 容忍）；匹配不到 → 按开关自动创建（默认 `tracking='quantity'`，可在预览里逐行改）。
- **去向分类**（关键，直接影响"当前去向"对不对）：
  1. 精确匹配某 `workstations.code` → `workstation`
  2. 否则精确匹配某 `assets.asset_code` → `asset`
  3. 否则为空 / `-` → `stock`（在库）
  4. 否则 → `user`（自由文本）
  - 支持在导入表里加一列「去向类型」显式指定，**显式优先于猜测**；预览里逐行展示"猜成了什么"，用户可改。
  - 例：任务书里的 `01室闲置 0089` 既不匹配真实点位码（`W01…W69`）也不匹配真实资产码（`A100-03-00001`）→ 会被猜成 `user`，预览里标黄要求人工确认。**不硬编码 `PC-0089` 这类示例编号。**
- **写一条流水**：`op='allocate'`，`origin='import'`，`occurred_at` = 表里的分配时间，`from_kind=NULL`，`to_*` = 上面解析结果，`import_batch` = 本次批次号。

**"不会重复生成当前分配"的两道保证**：

1. **`origin='import'` 与 manual 分开记账**：导入不改库存、不触发"新增分配"业务逻辑。
2. **自然键去重**：以 `(品类, 去向, occurred_at, quantity, origin='import' 批次无关)` 为键查重，已在库则跳过并在结果里报「重复 N 行，已跳过」——**同一份 Excel 导两遍，第二遍是 0 新增 0 跳过变化**（对齐决策 6"无变化桶"的精神）。

**当前状态怎么算**：导入后无需"重算"——当前状态本来就是查询时从流水现算的（2.5）。导入只是往流水里补了带原始时间的记录，所以"历史能查全 + 当前持有也对"**天然同时成立**，不存在"导入时把每条历史都变成一次新分配"的窗口。

### 2.7 数据库迁移方案

| 动作 | 需要做什么 |
| --- | --- |
| 新增 3 张表 | **零迁移代码**，`create_all` 自动建（新库老库都一样） |
| 加进 `BUSINESS_TABLES` | **必须**（红线 7）。按外键依赖排（先删引用方）：<br>`…, light_movements, light_items, light_categories, assets, relation_exceptions, workstations, floor_maps, audit_logs` |
| `reset.py` | 不用改代码，读 `BUSINESS_TABLES` 全清 |
| Alembic | **不引**（约束明确） |
| 版本号 | 实施阶段同步四处：`config.py` / `app/__init__.py` / `package.json` / `package-lock.json`（只改根 version 两处）|

**⛔ 明确不做**：不给 `assets` 加"是否有配件"之类的列（派生，红线 2）；不改 `asset_events` 结构。

### 2.8 接口设计（`/api/light/*`）

| 方法 | 路径 | 说明 | 批次 |
| --- | --- | --- | --- |
| GET | `/api/light/categories` | 品类列表（含派生统计：库存/已分配/单件数） | P0 |
| POST | `/api/light/categories` | 新增品类 | P0 |
| PUT | `/api/light/categories/{id}` | 编辑（改 tracking 需 409 拦住"已在用的品类"） | P0 |
| DELETE | `/api/light/categories/{id}` | 软删（仍被流水引用时 409 + 清单） | P0 |
| GET | `/api/light/items` | 单件条目列表（按品类/序列号/当前持有方筛选） | P0 |
| POST | `/api/light/items` | 新增单件（不改状态，只建档） | P0 |
| GET/PUT/DELETE | `/api/light/items/{id}` | 详情 / 编辑（序列号、备注）/ 注销 | P0 |
| GET | `/api/light/items/{id}/history` | **单件流转历史**（场景 4） | P0 |
| GET | `/api/light/movements` | 流水列表（按品类/时间/操作人/op 筛选 + 分页） | P0 |
| POST | `/api/light/movements` | 登记一次流转（唯一写入口，`op` 决定语义） | P0 |
| GET | `/api/light/summary` | 数量模式汇总（库存/已分配/未登记入库） | P0 |
| **POST** | **`/api/light/import/preview`** | **导入预览（只读，不写库）** | P0 |
| **POST** | **`/api/light/import`** | **导入落库** | P0 |
| GET | `/api/light/by-asset/{asset_id}` | 某正式资产**当前关联**的配件 | P1 |
| GET | `/api/light/by-workstation/{station_id}` | 某点位上的配件 | P1 |
| GET | `/api/light/export` | 导出台账/流水（复用 `spreadsheet.py`） | P1 |

> ⚠️ **路由顺序**：`/summary`、`/import/preview`、`/import`、`/by-asset/...` 这些**字面量路径必须声明在带 `{id}` 的路由之前**（FastAPI 按声明顺序匹配）。同时把这批新写操作登记进 `main.py::_FAILED_ROUTES`，否则失败请求会被兜底记成"编辑资产/操作对象：系统"。

### 2.9 前端设计

| 项 | 方案 |
| --- | --- |
| 新路由 | `/light`（轻资产台账）、`/light/category/:id`（品类详情 = 库存 + 流水 + 单件列表）。导航加在 `App.vue` 的 `<nav>`（建议放"配对管理"附近） |
| 新增类目 | `api/index.ts` 加 `lightApi`；`types.ts` 加 `LightCategory/LightItem/LightMovement/...` |
| 复用 | 使用人下拉走 `composables/useFreeTextOptions.ts`（拼音 + 新建项垫底，决策 50）；人名展示走 `utils/format.ts`（`hasOwner`/`userLabel`）；编码排序走 `utils/workstationMap.ts::compareStationCode`（决策 41）；`**重点**` 文案走 `utils/richText.ts` |
| 交互范式 | 危险/批量操作走"**预览 → 确认 → 执行**"（决策 39/82）：预览把 `操作前 → 操作后` **两侧都摆出来，不变的显式标「不变」**；`notes`（只说明）与 `confirmations`（必须点头）分家 |
| 视觉 | 沿用全站卡片风（底色 `#f5f6f7`、卡片白 + 1px `#e5e6eb` + 圆角 12px、主色 `#3370ff`）；**不用红色**表达中性事实（决策 46/72） |
| 导入页 | 可挂在现有 `/import` 页里加一个 Tab，或独立 `/light/import`；**复用同一套预览组件形态** |

### 2.10 审计接入

新增 `AuditAction`（沿用现有粒度：`ASSET_OPERATE` 一个动作覆盖六种流转，所以这里也**不按 op 拆**）：

- `LIGHT_CATEGORY_CREATE / UPDATE / DELETE`
- `LIGHT_ITEM_CREATE / UPDATE / DELETE`
- `LIGHT_OPERATE`（入库/分配/归还/拆卸/转移/更换/报损；`op` 进 `detail` 与 `summary`）
- `LIGHT_IMPORT`

新增 `AuditTarget`：`LIGHT_CATEGORY` / `LIGHT_ITEM`。

**一次操作 = 1 条审计**（批量导入同口径，决策 4/34）。同时**更新 `AuditAction.GROUPS`**（审计页筛选分组）。

### 2.11 与 10 条验收场景的对照

| # | 场景 | 设计如何满足 |
| --- | --- | --- |
| 1 | MK270 数量管理，分给 3 人，台账显示 3 套已分配 | 品类 `tracking=quantity`；3 条 `allocate` 流水；`/summary` 派生 `已分配=3` |
| 2 | SSD 单件追踪，记序列号 + 关联（例：PC-0089） | 品类 `tracking=serial` + `light_items` 行（`serial_number`）+ `attach` 流水（`to_kind='asset'`，存 id + `asset_code` 快照） |
| 3 | 主机的使用人变了 → SSD 当前使用环境展示正确 + 保留历史 | **不复制**使用人，展示时现读 `assets.user_name`（2.3）；历史在 `light_movements` 里不动 |
| 4 | SSD 从 A 拆到 B，保留拆卸/转移/安装历史 | `detach` → `transfer`(或直接 `attach`) 两条流水，`from_*` / `to_*` 完整留痕，`/items/{id}/history` 按 `occurred_at` 展示 |
| 5 | 键鼠独立分配给员工，不关联主机 | `allocate` 的 `to_kind='user'` |
| 6 | SSD 放闲置点位，无使用人无主机，仍可查 | `attach`/`allocate` 的 `to_kind='workstation'`；列表按"当前持有方"筛选 |
| 7 | 主机报废时发现关联配件并要求确认 | 在 `run_operation`（SCRAP）时查"当前 `to_kind='asset'` 且 `to_ref_id=该主机` 的流水" → 返回 `confirmations` 要求确认处置；**绝不自动改轻资产归属**（规则 7）。P1 可再加一条问题中心规则 |
| 8 | 导入 8 条历史 → 历史能查全、不重复生成当前分配 | 2.6：`origin='import'` + 自然键去重 |
| 9 | 入库/分配/归还/报损后库存正确、不负数不重复 | 2.5 的派生公式 + 守恒断言 + manual 校验（`库存 ≥ q`） |
| 10 | 现有正式资产/流转/人员/点位不受影响 | **不改任何既有表与既有路由**；只新增表、只新增枚举值、只在新模块里读写 |

---

## 3. 实施步骤、风险与验收标准

### 3.1 实施步骤（建议分三批）

**批次 A（P0 底座）**
1. `models.py` 加 3 个模型 + 枚举（`LightTracking` / `LightOp` / `LightHolderKind`）。
2. `database.py::BUSINESS_TABLES` 插入三张新表（按依赖顺序）。
3. `schemas_light.py`（新 DTO 文件，仿 `schemas_workstations.py` 分文件）。
4. `services/light.py`：`record_movement()`、`current_state_of_item()`、`category_stock()`、`resolve_holder()`（**`has_owner` 复用**）。
5. `routers/light.py` 品类/条目/流水 CRUD + `/summary`；登记进 `routers/__init__.py` 与 `main.py`，补 `_FAILED_ROUTES`。
6. `AuditAction` / `AuditTarget` / `GROUPS` 补枚举。
7. 前端：`router` + `App.vue` 导航 + `api`/`types` + 台账页 + 品类详情页 + 登记流转弹窗。
8. 单测：库存守恒、单件状态派生、校验（负库存/重复提交）。

**批次 B（P0 导入）**
9. `services/light_importer.py`（照抄 `importer.py` 骨架）+ 模板下载 + 预览/落库接口 + 导入页。

**批次 C（P1 扩展）**
10. `replace` / `scrap` / `adjust` + `/by-asset` / `/by-workstation` / `/export`。
11. 主机报废提示（场景 7）。

### 3.2 风险清单

| 风险 | 等级 | 对策 |
| --- | --- | --- |
| 导入 "去向" 猜错（如 `01室闲置 0089`） | **高** | 预览逐行展示猜测 + 支持「去向类型」显式列 + 人工确认；**绝不静默入库** |
| id 复用导致历史指错对象 | 中 | 流水存 `*_ref_code` 快照（2.2） |
| 把"当前状态"落库 → 两处真相 | 中 | 一律派生（2.1/2.5），配守恒单测 |
| 改 `tracking` 破坏已有数据 | 中 | 品类已在用时 409 拦住；要改必须先清空相关流水 |
| 误把历史流水算进库存 → 负库存 | 中 | `origin='import'` 不进库存口径（2.5/2.6） |
| 新模块动了既有表/统计口径 | 高 | **不碰 assets 表与既有接口**；模块自带筛选/统计 |
| 验收脚本打到真实库 | **高** | 写操作一律 `IT_ASSET_DATA_DIR=<临时库>` + 端口 8099（红线 9） |
| `asset_events` 被"顺手扩展" | 中 | 明确不加 `target_type`（1.7-3） |

### 3.3 验收标准

- 上述 10 个场景**全部通过**（用临时库跑，禁止打真实库）。
- 单测：库存守恒恒等式、单件状态派生、负库存 409、重复提交 409、导入幂等（导两遍 0 变化）。
- **回归**：现有 `backend/tests/*` 全绿；`/api/assets`、`/api/workstations`、审计页接口**返回值不变**。
- 文档：CS 加"数据模型 + 决策 + 功能现状"三段、CHANGELOG 加一条、README 加一节（使用者视角）。

---

## 4. 明确的「不做」清单（边界，避免滑向 CAD/ERP）

1. 不做采购、供应商、成本核算、审批流（项目约束原文）。
2. 不做完整仓储（多仓库、货位、调拨单、出入库单据流）。
3. 不做轻资产的**正式资产编码**（就因为它没有编码才叫轻资产）。
4. 不给点位/资产加"配件数量"冗余列。
5. 不做序列号强必填（历史数据缺 SN，允许为空）。
6. 不做多张地图式的多…（不相关，忽略）。
7. **不重构任何既有模块**；不在 `assets`/`asset_events`/`asset_relations` 上加列。
8. 暂不做库存预警、盘点差异（任务书 P2）。

---

## 5. 需要你拍板的 5 个问题

1. **导入历史流水的定位**：采用「`origin='import'` 只进历史、不进库存口径」+ 自然键去重（本文 2.6 方案）？还是你希望导入的分配**直接成为当前持有且参与库存**（那样需要你先给出"期初入库量"，否则库存会失真）？
2. **品类是否要分 `brand`/`spec`**：我按"name + 选填 brand/spec"设计。可否？
3. **数量模式的默认追踪、单件模式的切换时机**：默认 `quantity`；要不要在品类编辑里允许多次切换（我建议"已用过的品类禁止改 tracking"）。
4. **入口位置**：新模块挂在顶栏导航（`/light`）+ 现有导入页加 Tab，还是单独一个"批量导入"入口？
5. **场景 7（主机报废提示）**：放在资产状态流转的 `confirmations` 里（P1），还是同时加一条问题中心规则？

---

> **下一步**：你确认本文（或指出要改的点）后，我按 3.1 分批实施，边做边按 3.3 验收；
> 每批完成给"修改文件清单 + DB 变更 + 测试结果 + 未完成事项"，并在里程碑处同步 CS / CHANGELOG。
