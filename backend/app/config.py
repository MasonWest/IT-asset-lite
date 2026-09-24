"""运行期配置。

所有可调项都走环境变量，方便同一份代码在别人机器上直接跑。
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from .database import BASE_DIR, DB_FILE

APP_NAME = "IT 资产管理系统"
# 版本基线：三个阶段（台账+扫码 / 配对+盘点 / 审计+导入导出）合并后的首个对外版本
APP_VERSION = "1.0.0"

HOST = os.getenv("IT_ASSET_HOST") or "0.0.0.0"
# 默认 8080：8000 在很多机器上被打印控件 / 其他服务占着
PORT = int(os.getenv("IT_ASSET_PORT") or os.getenv("PORT") or 8080)

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
