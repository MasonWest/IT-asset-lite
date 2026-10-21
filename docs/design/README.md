# 设计稿与规格（docs/design）

> 状态：索引·随新增设计稿更新 · 2026-10-20

这里是**一次性产物**：做某个功能之前写的方案、评估、规格、诊断。它们**不参与日常维护**，
也不代表今天的代码——**今天的口径永远看 [`CURRENT_STATE.md`](../../CURRENT_STATE.md) 和
[`docs/dev/`](../dev/README.md)**。

放进来的唯一理由是「当初为什么这么设计」这个问题的答案只在这里。
按决策 7：**一份都不删**。所以有些设计稿文末还留着当年那句"结论并进 CURRENT_STATE 后删掉本文件"——
那是 2026-09 的旧处置约定，**不执行**。

## 怎么用这一层

1. 想知道**现在**系统怎么工作 → 别在这里找，去看 [docs/dev/](../dev/README.md) 和 [docs/user/](../user/install.md)。
2. 想知道**某个功能当初为什么这样做**（改需求、评估返工成本、判断能不能推翻） → 按下面的表找。
3. 每份文档**前 5 行内有一行 `> 状态：`**，先看它再看内容：状态为"已取代"或"未实施"的稿子，
   里面的字段名、接口名、表结构都**不要**当现行口径引用。
4. 旧的引用写法（`设计方案-轻资产管理模块-修订版.md` 这种中文长名）在文末对照表里能查到新位置。

## 目录规则

| 规则 | 说明 |
| --- | --- |
| 文件名 | 英文 kebab-case，标题仍写中文（决策 1：中文名在 URL/Pages 上会变百分号编码） |
| 分目录 | 按「主题 + 年-月」：`2026-10-map/` `2026-10-light/` `2026-10-insights/` `2026-10-icons/` `2026-10-docs/` |
| `unbuilt/` | 明确**没实施**的提案放这里，与"已落地的历史方案"分开 |
| 状态横幅 | 每份 `.md` 顶部**有且只有一行** `> 状态：`，取值见下 |

### 状态横幅的七种取值

```text
> 状态：设计（未实施）· 日期
> 状态：已实施（vX.Y.Z）· 日期
> 状态：部分实施（哪些做了、哪些没做）· 日期
> 状态：已取代 → [新文档](相对路径) · 日期
> 状态：素材·原始需求（不是规格，不据以实施）· 日期
> 状态：已废弃·仅存档 · 日期
> 状态：索引·随新增设计稿更新 · 日期
```

`tools/docs-check.py` 会断言两件事：`docs/design/**/*.md` 的前 5 行内，`> 状态：` **恰好出现一次**；
且它的值**必须以这七个词之一开头**（防止半年后冒出"状态：大概做了一半"这种既不能筛也不能搜的值）。

## 索引：我要找什么

### 地图（`2026-10-map/`）

| 文件 | 状态 | 这份东西回答了什么问题 |
| --- | --- | --- |
| [map-phase1-spec.md](2026-10-map/map-phase1-spec.md) | 已实施 v1.2.0 | 地图一期：怎么把真实工位数据接进来，验收断言怎么写（含种子文件核验） |
| [map-editor-design.md](2026-10-map/map-editor-design.md) | 已实施 v1.2.0 | 地图编辑器（`/map/editor`）怎么画底图、图元怎么存。⚠️ 文首写了哪一处落地时被推翻 |
| [map-search-expansion.md](2026-10-map/map-search-expansion.md) | 已实施 v2.9.5 | 搜索框为什么扩成四个维度、为什么删掉「房间」、为什么不做「点开图层才能搜」 |

### 轻资产（`2026-10-light/`）

| 文件 | 状态 | 这份东西回答了什么问题 |
| --- | --- | --- |
| [light-assets-v1.md](2026-10-light/light-assets-v1.md) | 已取代 | 第一版设计（按"位置枚举"建模）。**被推翻的原始思路**，看它才知道修订版在反对什么 |
| [light-assets-v2-revised.md](2026-10-light/light-assets-v2-revised.md) | 已实施 v2.9.0 | 现行 holdings 模型的完整推导：唯一写入口、守恒式、期初/导入、四组位置 |
| [light-assets-defects.md](2026-10-light/light-assets-defects.md) | 已实施 v2.8.1 | 那批 P0 缺陷的复现证据与修复口径；修完仍留着当回归用例清单 |
| [prototype-light-ui.html](2026-10-light/prototype-light-ui.html) | 设计稿 | 轻资产界面原型（静态单页，双击用浏览器打开）。人名与编号已脱敏 |

### 业务感知 / 问题中心（`2026-10-insights/`）

