# 业务感知与联动能力 —— 交付说明

> 状态：已实施（v2.5.0）· 2026-10-14

> 对应任务书《资产管理系统业务感知与联动能力实施任务》（P0 关系核对引擎与地图内联联动 /
> P1 资产点位迁移助手 / 数据质量扫描 / 集成与交付）。
> 版本 **v2.5.0**（2026-10-14），基线 v2.4.2。**代码已全部落地并验证，不是设计稿。**
>
> 项目状态与口径的权威入口仍是 [CURRENT_STATE.md](../../../CURRENT_STATE.md)，本文件只交代"这一轮交了什么"。

---

## 0. 先交代一件事：动手前与既有代码的对账结论

任务书列了 6 项能力。**先逐文件读完既有实现**（`ownership.py` / `relations.py` /
`schemas_workstations.py` / `workstations.py` / `models.py` / `inventory.py` / `events.py` /
`audit.py` / `database.py` / `main.py` / `types.ts` / `api/index.ts` / `format.ts` /
`workstationMap.ts` / `MapStationPanel.vue` / `SpaceMapView.vue`），结论是**两项早就有了**：

| 任务书要求 | 既有实现 | 本轮动作 |
| --- | --- | --- |
| 资产变更时间线 | `asset_events` + `_TRACKED_FIELDS`（v2.0 起） | **不重做**，复用 |
| 变更追溯 / 审计日志 | `audit_logs`（22 种 action，失败也留痕） | **不重做**，新动作按既有 `AuditAction` 扩 |
| 地图四态关系层 | `utils/workstationMap.ts::relationOf`（v2.2.0） | **不重做**（它是前端渲染派生层），新引擎与它同口径 |
| 资产与点位绑定接口 | `POST/DELETE /api/workstations/{id}/assets` | **不重做**，迁移路由直接改同一张表的同一列 |
| 「什么算有人」的判断 | `services/ownership.py` 是**唯一实现**（有对账脚本守） | **复用**（`import has_owner`），**绝不另写一套** |
| 健康检查 / 问题中心 / 路由助手 | **没有** | ← 本轮的活 |

---

## 1. 变更文件清单

**新增 10 个代码文件（3,126 行）**

| 文件 | 行数 | 作用 |
| --- | --- | --- |
| `backend/app/services/relation_check.py` | 436 | P0 规则引擎。**纯函数、零 IO**，连改数据的能力都没有 |
| `backend/app/services/relocation.py` | 341 | P1 规划器。纯函数，产物里**刻意没有** `x`/`y`/`facing` |
| `backend/app/schemas_relations.py` | — | 两个模块的请求 / 响应 DTO |
| `backend/app/routers/relation_check.py` | 351 | 4 个接口：现算核对、例外列表、确认、撤销 |
| `backend/app/routers/relocation.py` | 389 | 2 个接口：预览（纯读）、执行（令牌 + 单事务） |
| `backend/scan_relations.py` | — | 只读数据质量扫描（`mode=ro`，四分类，`--json`） |
| `backend/tests/test_relation_check.py` | — | 26 项单测 |
| `backend/tests/test_relocation.py` | — | 18 项单测（含 `test_never_touches_coordinates`） |
| `frontend/src/components/RelationCheckCard.vue` | 354 | 侧栏内联核对卡片（静默优先 + 例外确认 + 撤销） |
| `frontend/src/components/RelocationDialog.vue` | 383 | 迁移三步流程对话框 |

**修改 14 个文件**

| 文件 | 改了什么 |
| --- | --- |
| `backend/app/models.py` | 新增 `RelationException` 表；`AuditAction` 加 3 个动作（含 LABELS/ICONS/GROUPS） |
| `backend/app/database.py` | `BUSINESS_TABLES` 加入 `relation_exceptions`（排在 `workstations` 前） |
| `backend/app/main.py` | 挂载两个新路由；`_FAILED_ROUTES` 补 3 条（带子路径的排最前） |
| `backend/app/routers/__init__.py` | 注册新路由 |
| `backend/app/config.py` · `backend/app/__init__.py` | 版本号 → 2.5.0 |
| `frontend/src/types.ts` | 新增 17 个类型 |
| `frontend/src/api/index.ts` | 新增 `relationCheckApi` / `relocationApi` |
| `frontend/src/components/MapStationPanel.vue` | 接 `RelationCheckCard`；资产行就地标记 + 「换点位」入口 |
| `frontend/src/views/SpaceMapView.vue` | 选中点位时**并行**拉资产与核对结果；接线迁移对话框 |
| `frontend/package.json` · `package-lock.json` | 版本号 → 2.5.0（只改根 version 两处） |
| `CURRENT_STATE.md` · `CHANGELOG.md` | 决策 #80/#81、功能现状、接口一览、下一步、CHANGELOG 条目 |

