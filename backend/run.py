"""启动脚本：python run.py [--host 0.0.0.0] [--port 8080]"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 允许从任意目录执行本文件
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 IT 资产管理系统后端")
    parser.add_argument("--host", default=os.getenv("IT_ASSET_HOST", "0.0.0.0"), help="监听地址")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("IT_ASSET_PORT") or os.getenv("PORT") or 8080),
        help="监听端口",
    )
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    # config.py 在 import 时读环境变量，所以必须先写回环境
    os.environ["IT_ASSET_HOST"] = args.host
    os.environ["IT_ASSET_PORT"] = str(args.port)

    import uvicorn

    from app.main import app

    if args.reload:
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
