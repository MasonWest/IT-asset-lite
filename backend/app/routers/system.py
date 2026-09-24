"""系统信息 + 二维码生成。"""

from __future__ import annotations

import io

import qrcode
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import (
    APP_NAME,
    APP_VERSION,
    BACKUP_DIR,
    PORT,
    detect_lan_ip,
    get_base_url,
    get_database_file,
)
from ..database import get_db
from ..models import Asset
from ..schemas import SystemInfo

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system/info", response_model=SystemInfo, summary="系统信息 / 扫码基准地址")
def system_info(db: Session = Depends(get_db)):
    count = db.execute(select(func.count(Asset.id))).scalar_one()
    return SystemInfo(
        app_name=APP_NAME,
        version=APP_VERSION,
        port=PORT,
        lan_ip=detect_lan_ip(),
        base_url=get_base_url(),
        database_file=get_database_file(),
        backup_dir=str(BACKUP_DIR),
        asset_count=int(count),
    )


@router.get(
    "/qrcode.png",
    summary="按内容生成二维码 PNG",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def qrcode_png(
    text: str = Query(..., min_length=1, max_length=1024, description="二维码内容，通常是资产详情页 URL"),
    size: int = Query(240, ge=64, le=1024, description="输出图片边长（像素）"),
    border: int = Query(1, ge=0, le=6, description="静默区宽度（模块数）"),
):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=border,
    )
    qr.add_data(text)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#1f2329", back_color="white").convert("RGB")
    # NEAREST 放大保持模块边缘锐利，不糊
    img = img.resize((size, size), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-cache, max-age=0"},
    )