**没有动的**：`services/ownership.py`、`services/events.py`、`services/audit.py`、
`services/filters.py`、`routers/transfer.py`（导入导出）、`routers/inventory.py`、盘点三入口。

---

## 2. 接口契约

### P0 —— `/api/relation-checks`

| 方法 | 路径 | 请求 | 响应 | 写库 |
| --- | --- | --- | --- | --- |
| GET | `/workstation/{station_id}` | — | `StationRelationCheckOut` | **否**（每次现算） |
| GET | `/exceptions?workstation_id=` | `include_inactive?` | `RelationExceptionOut[]` | 否 |
| POST | `/workstation/{station_id}/exceptions` | `{rule, asset_id, fingerprint, reason*, operator?}` | 刷新后的 `StationRelationCheckOut` | 是（只写例外表 + 审计） |
| DELETE | `/exceptions/{exception_id}` | `operator?` | 刷新后的 `StationRelationCheckOut` | 是（`active=0` 软删 + 审计） |

`StationRelationCheckOut` 关键字段：

```
workstation_id · code · user_name · applicable
verdict ∈ {consistent, mismatch, unregistered, undetermined}   ← 派生值，不含 error 语义
review_count                        ← 已扣掉被有效例外盖住的（前端徽标读它）
issues[]      {rule, status, severity, scope, asset_id, title, detail, suggestion,
               fingerprint, subjects[], exception{...valid}, suppressed}
asset_checks[]{asset_id, asset_code, asset_user, state}         ← 逐台给，供资产行就地标记
checked_at
```

**错误码**：`404` 点位不存在 / 差异在当前数据里已不存在；`409` 提交的 `fingerprint` 与当前关系不符
（挡住"看着旧差异点确认、结果确认到了新差异上"）；`422` `reason` 为空（schema 层 `min_length=1`）。

### P1 —— `/api/relocations`（**独立前缀，不并进 `/api/transfer`**）

| 方法 | 路径 | 请求 | 响应 | 写库 |
| --- | --- | --- | --- | --- |
| POST | `/preview` | `RelocationPreviewRequest` | `RelocationPreviewOut`（含 `preview_token`） | **否** |
| POST | `/apply` | `RelocationApplyRequest`（含 `preview_token`、`acknowledge_warnings`） | `RelocationApplyResult`（含 `relation_checks`） | 是（单事务） |

```
mode ∈ {assets, station}
assets  → 只改 assets.workstation_id                    （绝不碰使用人）
station → 上面一项 + 点位使用人（按 move_user 勾选）+ 可选退役源点位（retire_source）
```
**错误码**：`409` ①令牌不一致（并发改动 / 重复提交）②本次迁移不成立（冲突）③有未确认差异却直接执行；
`404` 设备或点位不存在 / 目标点位已删除；`500` 任何意外（**已回滚，数据未改动**）。

**响应里没有 `x` / `y` / `facing`** —— 单测 `test_never_touches_coordinates` 钉住这条。

---

## 3. 测试说明

| 层 | 命令 | 结果 |
| --- | --- | --- |
| 纯函数单测 | `cd backend && python -m unittest discover -s tests` | **44 PASS / 0 FAIL**（26 + 18） |
| 前后端口径对账 | `cd backend && python check_ownership_parity.py` | **15 PASS / 0 FAIL** |
| 浏览器端到端 | `NODE_PATH=<ws>/node_modules node e2e-relation-reloc-1014.js` | **76 PASS / 0 FAIL** |
| 数据质量扫描（只读） | `cd backend && python scan_relations.py` | 见下 |

**端到端脚本刻意一个数字都不写死**（本项目的既定纪律：用户自己会加点位、改使用人）：
差异点位 / 一致点位 / 源点位 / 目标点位四种角色**按条件从接口现挑**，挑不到就 `exit 2`；
界面上读到的每一个字（标题 / 正文 / 状态标签 / 概要）都跟**同一时刻的接口返回逐字比** ——
这才是"前后端口径一致"的硬证据。在真实库副本上运行，脚本自己挑出了
W61（2 处差异）/ W62（源）/ W68（目标）/ W69（第三方），76 项全绿。

