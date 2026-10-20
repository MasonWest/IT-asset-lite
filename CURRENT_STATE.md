# IT 资产管理系统 · 当前状态

> | 版本基线 | **v2.9.10**（2026-10-20） |
> | --- | --- |
> | 这一篇是什么 | **状态入口**：现在是什么状态、改之前必须知道什么 |
> | 不写什么 | 不记演进（→ [CHANGELOG](CHANGELOG.md)）、不记实施细节（→ [docs/dev/](docs/dev/README.md)）、不写用户操作步骤（→ [docs/user/](docs/user/)） |
> | 找不到东西 | 先看 [docs/README.md](docs/README.md)（按"我想干什么"查） |

**一句话现状**：台账 + 扫码 + 配对 + 状态流转 + 盘点 + 导入导出 + 审计 全部可用；空间地图含平面图编辑器与地图盘点；
点位关系核对（四态派生）+ 例外确认 + 问题中心 + 关联建议已上线；轻资产按 holdings/movements 双表运行。
**没有登录体系、没有附件、地图还没有多楼层** —— 这三条是设计选择，不是欠账。

---

## 一、项目约束

> 以下是项目立项时定下的硬约束，**逐字保留，后续任何开发都不得违背**。
> 原文文件名：`每阶段总约束.txt`，已于 2026-09-24 并入本文并删除 —— 以后不需要再去翻那个文件。

```
这是一个轻量级 IT 资产管理系统，用于管理办公室的主机、显示器等设备。
技术约束：

单体应用，不要微服务，不要 K8s。

数据库用 SQLite，通过 ORM 访问，方便以后迁移。

后端推荐：Python FastAPI + SQLAlchemy；前端推荐：Vue3 + TypeScript + Vite + Element Plus。

前端打包后由后端托管，最终在 Windows 上能一条命令或双击脚本启动。

不要强制依赖 Docker，Docker 只能作为可选方案。

中文界面，风格现代简洁，参考飞书/Notion，不要传统后台那种密集表格感。

手机浏览器能正常打开、扫码、查看。

每阶段交付：源码 + README + 启动方式 + 验收步骤。

不要做采购、合同、财务、工单、审批流。
```

### 约束 → 落实对照

| 约束 | 怎么落实的 |
| --- | --- |
| 单体应用，不要微服务 / K8s | 一个 Python 进程同时提供 API 和前端静态文件，只有一个端口 |
| SQLite + ORM | SQLAlchemy 2.x + SQLite，没有裸 SQL 拼业务逻辑；换库只需改 `database.py` 的连接串 |
| FastAPI + SQLAlchemy / Vue3 + TS + Vite + Element Plus | 完全按推荐实现，未引入替代品 |
| 前端打包后由后端托管 | `frontend/dist` 由 FastAPI 的 `StaticFiles` 托管，`/` 直接出页面 |
| Windows 上一条命令或双击启动 | `start.bat` 双击即用；手动则是 `python run.py` 一条命令 |
| 不强制依赖 Docker | 仓库里没有 Dockerfile 也没有 compose，完全不涉及 |
| 中文界面 / 现代简洁 / 不密集 | 全中文；卡片 + 时间线式排版，没有传统后台那种密集表格堆砌 |
| 手机浏览器能打开、扫码、查看 | 全站响应式；每台设备出二维码，手机扫码直达详情页；手机端有盘点快捷条 |
| 每阶段交付源码 + README + 启动 + 验收 | 三个阶段均按此交付；验收结论见本文第十节，操作步骤见 README |
| 不做采购 / 合同 / 财务 / 工单 / 审批流 | 代码里完全没有相关模块、字段和路由 —— 这是**边界**，不是"还没做" |

---

## 二、这个系统是干什么的

办公室 IT 设备台账工具。流程一句话：

**录入 → 扫码看详情 → 主机显示器配对 → 状态流转 → 盘点 → 审计追溯 → 批量导入导出**

每台设备一个二维码贴在机器上；显示器挂在哪台主机上一页看清；领用/归还/送修/报废进时间线；
盘点手机扫码逐台标记；每次写操作（含失败的）都留审计；资产可 Excel 批量进出。手机浏览器直接用。

---

---

## 三、现在有什么能力

