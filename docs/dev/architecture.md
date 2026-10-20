# 架构与技术栈

> 面向：接手的开发者。这里回答"东西在哪、怎么跑起来的"。
> 目录树只要改动超过三行，就顺手更新 —— 它最容易过期。

## 三、技术栈与运行环境

单体应用：FastAPI + SQLAlchemy 2.x + SQLite；前端 Vue 3 + TS + Vite + Element Plus，
构建产物由后端托管 —— 一个进程、一个端口。不依赖 Docker / Nginx / 微服务，
没有 Node 运行时依赖（只在构建前端时用 Node）。二维码用后端 `qrcode` 出图，表格用 `openpyxl`。

### 运行环境约定（重要）

**启动脚本使用「电脑上已经装好的 Python」，不创建虚拟环境、不自动装依赖。**

这是 2026-09-24 的明确决定：以前 `start.bat` 会自己建 `backend\.venv` 再往里装依赖，
在"本机 Python 本来就能跑"的机器上反而会因建环境/装包失败而起不来 —— 自找麻烦。

- 脚本按 `py -3` → `python` → `python3` 顺序**实测**，选第一个"能跑且依赖齐全"的
  （不是 `where python` 取第一个 —— 能找到一个 Python 和它能跑本系统是两回事）
- 缺依赖时只打印**缺哪几个包** + 一行可直接复制的安装命令，绝不自动往用户的 Python 里装东西
- 依赖自检交给 `backend/check_env.py`（只用标准库，任何 Python 都能跑它）
- 依赖只需**手动装一次**：`python -m pip install -r backend\requirements.txt`

---

## 四、目录结构

```
IT_snipe/
├── start.bat / stop.bat / backup.bat / dev.bat    ★ 启动 / 停止 / 备份 / 开发模式
├── README.md                 面向使用者的说明（怎么装、怎么用、FAQ）
├── LICENSE                   Apache-2.0 全文（**逐字取自 apache.org，不许手改**，见决策 105）
├── NOTICE                    署名与「哪些不在本仓库内」三条事实（同上：不许写成额外限制）
├── CURRENT_STATE.md          ← 本文件：项目状态唯一入口
├── CHANGELOG.md              版本变更记录
├── .gitignore / .gitattributes
├── backups/                  备份文件（不入库）
├── .tmp/                     运行期临时文件，如 last-port.txt（不入库）
├── backend/
│   ├── run.py                启动入口
│   ├── check_env.py          依赖自检（标准库 only，给 .bat 探测用）
│   ├── backup_db.py          备份脚本（SQLite 在线备份接口 + 完整性校验）
│   ├── seed.py               手动灌演示数据（启动时不会自动灌）
│   ├── link_monitors.py      按编号后缀批量补主机↔显示器配对（默认干跑，--yes 才写）
│   ├── import_workstations.py 灌点位 JSON（默认干跑，--yes 才写；显式忽略 JSON 里的 assets）
│   ├── link_workstations.py  按使用人把「1 主机 + 1 显示器」的资产关联到点位（默认干跑）
│   ├── restore_station.py    恢复误删的点位（默认干跑，--yes 才写；编码不回收，位置自己拖）
│   ├── reset.py              清空业务数据（自动备份 + 输入 RESET 二次确认）
│   ├── requirements.txt
│   ├── data/it_assets.db     SQLite 数据库（首次启动自动建表 + 初始化设备类型字典）
│   └── app/
│       ├── main.py           应用装配 + 静态托管 + 审计中间件
│       ├── config.py         端口 / 局域网 IP / 扫码基准地址 / 备份目录 / 版本号
│       ├── database.py       引擎、会话、表分类、老库迁移（ensure_schema）
│       ├── models.py         13 张表的模型定义（设备 / 点位 / 盘点 / 审计 / 轻资产五个域）
│       ├── schemas.py        请求响应模型
│       ├── schemas_workstations.py  点位 / 地图盘点视图的模型（单独一个文件，免得 schemas.py 继续膨胀）
│       ├── schemas_floor_maps.py    平面图读写的模型（layout 走 JSON 文档）
│       ├── seed.py           字典自动初始化（ensure_dictionary）+ 演示数据手动灌入（seed_demo）
│       ├── services/         业务逻辑（路由只管收发）
│       │   ├── events.py         变更事件 + 字段差异比较
│       │   ├── pairing.py        配对绑定 / 解绑 / 换绑
│       │   ├── serialize.py      模型 → 响应对象的统一出口
│       │   ├── filters.py        列表筛选与盘点范围共用的条件构造
│       │   ├── audit.py          审计写入（同事务 / 独立会话两条路径）
│       │   ├── spreadsheet.py    CSV / xlsx 导出与解析公共件
│       │   ├── workstation_importer.py  点位 JSON 导入（四桶 + 显式忽略 mock assets）
│       │   ├── floor_map_layout.py      平面图 layout 的**唯一解析入口**（parse_layout）
│       │   └── importer.py       批量导入：列定义 / 模板 / 预览 / 落库
│       └── routers/
│           ├── assets.py         资产 CRUD + 时间线 + 状态流转
│           ├── relations.py      配对管理
│           ├── inventory.py      盘点任务 / 标记 / 差异 / 导出 / 扫码上下文 / 地图盘点视图
│           ├── workstations.py   点位 CRUD / 批量布局 / 资产挂靠 / JSON 导入 / 批量生成
│           ├── floor_maps.py     平面图读 / 存（整张覆盖 + 乐观锁，**单图**）
│           ├── device_types.py   设备类型管理
│           ├── transfer.py       批量导入 / 导出 / 模板（/api/transfer）
│           ├── audit.py          审计查询与导出（**只有 GET**）
│           ├── meta.py           筛选候选值 / 配对统计
│           └── system.py         系统信息 + 二维码出图
└── frontend/
    ├── vite.config.ts
    └── src/
        ├── App.vue            外壳：顶栏导航 + 设置弹窗（操作人 / 扫码地址 / 备份目录）
        ├── api/index.ts       接口封装
        ├── stores/app.ts      系统信息 & 扫码地址 & 当前操作人
        ├── composables/       跨页面复用的行为：useBackNav（返回上一页）、
        │                      useFreeTextOptions（自由文本候选下拉：拼音筛选 + 新建项垫底）
        ├── utils/format.ts    图标 / 状态色 / 事件色 / 审计配色 / 日期格式化
        ├── components/        资产表单、类型管理、状态流转、配对面板、时间线、盘点条
        └── views/             台账列表、设备详情、配对管理、盘点列表/详情、
                               批量导入、操作审计、标签打印、系统设置
```

---