**老库升级路径单独验过**：拿**真实库副本**（69 点位 / 148 资产，**没有** `relation_exceptions` 表）
起实例 → `/api/health` 返回 `2.5.0`，`create_all` 自动建出新表，无需任何迁移脚本或 Alembic。

---

## 4. P0 验收结果（逐条对任务书）

| 任务书验收条款 | 结论 | 证据 |
| --- | --- | --- |
| 前后端口径一致 | ✅ | e2e 3.3–3.7 / 4.11 / 5.5 / 8.14：界面文字与接口返回**逐字比**；`check_ownership_parity.py` 15 项 |
| 无人使用的资产点位**不判为错误** | ✅ | `test_none_asset_station_is_info_not_error`；e2e 8.9/8.10 —— 结论 `unregistered`、`review_count=0`、文案含「业务上是正常的」 |
| 不同使用人只产生"待核对"、不擅改 | ✅ | 引擎是纯函数（无写能力）；`severity=review`、配色琥珀（e2e 3.8 色度判据 G>100，非红）；文案含「系统不会自动改任何一侧」（3.9） |
| 关系变化后旧例外不掩盖新差异 | ✅ | 指纹机制；e2e 5.1–5.7：改点位使用人后 **2 条例外全失效**、差异重新露出、界面显示「旧例外已失效」**并保留当时理由** |
| 检查逻辑可脱离页面独立测试 | ✅ | `backend/tests/test_relation_check.py`，26 项纯函数测试（含 `/`、全角 `／`、空值、空点位、id 复用不串号、指纹） |
| 未登记不自动等同错误 | ✅ | 三档 severity 里**没有 error**；`unregistered` 走 `info` |
| 合理差异不阻止正常操作 | ✅ | 核对是**只读**的；迁移预览里的差异只进 `confirmations`（勾选即放行），不阻断 |
| 例外确认有理由 + 可追溯 + 失效 | ✅ | `reason` 必填（e2e 4.3 空理由被拦）；写审计 `relation_exception_confirm` / `_revoke`；**软删不删行** |
| 静默优先提示 | ✅ | 一致 / 无可核对对象时只有一行浅灰文字，不弹窗（e2e 2.4/2.5） |

**顺手修掉的一个真缺陷**（P0 自测时发现）：例外确认后 `verdict` 仍是 `mismatch`（它是数据派生值，
不叠例外），概要文案落进 `switch` 的 default，说出了**「两侧都没有登记使用人」这种与事实相反的话**。
修法：概要里先显式判"差异是否已被有效例外盖住"，并给卡片加 `handled` 态（收成中性色）——
e2e 4.7/4.9/4.10 守住。**教训：派生值与叠加层必须分开判断，别让叠加层去猜派生值的意思。**

---

## 5. P1 验收结果（逐条对任务书）

| 任务书验收条款 | 结论 | 证据 |
| --- | --- | --- |
| 预览不修改数据 | ✅ | e2e 6.10（设备 `workstation_id` 未变）/ 6.11（坐标未变）；`POST /preview` 全程只读 |
| 迁移后关联正确 | ✅ | e2e 8.3 —— 两台设备都落到目标点位 |
| 失败时数据一致 | ✅ | 单事务 + 任一步抛错整体回滚；e2e 7.2 —— 被 409 挡住时**一个字段都没写** |
| 事件可追溯 | ✅ | e2e 8.11 设备履历（`asset_events`，每台各 1 条）/ 8.12（一次迁移 = **1 条** `relocation_apply` 审计） |
| 迁移后 P0 结果一致 | ✅ | 执行接口**当场返回**受影响点位重算后的核对结果；e2e 8.8/8.9/8.13/8.14 |
| 导入导出不受影响 | ✅ | `/api/transfer/template` `/export` `/api/assets/{id}` `/api/workstations` `/api/audit` `/api/inventory/*` 全部 200；`routers/transfer.py` **一行未动** |
| 不把坐标调整当归属迁移 | ✅ | 独立路由 + 对话框常驻文字写明「不动地图坐标，挪桌子请用「调整位置」」（e2e 6.2）；产物无坐标字段；8.5/8.6 实测坐标未变 |
| 不自动推断使用人变更 | ✅ | `mode=assets` 只改 `workstation_id`；`mode=station` 要不要搬使用人**由用户勾**；e2e 8.4/8.7 |
| 区分三种动作 | ✅ | 表格见 `CURRENT_STATE.md` 第八节；CS 决策 #81 |
| 评估权限 / 并发 / 重复提交 | ✅ | 预览令牌一条机制同时挡住并发改动与重复提交（e2e 7.1 **真的收到了 409**，9.2b 从浏览器侧确认）；权限沿用现状（本项目无登录，见红线 5），操作人记进审计 |
| 先展示再确认 | ✅ | 三步流程；**任何输入变化都让旧预览作废**（免得照着旧清单点执行） |