三个阶段已全部交付，**现在有的能力**：资产台账（CRUD / 筛选 / 二维码 / 标签打印）·
主机-显示器配对（绑定 / 换绑 / 解绑，解绑留记录）· 状态流转（领用 / 归还 / 送修 / 修好 / 报废 / 恢复，
非法流转 409）· 盘点（发起 / 扫码标记 / 差异 / 导出）· 操作审计（22 种操作类型，失败也留痕，只读）·
批量导入导出（模板 → 预览四桶 → 入库，列与导出一致）·
台账**套装聚合视图**（把主机和它挂的显示器合并成一行看，只换看法不动数据）·
**点位与资产空间地图**（点位 CRUD / 拖动攒批保存 / 资产挂靠 / JSON 导入 / 地图盘点视图 / 前端页面）·
**关系核对（点位使用人 ↔ 设备使用人）**（每次现算的四态结论，就地呈现在点位侧栏与资产行上，
差异可**记为「已确认的例外」**并带理由进审计、**关系一变自动失效**；只读扫描脚本 `backend/scan_relations.py`）·
**迁移助手**（资产换点位 / 点位业务迁移，**预览 → 确认 → 执行**三步 + 预览令牌，单事务，从不碰地图坐标）·
**问题中心**（`/insights`：把五条规则的问题**现算**成一份统一契约，每条带来源 / 原因 / 严重程度 /
可执行动作；已确认的例外不计入待办但仍可查看可撤销；**只读、不落库、没有"已解决"状态**）。

> 用户视角的完整操作说明在 [docs/user/](docs/user/)；实现细节在 [docs/dev/](docs/dev/README.md)。

---

## 四、改代码之前必须知道（11 条红线）

**全文在 [docs/dev/invariants.md](docs/dev/invariants.md)** —— 那里每条都带完整理由和事故现场，
这里只给索引。**红线不是"建议"，是"这么干过、然后出过事"。**

| # | 红线 | 一句话 |
| --- | --- | --- |
| 1 | 改状态与记历史同事务 | `record_event()` / `record_audit()` 只 `add` 不 `commit` |
| 2 | 派生状态算出来，不落库 | 挂了几台显示器、盘点进度百分比，都现算 |
| 3 | 时间一律存本地时间 | `func.now()` 在 SQLite 下是 UTC，东八区会差 8 小时 |
| 4 | 审计日志只读 | 只有 3 个 GET，没有写接口、没有删除 |
| 5 | 不做登录体系 | 操作人靠手填 + IP，不要"顺手加个登录" |
| 6 | 启动只建表 + 补字典 | 删库重启必须得到空库，演示数据只能显式触发 |
| 7 | 加表要同步 `database.py` 表分类 | 漏一个就让 `reset.py` 留下残缺数据 |
| 8 | `.py` 要重启进程，前端 `vite build` + 刷新 | 两者生效方式不同，混了会白查一轮 |
| 9 | 真实库不许跑写操作验收 | 另起 8099 + 库副本，写脚本不许有"默认真实库"兜底 |
| 10 | 公开前必须扫隐私 | `.gitignore` 只挡未来，历史里的删不掉 |
| 11 | "像 bug"先确认跑的是新代码 | 先 `curl` 接口，再看代码 |

---

## 五、关键设计决策（104 条）

**编号永不改**（代码注释、CHANGELOG、验收脚本都在引用「见决策 101」）。
完整内容按主题分 10 册，见 [docs/dev/decisions/](docs/dev/decisions/README.md)；
其中最贵的 **28 条"约束型"（不许这么改）全文另存 [invariants.md](docs/dev/invariants.md)**。

