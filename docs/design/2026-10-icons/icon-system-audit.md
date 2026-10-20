# 图标体系盘点与统一 SVG 改造方案

> 状态：已实施（v2.9.5 起分批落地，v2.9.9 收尾）· 2026-10-20

> **范围**：`E:\MMMason\IT_snipe`（`frontend/src` + `backend/app` + SQLite 只读实查）
> **性质**：本次为**只读分析**。未修改任何代码、未执行数据库迁移、未安装依赖、未改变任何业务行为。
> **证据标识**：文中 `文件路径:行号` 均来自实际读取；标注「需进一步确认」的条目是本轮未能核实的，未凭猜测补齐。

---

## 一、现状结论

### 1.1 系统里同时存在四套互不相干的图标机制

| # | 机制 | 实现位置 | 载体 | 图标由谁决定 |
|---|---|---|---|---|
| ① | **设备类型 emoji** | `frontend/src/utils/format.ts:28-94` | Emoji 字符 | 前端按「类型名」查精确表，失配后按关键字兜底，再失配退 📦 |
| ② | **轻资产品类 SVG** | `frontend/src/components/AppIcon.vue` + `LightListView.vue:128` | 内联 SVG path | 前端按「品类名 + 规格」**正则猜测** |
| ③ | **状态 / 动作 / 事件 / 审计 emoji** | `frontend/src/utils/format.ts:103-328` | Emoji 字符 | 前端常量表，按枚举 key 取值 |
| ④ | **后端审计动作 emoji** | `backend/app/models.py:138-145`、`364-403` | Emoji 字符 | 后端常量表，经 `/api/audit/meta` 下发（前端**未使用**） |

**四套机制的耦合点是"没有耦合"**：它们之间没有共享的图标标识体系，同一个业务对象在不同页面的图标由不同代码各自决定。

### 1.2 关键结论（逐条有据）

1. **主资产图标全部是 Emoji，且完全由"名称字符串"决定。**
   `typeIcon(name?: string | null)`（`format.ts:85`）只接收名称，不接收 id。`TYPE_ICONS` 精确表（`format.ts:28-55`）与 `TYPE_KEYWORDS` 关键字表（`format.ts:63-83`）都按中文名索引。共 **10 个渲染点**调用它（见 §2.2）。

2. **设备类型有稳定 ID，但图标体系没有用它。**
   `DeviceType`（`models.py:490-508`）有自增主键 `id` 与唯一约束 `name`；`Asset.device_type_id` 是外键（`models.py:524-525`）。图标却按名称映射，等于**放着稳定标识不用**。

3. **轻资产品类图标是正则猜出来的，且不落库。**
   `LightCategory`（`models.py:1204-1251`）字段只有 `name / brand / spec / unit / notes / active / created_at / updated_at`——**没有 icon**。图标由 `LightListView.vue:127-133` 的 `catIcon()` 现算：

   ```ts
   function catIcon(row: LightCategory): string {
     const s = `${row.name} ${row.spec ?? ''}`.toLowerCase()
     if (/键鼠|键盘|鼠标|套装/.test(s)) return 'keyboard'
     if (/ssd|固态|硬盘|内存|芯片|m\.2|nvme/.test(s)) return 'chip'
     return 'box'
   }
   ```
   仍是"键鼠套装 → 键盘图标"这条链路的完整解释。

4. **同一品类在不同页面图标不一致（已确认 3 处）。**
   - 轻资产列表卡片 / 列表视图：`catIcon()` → 键鼠套装显示**键盘**（`LightListView.vue:291`、`:374`）
   - 正式资产详情页「关联轻资产」面板：**写死 `chip`**（`AssetLightAssetsPanel.vue:116`）→ 键鼠套装显示**芯片**
   - 点位侧栏轻资产区（`MapStationPanel.vue:722-736`）与轻资产品类详情页（`LightCategoryView.vue`）：**完全不显示品类图标**

5. **`CATEGORY_META.icon` 是死代码。**
   `format.ts:117-122` 给 host/display/other 各配了 emoji，但全站唯一的 `categoryMeta()` 调用点（`DeviceTypeDialog.vue:119`）只取 `.label`。三个 emoji 从未被渲染。

6. **后端下发了一份图标，前端不读。**
   `AuditAction.ICONS`（`models.py:364-403`，38 项）经 `/api/audit/meta` 组装成 `action_icon`（`routers/audit.py:216`），前端 `types.ts:437` 有该字段，但**没有任何组件渲染它**——审计页用的是前端自己的 `AUDIT_VISUALS`（`format.ts:288-307`，18 项，`AuditView.vue:402`）。这是**同一件事存了两份**。

7. **`AppIcon.vue` 已经是可复用的本地 SVG 资源，且已内置兜底。**
   25 个图标（`AppIcon.vue:21-52`）：`box, keyboard, chip, user, users, pin, screen, plus, arrowIn, arrowOut, swap, wrench, trash, undo, edit, close, check, list, grid, alert, doc, download, info, clock, sum`。
   统一 `viewBox="0 0 24 24"`、`stroke="currentColor"`、`stroke-width="1.7"`、`fill="none"`；未知 name 自动退 `box`（`AppIcon.vue:68`）。**零图标库依赖、随前端打包、无运行时请求**——完全符合本次设计目标。

8. **图标改造与业务数据完全解耦（低风险）。**
   - 资产导出列定义只有 `device_type_name`，无 icon（`services/importer.py:57-73`）
   - 统计按 `device_type_id` 计数（`routers/meta.py:29`），不涉及图标
   - 点位地图只存 `workstation_id` 与布局 JSON，点位表不存资产列表
   - 轻资产库存/已分配全部由 `light_holdings` 现算（`schemas_light.py:155-164`）
   → **图标是纯展示层关注点，改它不会影响流转、统计、点位关系或轻资产数量。**

### 1.3 实际数据快照（只读实查 `backend/data/it_assets.db`）

**设备类型（12 条）**

| id | name | category | 资产数 |
|---|---|---|---|
| 1 | 主机 | host | 60 |
| 2 | 笔记本 | other | 7 |
| 3 | 显示器 | display | 55 |
| 4 | 键盘 | other | 0 |
| 5 | 鼠标 | other | 0 |
| 6 | 打印机 | other | 1 |
| 7 | 路由器 | other | 1 |
| 8 | 扫描仪 | other | 1 |
| 9 | 电视 | display | 6 |
| 10 | 一体机 | other | 3 |
| 11 | 家具 | other | 15 |
| 12 | 装饰摆件 | other | 1 |

> 注：`seed.py:37-44` 里「笔记本」写的是 `DeviceCategory.HOST`，库里实际是 `other`——说明该条被人工改过。这不影响图标方案（图标挂在类型 id 上），但说明**类别确实会被用户改动**，方案不能假设它等于名字推断结果。

**轻资产品类（2 条）**：`罗技MK270键鼠套装`（罗技 / 2.4G无线）、`M.2固态 宏碁N3500 SSD`（宏碁 / 256GB）。两者都恰好能命中 `catIcon()` 的正则，所以历史上没暴露"猜不出来"的问题。

---

## 二、全站 Emoji 盘点表

> 检索方式：对 `frontend/src` 下全部 `.vue` / `.ts` 做 Unicode Emoji 字符区段扫描（`Emoji_Presentation` 区段），并区分「代码注释里的说明符号」与「真正参与渲染的位置」。**注释中的 ⚠️ / → / ─ 等 69 处不计入图标，不列**。
> 下表为**真正渲染到界面**的位置。

### 2.1 按设备类型映射的 Emoji（机制 ①）

| 图标使用位置 | 当前实现方式 | 当前图标或映射规则 | 对应业务含义 | 建议目标 SVG | 备注 |
|---|---|---|---|---|---|
| `utils/format.ts:28-55` | 前端常量表 `TYPE_ICONS` | 20 条精确映射（中文名 → emoji） | 设备类型 | 见 §3.2 映射表 | 名称精确匹配 |
| `utils/format.ts:63-83` | 前端常量表 `TYPE_KEYWORDS` | 19 组关键字（中/英）→ emoji | 自建类型的兜底 | 同上 | 大小写不敏感子串匹配 |
| `utils/format.ts:86` | 函数内硬编码 | `📦` | 名称为空 | `package`（新增） | |
| `utils/format.ts:93` | 函数内硬编码 | `📦` | 表与关键字都没命中 | `package`（新增） | 静默兜底，无提示 |
| `AssetListView.vue:753` | `typeIcon()` | 见上 | 台账卡片主图标 | 同名 SVG | **另有 `🧩` 分支**：`g.kind === 'bundle' ? '🧩' : typeIcon(...)` |
| `AssetDetailView.vue:235` | `typeIcon()` | 见上 | 资产详情页 hero | 同名 SVG | |
| `PairingView.vue:438` | `typeIcon()` | 见上 | 配对页主机侧 | 同名 SVG | |
| `PairingView.vue:467` | `typeIcon()` | 见上 | 配对页候选显示器 | 同名 SVG | |
| `PairingView.vue:505` | `typeIcon()` | 见上 | 配对页显示器列表 | 同名 SVG | |
| `LabelPrintView.vue:243` | `typeIcon()` | 见上 | 标签打印选择列表 | 同名 SVG | 28px 徽标 |
| `InventoryDetailView.vue:403` | `typeIcon()` | 见上 | 盘点明细行 | 同名 SVG | |
| `AssetRelationsPanel.vue:179` | `typeIcon()` | 见上 | 资产关系-显示器 | 同名 SVG | |
| `AssetRelationsPanel.vue:221` | `typeIcon()` | 见上 | 资产关系-主机 | 同名 SVG | |
| `MapStationPanel.vue:659` | `typeIcon()` | 见上 | 点位侧栏资产行 | 同名 SVG | |
| `utils/format.ts:117-122` | 前端常量表 `CATEGORY_META` | `🗄️` / `🖥️` / `📦` | 设备**类别**（host/display/other） | 与主机/显示器/盒子同名 | **死代码**：全站唯一调用点 `DeviceTypeDialog.vue:119` 只取 `.label` |

