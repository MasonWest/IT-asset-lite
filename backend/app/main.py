"""FastAPI 应用入口。

一个进程同时提供三件事：
  1. /api/*   后端接口
  2. /        前端构建产物（frontend/dist）
  3. /api/qrcode.png  二维码图片

所以手机只要连同一个局域网，访问 http://<本机IP>:8080 就能用。

另外这里挂了一个中间件，负责两件和审计有关、但和具体路由无关的事：
把客户端 IP 塞进 ContextVar（业务代码不用传 Request），
以及给失败的写请求补一条审计（业务事务已经炸了，只能用独立会话写）。
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from . import __version__
from .config import APP_NAME, FRONTEND_DIST, PORT, detect_lan_ip, get_base_url, get_database_file
from .database import SessionLocal, ensure_schema
from .models import AuditAction, AuditTarget
from .routers import assets, audit, device_types, inventory, meta, relations, system, transfer
from .seed import seed_if_empty
from .services import audit as audit_service

logger = logging.getLogger("it_asset")

#: 需要审计「失败」的请求方法。GET 只读不记 —— 除了导出，那个在路由里显式记。
_AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: 是否信任反向代理传来的 X-Forwarded-For。
#: 默认不信任：这东西客户端可以随便伪造，审计日志里的 IP 一旦能自己填就毫无意义了。
#: 只有确实部署在 Nginx / 网关后面时才设 IT_ASSET_TRUST_PROXY=1。
_TRUST_PROXY = (os.getenv("IT_ASSET_TRUST_PROXY") or "").strip() in ("1", "true", "True", "yes")

#: 失败请求 → 人话操作名。审计页上写「POST /api/assets/7/operations」没人看得懂。
_FAILED_ROUTES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^/api/assets/(\d+)/operations$"), AuditAction.ASSET_OPERATE, AuditTarget.ASSET),
    (re.compile(r"^/api/assets/(\d+)$"), AuditAction.ASSET_UPDATE, AuditTarget.ASSET),
    (re.compile(r"^/api/assets$"), AuditAction.ASSET_CREATE, AuditTarget.ASSET),
    (re.compile(r"^/api/pairings/\d+/release$"), AuditAction.ASSET_UNPAIR, AuditTarget.ASSET),
    (re.compile(r"^/api/pairings/rebind$"), AuditAction.ASSET_REBIND, AuditTarget.ASSET),
    (re.compile(r"^/api/pairings$"), AuditAction.ASSET_PAIR, AuditTarget.ASSET),
    (re.compile(r"^/api/inventory/tasks/\d+/mark$"), AuditAction.INVENTORY_MARK, AuditTarget.INVENTORY),
    (re.compile(r"^/api/inventory/tasks/\d+/close$"), AuditAction.INVENTORY_CLOSE, AuditTarget.INVENTORY),
    (re.compile(r"^/api/inventory/tasks/\d+/reopen$"), AuditAction.INVENTORY_REOPEN, AuditTarget.INVENTORY),
    (re.compile(r"^/api/inventory/tasks/\d+$"), AuditAction.INVENTORY_DELETE, AuditTarget.INVENTORY),
    (re.compile(r"^/api/inventory/tasks$"), AuditAction.INVENTORY_CREATE, AuditTarget.INVENTORY),
    (re.compile(r"^/api/transfer/import$"), AuditAction.ASSET_IMPORT, AuditTarget.FILE),
    (re.compile(r"^/api/device-types/\d+$"), AuditAction.TYPE_UPDATE, AuditTarget.DEVICE_TYPE),
    (re.compile(r"^/api/device-types$"), AuditAction.TYPE_CREATE, AuditTarget.DEVICE_TYPE),
]


def _client_ip(request: Request) -> str:
    """取客户端 IP。

    默认只认真实连接来源，不认 X-Forwarded-For（可伪造）。
    部署在反代后面时设 IT_ASSET_TRUST_PROXY=1 才读转发头。
    """
    if _TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real = request.headers.get("x-real-ip")
        if real and real.strip():
            return real.strip()
    return request.client.host if request.client else ""


def _infer_failed_target(path: str) -> tuple[str, Optional[int], str]:
    """从失败请求的 URL 猜出「想动哪个对象」，让审计里那条记录不至于干巴巴只有一行 404。"""
    for pattern, action, target_type in _FAILED_ROUTES:
        match = pattern.match(path)
        if not match:
            continue
        target_id = int(match.group(1)) if match.groups() else None
        return action, target_id, target_type
    return AuditAction.ASSET_UPDATE, None, AuditTarget.SYSTEM


def _audit_failed_request(request: Request, ip: str, status_code: int, error: str) -> None:
    """给失败的写请求补一条审计。用独立会话，失败也不抛。"""
    action, target_id, target_type = _infer_failed_target(request.url.path)

    label: Optional[str] = None
    if target_type == AuditTarget.ASSET and target_id is not None:
        with SessionLocal() as db:
            label = audit_service.asset_label_by(db, target_id)
    elif target_type == AuditTarget.INVENTORY and target_id is not None:
        label = f"盘点任务 #{target_id}"

    reason = error or f"请求被拒绝（HTTP {status_code}）"
    audit_service.write_audit_standalone(
        action,
        target_type=target_type,
        target_id=target_id,
        target_label=label,
        summary=f"操作未成功：{reason}",
        detail={"method": request.method, "path": request.url.path, "status_code": status_code},
        ip=ip or None,
        status="failed",
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ensure_schema 负责建表 + 轻量迁移，前两阶段跑起来的库直接升级，不用重建
    migration = ensure_schema()
    with SessionLocal() as db:
        result = seed_if_empty(db)

    line = "-" * 62
    print(f"\n{line}")
    print(f"  {APP_NAME} v{__version__}")
    print(line)
    print(f"  本机访问 : http://127.0.0.1:{PORT}")
    print(f"  手机扫码 : {get_base_url()}")
    print(f"  接口文档 : http://127.0.0.1:{PORT}/docs")
    print(f"  数据库   : {get_database_file()}")
    if migration.get("category_added"):
        print("  已升级：device_types 补上 category 列并回填设备类别")
    if migration.get("timestamps_shifted"):
        print(
            f"  已升级：{migration['timestamps_shifted']} 个时间戳由 UTC 校正为本地时间"
            f"（本机时区偏移 {migration['offset_minutes']:+d} 分钟）"
        )
    if result["types_created"] or result["assets_created"]:
        print(f"  已写入种子数据：设备类型 {result['types_created']} 个 / 资产 {result['assets_created']} 台")
    if result.get("relations_created") or result.get("events_created"):
        print(
            f"  已回填：配对 {result.get('relations_created', 0)} 条 / 历史 {result.get('events_created', 0)} 条"
        )
    if detect_lan_ip() == "127.0.0.1":
        print("  [!] 未探测到局域网 IP，手机可能访问不了，请用 IT_ASSET_PUBLIC_BASE_URL 手动指定")
    if not FRONTEND_DIST.is_dir():
        print(f"  [!] 未找到前端构建产物 {FRONTEND_DIST}，请先在 frontend/ 执行 npm run build")
    print(f"{line}\n")

    yield


app = FastAPI(
    title=APP_NAME,
    version=__version__,
    description=(
        "办公室 IT 资产台账 · 二维码扫码查看 · 主机显示器配对 · 状态流转 · 盘点 · "
        "操作审计 · 批量导入导出"
    ),
    lifespan=lifespan,
)

# 开发模式下前端跑在 5173，跨域放开；生产同源其实用不上
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    """注入客户端 IP + 给失败的写请求补审计。

    成功的写操作由各路由自己调 record_audit()（它知道操作对象和变更内容），
    这里只兜住"路由自己记不了"的那半边：事务已经失败的请求。
    """
    ip = _client_ip(request)
    audit_service.set_request_context(ip, request.url.path)

    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 —— 记完再原样抛出，交给 FastAPI 的异常处理
        is_write = request.method in _AUDITED_METHODS and request.url.path.startswith("/api/")
        if is_write:
            _audit_failed_request(request, ip, 500, f"{type(exc).__name__}: {exc}")
        raise

    is_write = request.method in _AUDITED_METHODS and request.url.path.startswith("/api/")
    if is_write and response.status_code >= 400:
        _audit_failed_request(request, ip, response.status_code, "")
    return response


app.include_router(system.router)
app.include_router(meta.router)
app.include_router(device_types.router)
app.include_router(assets.router)
app.include_router(relations.router)
app.include_router(inventory.router)
app.include_router(transfer.router)
app.include_router(audit.router)


@app.get("/api/health", tags=["system"], summary="健康检查")
def health():
    return {"status": "ok", "version": __version__}


class SpaStaticFiles(StaticFiles):
    """静态托管前端，并为前端路由（history 模式）做 index.html 回退。

    手机扫 /asset/3 进来时，磁盘上并没有这个文件，需要回落到 index.html 交给 Vue Router。
    但 /api/* 的 404 必须原样抛出，不能吞成 HTML。
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Windows 上 os.path.normpath 会把 "api/xxx" 变成 "api\\xxx"，所以先统一分隔符
            normalized = path.replace("\\", "/").lstrip("/")
            is_api = normalized == "api" or normalized.startswith("api/")
            if exc.status_code == 404 and not is_api:
                return await super().get_response("index.html", scope)
            raise


if FRONTEND_DIST.is_dir():
    app.mount("/", SpaStaticFiles(directory=str(FRONTEND_DIST), html=True), name="spa")
else:

    @app.get("/", include_in_schema=False)
    def frontend_not_built():
        return JSONResponse(
            status_code=503,
            content={
                "detail": "前端尚未构建",
                "hint": f"请在 frontend 目录执行 npm install && npm run build，产物应为 {FRONTEND_DIST}",
            },
        )