---

## 6. 集成结果与未解决问题

**集成结果**：P0 与 P1 **共用同一套数据契约**（`pair_state()` 与规格引擎是同一个函数；
执行接口直接复用 `build_check()` 返回重算结果），不存在两套判断。交叉集成点走通：
迁移后立刻重算 → 界面刷新 → 结论与接口一致（e2e 8.13/8.14）。

**已知未解决 / 本轮刻意不做**

1. **部署需重启后端**：改的是 `.py`，而 8080 上那个进程比改动早 —— **必须重启才生效**（前端 `dist` 已重建，刷新即可）。
2. **关系核对只在点位侧栏**，台账列表页没有汇总入口（"问题中心"级别的视图不在本轮范围）。
3. **例外确认没有批量确认**：一次一台、理由逐条写 —— 这个功能的全部价值就在那句理由上。
4. **`markStation`（把整个点位设备一键标为已盘）仍是逐台循环调接口**，一个点位 N 台 = N 个请求 + N 条审计，
   与"一次批量操作 = 1 条审计"的既定口径相悖。**v2.4.2 时就记着账，本轮仍未动**（属口径变更，要单独一轮）。
5. **扫描脚本给出的"待人工判定"没有自动闭环**：真实库现在有 W61 的 2 处差异（`A100-03-00036-M` /
   `A100-03-00232`，使用人写「公用（前台）」）+ W03/W04 台账没填全 —— **没有替用户改任何数据**，
   等他在界面上核对后决定（记例外 / 改归属 / 迁移）。
6. **工作区尚未提交**：14 改 + 10 增 + 2 份设计草案文档未跟踪，**等确认后再 commit / push**。

**数据质量扫描（真实库，只读）**：69 个点位 → 一致 46 / 差异 1（W61，实为 2 台设备）/ 未登记 5 / 无法判断 7；
分类为 待人工判定 2 · 历史遗留 2（W03、W04）· 合理差异 3（W64/W66/W67 未登记使用人）。
相邻检查：挂在已删点位上的设备 **0**（外键没问题）；使用人写成占位符的设备 23 台。

---

## 7. 文档更新建议

**本轮已改**（口径变更，按 CS 决策 75 的分级税属于"必须动"的那一档）：
`CURRENT_STATE.md` 的决策表（新增 #80 / #81）、功能现状、接口一览、下一步；`CHANGELOG.md` 新增 v2.5.0 条目；
版本号四处同步（`config.py` / `app/__init__.py` / `package.json` / `package-lock.json`）。

**建议后续（本轮没做，需要时再说）**：
- `README.md` 是纯使用者视角，**本轮没动它** —— 关系核对与迁移都是地图页里的就地能力，
  没有新增页面/入口，README 的"怎么用"结构不用改。真要补，建议在「空间地图」一节加 3 行使用说明。
- 第十节的验收表**不放数字**（CS 决策 75），端到端脚本 `e2e-relation-reloc-1014.js` 建议**留在**
  `…binaries/node/workspace/` 与其他验收脚本并列，作为可复跑的回归资产。
- 若后续要做"问题中心"级别的汇总视图，先读 CS 决策 #80 —— 别顺手建"问题状态表"。

---

## 8. 部署与复核

```bash
# 后端（改的是 .py，必须重启）
stop.bat && start.bat          # 或 IT_ASSET_PORT=8080 python backend/run.py

# 复核
curl -s http://127.0.0.1:8080/api/health          # 期望 {"status":"ok","version":"2.5.0"}
cd backend && python -m unittest discover -s tests  # 44 项
cd backend && python check_ownership_parity.py      # 15 项
cd backend && python scan_relations.py              # 只读体检
```

**验收证据**：截图与逐项结果在 `.tmp/shots-reloc-1014-real/`（含 `result.json`），
`E:/MMMason/IT_snipe/.tmp/` 已被 gitignore，不会进版本库。