### 2.2 按名称正则匹配的 SVG 图标（机制 ②）

| 图标使用位置 | 当前实现方式 | 当前图标或映射规则 | 对应业务含义 | 建议目标 SVG | 备注 |
|---|---|---|---|---|---|
| `LightListView.vue:127-133` | `catIcon()` 正则 | `/键鼠\|键盘\|鼠标\|套装/` → `keyboard`；`/ssd\|固态\|硬盘\|内存\|芯片\|m\.2\|nvme/` → `chip`；否则 `box` | 轻资产品类 | 改为读品类自带 `icon` 字段 | **唯一**的品类图标推断实现 |
| `LightListView.vue:291` | 调 `catIcon()` | 同左 | 轻资产卡片缩略图（20px） | 同上 | |
| `LightListView.vue:374` | 调 `catIcon()` | 同左 | 轻资产表格视图（17px） | 同上 | |
| `AssetLightAssetsPanel.vue:116` | **写死** | `<AppIcon name="chip" />` | 正式资产详情页「关联轻资产」 | 改为读 `row.category_icon` | **不一致点**：键鼠套装在这里显示芯片 |

### 2.3 直接写死的 Emoji / SVG（按语义归组）

| 图标使用位置 | 当前实现方式 | 当前图标或映射规则 | 对应业务含义 | 建议目标 SVG | 备注 |
|---|---|---|---|---|---|
| **空态插画（`.big` / `.dz-icon`）** | | | | | |
| `AssetListView.vue:972` | 写死 emoji | `📦` | 台账空态 | `package` | |
| `LightListView.vue:450` | 写死 emoji | `📦` | 轻资产空态 | `package` | |
| `PairingView.vue:477` / `:537` / `:551` | 写死 emoji | `🖥️` / `📦` / `🖥` | 配对页三种空态 | `monitor` / `package` | 55px 大图 |
| `InventoryListView.vue:370` | 写死 emoji | `📋` | 盘点列表空态 | `clipboard` | |
| `InventoryDetailView.vue:254` | 写死 emoji | `🔍` | 盘点明细空态 | `search` | |
| `AssetDetailView.vue:217` | 写死 emoji | `🔍` | 资产详情空态 | `search` | |
| `AuditView.vue:445` | 写死 emoji | `📜` | 审计空态 | `scroll` | |
| `ImportView.vue:189` / `:194` | 写死 emoji | `📄` / `📗` | 导入拖放区 | `file` / `sheet` | |
| **统计卡 `.k > span`** | | | | | |
| `AssetListView.vue:223-231` | 数组常量内写死 | `📦` `✅` `🗄️` `🔧` `🎯` | 台账统计卡（全部/在用/在库/维修中/无点位） | `package` `check` `cabinet` `wrench` `target` | **`status` 语义被拆成两份**：与 `STATUS_META` 重复 |
| `PairingView.vue:354-374` | 模板写死 | `🖥️` `🖥` `🔗` `📦` `⚠️` | 配对页 5 张统计卡 | `monitor` `link` `package` `alert` | |
| `InventoryListView.vue:282-294` | 模板写死 | `🔄` `✅` `📦` `⚠️` | 盘点列表统计卡 | `refresh` `check` `package` `alert` | |
| `InventoryDetailView.vue:231-234` | 数组常量内写死 | `📦` `✅` `⚠️` `⏳` | 盘点明细统计卡 | `package` `check` `alert` `clock` | **与 `INVENTORY_RESULT_META` 重复**（后者是 3 项，这里是 4 项含"全部"） |
| `AuditView.vue:352-360` | 模板写死 | `📜` `📅` `⚠️` | 审计统计卡 | `scroll` `calendar` `alert` | |
| `ImportView.vue:287-303`、`:402-414` | 模板写死 | `📄` `✨` `✏️` `➖` `⚠️` | 导入结果统计卡 | `file` `sparkles` `edit` `minus` `alert` | **与 `IMPORT_ACTION_META` 完全重复**（含配色，这里只重复了图标） |
| `InventoryContextBar.vue:104` | 模板写死 | `📋` | 盘点上下文条 | `clipboard` | 配色取自 `inventoryResultMeta().bg/color`，**图标写死** |
| **行内 meta 小图标** | | | | | |
| `AssetListView.vue:774,778,782,793,799` | 模板写死 | `👤` `📍` `🎯` `🛡️` `🔗` | 卡片副信息（使用人/位置/点位/保修/配对） | `user` `pin` `target` `shield` `link` | |
| `AssetListView.vue:807` `:919` | 模板写死 | `⚠️` | 套装状态不一致提示 | `alert` | |
| `AssetListView.vue:822` | 模板写死 | `🖥️` | 套装内显示器行 | `monitor` | |
| `AssetDetailView.vue:250` `:253` | 模板写死 | `🖥` `🔗` | 显示器数 / 挂在某主机 | `monitor` `link` | |
| `AuditView.vue:423` `:425` | 模板写死 | `👤` `🌐` | 操作人 / IP | `user` `globe` | |
| `App.vue:86` `:94` | 模板写死 | `👤` `⚙` | 顶栏操作人 / 设置 | `user` `settings` | |
| `AssetRelationsPanel.vue:100` | 字符串内写死 | `⚙` | 提示文案里的指引 | `settings` 或保留纯文字 | 文案嵌入，非独立图标元素 |
| `InventoryDetailView.vue:298` | 模板写死 | `📷` | 扫码提示 | `camera` | |
| `InventoryDetailView.vue:375` `InventoryContextBar.vue:142` `ImportView.vue:389` `InsightsView.vue:297` | 字符串/模板写死 | `✅` / `✓` | 完成态提示 | `check` | 三种写法（emoji + 字符）不统一 |
| `MapStationPanel.vue:580,584,626,634` | 模板写死 | `⚡` `🟢` `🔴` | 点位快捷盘点 / 正常 / 异常 | `zap` `circle-check` `circle-alert` | **彩色圆点是 emoji 专有表达**，SVG 需实心点方案 |
| `MapStationPanel.vue:615` | 模板写死 | `⚠️` | 盘点后账面已变更 | `alert` | |
| `MapStationPanel.vue:598` | 模板写死 | `🖥️` | 单台资产明细行图标 | `monitor`（或按 `device_type_id`） | **此处拿得到 `device_type_name`，却没用 `typeIcon()`** |
| `utils/workstationMap.ts:49,50` | 常量表写死 | `✓` / `⚠️` | 盘点状态徽标文案 | `check` / `alert` | badge 是**字符串**，需改结构才能插 SVG |
| `utils/issueMeta.ts:31,38,45` | 常量表写死 | `◐` `·` `●` | 问题严重度（待核对/提示/正常） | `circle-dot` 系列或保留几何符号 | **几何符号非 emoji**，跨平台一致，可保留 |
| `utils/issueMeta.ts:58-62` | 常量表 `RULE_ICONS` | `👥` `❓` `📍` `🗑️` `🩹` | 5 条问题规则 | `users` `help` `pin` `trash` `bandage` | 兜底 `•` |
| **已在用的内联 SVG（可作为风格参照）** | | | | | |
| `components/MapStationPanel.vue:493-497` | 内联 `<svg>` | 硬编码 path（柜子） | 点位未选中空态 | 建议迁到 `AppIcon` 成为 `cabinet` | 注释明说"用内联 SVG 不用 emoji，因为 emoji 在精简 Windows 会变豆腐块" |
| `views/MapEditorView.vue:145-148` | 内联 `<svg>` | 硬编码 path（入口） | 地图编辑器引导 | 建议迁到 `AppIcon` | |

