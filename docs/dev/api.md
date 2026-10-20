# 接口一览

> 参数级文档不在 md 里 —— 服务起来后打开 `/docs`（FastAPI 自动生成）。
> 这一篇只给"按前缀分组 + 容易踩的字段口径"。

## 十一、接口一览

服务起来后打开 `http://127.0.0.1:<端口>/docs` 有交互式文档。按前缀分组：

| 前缀 | 覆盖 |
| --- | --- |
| `/api/assets` | 台账 CRUD、时间线、配对记录、状态流转；`/api/assets/grouped` = 套装聚合视图（只读） |
| `/api/pairings` | 配对看板、绑定、换绑、解绑、记录 |
| `/api/inventory` | 盘点任务、标记、差异、导出、扫码上下文；`/api/inventory/tasks/{id}/map-view` = 地图盘点视图（按 task_id 显式取，返回五种点位状态） |
| `/api/workstations` | 点位 CRUD、批量布局保存、资产挂靠/摘除、JSON 导入（预览 / 落库）、**批量生成（`POST /bulk`，行列排布，1 条审计）** |
| `/api/floor-maps` | 平面图：列表（**恒为一张**）/ `default` / 单张 / 保存（整张覆盖 + 乐观锁 409）。**前端只认一张，无多图入口** |
| `/api/relation-checks` | 关系核对：`GET /workstation/{id}`（**现算、不落库**）、`GET /exceptions?workstation_id=`、`POST /workstation/{id}/exceptions`（理由必填，指纹不符 409）、`DELETE /exceptions/{id}`（软删，行留着） |
| `/api/insights` | 统一问题中心：`GET /issues`（**现算、只读、不写审计、不落库**）。参数 `rule` / `severity` / `page` / `page_size`；未知 `rule`/`severity` **返回空集而不是 500**，`page_size` 夹到 `[1,100]`。汇总（`by_rule` / `counts` / `total` / `checked`）**永远是全库口径**，只有 `items` 跟着筛 |
| `/api/relocations` | 迁移：`POST /preview`（**纯读，一个字段都不写**；回包里下发 `user_move` 与 `{source,target}_user_{before,after}` —— 归属**会不会跟着走**看它，别看用户勾没勾）、`POST /apply`（令牌不一致 / 有未确认差异 → 409；单事务；返回受影响点位的**重算后**核对结果）。**从不修改 `x` / `y`** |
| `/api/light` | 轻资产：品类 CRUD / **当前分配记录（`GET /holdings`）** / **流水（唯一写入口，`POST /movements`）** / 冲正（`POST /movements/{id}/reverse`） / **更换配件（`POST /replace`，一次两条流水同事务）** / 期初确认（`POST /opening/preview` · `/opening`） / 历史导入（`POST /import/preview` · `/import`） / 导出（`GET /export`） / 汇总 / 筛选候选 / 宿主搜索（`GET /holders`） / **某资产的配件（`GET /by-asset/{id}`）** / **某点位的配件（`GET /by-workstation/{id}`，返回 `direct` / `via_assets` 两组）**。`light_holdings` **只允许被 `apply_holding_delta()` 修改**，没有任何"改库存"接口 |
| `/api/device-types` | 设备类型增删改查 |
| `/api/transfer` | 模板下载、批量导入、批量导出 |
| `/api/audit` | 审计列表、筛选项、导出（**只有 GET**） |
| `/api/meta/filters` | 筛选候选值 + 配对统计 |
| `/api/qrcode.png` · `/api/system/info` · `/api/health` | 二维码出图、系统信息、健康检查 |

### ⚠️ `GET /api/workstations` 顶层三个计数字段（v2.4.0 起 `empty` 已改名）

| 字段 | 含义 | 判据 |
| --- | --- | --- |
| `total` | 点位总数 | — |
| `assigned` | **有人**的点位数 | `ownership.has_owner()`：空值与「`/`」「`／`」都算**没归属** |
| `without_asset` | **一台设备都没挂**的点位数 | `asset_count == 0` |

**`items[]` 里只有一个派生计数**：`asset_count`（挂了几台资产）。
⚠️ v2.9.6 **删掉了** `light_direct` / `light_via_assets` —— 它们是地图角标的数据源，
角标退役后没有消费方；留着只会变成下一次"两处数字对不上"的种子。
轻资产的"有什么"只能从 `GET /api/light/by-workstation/{id}` 的四组拿（那是明细，不是聚合）。
单点位 `GET /api/workstations/{id}` 也真查资产数（`_serialize_one`），不留"没查的 0"。

**⚠️ `assigned` 与 `without_asset` 是两个正交轴，不是互补的两半** ——
它们回答的是"有没有人"和"有没有设备"。**一个点位可以同时满足两个**（有人但没设备）。
真正互补的是 `assigned` 与 `total - assigned`（前端「空闲点位」= **没人用**）。

**这个字段 2026-10-08 从 `empty` 改名 `without_asset`**：旧名字看起来像"空闲点位"，
而前端的「空闲点位」算的是**人员轴**（`total - assigned`）——
**两个都叫"空闲"，指的却是两件事**，同一页上对不上且不报错。
现在名字自带语义。**别拿 `without_asset` 和界面上的「空闲点位」互相对账。**

参数级定义以 `/docs` 为准，本文不重复维护。

---
