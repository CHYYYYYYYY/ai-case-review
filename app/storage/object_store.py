"""MinIO 对象存储封装.

负责:
  - 启动时确保 bucket 存在
  - 上传图片文件, 返回可访问 URL
  - 任务完成后清理过期文件(beat 定时调用)
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minio import Minio
from minio.error import S3Error

from app.core.config import get_config
from app.core.logging import get_logger

_log = get_logger(__name__)

# 公网访问 URL 前缀(生产换成实际域名), 本地直连 minio 容器
PUBLIC_URL_PREFIX_ENV = "MINIO_PUBLIC_URL"


class ObjectStore:
    """MinIO 客户端封装."""

    def __init__(self) -> None:
        cfg = get_config().storage
        self._bucket = cfg.bucket()
        self._secure = cfg.secure
        self._client = Minio(
            cfg.endpoint(),
            access_key=cfg.access_key(),
            secret_key=cfg.secret_key(),
            secure=cfg.secure,
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                _log.info("bucket_created", bucket=self._bucket)
        except S3Error as e:
            _log.error("bucket_ensure_failed", bucket=self._bucket, error=str(e))

    def upload_image(self, data: bytes, ext: str = "jpg", prefix: str = "audits") -> str:
        """上传图片, 返回对象 key(后续可用 public_url 拼接访问)."""
        ext = ext.lstrip(".").lower()
        if ext == "jpeg":
            ext = "jpg"
        if ext not in {"jpg", "png", "webp"}:
            ext = "jpg"
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        obj_id = uuid.uuid4().hex
        object_name = f"{prefix}/{today}/{obj_id}.{ext}"
        content_type = {
            "jpg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
        }[ext]

        self._client.put_object(
            bucket_name=self._bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return object_name

    def public_url(self, object_name: str) -> str:
        """构造可访问 URL.

        本地: http://localhost:9000/{bucket}/{object}
        生产: 由环境变量 MINIO_PUBLIC_URL 覆盖
        """
        import os
        prefix = os.environ.get(PUBLIC_URL_PREFIX_ENV)
        if prefix:
            return f"{prefix.rstrip('/')}/{self._bucket}/{object_name}"
        cfg = get_config().storage
        scheme = "https" if cfg.secure else "http"
        return f"{scheme}://{cfg.endpoint()}/{self._bucket}/{object_name}"

    def presigned_url(self, object_name: str, expires_hours: int = 24) -> str:
        """生成预签名 URL(给 LLM 调用图片用)."""
        return self._client.presigned_get_object(
            self._bucket, object_name,
            expires=timedelta(hours=expires_hours),
        )

    def proxy_url(self, task_id: str, photo_id: str) -> str:
        """返回 API 代理图片 URL，绕过预签名过期问题."""
        return f"/api/v1/audits/{task_id}/photos/{photo_id}/image"

    def delete_object(self, object_name: str) -> None:
        try:
            self._client.remove_object(self._bucket, object_name)
        except S3Error as e:
            _log.warning("delete_object_failed", object_name=object_name, error=str(e))


class LocalObjectStore:
    """本地目录存储(DGX 宿主机部署, 无需 MinIO)."""

    def __init__(self) -> None:
        cfg = get_config().storage
        self._root = cfg.local_dir().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        _log.info("local_storage_ready", root=str(self._root))

    def upload_image(self, data: bytes, ext: str = "jpg", prefix: str = "audits") -> str:
        ext = ext.lstrip(".").lower()
        if ext == "jpeg":
            ext = "jpg"
        if ext not in {"jpg", "png", "webp"}:
            ext = "jpg"
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        obj_id = uuid.uuid4().hex
        object_name = f"{prefix}/{today}/{obj_id}.{ext}"
        path = self._root / object_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return object_name

    def public_url(self, object_name: str) -> str:
        return str(self._root / object_name)

    def proxy_url(self, task_id: str, photo_id: str) -> str:
        """返回 API 代理图片 URL，绕过浏览器 file:// 限制和跨域问题."""
        return f"/api/v1/audits/{task_id}/photos/{photo_id}/image"

    def presigned_url(self, object_name: str, expires_hours: int = 24) -> str:
        return f"file://{self._root / object_name}"

    def delete_object(self, object_name: str) -> None:
        path = self._root / object_name
        if path.exists():
            path.unlink()


# 模块级单例
_store: ObjectStore | LocalObjectStore | None = None


def get_object_store() -> ObjectStore | LocalObjectStore:
    global _store
    if _store is None:
        backend = get_config().storage.backend
        if backend == "local":
            _store = LocalObjectStore()
        else:
            _store = ObjectStore()
    return _store