### 2.4 状态 / 动作 / 事件 / 审计类图标（机制 ③，非资产类型）

| 图标使用位置 | 当前实现方式 | 当前图标或映射规则 | 对应业务含义 | 建议目标 SVG | 备注 |
|---|---|---|---|---|---|
| `utils/format.ts:103-108` | 常量 `STATUS_META` | **无 icon**，只有 label/color/bg/border | 资产状态 | — | 状态本来就靠色块+文字，暂不必加图标 |
| `utils/format.ts:144-193` | 常量 `OPERATION_META` | `📤` `📥` `🔧` `✅` `🗑️` `♻️` | 6 种状态流转操作 | `upload` `download` `wrench` `check` `trash` `recycle` | 渲染点 `AssetDetailView.vue:274`、`AssetOperationDialog.vue` |
| `utils/format.ts:215-232` | 常量 `EVENT_META` | `✨` `✏️` `🔁` `🔗` `✂️` `📋` `📤` `📥` `🔧` `✅` `🗑️` `♻️` `🔀` `🩹` | 14 类资产变更事件 | `sparkles` `edit` `refresh` `link` `scissors` `clipboard` `upload` `download` `wrench` `check` `trash` `recycle` `shuffle` `bandage` | 渲染点 `AssetTimeline.vue:106`、`:120` |
| `utils/format.ts:263-267` | 常量 `INVENTORY_RESULT_META` | `⏳` `✅` `⚠️` | 盘点结果（未盘/已盘/异常） | `clock` `check` `alert` | 渲染点 `InventoryDetailView.vue:447`、`InventoryContextBar.vue:109` |
| `utils/format.ts:288-307` | 常量 `AUDIT_VISUALS` | 18 项（`✨` `✏️` `🗑️` `🔁` `🔗` `✂️` `🔀` `📥` `📤` `🧭` `📋` `🏁` `↩️` `🏷️`） | 审计动作 | 见左 | 渲染点 `AuditView.vue:402` |
| `utils/format.ts:316-324` | 常量 `IMPORT_ACTION_META` | `✨` `✏️` `➖` `⚠️` | 导入行结果 | `sparkles` `edit` `minus` `alert` | 渲染点 `ImportView.vue:338` |
| `backend/app/models.py:138-145` | 后端常量 | 6 项（与 `OPERATION_META` 图标**不同源**） | 资产操作 | — | **未见前端消费该表**（需进一步确认是否有接口下发） |
| `backend/app/models.py:364-403` | 后端常量 `AuditAction.ICONS` | 38 项 emoji | 审计动作 | — | 经 `GET /api/audit/meta` 下发为 `action_icon`（`routers/audit.py:216`），**前端 `types.ts:437` 有字段但无任何渲染点**——**已确认它是 API 展示协议的一部分，见 §9.2** |

---

## 九、两个待确认项的核实结论（用户点名，已实测）

### 9.1 `MapStationPanel.vue` 两处图标的数据对象

**结论：两处渲染的是同一批资产，应当统一。**

| | `:598`（单台资产盘点明细） | `:659`（绑定资产清单） |
|---|---|---|
| 所在区块 | `v-if="auditMode && mapRow?.assets.length"`（`:589-590`） | `v-if="assets.length"`（`:657`），**任何模式都显示** |
| 遍历对象 | `mapRow.assets`（`:592`） | `assets`（`:658`） |
| 元素类型 | `MapInventoryAsset`（`schemas_workstations.py:243-275`） | `Asset`（props，`:59`） |
| 是否含设备类型 | **含**。`device_type_name`（`:602` 已用它渲染副标题） | **含**。`device_type_name`（`:663` 已用它渲染） |
| 图标当前实现 | **写死 `🖥️`**（`:598`） | `typeIcon(asset.device_type_name)`（`:659`） |
| 是否标注盘点结果 | 是（`row.result` / `row.result_label`） | 仅在盘点模式下追加一行文字（`:668-670`） |

**判定依据**：两者指向的是**同一个业务对象（该点位上挂着的一台真实资产）**，且**都已经在渲染 `device_type_name`**。差异只在"是否带本轮盘点结果"，那是**附加信息**（额外的文字行与按钮），不是图标要表达的不同语义。

- `MapInventoryAsset` 确实缺 `device_type_id`（只有 name），所以 T2.7 要为它补字段；
- 但**即使只用 `device_type_name` 走 legacy 推断**，也应该统一——现在写死 `🖥️` 意味着点位上挂的打印机在盘点明细里显示成电脑，这正是你举的例子。

**处理**：T5.1 改为"统一用 `assetTypeIcon()`"，不再是"待确认"。§附录第 2 项关闭。

### 9.2 后端审计 emoji 是否作为 API 数据返回

**结论：是，且它是当前唯一的"emoji 作为接口展示协议"实例。建议改契约。**

证据链：

| 环节 | 位置 | 事实 |
|---|---|---|
| 定义 | `models.py:364-403` | `AuditAction.ICONS`，38 项，值为 emoji 字符串 |
| 取值 | `models.py:443-445` | `AuditAction.icon(action)` → `cls.ICONS.get(action, "•")` |
| **进入列表接口** | `routers/audit.py:63` | `action_icon=AuditAction.icon(entry.action)` —— **逐条审计记录都带一个 emoji 字段** |
| **进入元数据接口** | `routers/audit.py:216` | `AuditActionOption(value=key, label=..., icon=AuditAction.icon(key))` —— 筛选下拉的 38 个候选也各带一个 emoji |
| Schema 契约 | `schemas.py:569`（`action_icon: str = "•"`）、`schemas.py:588-591`（`AuditActionOption.icon`） | 字段是**响应模型的正式成员**，不是可选装饰 |
| 前端类型 | `types.ts:437` | `action_icon: string` 已声明 |
| 前端消费 | **无任何渲染点** | 前端审计页用的是自己的 `AUDIT_VISUALS`（`format.ts:288-307`，18 项） |

**所以现在的状态是**：后端把 emoji 当成展示协议往外发，前端**收下但不用**（因为它自己另有一套 18 项的映射，且只覆盖一半动作）。这是典型的"接口里带着一份永远不会被渲染的展示数据"。

**建议改法**（符合你说的"后端返回稳定标识、前端统一映射 SVG"）：

1. 后端 `AuditAction.ICONS` 的 38 个 emoji **改为图标标识**（`'sparkles'` / `'link'` / `'trash'` …），字段语义从"图标字符"变成"图标名"；
2. 字段改名以体现契约变化：`action_icon` → **`action_icon_name`**（`AuditActionOption.icon` → `icon_name`）。改名而非只改值，能让任何遗漏的消费点直接编译报错，而不是静默拿到一个不再是 emoji 的字符串；
3. 前端**删掉 `AUDIT_VISUALS`**，改用 `action_icon_name` 走 `AppIcon` —— 一处定义、38 项全覆盖（当前前端只认 18 项，另外 20 项掉到 `•`）。

**其余后端 emoji 已排查，均不在接口层**：

| 位置 | 内容 | 是否进 API | 说明 |
|---|---|---|---|
| `models.py:138-145`（`AssetOperation.ICONS`，6 项） | 📤📥🔧✅🗑️♻️ | **否** | **无任何消费点**（`rg` 全仓 `AssetOperation.ICONS` / `.icon(` 均无命中）。是死代码，建议删除 |
| `services/link_suggestions.py:16-19` | ✅❌ | **否** | 只出现在模块 docstring 的 markdown 表格里，是**注释** |
| `services/ownership.py:30-32` | ✅❌ | **否** | 同上，docstring 里的说明 |
| `routers/`、`schemas*.py` | — | **否** | 全目录扫描无 emoji（`action_icon` 的值例外，来自 `models.ICONS`） |

因此**后端只需要改 `AuditAction.ICONS` 这一处**（38 项值 + 字段改名），其余三处要么删、要么本就是注释。

---

## 十、改造范围的两层划分（按用户确认的口径）

**核心原则**：**需要"可维护"的加字段；只需要"统一展示"的走代码映射。** 不把每个按钮、动作、状态都变成可维护的数据配置。

### 10.1 第一类：需要可维护的图标配置（加数据库字段）

| 对象 | 承载方式 | 维护入口 | 理由 |
|---|---|---|---|
| 主资产**设备类型** | `device_types.icon` | 现有「设备类型与类别」弹窗 | 类型可自定义增删改，用户会自建"NAS 存储""工控机"这类类型，图标必须能跟着配 |
| 轻资产**品类** | `light_categories.icon` | 现有轻资产品类表单 | 品类也是用户自建的（现在库里 2 个，实际会持续增加） |

**只有这两个对象加字段。** 它们是用户会持续新增的业务对象，图标配不上就会长期错。

### 10.2 第二类：只需统一展示的图标（不动数据库，走代码映射）

