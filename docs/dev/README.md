# 开发文档入口

> **30 秒版**：先读 [invariants.md](invariants.md)（11 条红线，每条都写着"这么干出过什么事"），
> 再读 [data-model.md](data-model.md)（13 张表 + 什么不许落库），
> 然后按你要改的模块读对应的那一篇。**别从头读全套，25 篇读不完。**

## 我改哪块，读哪篇

| 我要改的 | 读这篇 | 顺带看一眼 |
| --- | --- | --- |
| 后端接口 / 加一个路由 | [api.md](api.md) + [architecture.md](architecture.md) | [invariants.md](invariants.md) 第 1、7 条 |
| 表结构 / 派生口径 | [data-model.md](data-model.md) | [decisions/](decisions/README.md) 里的 map-relations、inventory |
| 空间地图 / 点位 | [map.md](map.md) | [frontend.md](frontend.md)（断点与布局不变量） |
| 平面图编辑器 / 批量生成 | [map.md](map.md) + [decisions/floor-map.md](decisions/floor-map.md) | [user/map.md](../user/map.md) 看用户怎么用 |
| 轻资产 | [light-assets.md](light-assets.md) | [user/light-assets.md](../user/light-assets.md) |
| 台账页 / 筛选 / 下拉 | [frontend.md](frontend.md) + [decisions/ui-ledger.md](decisions/ui-ledger.md) | — |
| 任何"图标 + 文字"的地方 | [frontend.md](frontend.md) + [decisions/ui-contract.md](decisions/ui-contract.md) | 决策 88 / 101 / 103 / 104 |
| 导入导出 | [decisions/import-export.md](decisions/import-export.md) | [api.md](api.md) |
| 审计 | [decisions/audit.md](decisions/audit.md) | [invariants.md](invariants.md) 第 1、4 条 |
| 端口 / 数据目录 / 扫码地址 | [configuration.md](configuration.md) | — |
| 写验收脚本 / 跑验收 | [acceptance.md](acceptance.md) | [invariants.md](invariants.md) 第 9 条 |
| 提交前该做什么 | [maintenance.md](maintenance.md) | — |
| 术语（改文案前） | [glossary.md](glossary.md) | — |

## 怎么把它跑起来

1. 装一次依赖：`python -m pip install -r backend\requirements.txt`
2. 双击 `start.bat`（挑端口 → 实测 Python → 查依赖 → 起服务 → 5 秒后开浏览器）
3. 停：窗口内 `Ctrl+C`，或双击 `stop.bat`；备份：双击 `backup.bat`
4. 不用脚本：`cd backend && python run.py`（换端口加 `--port 18080`）
5. 局域网 / 手机访问、防火墙、探测到的 IP 不对怎么办 → [../user/network.md](../user/network.md)
6. 数据维护命令（`seed.py` 灌演示 / `link_monitors.py` 批量配对 / `reset.py` 清空）→ [../user/operations.md](../user/operations.md)

> **不建虚拟环境**是刻意的（决策 10）：脚本按 `py -3` → `python` → `python3` 逐个**实测**，
> 谁能跑起依赖自检就用谁。你自己想建 venv 当然可以，只是脚本不会替你建。

## 这个仓库的特殊约定

- **改了 `.py` 要重启进程**；改了前端要 `npx vite build`（`dist` 被 gitignore，构建产物不入库）。
  两条混了，就会出现"改的明明是代码，界面却没变"的假 bug。
- **验收脚本的实体在 `.tmp/`**（不入库）：文档只记"有哪些脚本、各自守什么"，数字由脚本自己打印。
- **文档只有 5 类**（门面 / 状态 / 变更 / 用户 / 开发），改任何一份之前先看 [maintenance.md](maintenance.md)
  的改动税分级表 —— 默认什么都不用改。