| # | 一句话 | 版本 | 主册 |
| --- | --- | --- | --- |
| 1 | 审计和变更历史是两张表 | — | [audit](docs/dev/decisions/audit.md) |
| 2 | 失败的请求也写审计，走独立会话 | — | [audit](docs/dev/decisions/audit.md) |
| 3 | 审计 IP 不信任 `X-Forwarded-For`… | — | [audit](docs/dev/decisions/audit.md) |
| 4 | 导入/导出/模板共用同一份列定义（… | — | [import-export](docs/dev/decisions/import-export.md) |
| 5 | 更新已有设备时，空单元格 = 不修改，不是清空 | — | [import-export](docs/dev/decisions/import-export.md) |
| 6 | 导入结果分新增 / 更新 / 无变化 / 失败四桶… | — | [import-export](docs/dev/decisions/import-export.md) |
| 7 | 中文编码三处兜底… | — | [import-export](docs/dev/decisions/import-export.md) |
| 8 | `/api/transfer` 独立前缀 | — | [import-export](docs/dev/decisions/import-export.md) |
| 9 | 配对解绑不删行，改 `active=false` | — | [audit](docs/dev/decisions/audit.md) |
| 10 | 启动脚本不建虚拟环境；不自动装依赖… | — | [process](docs/dev/decisions/process.md) |
| 11 | 三个 `.bat` 一律 GBK + CRLF、无 BOM | — | [process](docs/dev/decisions/process.md) |
| 12 | 字典数据 ≠ 业务数据… | — | [import-export](docs/dev/decisions/import-export.md) |
| 13 | 启动时不灌演示数据，只自动初始化字典 | — | [import-export](docs/dev/decisions/import-export.md) |
| 14 | 一次性迁移（补列 / 时区校正）只平移… | — | [process](docs/dev/decisions/process.md) |
| 15 | `reset.py` 要求亲手输入 `RESET`（不是 y… | — | [process](docs/dev/decisions/process.md) |
| 16 | 台账筛选里设备类型 / 位置 / 使用人是多选… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 17 | 配对页主机列有「只看没配显示器的」… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 18 | 「加显示器」弹窗默认只列还没配出去的显示器… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 19 | 多值筛选走逗号分隔的字符串（`1；3`）… | — | [import-export](docs/dev/decisions/import-export.md) |
| 20 | 套装聚合在后端做（… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 21 | 组内任意一台命中筛选… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 22 | 套装行不参与标签与导出逻辑，一律展开成设备明细 | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 23 | 台账页把两种模式统一成同一种「行」结构渲染 | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 24 | 配对页默认隐藏使用人写 `/` 的主机（半角 `/` 和全角… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 25 | 过滤生效时… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 26 | 台账的筛选 / 页码 / 视图全部写在 URL query … | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 27 | 「使用人」下拉的拼音别名在前端现算（… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 28 | 「使用人」下拉里；库里没有的名字渲染成候选的最后一项… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 29 | 凡是把使用人当人名展示的候选（配对页与资产详情的选择弹窗）… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 30 | 审计页顶部三张统计卡（全部记录 / 今日操作 / 失败操作）… | — | [audit](docs/dev/decisions/audit.md) |
| 31 | 点位（资产空间地图）不建绑定表… | — | [map](docs/dev/decisions/map.md) |
| 32 | 点位软删除（`active=False`），编码不释放 | — | [map](docs/dev/decisions/map.md) |
| 33 | 点位删除时绝不级联删资产；有资产先 409 + 列清单… | — | [map](docs/dev/decisions/map.md) |
| 34 | 拖动点位攒批保存… | — | [map](docs/dev/decisions/map.md) |
| 35 | 地图盘点的 `empty`（空位）与… | — | [map](docs/dev/decisions/map.md) |
| 36 | 地图盘点必须按 task_id 显式取（… | — | [map](docs/dev/decisions/map.md) |
| 37 | 地图的进度分母用资产数（`task.total`）… | — | [map](docs/dev/decisions/map.md) |
| 38 | 点位导入时显式忽略 JSON 里的 `assets` 字段… | — | [import-export](docs/dev/decisions/import-export.md) |
| 39 | 地图顶部的进度必须写 `已盘/应盘`；异常单独列… | — | [map](docs/dev/decisions/map.md) |
| 40 | 拖动点位的 `mouseup` 挂在 document 上… | — | [map](docs/dev/decisions/map.md) |
| 41 | 地图搜索用 `hasQuery` 而不是… | — | [map](docs/dev/decisions/map.md) |
| 42 | 地图页的搜索也支持拼音（复用… | — | [map](docs/dev/decisions/map.md) |
| 43 | 侧栏资产用 `asset_code` 当主标识；不用 SN… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 44 | 「所在点位」做在资产表单里（资产台账 → 编辑）… | — | [map](docs/dev/decisions/map.md) |
| 45 | 资产表单提交时 `workstation_id` 必须显式带… | — | [map](docs/dev/decisions/map.md) |
| 46 | 地图侧栏「该点位暂无绑定资产」的链接指向资产台账… | — | [map](docs/dev/decisions/map.md) |
| 47 | 点位字段的下拉标签带「已有 N 台」 | — | [map](docs/dev/decisions/map.md) |
| 48 | `/map` 是唯一满铺的页面（路由… | — | [map](docs/dev/decisions/map.md) |
| 49 | 「所在点位」下拉的编码排序加在前端消费侧（… | — | [map](docs/dev/decisions/map.md) |
| 50 | 「使用人 / 点位使用人」这套自由文本下拉抽成… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 51 | 组合式函数返回 ref 时… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 52 | 台账页顶部统计卡里混着的两种筛选轴（`status` /… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 53 | 新增一条筛选条件；必须同时改到 6 处… | — | [ui-ledger](docs/dev/decisions/ui-ledger.md) |
| 54 | 顶栏「宽屏全展开 / 手机收进「更多」」用同一份 DOM +… | — | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 55 | 手机上要缩短的长文案… | — | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 56 | 手机端进盘点模式就把顶部那三个全局统计隐藏掉（… | — | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 57 | 手势统一走一张指针表（… | — | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 58 | 捏合过的这一轮手势里… | — | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 59 | ⛔ 绝对不给 `.viewport` 加… | — | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 60 | 平面图图元存 `floor_maps.layout` JSO… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 61 | 不加桌椅图元（原型里的 `.f-tbl` /… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 62 | 编辑器做独立页 `/map/editor`… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 63 | 图元删除不弹确认；只提示"已删除（Ctrl+Z 可撤销）"… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 64 | 单图心智模型；后端有 `map` 概念… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 65 | 批量生成的编码真相在后端；前端面板显示的号段只是"预期"… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 66 | 图标级的"整图固定件"（`entry` 大楼入口 /… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 67 | 「批量生成点位」放在地图页「调整位置」里… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 68 | 批量生成不代填 `floor_map_id`… | — | [floor-map](docs/dev/decisions/floor-map.md) |
| 69 | 含真实姓名 / 平面图 / 资产编码的本机素材一律排除出版本… | — | [process](docs/dev/decisions/process.md) |
| 70 | 「工位」在界面与文档里统一叫「点位」（正式名「资产点位」）… | v2.1.0 | [process](docs/dev/decisions/process.md) |
| 71 | 点位四态（●◐○·）是渲染时派生的… | — | [map-relations](docs/dev/decisions/map-relations.md) |
| 72 | 地图只用蓝（有人）+ 灰（无人）两个色系… | v2.2.0 | [map-relations](docs/dev/decisions/map-relations.md) |
| 73 | 空库不塞默认底图；真·空库时 `floor_maps` 留空… | v2.3.0 | [map-relations](docs/dev/decisions/map-relations.md) |
| 74 | 空画布引导的消失判据只有「他建出东西了」这一条 | v2.3.0 | [map-relations](docs/dev/decisions/map-relations.md) |
| 75 | 文档改动按分级税计：默认什么都不用改，只有改口径才动 CS | v2.3.1 | [process](docs/dev/decisions/process.md) |
| 76 | 「什么算有人」前后端必须同口径… | v2.4.0 | [map-relations](docs/dev/decisions/map-relations.md) |
| 77 | 画布护栏；把缩放上下限与步长钉成契约… | v2.4.0 | [map-relations](docs/dev/decisions/map-relations.md) |
| 78 | 「新增点位」不许被房间拦住，新增态下也必须能平移 | v2.4.1 | [map-relations](docs/dev/decisions/map-relations.md) |
| 80 | 关系核对是"每次现算的派生结论"… | v2.5.0 | [inventory](docs/dev/decisions/inventory.md) |
| 81 | 迁移走独立路由 `/api/relocations`… | v2.5.0 | [inventory](docs/dev/decisions/inventory.md) |
| 82 | 「归属会不会跟着走」是派生结论；不是"勾没勾"本身… | v2.5.1 | [inventory](docs/dev/decisions/inventory.md) |
| 83 | 问题中心是"检查引擎的一个视图"；不是一张表… | v2.6.0 | [inventory](docs/dev/decisions/inventory.md) |
| 84 | 时间线补口；迁移用专属事件；例外进的是"设备级"履历… | v2.6.0 | [inventory](docs/dev/decisions/inventory.md) |
| 85 | 关联建议是"算出来的临时结果"；与问题中心共用归属口径… | v2.7.0 | [inventory](docs/dev/decisions/inventory.md) |
| 86 | 关联建议只做"事后没人兜底"的方向；扩场景的准入判据写死… | v2.7.1 | [inventory](docs/dev/decisions/inventory.md) |
| 87 | 「持有位置」的接口必须同时给出「是什么」；主行永远是内容… | v2.9.2 | [light-assets](docs/dev/decisions/light-assets.md) |
| 88 | 图标 + 文字的按钮必须 `inline-flex`… | v2.9.2 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 89 | 默认值不替用户做决定；"这条其实有几件"要自己说出来 | v2.9.3 | [light-assets](docs/dev/decisions/light-assets.md) |
| 90 | 同一份数据的两种排法；必须是同一套视觉语言… | v2.9.3 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 91 | 候选下拉的默认条数不许"取前 N" —— 它让人以为"库里就… | v2.9.4 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 92 | 两种不同实体共用一个 id 字段时… | v2.9.4 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 93 | 删掉一个维度要连"喂它的入口"一起删；否则留下的是"能填… | v2.9.5 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 94 | 搜索不绑图层开关；命中必须能解释… | v2.9.5 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 95 | 索引有上限就必须把"只搜了一部分"说出来 | v2.9.5 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 96 | 窄屏搜索框聚焦时不许变宽 | v2.9.5 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 97 | 聚合会把"身份"丢掉；凡是聚合后无法回答"是哪一条"的东西… | v2.9.6 | [light-assets](docs/dev/decisions/light-assets.md) |
| 98 | "人 → 工位"只能作为推断给出候选；绝不能当成位置关系… | v2.9.6 | [light-assets](docs/dev/decisions/light-assets.md) |
| 99 | 位置有歧义就显示歧义，不要为了"定下来"新增一个字段 | v2.9.6 | [light-assets](docs/dev/decisions/light-assets.md) |
| 100 | "库存写明点位"要算位置 | v2.9.6 | [light-assets](docs/dev/decisions/light-assets.md) |
| 101 | 图标 + 文字同排的容器必须显式 flex… | v2.9.7 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 102 | 凡是要进库的时间；客户端只能发"本地 naive"… | v2.9.8 | [process](docs/dev/decisions/process.md) |
| 103 | 一张卡片里的内容块要自己补横向内边距 —— `.panel`… | v2.9.8 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 104 | 「图标 + 文字」的检查要扫两遍… | v2.9.9 | [ui-contract](docs/dev/decisions/ui-contract.md) |
| 105 | 许可是 Apache-2.0… | v2.9.10 | [process](docs/dev/decisions/process.md) |