| 对象 | 位置 | 承载方式 | 是否加字段 |
|---|---|---|---|
| 资产状态 | `format.ts:103-108` `STATUS_META` | `STATUS_META.icon` 补图标名（现无 icon） | **否** |
| 状态流转操作 | `format.ts:144-193` `OPERATION_META` | 值改为图标名 | **否** |
| 资产变更事件 | `format.ts:215-232` `EVENT_META` | 值改为图标名 | **否** |
| 盘点结果 | `format.ts:263-267` `INVENTORY_RESULT_META` | 值改为图标名 | **否** |
| 审计动作 | **后端** `AuditAction.ICONS` → 前端 `action_icon_name` | 后端值改为图标标识，前端删掉本地重复表 | **否**（不改表结构，只改常量与契约字段名） |
| 导入结果 | `format.ts:316-324` `IMPORT_ACTION_META` | 值改为图标名 | **否** |
| 问题规则 / 严重度 | `utils/issueMeta.ts:58-62`、`:31/38/45` | 值改为图标名；几何符号 `◐ · ●` 保留 | **否** |
| 导航 / 空态 / 统计卡 / 行内提示 | 约 15 个 `.vue` 内的写死 emoji | 直接换成 `<AppIcon>` | **否** |

**这类的共同点**：它们是**代码里枚举的固定集合**（状态 4 种、操作 6 种、事件 14 种…），不会由用户新增。图标跟着枚举走、改一行常量即可，做成数据配置反而是过度设计。

**这一步把"全站 SVG 化"和"全站 emoji 清零"真正打通**——原方案 §8 的 T4 + T5 就是这一类的完整落地，本次明确其性质为**"统一展示层"，不产生任何数据库改动**。


## 三、主资产图标映射表

### 3.1 设备类型与设备类别的实际关系（已核实）

| 问题 | 结论 | 证据 |
|---|---|---|
| 分别对应什么模型 | 设备类型 = `DeviceType` 表；设备类别 = `DeviceType.category` 上的一个**字符串枚举**，**没有独立表** | `models.py:490-508`、`models.py:88-113` |
| 字段 | `DeviceType`: `id / name / sort_order / category / created_at`；枚举值 `host` / `display` / `other` | `models.py:496-503` |
| 怎么关联 | **类别是类型的属性**，多对一：一个类别下可挂多个类型，一个类型只能属于一个类别 | `DeviceType.category` 单列存储 |
| 是否存在"一个类别含多个类型" | **是**。库中 `other` 含 7 个类型（笔记本/键盘/鼠标/打印机/路由器/扫描仪/一体机/家具/装饰摆件） | 见 §1.3 实查 |
| 由哪个页面维护 | **同一个弹窗** `DeviceTypeDialog.vue`，从台账页"设备类型"按钮打开 | `AssetListView.vue:620,1005-1008` |
| 类型能否有稳定 ID | **能**。`device_types.id` 自增主键，`assets.device_type_id` 外键 `ondelete=RESTRICT` | `models.py:524-525` |
| 类别是否可靠 | **不完全可靠**：`seed.py:39` 把"笔记本"写成 `HOST`，库里实际是 `other` —— 用户会改 | §1.3 实查 |
| 类别怎么得默认值 | 后端 `DeviceCategory.guess()`（`models.py:104-113`）与前端 `guessCategory()`（`DeviceTypeDialog.vue:52-57`）**各写了一份相同规则** | 两处需同步维护 |

> **结论**：不需要引入任何新分类体系。图标直接挂在 `DeviceType.category` 旁边（同一行、同一弹窗），与现有"类别"下拉并列即可。

### 3.2 现有设备类型 → 建议 SVG 映射

> **AppIcon 复用**列：✓ = 现有 25 个图标里已有，直接用；新增 = 需在 `AppIcon.vue` 的 `PATHS` 里补一条 path。

| 库中 id | 类型名 | 类别 | 资产数 | 当前 Emoji | 建议 SVG 名 | AppIcon 复用 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 主机 | host | 60 | `🗄️` | `tower` | 新增 | 立式机箱。**必须换**：`🗄️` 是"文件柜"，且该码点默认文本呈现（见 §3.3） |
| — | 台式机（表内有，库中无） | — | 0 | `🗄️` | `tower` | 新增 | 与主机同一个图形即可 |
| 2 | 笔记本 | other | 7 | `💻` | `laptop` | 新增 | |
| 10 | 一体机 | other | 3 | `🖳` | `aio` | 新增 | **高风险码点**，见 §3.3；也可与 `monitor` 合并 |
| — | 服务器（表内有，库中无） | — | 0 | `🖧` | `server` | 新增 | **高风险码点**，见 §3.3 |
| 3 | 显示器 | display | 55 | `🖥️` | `monitor` | 新增（**不要复用 `screen`**） | `screen` 已被 `holderIconName()` 占用表示"正式资产"，语义会撞车（见 §5 第 11 项） |
| 9 | 电视 | display | 6 | `📺` | `tv` | 新增 | |
| — | 投影仪（表内有） | — | 0 | `📽️` | `projector` | 新增 | |
| 4 | 键盘 | other | 0 | `⌨️` | `keyboard` | ✓ **复用** | |
| 5 | 鼠标 | other | 0 | `🖱️` | `mouse` | 新增 | 轻资产也要用，做一份即可 |
| 6 | 打印机 | other | 1 | `🖨️` | `printer` | 新增 | |
| 8 | 扫描仪 | other | 1 | `📠` | `scanner` | 新增 | 当前 emoji 是"传真机"，形似但不是扫描仪 |
| — | 摄像头（表内有） | — | 0 | `📷` | `camera` | 新增 | |
| — | 音箱（表内有） | — | 0 | `🔊` | `speaker` | 新增 | |
| 7 | 路由器 | other | 1 | `📶` | `router` | 新增 | 当前是"信号强度条"，不是设备形状 |
| — | 交换机（表内有） | — | 0 | `📡` | `switch` / `network` | 新增 | 当前是"卫星天线"，**无语义关联**，必须换 |
| — | 手机（表内有） | — | 0 | `📱` | `smartphone` | 新增 | |
| — | 平板（表内有） | — | 0 | `📱` | `tablet` | 新增 | **与手机共用同一个 emoji**，无法区分，本次顺带拆开 |
| 11 | 家具 | other | 15 | `🪑` | `chair` | 新增 | 按你的要求**不拆子类型**；若将来出现"会议桌/文件柜"，一张椅子概括不了，届时应新增图标而不是拆类型 |
| 12 | 装饰摆件 | other | 1 | `🖼️` | `frame` | 新增 | |
| — | （套装聚合 `kind='bundle'`） | — | — | `🧩`（`AssetListView.vue:753` 写死） | `box` | ✓ **复用** | 或新增 `layers` 表示"一组的门面" |
| — | 名称/关键字都没命中 | — | — | `📦` | `box` | ✓ **复用** | 兜底，保持不变 |

**建议新增图标共 17 个**（`tower, laptop, aio, server, monitor, tv, projector, mouse, printer, scanner, camera, speaker, router, switch, smartphone, tablet, chair, frame` 中减去可合并项）。全部走 Lucide 的 24×24 / stroke 1.7 风格，与现有 `AppIcon` 参数一致。

### 3.3 必须标记的风险项：两个"看着最贴切、实际最不稳"的码点

本轮实测了各 emoji 码点的呈现属性（`.box-agent-scratch/emoji_pres.py`，只读检测）：

| Emoji | Unicode 名 | Emoji_Presentation | 含义 |
|---|---|---|---|
| `🗄️` 主机 | FILE CABINET | **No** | 默认**文本**呈现，靠 `FE0F` 才变彩色 |
| `🖳` 一体机 | OLD PERSONAL COMPUTER | **No** | 同上，且属冷门码点 |
| `🖧` 服务器 | THREE NETWORKED COMPUTERS | **No** | 同上，已废弃风格 |
| `🖥️` 显示器 | DESKTOP COMPUTER | **No** | 同上 |
| `⌨️` 键盘 | KEYBOARD | **No** | 同上 |
| `🖱️` 鼠标 | THREE BUTTON MOUSE | **No** | 同上 |
| `🖨️` 打印机 | PRINTER | **No** | 同上 |
| `📽️` 投影仪 | FILM PROJECTOR | **No** | 同上 |
| `🖼️` 装饰摆件 | FRAME WITH PICTURE | **No** | 同上 |
| `💻` `📺` `📠` `📷` `🔊` `📶` `📡` `📱` `🪑` `📦` | — | **Yes** | 自带彩色呈现 |

**这意味着**：设备类型图标里**过半数**（主机/一体机/服务器/显示器/投影仪/键盘/鼠标/打印机/装饰摆件）都依赖那个**不可见的 `FE0F` 变体选择符**才显示为彩色。历史上这道防线已经破过一次（`format.ts:12-17` 记录：主机与显示器原为同一码点，Chromium fallback 到不同字体，渲染结果整体错位）。同一份代码在不同字体回退路径下会呈现为灰阶线稿或豆腐块。

