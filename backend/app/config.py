"""运行期配置。

所有可调项都走环境变量，方便同一份代码在别人机器上直接跑。
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from .database import BASE_DIR, DB_FILE

APP_NAME = "IT 资产管理系统"
# 版本基线：三个阶段（台账+扫码 / 配对+盘点 / 审计+导入导出）合并后的对外版本
APP_VERSION = "1.1.0"

HOST = os.getenv("IT_ASSET_HOST") or "0.0.0.0"
# 默认 8080：8000 在很多机器上被打印控件 / 其他服务占着
PORT = int(os.getenv("IT_ASSET_PORT") or os.getenv("PORT") or 8080)

#: 启动时是否自动灌演示数据（10 台示例资产 + 配对 + 初始履历）。
#: **默认关闭。** 删掉 .db 重启后应该是一个空库：只有设备类型这类字典数据，
#: 看不到任何测试资产。想要样例数据就显式执行 `python seed.py`。
#: 做演示 / 截图时想让它自动灌：设 `IT_ASSET_SEED_ON_STARTUP=1`，但别长期开着 ——
#: 那样一旦误删数据库文件，重启就会"长"出十台假设备，很难分辨真假。
SEED_ON_STARTUP = (os.getenv("IT_ASSET_SEED_ON_STARTUP") or "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# 备份目录（backup.bat 往这里写；也给将来的定时备份留个统一口径）
BACKUP_DIR = Path(os.getenv("IT_ASSET_BACKUP_DIR") or (BASE_DIR.parent / "backups"))

# 前端 dist 目录（前端构建产物由后端一起托管）
FRONTEND_DIST = Path(os.getenv("IT_ASSET_FRONTEND_DIST") or (BASE_DIR.parent / "frontend" / "dist"))

# 手动指定对外访问地址；不填就自动探测本机局域网 IP
_PUBLIC_BASE_URL_OVERRIDE = (os.getenv("IT_ASSET_PUBLIC_BASE_URL") or "").strip()


def detect_lan_ip() -> str:
    """探测本机局域网 IP。

    对着公网地址开一个 UDP socket 拿本机出口 IP —— UDP connect 不发包，
    只是让内核选路由，所以断网环境下也会退化到 127.0.0.1 而不报错。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def get_base_url() -> str:
    """手机扫码要用的绝对地址，例如 http://192.168.1.23:8000"""
    if _PUBLIC_BASE_URL_OVERRIDE:
        return _PUBLIC_BASE_URL_OVERRIDE.rstrip("/")
    return f"http://{detect_lan_ip()}:{PORT}"


def get_database_file() -> str:
    return str(DB_FILE)