---

# 附录：v2.5.1 修复（2026-10-15）

**来源：用户实际使用后反馈的两条，加上顺着他的报告查出来的一条真缺陷。**
用户在试用「点位业务迁移」时报告：① 选目标点位时下拉不按编号排，"选择困难"；
② 他做了 `W46 → W47` 的「点位业务迁移 + 一并迁移设备」，执行完发现**使用人没跟着走**，翻审计日志才看懂。

## 1. 排序（用户反馈 ①）

`RelocationDialog.vue` 的目标候选直接用了 `GET /api/workstations` 的顺序，而那是 `y, x, id`
——**地图渲染的顺序**（同一排的挨着给）。放进"从 68 个点位里挑一个"的下拉里就是
`W68, W69, W01, W02…`（这是浏览器里读到的真实顺序，见验收 A4）。

修法是**接上已有的唯一实现** `utils/workstationMap.ts::compareStationCode`
（`AssetFormDialog` 的点位下拉一直在用；`W10` 排在 `W2` 前面正是它存在的理由），
**没有另写一份**。排序留在前端：`y, x` 对地图是对的，动接口等于让地图迁就一个下拉的偏好。

## 2. 「使用人没跟着走」看不见（用户反馈 ②）

后端没错。审计里 `"move_user": false` 白纸黑字，`W46` 的使用人本来就是 `/`（= 没填），
**没有归属可搬**；而他没勾「迁移点位使用人」，所以两侧都没动 —— 行为完全符合设计。

**错的是预览：它只列"会变的东西"，从不提"不会变的东西"。** 于是一处关键事实
（"使用人不会跟着走"）只能靠事后翻审计日志才能发现，那已经太晚。

改法（三件）：

| 件 | 内容 |
| --- | --- |
| 预览新增常驻「点位归属」块 | 源、目标**两侧都把 `迁移前 → 迁移后` 摆出来**，不变的显式标**「不变」**；不跟着走时给出"想搬就勾「迁移点位使用人」"的指引 |
| 结构化字段 | 新增 `user_move` / `{source,target}_user_{before,after}`；**勾没勾、甚至校验有冲突时都算出来**，界面不再靠文案猜；原值照实给（含 `None` 与 `/`），显示统一走 `userLabel()` |
| `notes` 与 `confirmations` 分家 | `notes` = 只说明情况、**不要求点头**；`confirmations` = 必须点头。说清一件事不该顺手加一次确认负担，但那件事必须说出来 |

## 3. ⭐ 真缺陷：源点位使用人是 `/` 会把目标点位的人覆盖掉

`services/relocation.py` 判"有没有人"用的是自写的 `(user_name or "").strip()`
—— **违反决策 76**（"有没有人"只认 `services/ownership.py::has_owner`）。这不是显示问题，**是写坏数据**：

> 源点位 `user_name = "/"` → `strip()` 后仍是 `"/"`、是**真值** → 被当成**一个真使用人** →
> 勾上「迁移点位使用人」执行 → **目标点位的人被写成 `/`**（归属丢失）。
> 而同一份预览里还写着"源点位没有登记使用人，勾选「迁移使用人」实际不会改变任何东西" —— **文案与行为完全相反**。

用户在真实数据上点的那次**没勾**这一项，所以没造成损失；但这条路径是通的。

三处一起改：判据换 `has_owner()`；源点位没有归属时**一个字都不写**（拿"空"去覆盖目标不是迁移，是丢归属）；
预览文案与行为对齐（那句不成立的 confirmation 改成如实的 `notes`）。
口径写进 **CS 决策 #82**。

## 4. 验收证据

| 层 | 脚本 | 结果 |
| --- | --- | --- |
| 纯函数单测 | `backend/tests/test_relocation.py` | 18 → **22 PASS**（新增 `/`、全角 `／`、归属前后值、不覆盖目标） |
| 全部单测 | `backend/tests/` | **48 PASS** |
| 前后端口径对账 | `backend/check_ownership_parity.py` | 15 PASS |
| 接口级回归（真实库副本） | `.tmp/it-fix-1015/probe_owner.py` | **30 PASS / 0 FAIL** |
| 浏览器（新增，本次修复） | `e2e-reloc-owner-1015.js` | **37 PASS / 0 FAIL** |
| 浏览器（P0/P1 全量回归） | `e2e-relation-reloc-1014.js` | **76 PASS / 0 FAIL**（证明没碰坏既有能力） |