> 编号 **79 空缺**是历史遗留（全仓无引用），不补号以免打乱已有引用。

---

## 六、还没做的

按"值不值得做"排，细节与理由见 [docs/dev/roadmap.md](docs/dev/roadmap.md)。

| 事项 | 状态 | 备注 |
| --- | --- | --- |
| `markStation` 的审计口径（一键标整点位现在是 N 条审计） | **单独排期** | 要先定事务边界 / 部分失败 / 审计模型三件事，不是顺手能改的 |
| Phase 5 · 管理建议与统计（长期没绑定的点位 / 没位置的资产 / …） | 未开始 | 每条建议必须能点开看到构成它的原始记录 |
| 地图多楼层 | 未做 | 唯一还缺的地图能力（接口恒返回一张图、前端无楼层概念） |
| 附件（发票 / 保修卡照片） | 未做 | — |
| 保修到期提醒 | 未做 | `warranty_until` 字段已在库里，只差一个视图 |
| 审计归档（按时间导出后清理） | 未做 | 当前只能靠筛选分页 |
| 主机 ↔ 显示器自动匹配提示 | **暂缓**（方案已存） | 存量只有 2 组可捞回；重开时**加一条规则即可，别做第二个引擎** |
| 登录与权限 | **明确不做** | 会打破「不做登录」的当前设计 |