| 文件 | 状态 | 这份东西回答了什么问题 |
| --- | --- | --- |
| [business-awareness-requirements.md](2026-10-insights/business-awareness-requirements.md) | 素材 | **原始需求对话体**，不是规格。想知道"这套东西最初想解决什么"才看 |
| [business-awareness-roadmap.md](2026-10-insights/business-awareness-roadmap.md) | 部分实施 | 路线图提案：Phase 0–3 已落地（v2.5.0–v2.6.0），**Phase 4–5 没做** |
| [issue-center-phase3.md](2026-10-insights/issue-center-phase3.md) | 已实施 v2.6.0 | 统一问题中心：哪些检查归它、`key` / `fingerprint` 怎么定、接口怎么给 |
| [business-awareness-delivery.md](2026-10-insights/business-awareness-delivery.md) | 已实施 v2.5.0 | 那一轮交付了什么（关系核对引擎 / 迁移助手 / 数据质量扫描） |
| [asset-status-consistency.md](2026-10-insights/asset-status-consistency.md) | 已实施 v2.9.12 | **状态**要不要跟点位使用人联动？结论是不自动改、只做组合校验（规则 `asset_status_vs_holder`）。含真库里 10 台矛盾设备的实测 |

### 图标（`2026-10-icons/`）

| 文件 | 状态 | 这份东西回答了什么问题 |
| --- | --- | --- |
| [icon-system-audit.md](2026-10-icons/icon-system-audit.md) | 已实施 v2.9.5–2.9.9 | emoji → 内联 SVG 的盘点与改造方案（T1–T5 五档），以及后来的两轮"图标+文字挤成两行"收尾 |

`evidence/` 下有两张截图，是 v2.9.9 那次修复的现场。**它们不入库**（画面里的资产编号是库内真实编码，
脱敏只能改文本、改不了像素），只在本机留存。

### 文档体系自身（`2026-10-docs/`）

| 文件 | 状态 | 这份东西回答了什么问题 |
| --- | --- | --- |
| [docs-governance-audit.md](2026-10-docs/docs-governance-audit.md) | 已实施 | 2026-10-20 的文档治理评估：17 份平铺在根目录怎么造成的、为什么这么拆、每个阶段的回滚方案 |

### 没实施的提案（`unbuilt/`）

| 文件 | 状态 | 这份东西回答了什么问题 |
| --- | --- | --- |
| [auto-pairing-hints.md](unbuilt/auto-pairing-hints.md) | 设计（未实施） | 主机 ↔ 显示器「自动配对提示」的提案。当年评估过、没做；若要重开，先读它 |

### 已归档的旧稿（`docs/archive/`，不入库）

| 文件 | 为什么不入库 |
| --- | --- |
| `map-integration-assessment.md` | 含真实人名举例（原 `资产空间地图-接入评估与最小迁移方案.md`，结论已在 v1.2.0 落地） |
| `HANDOFF-2026-09.md` | 含本机绝对路径、且自述"第六节起已过时"（原 `PROJECT-HANDOFF.md`） |

另外，地图的**视觉基准**是根目录的 `参考-地图原型V19.html`（V19 单页原型），同样不入库。

## 旧文件名 → 新位置对照

根目录那 17 份平铺文档在 2026-10-20 全部归位。下表用来消化旧引用（issue、口头交接、老 commit 里的写法）。

| 旧名（根目录） | 现在在哪 |
| --- | --- |
| `图标体系盘点与统一SVG改造方案.md` | `docs/design/2026-10-icons/icon-system-audit.md` |
| `资产空间地图-第一阶段开发规格与验收.md` | `docs/design/2026-10-map/map-phase1-spec.md` |
| `资产空间地图-地图编辑器设计方案.md` | `docs/design/2026-10-map/map-editor-design.md` |
| `设计方案-空间地图搜索拓展.md` | `docs/design/2026-10-map/map-search-expansion.md` |
| `设计方案-轻资产管理模块.md` | `docs/design/2026-10-light/light-assets-v1.md` |
| `设计方案-轻资产管理模块-修订版.md` | `docs/design/2026-10-light/light-assets-v2-revised.md` |
| `诊断-轻资产管理模块缺陷清单.md` | `docs/design/2026-10-light/light-assets-defects.md` |
| `原型-轻资产界面改版.html` | `docs/design/2026-10-light/prototype-light-ui.html` |
| `Phase3-统一问题中心-落地清单.md` | `docs/design/2026-10-insights/issue-center-phase3.md` |
| `交付说明-业务感知与联动.md` | `docs/design/2026-10-insights/business-awareness-delivery.md` |
| `设计方案-业务感知能力路线图.md` | `docs/design/2026-10-insights/business-awareness-roadmap.md` |
| `资产管理系统业务感知与联动机制设计方案.md` | `docs/design/2026-10-insights/business-awareness-requirements.md` |
| `设计方案-自动配对提示.md` | `docs/design/unbuilt/auto-pairing-hints.md` |
| `文档体系治理评估.md` | `docs/design/2026-10-docs/docs-governance-audit.md` |
| `资产空间地图-接入评估与最小迁移方案.md` | `docs/archive/map-integration-assessment.md`（不入库） |
| `PROJECT-HANDOFF.md` | `docs/archive/HANDOFF-2026-09.md`（不入库） |
| `资产详情头1.png` / `资产详情头2.png` | `docs/design/2026-10-icons/evidence/`（不入库） |
