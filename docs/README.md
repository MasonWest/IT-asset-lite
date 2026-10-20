# 文档总入口

> 这个目录按「**你现在想干什么**」组织，不按文件类型。找不到就回来这里。

## 我是来用系统的（办公室 IT / 普通同事）

| 我想…… | 读哪篇 |
| --- | --- |
| 把它装起来、跑起来 | [安装与启动](user/install.md) |
| 让手机 / 别的电脑也能打开 | [局域网与手机访问](user/network.md) |
| 用手机对着设备看信息、盘点 | [空间地图 · 用手机盘点](user/map.md#用手机盘点推荐这么用) |
| 录入设备、配对显示器、状态流转 | [资产台账](user/assets.md) |
| 发起一次盘点、看差异 | [盘点](user/inventory.md) |
| 在平面图上排点位、画房间、批量生成 | [空间地图](user/map.md) |
| 管键鼠 / SSD / 内存这些配件 | [轻资产](user/light-assets.md) |
| 看「现在有什么值得看一眼」 | [问题中心与关联建议](user/insights.md) |
| 备份、恢复、清库、灌演示数据 | [备份与维护命令](user/operations.md) |
| 出问题了 / 想知道哪些做不到 | [常见问题](user/faq.md) |

## 我是来改代码的（开发者 / agent）

| 我想…… | 读哪篇 |
| --- | --- |
| 30 秒知道先读什么、红线在哪 | [开发文档入口](dev/README.md) |
| 搞清楚技术栈、目录、启动链路 | [架构](dev/architecture.md) |
| 改数据模型、看 13 张表 | [数据模型](dev/data-model.md) |
| 配置端口 / 数据目录 / 扫码地址 | [配置项](dev/configuration.md) |
| 查"这个口径当时为什么这么定" | [决策索引](dev/decisions/README.md) |
| 跑验收、写验收脚本 | [验收](dev/acceptance.md) |
| 提交前该做什么 | [维护约定](dev/maintenance.md) |

## 历史与设计（不参与日常维护）

| 我想…… | 读哪篇 |
| --- | --- |
| 找某个功能当初的设计稿 | [设计稿索引](design/README.md) |
| 查迁移前的原文、或某段内容搬到哪去了 | [归档区说明](archive/README.md) |
| 查每个版本改了什么 | [CHANGELOG](../CHANGELOG.md) |

---

## 三条约定

1. **根目录只有三份**：`README.md`（门面）、`CURRENT_STATE.md`（状态入口）、`CHANGELOG.md`（变更记录）。
   其余全部在 `docs/` 下分类。
2. **`docs/user/` 不写表结构、SQL、内部决策** —— 那是 `docs/dev/` 的事。
   反过来，`docs/dev/` 不教你"怎么点按钮"。
3. **设计稿是时点快照**：`docs/design/` 下每份文件开头都有一行 `> 状态：`（更新日期 / 已实施版本 / 是否被取代）。
   `tools/docs-check.py` 会断言"每份都有且只有一行、取值在约定范围内"——**别再拿设计稿当今天的口径读**，
   今天的真相只认 [`CURRENT_STATE.md`](../CURRENT_STATE.md) 和 [`docs/dev/`](dev/README.md)。