关键物证（浏览器里读到的原话）：

```
接口前 6：W68, W69, W01, W02, W03, W04      ← 修复前下拉就是这个顺序
下拉前 6：W01, W02, W03, W04, W05, W06      ← 修复后

点位归属（不跟着走）
W62   林晓薇 → 林晓薇   不变
W03   陈默   → 陈默     不变
源点位本来就没有登记使用人，没有归属可搬；目标点位现有的人不会被覆盖。
```

> 注：上面这段引的是当次浏览器输出的**结构**，人名为虚构
> （本仓库公开，真实姓名一律不落盘 —— CS 决策 69 / 红线 10）。

## 5. 版本与复核

版本号 **2.5.0 → 2.5.1**（四处同步：`config.py` / `app/__init__.py` / `package.json` / `package-lock.json`）。
递增的目的是**让你能一眼确认跑的是新代码**：重启后

```bash
curl -s http://127.0.0.1:8080/api/health     # 期望 {"status":"ok","version":"2.5.1"}
```

## 6. 明确没做

- ❌ **没把「迁移点位使用人」默认勾上**。它一旦勾上会改写**目标点位**的归属（虽然要人点头），
  默认打开这种破坏性开关不符合本项目"只做加法 / 不擅自改"的口径。改法是**把后果说到看得见**，
  而不是替用户预设答案 —— 若你希望默认勾选，说一声，那是另一个决定。

---

# 附录二：v2.6.0 —— Phase 3 问题中心 + Phase 4 时间线补口 + Phase 0 数据卫生

按已评审通过的《Phase3-统一问题中心-落地清单.md》实施（该文件**按你的要求保持未跟踪、未提交、未删除**）。
下面按你要求的四项交付：**变更文件清单 / 测试结果 / 清洗前后差异 / 仍未解决的问题**。

## 1. 变更文件清单

### 后端 · 新增（4 个）

| 文件 | 行数 | 内容 |
| --- | --- | --- |
| `backend/app/services/issues.py` | 569 | **问题派生引擎（纯函数）**：5 条规则的目录、稳定 `key` 的构造、`handled` 折算、汇总与稳定排序、动作 URL 白名单自检 |
| `backend/app/routers/insights.py` | 259 | `GET /api/insights/issues`（只读、不写审计）；取数 3 个查询无 N+1；未知 rule/severity 返空集、分页夹上限 |
| `backend/app/schemas_insights.py` | 96 | DTO（含 `checked`：空状态要说清"检查了什么"） |
| `backend/tests/test_issues.py` | 336 | 23 项纯函数测试 |

### 后端 · 修改（8 个）

| 文件 | 改了什么 |
| --- | --- |
| `services/relation_check.py` | 加 `Severity.RANK`；抽 **`exception_still_valid()`（"例外还有效吗"的唯一实现）** —— 接口层与问题中心共用，不再各写一遍比较 |
| `routers/relation_check.py` | 改用上面那个唯一实现；例外确认 / 撤销给**设备级**例外补 `asset_events`（Phase 4） |
| `models.py` | 新增事件类型 `relocate`（点位迁移）与 `relation_exception`（关系例外）+ LABELS |
| `routers/relocation.py` | 迁移写 `relocate` 而不是通用 `update` |
| `main.py` / `routers/__init__.py` | 注册 insights 路由 |
| `scan_relations.py` | Phase 0：加**使用人值清单** + `--clean-plan`（只读产出审核清单 JSON + 可直接导入的 CSV） |

> 另外 `services/relocation.py` / `routers/relocation.py` / `schemas_relations.py` / `tests/test_relocation.py`
> 的改动是 **v2.5.1 那批**（使用人覆盖缺陷）留下的，一并在工作区里。

### 前端 · 新增（4 个）

| 文件 | 行数 | 内容 |
| --- | --- | --- |
| `views/InsightsView.vue` | 576 | 三段式页面：一句话结论 → 规则卡（为什么值得关注）→ 展开逐条（能做什么）。琥珀/灰蓝、无红色；待办清零的卡收成中性色；空状态列出检查过的规则 |
| `utils/issueMeta.ts` | 67 | 严重程度配色与规则图标（**不用红色**这条写在文件头） |
| `utils/richText.ts` | 28 | `**重点**` → `<b>` 的最小渲染（**唯一实现**），先转义再替换 |
| `composables/useInsightBadge.ts` | 42 | 导航角标：只在 `review > 0` 时显示；拉不到就静静不显示（不许弹错） |

