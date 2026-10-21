# 决策索引（105 条）

> **编号永不改**：代码注释、CHANGELOG、验收脚本里都在引用「见决策 101」这类说法，改号等于让它们全部失效。
> 一条决策只归**一个主册**（避免同一段话两处维护），跨主题的靠正文互相指。
> 其中 **29 条属于"约束型"（不许这么改）**，全文另存 [invariants.md](../invariants.md) ——
> 它们的读者是"正准备违反它的人"，只给摘要等于没给。
> 编号 **79 空缺**：历史遗留（当年跳过，全仓无任何引用），不补号以免打乱已有引用。

| # | 一句话 | 版本 | 主册 |
| --- | --- | --- | --- |
| 1 | 审计和变更历史是两张表 | — | [audit](audit.md) |
| 2 | 失败的请求也写审计，走独立会话 | — | [audit](audit.md) |
| 3 | 审计 IP 不信任 `X-Forwarded-For`… | — | [audit](audit.md) |
| 4 | 导入/导出/模板共用同一份列定义（… | — | [import-export](import-export.md) |
| 5 | 更新已有设备时，空单元格 = 不修改，不是清空 | — | [import-export](import-export.md) |
| 6 | 导入结果分新增 / 更新 / 无变化 / 失败四桶… | — | [import-export](import-export.md) |
| 7 | 中文编码三处兜底… | — | [import-export](import-export.md) |
| 8 | `/api/transfer` 独立前缀 | — | [import-export](import-export.md) |
| 9 | 配对解绑不删行，改 `active=false` | — | [audit](audit.md) |
| 10 | 启动脚本不建虚拟环境；不自动装依赖… | — | [process](process.md) |
| 11 | 三个 `.bat` 一律 GBK + CRLF、无 BOM | — | [process](process.md) |
| 12 | 字典数据 ≠ 业务数据… | — | [import-export](import-export.md) |
| 13 | 启动时不灌演示数据，只自动初始化字典 | — | [import-export](import-export.md) |
| 14 | 一次性迁移（补列 / 时区校正）只平移… | — | [process](process.md) |
| 15 | `reset.py` 要求亲手输入 `RESET`（不是 y… | — | [process](process.md) |
| 16 | 台账筛选里设备类型 / 位置 / 使用人是多选… | — | [ui-ledger](ui-ledger.md) |
| 17 | 配对页主机列有「只看没配显示器的」… | — | [ui-ledger](ui-ledger.md) |
| 18 | 「加显示器」弹窗默认只列还没配出去的显示器… | — | [ui-ledger](ui-ledger.md) |
| 19 | 多值筛选走逗号分隔的字符串（`1；3`）… | — | [import-export](import-export.md) |
| 20 | 套装聚合在后端做（… | — | [ui-ledger](ui-ledger.md) |
| 21 | 组内任意一台命中筛选… | — | [ui-ledger](ui-ledger.md) |
| 22 | 套装行不参与标签与导出逻辑，一律展开成设备明细 | — | [ui-ledger](ui-ledger.md) |
| 23 | 台账页把两种模式统一成同一种「行」结构渲染 | — | [ui-ledger](ui-ledger.md) |
| 24 | 配对页默认隐藏使用人写 `/` 的主机（半角 `/` 和全角… | — | [ui-ledger](ui-ledger.md) |
| 25 | 过滤生效时… | — | [ui-ledger](ui-ledger.md) |
| 26 | 台账的筛选 / 页码 / 视图全部写在 URL query … | — | [ui-ledger](ui-ledger.md) |
| 27 | 「使用人」下拉的拼音别名在前端现算（… | — | [ui-ledger](ui-ledger.md) |
| 28 | 「使用人」下拉里；库里没有的名字渲染成候选的最后一项… | — | [ui-ledger](ui-ledger.md) |
| 29 | 凡是把使用人当人名展示的候选（配对页与资产详情的选择弹窗）… | — | [ui-ledger](ui-ledger.md) |
| 30 | 审计页顶部三张统计卡（全部记录 / 今日操作 / 失败操作）… | — | [audit](audit.md) |
| 31 | 点位（资产空间地图）不建绑定表… | — | [map](map.md) |
| 32 | 点位软删除（`active=False`），编码不释放 | — | [map](map.md) |
| 33 | 点位删除时绝不级联删资产；有资产先 409 + 列清单… | — | [map](map.md) |
| 34 | 拖动点位攒批保存… | — | [map](map.md) |
| 35 | 地图盘点的 `empty`（空位）与… | — | [map](map.md) |
| 36 | 地图盘点必须按 task_id 显式取（… | — | [map](map.md) |
| 37 | 地图的进度分母用资产数（`task.total`）… | — | [map](map.md) |
| 38 | 点位导入时显式忽略 JSON 里的 `assets` 字段… | — | [import-export](import-export.md) |
| 39 | 地图顶部的进度必须写 `已盘/应盘`；异常单独列… | — | [map](map.md) |
| 40 | 拖动点位的 `mouseup` 挂在 document 上… | — | [map](map.md) |
| 41 | 地图搜索用 `hasQuery` 而不是… | — | [map](map.md) |
| 42 | 地图页的搜索也支持拼音（复用… | — | [map](map.md) |
| 43 | 侧栏资产用 `asset_code` 当主标识；不用 SN… | — | [ui-ledger](ui-ledger.md) |
| 44 | 「所在点位」做在资产表单里（资产台账 → 编辑）… | — | [map](map.md) |
| 45 | 资产表单提交时 `workstation_id` 必须显式带… | — | [map](map.md) |
| 46 | 地图侧栏「该点位暂无绑定资产」的链接指向资产台账… | — | [map](map.md) |
| 47 | 点位字段的下拉标签带「已有 N 台」 | — | [map](map.md) |
| 48 | `/map` 是唯一满铺的页面（路由… | — | [map](map.md) |
| 49 | 「所在点位」下拉的编码排序加在前端消费侧（… | — | [map](map.md) |
| 50 | 「使用人 / 点位使用人」这套自由文本下拉抽成… | — | [ui-ledger](ui-ledger.md) |
| 51 | 组合式函数返回 ref 时… | — | [ui-ledger](ui-ledger.md) |
| 52 | 台账页顶部统计卡里混着的两种筛选轴（`status` /… | — | [ui-ledger](ui-ledger.md) |
| 53 | 新增一条筛选条件；必须同时改到 6 处… | — | [ui-ledger](ui-ledger.md) |
| 54 | 顶栏「宽屏全展开 / 手机收进「更多」」用同一份 DOM +… | — | [ui-contract](ui-contract.md) |
| 55 | 手机上要缩短的长文案… | — | [ui-contract](ui-contract.md) |
| 56 | 手机端进盘点模式就把顶部那三个全局统计隐藏掉（… | — | [ui-contract](ui-contract.md) |
| 57 | 手势统一走一张指针表（… | — | [ui-contract](ui-contract.md) |
| 58 | 捏合过的这一轮手势里… | — | [ui-contract](ui-contract.md) |
| 59 | ⛔ 绝对不给 `.viewport` 加… | — | [ui-contract](ui-contract.md) |
| 60 | 平面图图元存 `floor_maps.layout` JSO… | — | [floor-map](floor-map.md) |
| 61 | 不加桌椅图元（原型里的 `.f-tbl` /… | — | [floor-map](floor-map.md) |
| 62 | 编辑器做独立页 `/map/editor`… | — | [floor-map](floor-map.md) |
| 63 | 图元删除不弹确认；只提示"已删除（Ctrl+Z 可撤销）"… | — | [floor-map](floor-map.md) |
| 64 | 单图心智模型；后端有 `map` 概念… | — | [floor-map](floor-map.md) |
| 65 | 批量生成的编码真相在后端；前端面板显示的号段只是"预期"… | — | [floor-map](floor-map.md) |
| 66 | 图标级的"整图固定件"（`entry` 大楼入口 /… | — | [floor-map](floor-map.md) |
| 67 | 「批量生成点位」放在地图页「调整位置」里… | — | [floor-map](floor-map.md) |
| 68 | 批量生成不代填 `floor_map_id`… | — | [floor-map](floor-map.md) |
| 69 | 含真实姓名 / 平面图 / 资产编码的本机素材一律排除出版本… | — | [process](process.md) |
| 70 | 「工位」在界面与文档里统一叫「点位」（正式名「资产点位」）… | v2.1.0 | [process](process.md) |
| 71 | 点位四态（●◐○·）是渲染时派生的… | — | [map-relations](map-relations.md) |
| 72 | 地图只用蓝（有人）+ 灰（无人）两个色系… | v2.2.0 | [map-relations](map-relations.md) |
| 73 | 空库不塞默认底图；真·空库时 `floor_maps` 留空… | v2.3.0 | [map-relations](map-relations.md) |
| 74 | 空画布引导的消失判据只有「他建出东西了」这一条 | v2.3.0 | [map-relations](map-relations.md) |
| 75 | 文档改动按分级税计：默认什么都不用改，只有改口径才动 CS | v2.3.1 | [process](process.md) |
| 76 | 「什么算有人」前后端必须同口径… | v2.4.0 | [map-relations](map-relations.md) |
| 77 | 画布护栏；把缩放上下限与步长钉成契约… | v2.4.0 | [map-relations](map-relations.md) |
| 78 | 「新增点位」不许被房间拦住，新增态下也必须能平移 | v2.4.1 | [map-relations](map-relations.md) |
| 80 | 关系核对是"每次现算的派生结论"… | v2.5.0 | [inventory](inventory.md) |
| 81 | 迁移走独立路由 `/api/relocations`… | v2.5.0 | [inventory](inventory.md) |
| 82 | 「归属会不会跟着走」是派生结论；不是"勾没勾"本身… | v2.5.1 | [inventory](inventory.md) |
| 83 | 问题中心是"检查引擎的一个视图"；不是一张表… | v2.6.0 | [inventory](inventory.md) |
| 84 | 时间线补口；迁移用专属事件；例外进的是"设备级"履历… | v2.6.0 | [inventory](inventory.md) |
| 85 | 关联建议是"算出来的临时结果"；与问题中心共用归属口径… | v2.7.0 | [inventory](inventory.md) |
| 86 | 关联建议只做"事后没人兜底"的方向；扩场景的准入判据写死… | v2.7.1 | [inventory](inventory.md) |
| 87 | 「持有位置」的接口必须同时给出「是什么」；主行永远是内容… | v2.9.2 | [light-assets](light-assets.md) |
| 88 | 图标 + 文字的按钮必须 `inline-flex`… | v2.9.2 | [ui-contract](ui-contract.md) |
| 89 | 默认值不替用户做决定；"这条其实有几件"要自己说出来 | v2.9.3 | [light-assets](light-assets.md) |
| 90 | 同一份数据的两种排法；必须是同一套视觉语言… | v2.9.3 | [ui-contract](ui-contract.md) |
| 91 | 候选下拉的默认条数不许"取前 N" —— 它让人以为"库里就… | v2.9.4 | [ui-contract](ui-contract.md) |
| 92 | 两种不同实体共用一个 id 字段时… | v2.9.4 | [ui-contract](ui-contract.md) |
| 93 | 删掉一个维度要连"喂它的入口"一起删；否则留下的是"能填… | v2.9.5 | [ui-contract](ui-contract.md) |
| 94 | 搜索不绑图层开关；命中必须能解释… | v2.9.5 | [ui-contract](ui-contract.md) |
| 95 | 索引有上限就必须把"只搜了一部分"说出来 | v2.9.5 | [ui-contract](ui-contract.md) |
| 96 | 窄屏搜索框聚焦时不许变宽 | v2.9.5 | [ui-contract](ui-contract.md) |
| 97 | 聚合会把"身份"丢掉；凡是聚合后无法回答"是哪一条"的东西… | v2.9.6 | [light-assets](light-assets.md) |
| 98 | "人 → 工位"只能作为推断给出候选；绝不能当成位置关系… | v2.9.6 | [light-assets](light-assets.md) |
| 99 | 位置有歧义就显示歧义，不要为了"定下来"新增一个字段 | v2.9.6 | [light-assets](light-assets.md) |
| 100 | "库存写明点位"要算位置 | v2.9.6 | [light-assets](light-assets.md) |
| 101 | 图标 + 文字同排的容器必须显式 flex… | v2.9.7 | [ui-contract](ui-contract.md) |
| 102 | 凡是要进库的时间；客户端只能发"本地 naive"… | v2.9.8 | [process](process.md) |
| 103 | 一张卡片里的内容块要自己补横向内边距 —— `.panel`… | v2.9.8 | [ui-contract](ui-contract.md) |
| 104 | 「图标 + 文字」的检查要扫两遍… | v2.9.9 | [ui-contract](ui-contract.md) |
| 105 | 许可是 Apache-2.0… | v2.9.10 | [process](process.md) |
| 106 | 状态不跟着位置 / 归属自动变；只多问一句「状态对不对得上」… | v2.9.12 | [inventory](inventory.md) |

共 105 条（编号 1–106，缺 79）。拿不准在哪一册时，先在 [invariants](../invariants.md) 里找 —— 最贵的错误都写在那儿。
