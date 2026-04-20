"""文件存储抽象层。

通过 STORAGE_BACKEND 环境变量切换：
  local — 保存到本地 uploads/{YYYY-MM}/{uuid}_{name}
  r2    — Cloudflare R2（S3 兼容 API，接口占位，生产阶段启用）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fastapi import UploadFile, HTTPException

from backend.config import STORAGE_BACKEND, UPLOAD_DIR, R2_BUCKET, R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY

# ── 常量 ─────────────────────────────────────────────────────────
MAX_FILE_SIZE = 10 * 1024 * 1024          # 10 MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


# ── 协议接口 ──────────────────────────────────────────────────────

class FileStorage(Protocol):
    async def save(self, file: UploadFile, filename: str) -> str:
        """保存文件，返回可访问的 URL / 相对路径。"""
        ...

    async def delete(self, url: str) -> None:
        """删除文件。"""
        ...


# ── LocalStorage ──────────────────────────────────────────────────

class LocalStorage:
    """开发环境：保存到本地磁盘。

    路径规则：uploads/{YYYY-MM}/{uuid}_{original_name}
    """

    def __init__(self, base_dir: Path = UPLOAD_DIR) -> None:
        self.base_dir = base_dir

    async def save(self, file: UploadFile, filename: str) -> str:
        # 1. 内容类型校验
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"不支持的文件类型 '{file.content_type}'，仅接受 JPG/PNG/WEBP/PDF",
            )

        # 2. 扩展名校验
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"不支持的扩展名 '{suffix}'，仅接受 .jpg / .jpeg / .png / .webp / .pdf",
            )

        # 3. 读取并校验大小
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 10MB 上限（当前 {len(content) / 1024 / 1024:.1f} MB）",
            )

        # 4. 生成路径：uploads/YYYY-MM/uuid_name
        month_slug = datetime.now().strftime("%Y-%m")
        month_dir = self.base_dir / month_slug
        month_dir.mkdir(parents=True, exist_ok=True)
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        dest = month_dir / unique_name
        dest.write_bytes(content)

        # 返回 web 可访问相对路径，由 FastAPI StaticFiles 服务
        return f"/uploads/{month_slug}/{unique_name}"

    async def delete(self, url: str) -> None:
        # url 形如 /uploads/YYYY-MM/xxx.png，还原为文件系统路径
        rel = url.lstrip("/")   # uploads/YYYY-MM/xxx.png
        path = self.base_dir.parent / rel
        if path.exists():
            path.unlink()


# ── R2Storage（接口占位，生产阶段启用）───────────────────────────

class R2Storage:
    """生产环境：Cloudflare R2（S3 兼容）。"""

    def __init__(self) -> None:
        try:
            import boto3
            self._s3 = boto3.client(
                "s3",
                endpoint_url=R2_ENDPOINT,
                aws_access_key_id=R2_ACCESS_KEY_ID,
                aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            )
        except Exception as e:
            raise RuntimeError(f"R2Storage 初始化失败，请检查 R2 环境变量：{e}") from e

    async def save(self, file: UploadFile, filename: str) -> str:
        if file.content_type not in ALLOWED_CONTENT_TYPES:
            raise HTTPException(status_code=422, detail="仅接受 JPG/PNG/WEBP/PDF")
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="文件超过 10MB 上限")

        key = f"{datetime.now().strftime('%Y-%m')}/{uuid.uuid4().hex}_{filename}"
        self._s3.put_object(Bucket=R2_BUCKET, Key=key, Body=content, ContentType=file.content_type)
        return f"{R2_ENDPOINT}/{R2_BUCKET}/{key}"

    async def delete(self, url: str) -> None:
        key = url.split(f"{R2_BUCKET}/", 1)[-1]
        self._s3.delete_object(Bucket=R2_BUCKET, Key=key)


# ── 工厂函数 ──────────────────────────────────────────────────────

def get_storage() -> LocalStorage | R2Storage:
    """根据 STORAGE_BACKEND 返回对应实现。"""
    if STORAGE_BACKEND == "r2":
        return R2Storage()
    return LocalStorage()