> ⚠️ **未在本机 Windows Chromium 做实机截图验证**（本轮为只读代码分析，未启动浏览器）。上述为码点属性 + 代码注释证据，实际渲染需在 T1 验收时用真实浏览器逐类型核对。

---

## 四、轻资产图标映射表

### 4.1 现有品类 → 建议 SVG

| 库中 id | 品类名 | 品牌 / 规格 | 当前图标 | 当前怎么来的 | 建议 SVG 名 | AppIcon 复用 | 备注 |
|---|---|---|---|---|---|---|---|
| 1 | 罗技MK270键鼠套装 | 罗技 / 2.4G无线 | `keyboard` | `catIcon()` 命中 `/键鼠\|键盘\|鼠标\|套装/` | `keyboard`（或新增 `keyboard-mouse`） | ✓ 复用 / 新增可选 | 语义是"键鼠**套装**"，键盘图标只表达了其中一半；若你希望精确表达，新增一个"键盘+鼠标"组合图标 |
| 2 | M.2固态 宏碁N3500 SSD | 宏碁 / 256GB | `chip` | `catIcon()` 命中 `/ssd\|固态\|.../` | `chip` | ✓ **复用** | 表达准确，无需改 |
| — | 任何未配置图标 / 新品类 | — | `box` | `catIcon()` 兜底 | `box` | ✓ **复用** | 改为 `icon` 字段为空时的兜底 |

### 4.2 轻资产品类可选图标清单（精简版，只列够用的）

用户要求"不为追求覆盖率提前设计大量用不到的图标"。按现有 2 个品类 + 已知会出现的配件，建议：

| 图标名 | 用途 | 来源 | 优先级 |
|---|---|---|---|
| `keyboard` | 键盘 / 键鼠套装 | 现有 | 必须 |
| `mouse` | 鼠标（与主资产共用） | 新增 | 必须 |
| `chip` | SSD / 内存 / 芯片 | 现有 | 必须 |
| `box` | 兜底（"说不清是什么的配件"） | 现有 | 必须 |
| `cable` | 线材 / 转接头 | 新增 | 建议 |
| `headset` | 耳机 | 新增 | 建议 |
| `dock` | 扩展坞 / 底座 | 新增 | 可选 |
| `keyboard-mouse` | 键鼠套装专用 | 新增 | 可选 |
| `battery` / `adapter` / `stand` / `bag` | 其他配件 | 新增 | **暂不建**（等真出现该品类再加） |

> 图标清单**只是选择器里的候选集合**，不是数据模型——加一个图标 = 在 `AppIcon.vue` 加一条 path + 在候选常量里加一行，不需要动数据库。

---

## 五、缺失及不一致清单

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | **同一轻资产品类在不同页面图标不同** | `LightListView.vue:291` 用 `catIcon()`（键鼠→键盘）vs `AssetLightAssetsPanel.vue:116` 写死 `chip`（键鼠→芯片） | 用户直接可见的矛盾 |
| 2 | **品类图标在多数位置缺失** | `MapStationPanel.vue:722-736`、`LightCategoryView.vue` 整个详情页都不显示品类图标 | 同一对象四处三种表现（键盘/芯片/无） |
| 3 | **审计动作图标存了两份** | 后端 `models.py:364-403`（38 项）vs 前端 `format.ts:288-307`（18 项） | 前端只认 18 项，后端 11 个动作（`relocation_apply`、`asset_linkage`、`asset_unlink`、`relation_exception_*`、`workstation_*`、`map_*`、`light_*`）掉到兜底 `•` |
| 4 | **资产状态图标在统计卡里被重新写了一遍** | `format.ts:103-108`（无 icon）vs `AssetListView.vue:223-226`（`📦✅🗄️🔧`） | 台账统计卡与行内状态胶囊视觉语言不同 |
| 5 | **盘点结果图标存了两份** | `format.ts:263-267`（3 项）vs `InventoryDetailView.vue:231-234`（4 项，含"全部"） | "全部"没有对应 meta，只能写死 |
| 6 | **导入结果图标存了两份** | `format.ts:316-324`（含配色）vs `ImportView.vue:287-303`、`:402-414`（只重复图标） | 配色走 meta、图标写死，改一处漏一处 |
| 7 | **`CATEGORY_META.icon` 是死代码** | `format.ts:117-122` 定义了 3 个 emoji，唯一调用只取 `.label`（`DeviceTypeDialog.vue:119`） | 误导后来者以为类别有图标 |
| 8 | **"完成"态三种写法** | `✅`（emoji，多处）/ `✓`（字符，`ImportView.vue:389`、`InsightsView.vue:297`）/ `check`（SVG `AppIcon`） | 同一语义三套呈现 |
| 9 | **同页内图标体系混用** | `MapStationPanel.vue`：493-497 用内联 SVG，580/598/615 用 emoji | 页面内不一致 |
| 10 | **点位资产行本可按类型取图标却写死** | `MapStationPanel.vue:598` 写死 `🖥️`，同文件 `:659` 却用 `typeIcon(asset.device_type_name)` | 同一面板两种口径（需进一步确认是否刻意区分"单台盘点明细"与"资产清单"两个区块） |
| 11 | **`screen` 这个图标名语义已被占用** | `utils/light.ts:151`：`case 'asset': return 'screen'`，表示持有方是"正式资产" | 若把显示器类型也映射成 `screen`，两处语义撞车，必须新增 `monitor` |
| 12 | **品牌/规格里出现设备词会误判品类** | `LightListView.vue:129` 把 `row.spec` 也拼进正则输入 | 规格含"套装"二字（如"3件套装"）会被判成键盘图标 |
| 13 | **导入自动创建的类型没有任何图标线索** | `services/importer.py:546`：`DT(name=name, sort_order=..., category=DeviceCategory.guess(name))` | 导入产生的类型只猜类别、不猜图标；新类型需要默认图标兜底，否则全变盒子 |

---

## 六、最小改造方案

### 6.0 总体判断：不需要为图标引入任何新业务模型

| 方案 | 做法 | 改动面 | 结论 |
|---|---|---|---|
| A. 纯前端映射表 | 把 `format.ts` 里 `TYPE_ICONS` 的 emoji 值换成 SVG 名，`typeIcon()` 改返回图标名 | 1 个文件 + 各渲染点换成 `<AppIcon>` | **不做**：图标仍不可维护，用户改名仍丢图标，不满足"可维护、可选择" |
| B. **复用现有业务表加一列 icon**（推荐） | `device_types` 加 `icon`、`light_categories` 加 `icon`；前端统一走 `AppIcon` | 后端：2 个模型 + 2 组 Schema + **无新接口**；前端：`AppIcon` 扩图标 + 2 个表单 + 各渲染点 | **采用**：可维护、可选择、有兜底、挂在稳定 id 上 |
| C. 独立图标管理后台 | 新建图标表 + 管理页 | 新表、新接口、新页面 | **不做**：与"不为图标单独建设复杂后台"的约束冲突 |

**核心取舍**：图标**不是一个新概念，而是现有业务对象的展示属性**——就像 `sort_order` 是设备类型的属性、`unit` 是轻资产品类的属性一样。它存在已有的表、已有的维护弹窗、已有的表单里，不新增表、不新增权限、不新增页面。

### 6.1 主资产设备类型的图标配置放在哪里

**放在 `device_types` 表上，与 `category` 并列；维护入口就是现有的「设备类型与类别」弹窗。**

1. 该弹窗已经是"设备类型的唯一维护处"（`AssetListView.vue:1005`），加一列图标不改变用户心智模型；
2. 图标只用 `device_types.id` 做 key，**与名称解耦**——用户把"主机"改名成"工控机"，图标不会丢。这正是现有 `category` 字段采用的同一思路，`routers/device_types.py:3-7` 写明："类别不按名字自动判断 —— 用户完全可以把「主机」这个类型改名成「工控机」，名字一改，靠名字猜的逻辑就全废了。"
3. **不需要新增接口**：`PUT /api/device-types/{id}` 已经是"重命名 / 调整类别 / 排序"的更新口，图标只是第 4 个可更新字段。

**与类别的关系**：
- 类别（host/display/other）**继续只承担配对语义**，不承担图标语义。`CATEGORY_META.icon` 那三个死 emoji 建议删除，避免"类别也能配图标"的误导。
- 类别提供**默认图标**：新建类型时若用户没选图标，按类别给默认值（host → `tower`、display → `monitor`、other → `box`）。**这只是默认值，不是规则**，用户随时可覆盖。