**明确不做**：采购、合同、财务、工单、审批流（立项约束）。

---

## 七、文档地图

| 我想查 | 去哪 |
| --- | --- |
| 这个功能怎么用（用户视角） | [docs/user/](docs/user/) |
| 架构 / 目录 / 启动链路 | [docs/dev/architecture.md](docs/dev/architecture.md) |
| 数据模型（13 张表 / 派生规则） | [docs/dev/data-model.md](docs/dev/data-model.md) |
| 术语（改文案前必读） | [docs/dev/glossary.md](docs/dev/glossary.md) |
| 工程红线 + 约束型决策全文 | [docs/dev/invariants.md](docs/dev/invariants.md) |
| 决策按主题查 | [docs/dev/decisions/](docs/dev/decisions/README.md) |
| 地图实现 / 前端契约 / 接口 / 验收 | [docs/dev/](docs/dev/README.md) |
| 设计稿（时点快照） | [docs/design/](docs/design/README.md) |
| 每个版本改了什么 | [CHANGELOG.md](CHANGELOG.md) |
| 重构前的原文、搬迁对照表 | [docs/archive/](docs/archive/README.md) |

> 改这个仓库的文档之前，先读 [docs/dev/maintenance.md](docs/dev/maintenance.md)：
> **默认什么都不用改**，只有改口径才动 CS（决策 75）。

---

*最后更新：2026-10-20 ｜ 版本只在上面「版本基线」写一次（页脚曾长期停在旧版本，读者第一眼看到的"新鲜度"是错的）。**「新增」和「调整位置」不冲突，是两条独立流程（见决策 78）***