### 前端 · 修改（7 个）

`types.ts`（问题中心类型 + 两个新事件类型）/ `api/index.ts`（`insightsApi`）/ `format.ts`（`EVENT_META` + `EVENT_TYPES`）/
`router/index.ts`（`/insights`）/ `App.vue`（导航项 + 角标，紧跟「资产台账」）/ `styles/main.css`（角标样式）/
`SpaceMapView.vue`（`?station=<code>` 深链，复用页面已有的 `?task=` query 约定）/
`components/RelationCheckCard.vue`（改用共享的 `richText`）

### 文档与版本

`README.md`（新增「问题中心怎么用」一节 + 能力表一行 + 维护命令两行）、
`CHANGELOG.md`（v2.6.0）、`CURRENT_STATE.md`（基线 + 决策 **83 / 84** + 接口一览 + 下一步）、
版本号四处同步 `2.5.1 → 2.6.0`。

### 验收脚本（不在仓库里，在 `.tmp/` 与 node workspace）

| 脚本 | 用途 |
| --- | --- |
| `.tmp/it-p3/probe_insights.py` | 接口级 63 项（含**用另一条接口独立对账**、**操作后问题真的消失/回来**） |
| `.tmp/it-p3/make_empty.py` | 把副本清成零问题库（好让空状态分支被真跑到） |
| `.tmp/p0-clean2/p0_verify.py` | Phase 0 清洗前后差异核对（只读） |
| `e2e-insights-1016.js` | 浏览器 34 + 12 项（同一脚本按数据状态走 A 段 / B 段） |
| `.tmp/p0-clean{,2}/clean-plan.{json,csv}` | 清洗审核清单与可导入 CSV |

## 2. 测试结果

| 层 | 命令 | 结果 |
| --- | --- | --- |
| 全部单测 | `cd backend && python -m unittest discover -s tests` | **71 PASS**（48 → 71，新增 23 项） |
| 前后端口径对账 | `python check_ownership_parity.py` | 15 PASS |
| 接口级（真实库副本 / 8099） | `python .tmp/it-p3/probe_insights.py` | **63 PASS / 0 FAIL** |
| 浏览器 A 段（库里有问题） | `node e2e-insights-1016.js` | **34 PASS / 0 FAIL** |
| 浏览器 B 段（零问题的库） | 同上（先跑 `make_empty.py`） | **12 PASS / 0 FAIL** |

**你给的 5 条重点审查项 → 落到哪条断言**：

| 要求 | 断言 |
| --- | --- |
| 1. 问题定义统一 | `3.7`（每条都有 key/rule/source/severity/reason/action）+ `3.6`（动作 URL 过白名单） |
| 2. 处理结果由真实业务变化决定 | `4.2`（挂上点位 → 该 key **消失**）/ `4.5`（摘下来 → **回来**）/ `4.6`（全程 `sqlite_master` 里没有 issues 表） |
| 3. 动作落到真实功能 | `A4.1-A4.4`（`/?workstation=none` 真的按条件查了后端且总数对得上）/ `A5.2`（`/map?station=W61` 真的**选中了 W61**）/ `A6.2`（`/asset/<id>` 落到的页面**真的有「编辑」**） |
| 4. 问题标识稳定 | `2.1`/`2.2`/`2.3`（两次请求一致、全库唯一、不含 fingerprint）+ `4.8`（改了设备的备注，key 原样不动）+ `A3.1b`（屏幕上每条 data-key 都能在接口里找到） |
| 5. 页面与接口一起验收 | `1.1`–`1.5`（**计数用另一条接口独立对账**）+ `B1`–`B5`（空状态说清检查了什么）+ `A3.4/A3.5`（分页不漏条、不重条）+ `9.1/9.2`（无控制台报错） |

**⚠️ 第 5 条里的"权限校验"**：这套系统**没有账号体系**（`App.vue` 里写着）。
按评审结论，它落成 **"输入校验 + 越权参数处理"**：未知 `rule`/`severity` → 空集不 500、
`page_size` 夹到 `[1,100]`、`action.url` **只由后端规则模板生成**且逐条过白名单
（断言 `3.1`–`3.6`）。**没有引入任何鉴权层。**