**为什么不继续用"名称推断图标"**：`typeIcon()` 的关键字表已经是这个思路，问题在于用户改个名字图标就变（"NAS 存储"→"阵列柜"就掉到 `📦`）、同一关键字命中多个类型时无法区分（"手机"和"平板"共用 `📱`）、新类型默认没有图标。保留关键字表**仅作为历史数据的初始化依据**（见 6.5），不作为长期运行时逻辑。

### 6.2 轻资产品类如何独立维护图标

**在 `light_categories` 表上加 `icon` 列；创建/编辑表单里加图标选择器。**

- 每个品类独立选择图标，**彻底删掉 `catIcon()` 正则函数**（`LightListView.vue:127-133`）。
- 选择器放在 `LightCategoryDialog.vue` 现有表单里（该表单是 `label-position="top"` 布局），作为一个 `el-form-item`，内容是 `AppIcon` 网格。
- 候选集合为 §4.2 那份精简清单，**按用途分组**（键鼠类 / 存储类 / 线材类 / 其他），不分组会让 8~17 个图标平铺显得随机。

### 6.3 复用 AppIcon 作为统一图标标识层

**直接复用，只做三件事：**

1. **扩充 `PATHS`**：按 §3.2 / §4.2 新增约 20 条 24×24 path，`stroke-width` 由组件统一给（1.7），不逐条写。
2. **给出图标名的 TS 类型与候选清单**：`types.ts` 加 `IconName` 联合类型；新增 `utils/icons.ts` 导出 `TYPE_ICON_CHOICES` / `LIGHT_ICON_CHOICES` 分组候选，供两个选择器使用。
3. **保持 `PATHS[name] ?? PATHS.box` 兜底不变**（`AppIcon.vue:68`）——这是"库里出现未知图标标识"时的最后一道防线。

**数据库只存标识**：`icon VARCHAR(32)`，存 `'keyboard'` 这样的字符串，**不存 SVG 源码**。与你"数据库保存稳定的图标标识"的要求一致，也让图标改样式时不必动库。

### 6.4 如何保证全站同一业务对象的图标一致

**把"取图标"收敛成三个纯函数，所有渲染点只准调它们，不准自己判断。**

| 函数 | 位置 | 输入 | 输出 | 替代掉 |
|---|---|---|---|---|
| `assetTypeIcon(deviceType)` | `utils/format.ts`（改造现有 `typeIcon()`） | `DeviceType`（含 `id/name/category/icon`） | 图标标识 | 现有 `typeIcon(name)` 的 10 个调用点 |
| `lightCategoryIcon(category)` | `utils/light.ts` | `LightCategory`（含 `icon`） | 图标标识 | `catIcon()`（删除）、`AssetLightAssetsPanel.vue:116` 写死的 `chip` |
| `holderIconName(kind)` | `utils/light.ts`（已存在，`light.ts:144`） | 持有方 kind | 图标标识 | 保持不变 |

**必须迁移的三个关键渲染点**（决定"不一致"是否真被消除）：
1. `LightListView.vue:291`、`:374` → `lightCategoryIcon(row)`
2. `AssetLightAssetsPanel.vue:116` → `lightCategoryIcon(row)`（**修复用户实际报障的关键**）
3. `MapStationPanel.vue:722-736`（点位侧栏轻资产区）→ **新增** `lightCategoryIcon(h)`

**图标标识从哪来**：
- `HoldingOut`（`schemas_light.py:86-107`）已带 `category_id / category_name / category_brand / category_spec / category_unit / category_label`，**再加一个 `category_icon` 即可**，前端不用额外请求。这符合该 Schema 自己的注释精神："品类永远在，所以是接口的责任，不能指望调用方自己去 join。"
- `AssetOut`（`schemas.py:162-171`）已带 `device_type_id / device_type_name / device_category / device_type`，**再加 `device_type_icon`**；或扩 `DeviceTypeBrief`（更干净但影响面更大，见 §7 待确认项）。
- 多数页面已在调 `metaApi.filters()` 拿 `device_types`（`AssetListView.vue:305`、`AssetDetailView.vue:134`、`LabelPrintView.vue:114`、`SpaceMapView.vue:535`），`meta.py:73-83` 加一个字段就能覆盖大部分场景。
- 但 **`PairingView` 走 `pairingApi.board()`、`InventoryDetailView` 走盘点接口**，它们的 `AssetBrief` / `MapInventoryAsset` 只有 `device_type_name` 与 `device_type_id`（`schemas.py:151-152`、`schemas_workstations.py:249`），需各自补字段，或前端补一次 meta 查表。

### 6.5 历史数据与未配置图标如何兜底

> **本节规则已按用户确认口径固定，不再是待定项。**

**三层兜底 + 一条硬边界：**

| 层 | `icon` 配置值 | 页面行为 | 实现位置 |
|---|---|---|---|
| ① | **已保存图标** | 显示用户选择的 SVG（**唯一"已确认"的配置**） | 正常路径 |
| ② | **未配置，但旧规则可推断** | 显示**推断图标**；**仅在维护界面**标记为"未确认"（如弹窗里加"· 建议，未保存"） | 取图标函数内分层 |
| ③ | **未配置，且无法推断** | 显示通用 `box` | `AppIcon.vue:68` 已有 |

**硬边界（用户明确要求，必须遵守）：**

> **不能因为显示了推断图标，就把它当作已经保存的配置。推断结果只存在于展示逻辑中，绝不自动写回数据库。**

具体约束：
- 取值函数**只读不写**——它没有能力、也没有途径触发 `UPDATE`；
- ② 层的"未确认"标记**只在维护界面出现**（`DeviceTypeDialog.vue` / `LightCategoryDialog.vue`），列表、详情、地图等**日常页面不加任何标记**，避免把技术状态泄漏成用户可见的噪音；
- 不提供任何自动/隐式的写库时机（不在启动时、不在保存其他字段时、不在导入时顺带写入）。

**因此不需要任何初始化回填**：
- **不做**一次性 SQL 回填；
- **不做** `ensure_schema()` 启动回填；
- 只提供**显式的**"一键套用建议图标"按钮（批量 `PUT`）——用户点了才写库，写了才算配置。

**导入自动创建的类型**（`services/importer.py:546`）：`icon` 留空即可，自动走 ② 层推断。**导入流程不增加图标选择步骤，也不为导入写入任何图标值。** 用户日后打开「设备类型与类别」弹窗时，会看到这些类型的建议图标，确认后再保存。

**为什么不用"关键字表 + 正则"作为长期方案**：它保留下来**只作为 ② 层的推断器**（历史数据与自建类型的过渡），新配置一律走 `icon` 字段。等用户把常用类型都确认一遍后，② 层自然退化为极少触发的兜底。


### 6.6 哪些改动涉及数据库，哪些只涉及前端

| 层 | 改动 | 涉及数据库 |
|---|---|---|
| **后端模型** | `DeviceType` 加 `icon: Mapped[str \| None]`（`String(32)`, nullable）；`LightCategory` 同样加一列 | **是**（2 列） |
| **后端迁移** | 在 `database.py::ensure_schema()` 里按现有 `_add_category_column()`（`database.py:213-235`）的模式加两处幂等 `ALTER TABLE`，不引入 Alembic | **是**（DDL） |
| **后端 Schema** | `DeviceTypeCreate/Update/Out`、`LightCategoryCreate/Update/Out`、`HoldingOut`、`AssetOut`/`AssetBrief` 加 `icon` / `*_icon`；`meta.py` 的 `DeviceTypeOut` 组装加一行 | 否 |
| **后端接口** | **无新增端点**。`PUT /api/device-types/{id}`（`routers/device_types.py:92-104`）与 `PUT /api/light/categories/{id}`（`routers/light.py:223-236`）都是 `for key, value in data.items()` 式的泛化写入，加字段自动生效 | 否 |
| **前端资源层** | `AppIcon.vue` 扩约 20 条 path；新增 `utils/icons.ts`；`types.ts` 加 `IconName` | 否 |
| **前端组件** | `DeviceTypeDialog.vue` 加图标列 + 选择器；`LightCategoryDialog.vue` 加图标选择器；约 15 个渲染点把 emoji 换成 `<AppIcon>` | 否 |
| **前端函数** | `typeIcon()` 改造、新增 `lightCategoryIcon()`、删除 `catIcon()`、删除 `CATEGORY_META.icon` | 否 |

**两处后端改动可直接照抄的写法先例**（`database.py:213-235`）：

```python
def _add_category_column(conn) -> bool:
    if "category" in _existing_columns(conn, "device_types"):
        return False
    conn.exec_driver_sql(
        "ALTER TABLE device_types ADD COLUMN category VARCHAR(16) NOT NULL DEFAULT 'other'"
    )
    ...
```

`icon` 全部 nullable，**不需要回填 DDL**，比 `category` 那一次更简单。

### 6.7 推荐的实施顺序

分 5 个阶段，**每阶段可独立验收、可独立回滚，且前端先行不影响后端**。详细任务拆分见 §8。

| 阶段 | 内容 | 涉及数据库 | 可独立交付 |
|---|---|---|---|
| **T1** | 图标资源层（`AppIcon` 扩图标 + 候选清单 + 类型） | 否 | 是 |
| **T2** | 主资产设备类型图标 | **是** | 是 |
| **T3** | 轻资产品类图标 | **是** | 是 |
| **T4** | 状态/动作/事件等非类型图标收敛 | 否 | 可与 T2/T3 并行 |
| **T5** | 硬编码与空态清理 | 否 | 是 |

---

## 七、风险与验收标准

### 7.1 兼容性风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| **Edge / iOS Safari 渲染** | 本轮**未做实机验证**。SVG `stroke-linecap="round"` + `currentColor` 在 Edge(Chromium) 与 iOS Safari 都支持，风险低；但 `stroke-width: 1.7` 在 1x DPR 下可能偏细 | T1 阶段在真实 Chrome + Edge + iOS Safari 各核对一次；必要时按尺寸微调 |
| **老库升级** | `ensure_schema()` 已有三处幂等补列先例（`category` / `assets.workstation_id` / `workstations.floor_map_id`），且 `icon` 全部 nullable | 照抄现有模式；验收点：拿一份**改造前的库副本**启动，确认补列成功且界面正常 |
| **前端缓存导致图标名不匹配** | 浏览器可能缓存旧 JS，出现"新图标名 + 旧 PATHS" | `PATHS[name] ?? PATHS.box` 兜底已有（`AppIcon.vue:68`），最差显示盒子，不会崩 |
| **视觉重量落差** | emoji 是彩色实心块，SVG 是细描边，替换后页面观感整体变"轻" | 这是预期效果，但要在 T1 用真实页面截图确认没变得"太空"；`.type-badge` 现有背景 + 边框（`styles/main.css:476-486`）能撑住 |
| **彩色语义丢失** | `MapStationPanel.vue:626/634` 的 🟢/🔴 是"正常/异常"的颜色语义 | 换成图标时必须同时传语义色，不能只用灰色描边 |

### 7.2 历史数据风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 无 `icon` 的行 | 12 个设备类型 + 2 个品类**全部**没有图标 | 三层兜底（6.5）；**这是默认路径，不是异常路径** |
| 名称推断不准确 | 关键字表 / 正则本身会猜错（§5 第 12、13 项） | 推断结果只在界面显示为"建议"，不写库 |
| `DeviceCategory.guess()` 前后端两份 | `models.py:104-113` 与 `DeviceTypeDialog.vue:52-57` 规则相同但分开维护 | 与本任务无直接关系，**建议不在本次处理**，避免范围蔓延 |
| 导入自动创建的类型 | `importer.py:546` 只猜类别 | `icon` 留空走兜底，无需改代码 |

### 7.3 不得触碰的业务红线（验收时逐条确认）

本次改造**不得**导致以下任何一项变化：

1. 资产流转（领用 / 归还 / 送修 / 报废）行为；
2. 统计口径（`/api/meta` 的各类计数、`device_type_counts`）；
3. 点位关系（配对、点位编号、`workstation_id`）；
4. 轻资产数量逻辑（库存 / 已分配 / 净存在，全部由 `light_holdings` 现算）；
5. 资产导入导出列（`ASSET_COLUMNS`，`services/importer.py:57-73` 不含 icon）；
6. 审计日志内容（图标不进 summary / detail）。

**验证方式**：改造前后各导出一次轻资产台账 + 资产台账 + 审计日志，**逐字节比对除图标外的内容**。

### 7.4 建议的浏览器验收清单

| # | 页面 | 检查点 |
|---|---|---|
| 1 | 资产台账（卡片 / 表格） | 12 种类型图标正确；套装行显示 `box`；空态图标 |
| 2 | 资产详情 | hero 类型图标、显示器数 / 挂载主机、操作按钮图标、关联轻资产面板（**键鼠套装必须是键盘图标**） |
| 3 | 配对管理 | 5 张统计卡、主机 / 显示器列表、3 种空态 |
| 4 | 空间地图 + 点位侧栏 | 未选中 SVG、单台资产行、轻资产区（**本次新增品类图标**）、快捷盘点按钮、🟢/🔴 语义色 |
| 5 | 轻资产台账（卡片 / 表格） | 2 个品类图标、统计卡 4 个、空态 |
| 6 | 轻资产品类详情 | 两个页签、持有方图标、流水动作图标、空态 |
| 7 | 盘点（列表 / 明细 / 上下文条） | 统计卡、结果图标、扫码提示、完成提示 |
| 8 | 批量导入 | 3 条提示、拖放区、两处结果统计卡 |
| 9 | 审计日志 | 38 种动作图标全覆盖、统计卡、空态 |
| 10 | 标签打印 | 选择列表类型图标、标签本体 |
| 11 | 顶栏 | 操作人、设置 |

---

## 八、实施任务拆分

> **实施状态（2026-10-19）**：**T1 已完成 ✅**、**T2 已完成 ✅**。T3–T5 待继续。
> 完成项的验收证据见 `CHANGELOG.md` 的「未发布 — 图标体系统一（T1 + T2）」一节。
> 与 T2/T3 相关的两个待确认项已核实并关闭，见 §9。

> 顺序即依赖顺序。每个任务末尾的【验】为该任务的验收判据。
> **范围口径见 §10**：T2/T3 属于"需要可维护"（加字段）；T4/T5 属于"只需统一展示"（**不动数据库**）。

### T1 图标资源层（纯前端 · 可独立交付）

- **T1.1** 扩充 `AppIcon.vue::PATHS`：新增 `tower, laptop, aio, server, monitor, tv, projector, mouse, printer, scanner, camera, speaker, router, switch, smartphone, tablet, chair, frame, cable, headset`（按 §3.2 / §4.2）。保持 `viewBox="0 0 24 24"`，stroke 由组件统一给。
  【验】逐个渲染核形状与语义；`PATHS` 无空 path。
- **T1.2** 新增 `frontend/src/utils/icons.ts`：导出 `TYPE_ICON_CHOICES` / `LIGHT_ICON_CHOICES`（分组）与 `IconName` 类型。
  【验】`npm run type-check` 通过；选择器可遍历该常量渲染。
- **T1.3** 图标预览（临时页，可放 `lab/` 下，参照既有 `lab/vgpu` 做法）。
  【验】真实浏览器三端（Chrome / Edge / iOS Safari）核对，无糊、无空白、粗细一致。

### T2 主资产设备类型图标（后端 + 前端）

- **T2.1** `models.py::DeviceType` 加 `icon: Mapped[str | None]`（`String(32)`, nullable=True）。
  【验】新建库 `create_all` 后列存在。
- **T2.2** `database.py` 加 `_add_icon_column(conn, "device_types")` 并接入 `ensure_schema()`（照抄 `_add_category_column` 模式，幂等）。
  【验】拿改造前的库副本启动 → 补列成功；再启动一次 → 不重复 ALTER。
- **T2.3** `schemas.py`：`DeviceTypeCreate/Update/Out` 加 `icon: Optional[str]`（`max_length=32`）。
  【验】`POST/PUT /api/device-types` 能写入与读回；不传 icon 不报错。
- **T2.4** `DeviceTypeDialog.vue`：类型行加图标列（显示当前或推断图标 + "未保存"标记）、新增行加图标选择器、加"套用建议图标"按钮。`deviceTypeApi.create/update` payload 类型加 `icon`（`api/index.ts:166-174`）。
  【验】选图标 → 保存 → 重开弹窗图标仍在；`asset_count` 等原有列不受影响。
- **T2.5** `format.ts::typeIcon()` 改造为分层：`icon` 字段 → 旧关键字表 → `box`；旧表保留为 legacy 推断器并加注释说明用途。
  【验】用库里 12 个类型名逐一调用，改造前后结果"语义等价"（不会突然全变盒子）。
- **T2.6** 10 个 `typeIcon()` 渲染点改为 `<AppIcon :name="assetTypeIcon(...)" />`：`AssetListView:753`、`AssetDetailView:235`、`PairingView:438/467/505`、`LabelPrintView:243`、`InventoryDetailView:403`、`AssetRelationsPanel:179/221`、`MapStationPanel:659`。
  【验】§7.4 表 1/2/3/5/7/10 中涉及设备类型的行全部正确；`.type-badge` 的 40px / 52px 布局不破。
- **T2.7** 补充数据链路：`AssetOut` / `AssetBrief` / `MapInventoryAsset` / `meta.py` 的 `DeviceTypeOut` 视需要补图标字段。
  【验】6 个调用页面都能拿到图标标识，前端无需额外请求。

### T3 轻资产品类图标（后端 + 前端）

- **T3.1** `models.py::LightCategory` 加 `icon: Mapped[str | None]`（`String(32)`, nullable）。
  【验】新建库列存在。