**顺手修掉一个真缺陷**：卡片文案里的 `**这不是错误**` 在界面上渲染成了**字面星号**
（v2.5.0 踩过的同一个坑，人眼核对截图时发现、断言没覆盖）。修法是把最小渲染抽成
`utils/richText.ts` 唯一实现、两处共用；并把判据补成断言（`A3.0a/0b`）。

## 3. Phase 0 清洗前后差异

**先备份**：`backend/backup_db.py --keep 0` → `backups/it_assets_20261015_083827.db`（524 KB，148 台设备），
已有备份一份没动。

**清单**：`scan_relations.py --clean-plan`（只读）产出，**逐行列出"哪一行、哪个字段、从什么改成什么、为什么"**。

**执行**：走**既有批量导入**（`POST /api/transfer/import`），两次都是**先 `dry_run=true` 看预览再落库**；
干跑结果：`total/update/error/will_write = 31/31/0/31` 与 `16/16/0/16`，每行的变更**只有"使用人"这一个字段**
（`/ → 空`、`公用 → 空`）。审计里两条 `asset_import`，操作人 `phase0-cleanup`。

| 项 | 清洗前 | 清洗后 |
| --- | --- | --- |
| 资产使用人 = `/` | 23 | **0** |
| 资产使用人 = `公用` | 16 | **0** |
| 资产使用人写成地点（会议室一/二、洽谈室一、公用（前台）） | 8 | **0** |
| 点位使用人 = `/`（W46） | 1 | **0** |
| 问题 `asset_user_unregistered` | 4 | **6** |
| 问题 `asset_unbound` | 28 | 28（本次不动绑定） |
| 「不是人名」的取值种类 | 资产 6 种 / 点位 1 种 | **0 / 0** |

**⚠️ 两条必须说清的**：

1. **"设备没登记使用人"从 4 升到 6 是本次清洗的预期方向**，不是退化：
   会议室 / 公共设备本来就该是"没有个人使用人"，之前把它们算在某个名下才是错的。
   （数据卫生的目标是"值不再是假的"，不是"问题数字变小"。）
2. **点位 W46 的清空没有走点位导入**。`POST /api/workstations/import` 要的是**整张点位文件**
   （带 `schema` / `canvas` 的完整 JSON），为清一个字段重导整张图会**动到坐标** —— 那正是
   决策 67 那条红线（排版动作与业务归属不能混）。所以它走的是**界面「点位设置」同款的**
   `PATCH /api/workstations/46`（`user_name: ""` → 清空），一个字段、一条审计。

顺带补了 `looks_like_place()`：把 `公用` / `公共` / `共用` / `备用`… 这类**状态词**也算作"不是人名"
（第一版漏了，`公用` 16 台差点被当成真名留下）。**只在数据卫生层认它，不动 `ownership.has_owner`** ——
引擎判"有没有人"的口径是决策 76 定的，改它会牵动前后端两份实现、P0 卡片与既有 e2e，那是另一件事。

## 4. 仍未解决的问题

1. **8080 上跑的还是 v2.5.1** —— 本轮改的是 `.py`，**要重启后端**才生效；
   重启后 `curl /api/health` 应当回 `2.6.0`（前端 `dist` 已重建，刷新即可）。
2. **markStation 的审计口径变更没做**（按你的安排单独排期）：一键把整个点位标为已盘仍是
   逐台循环 = N 条审计。要单排的理由（事务边界 / 部分失败 / 审计模型）写进了 CS 十三.1。
3. **`公用` 在引擎眼里仍是"一个使用人"**（`has_owner('公用') === true`）。它现在已被清掉，
   所以这次没有实际影响；但**如果以后又有人填 `公用`**，那一处会被判成"待核对"而不是"未登记"。
   要不要把它并进 `ownership` 的口径，是一个**独立的口径变更**（要动前后端两份实现 + 既有 e2e），
   我没有擅自做。
4. **`asset_unbound` 28 台没动** —— Phase 0 只管"使用人不是人名"，"设备没挂点位"是业务操作，
   该由人在问题中心点「去挂靠点位」逐台处理（或另开一轮讨论批量方案）。
5. **本轮没有提交任何东西**。工作区里现在有 v2.5.1 + v2.6.0 两批改动混在一起
   （27 改 + 8 增 + 1 份未跟踪的清单）。`Phase3-统一问题中心-落地清单.md`
   **未跟踪、未提交、未删除**，指纹 `23512 bytes / sha1 4b91d93a2fdc`（与我动手前一致）。
   要不要提交 / 分成两个 commit / 要不要推送，等你发话。