- **T3.2** `database.py` 加 `_add_icon_column(conn, "light_categories")`。
  【验】老库启动补列成功；幂等。
- **T3.3** `schemas_light.py`：`LightCategoryCreate/Update/Out` 加 `icon`；`HoldingOut` 加 `category_icon`；`services/light_serialize.py::to_category_out` / `to_holding_out` 补赋值。
  【验】`GET /api/light/categories`、`/light/by-asset/{id}`、`/light/by-workstation/{id}` 三个接口都带上图标标识。
- **T3.4** `LightCategoryDialog.vue` 加图标选择器（分组网格）。`lightApi.createCategory/updateCategory` 的 `LightCategoryPayload` 加 `icon`（`types.ts:1247-1254`）。
  【验】新建品类不选图标 → 保存成功且显示 `box`。
- **T3.5** `utils/light.ts` 新增 `lightCategoryIcon(category)`；**删除** `LightListView.vue::catIcon()`；`AssetLightAssetsPanel.vue:116` 改用它；`MapStationPanel.vue` 轻资产区**新增**图标。
  【验】**四处图标一致**（本任务核心验收，直接对应用户报障场景）。
- **T3.6** 提供"一键套用建议图标"（设备类型 + 轻资产品类各一处）。
  【验】点击后写入成功；不点也不影响使用。

### T4 非类型图标收敛（纯前端）

- **T4.1** `format.ts`：`OPERATION_META` / `EVENT_META` / `INVENTORY_RESULT_META` / `AUDIT_VISUALS` / `IMPORT_ACTION_META` 的 icon 改为图标标识；删除 `CATEGORY_META.icon`。
  【验】`format.ts` 内 UI emoji 计数为 0。
- **T4.2** 6 个渲染点改造：`AssetDetailView:274`、`AssetTimeline:106/120`、`InventoryDetailView:447`、`InventoryContextBar:109`、`AuditView:402`、`ImportView:338`。
  【验】时间线、审计页、导入结果图标正常，且 `.ico` 的 tone class 语义色保留。
- **T4.3** **改接口契约**（§9.2 已确认 `action_icon` 是 API 展示协议的一部分）：
  1. 后端 `AuditAction.ICONS`（`models.py:364-403`）的 38 个 emoji **值改为图标标识**（`'sparkles'` / `'link'` / `'trash'` …）；
  2. 字段改名 `action_icon` → `action_icon_name`（`schemas.py:569`）、`AuditActionOption.icon` → `icon_name`（`schemas.py:588-591`），同步 `routers/audit.py:63`、`:216`；**改名让遗漏的消费点直接编译报错，而不是静默拿到非 emoji 字符串**；
  3. 前端 `types.ts:437` 改字段名与语义；
  4. **删除前端 `AUDIT_VISUALS`**（`format.ts:288-307`，18 项），审计页改读 `action_icon_name` 走 `AppIcon`；`AuditView.vue:402` 改为 `<AppIcon>`，配色改由本地 `tone` 表提供（图标来自后端、配色留在前端——配色是纯视觉关注点）。
  【验】审计页 38 种动作**全部有图标**（当前只认 18 项，另外 20 项掉 `•`）；接口与前端不再各存一份（§5 第 3 项关闭）。
- **T4.3b** 删除后端死代码 `AssetOperation.ICONS`（`models.py:138-145`，6 项）——**已确认全仓无任何消费点**（§9.2）。
  【验】`rg 'AssetOperation.ICONS'` 无命中；删除后接口行为不变。
- **T4.4** 统一"完成 / 成功"态为 `check`：`ImportView:389`、`InsightsView:297` 与其余 `✅`。
  【验】全站该语义只有一种图标（§5 第 8 项关闭）。

### T5 硬编码与空态清理（纯前端）

- **T5.1** ~~确认两处是否刻意区分~~ → **已核实：统一**（§9.1）。`MapStationPanel.vue:598`（单台盘点明细，遍历 `mapRow.assets`）与 `:659`（资产清单，遍历 `assets`）**渲染的是同一批真实资产**，且都已渲染 `device_type_name`。将 `:598` 的写死 `🖥️` 改为 `assetTypeIcon()`，与 `:659` 一致。
  【验】同一台打印机在"盘点明细"与"资产清单"两处显示**同一图标**；§7.4 表 4 通过。
- **T5.2** 空态 `.big` / `.dz-icon`：`AssetListView:972`、`LightListView:450`、`PairingView:477/537/551`、`InventoryListView:370`、`InventoryDetailView:254`、`AssetDetailView:217`、`AuditView:445`、`ImportView:189/194`。
  【验】空态视觉重量与原有 emoji 相当（唯一需要"看图判断"的任务）。
- **T5.3** 统计卡写死 emoji：`AssetListView:223-231`、`PairingView:354-374`、`InventoryListView:282-294`、`InventoryDetailView:231-234`、`AuditView:352-360`、`ImportView:287-303/402-414`、`InventoryContextBar:104`。
  【验】统计卡图标与对应 meta 同源（§5 第 4/5/6 项关闭）。
- **T5.4** 行内小图标：`AssetListView:774/778/782/793/799/807/822/919`、`AssetDetailView:250/253`、`AuditView:423/425`、`App.vue:86/94`、`InventoryDetailView:298`、`MapStationPanel:580/584/598/615/626/634`、`ImportView:158-160`、`AssetRelationsPanel:100`。
  【验】逐页目视；🟢/🔴 语义色正确传递。
- **T5.5** `workstationMap.ts:49-50` 的 badge 结构从字符串改为 `{ text, icon }`（涉及 `MapStationPanel` 渲染点）。
  【验】"✓ 正常"/"⚠️ 异常"显示为"图标 + 文字"。
- **T5.6** 内联 SVG 迁入 `AppIcon`：`MapStationPanel.vue:493-497`（柜子 → 新增 `cabinet`）、`MapEditorView.vue:145-148`（入口）。
  【验】两处视觉不变（§5 第 9 项关闭）。
- **T5.7** **保留** `issueMeta.ts:31/38/45` 的 `◐ · ●`（几何字符，非 emoji），在文件注释写明"刻意保留"。
  【验】问题中心档位图标不变。

### 全局收尾

- **G1** `frontend/src` 下**参与渲染的** UI emoji 清零（注释中的说明符号可保留）。
  【验】重跑 `.box-agent-scratch/emoji_scan2.py`，"UI" 类计数 = 0。
- **G2** `npm run type-check` + `npm run build` 通过。
- **G3** §7.3 六条红线逐条确认；改造前后导出比对。
- **G4** 更新 `CHANGELOG.md`（该项目有维护该文件的既有习惯，见其 `1.1.1` 条目）与相关设计文档。

---

## 附：待确认事项（本轮已核实两条，其余保留）

### 已核实并关闭（见 §9）

1. ~~`AssetOperation.ICONS`（`models.py:138-145`）是否有接口下发~~ → **已确认：无任何消费点**（全仓 `rg` 无命中），是死代码，建议删除（T4.3b）。
2. ~~`MapStationPanel.vue:598` 与 `:659` 是否刻意用两套图标逻辑~~ → **已确认：渲染同一批资产，应统一**（§9.1）。
3. ~~后端审计 emoji 是否作为 API 数据返回~~ → **已确认：是**（`routers/audit.py:63`、`:216` 两处进入响应），且是唯一一处"emoji 作为接口展示协议"的实例（§9.2）。

### 仍未确认（未凭猜测补齐）

4. **`DeviceCategory.guess()` 前后端两份是否有意为之** —— `models.py:104-113` 与 `DeviceTypeDialog.vue:52-57` 规则相同但分开维护；未确认是否存在去重机制。建议不在本任务处理。
5. **iOS Safari 与 Edge 的实机渲染** —— 本轮为**静态代码分析，未启动浏览器**。§3.3 的码点风险结论基于 Unicode 属性与代码注释，**未做实机截图**；T1 验收时需补。
6. **扩 `DeviceTypeBrief` vs 加 `AssetOut.device_type_icon`** —— 若选前者，需确认所有引用它的接口与前端类型是否同步（未逐一核查完）。
7. **`lab/vgpu` 目录的性质** —— 含 `VgpuLabView.vue` 与 WGSL 着色器，疑似独立实验页，未确认是否在本次图标改造范围内。
8. **`DEVICE_TYPES` 种子数据与库中实际值的差异** —— `seed.py:37-44` 说"笔记本"是 `HOST`，库里是 `other`；未确认是人工改动还是迁移遗留。
9. **审计导出（Excel/CSV）是否含 `action_icon` 列** —— 已确认 `routers/audit.py` 只有两处引用 `AuditAction.icon()`，但**未逐行核对导出函数**是否把该字段写进文件；若无则导出不受契约改名影响，若有则需同步（T4.3 执行时确认）。

