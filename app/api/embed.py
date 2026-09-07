"""任务级、只读的 iframe 鉴定结果页与数据接口。"""
from __future__ import annotations

import hmac
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embed_tokens import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    EmbedTokenError,
    create_embed_token,
    verify_embed_token,
)
from app.db.models import AuditReport, Task, TaskPhoto
from app.db.session import get_async_session
from app.storage.object_store import LocalObjectStore, get_object_store


router = APIRouter(tags=["嵌入式鉴定结果"])
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


class EmbedTokenRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    expires_in_seconds: int = Field(
        default=DEFAULT_TTL_SECONDS,
        ge=MIN_TTL_SECONDS,
        le=MAX_TTL_SECONDS,
        description="Token 有效期，默认 1800 秒，最大 3600 秒",
    )


def _configured_issuer_key() -> str:
    return (
        os.environ.get("EMBED_API_KEY", "").strip()
        or os.environ.get("API_KEY", "").strip()
    )


def _extract_api_key(x_api_key: str | None, authorization: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


async def require_embed_issuer_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """仅合作方后端可签发嵌入 Token。"""
    expected = _configured_issuer_key()
    if not expected:
        raise HTTPException(503, "embed_api_key_not_configured")
    provided = _extract_api_key(x_api_key, authorization)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "unauthorized")


def _extract_embed_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "embed":
        return ""
    return value.strip()


def require_task_token(task_id: str, authorization: str | None) -> None:
    token = _extract_embed_token(authorization)
    if not token:
        raise HTTPException(401, "embed_token_required")
    try:
        verify_embed_token(token, expected_task_id=task_id)
    except EmbedTokenError as exc:
        status_code = 403 if exc.code == "task_access_denied" else 401
        raise HTTPException(status_code, exc.code) from exc


def _task_payload(task: Task, photos: list[TaskPhoto]) -> dict:
    total_stages = 10
    stage_order = [
        "p1_a", "p1_b", "p2_a", "p2_b", "p2_c",
        "p3", "p4_a1", "p4_a2", "p5_b", "p6",
    ]
    stage_index = stage_order.index(task.current_stage) + 1 if task.current_stage in stage_order else 0
    return {
        "task_id": task.task_id,
        "status": task.status,
        "current_stage": task.current_stage,
        "progress": {
            "current": stage_index,
            "total": total_stages,
            "detail": task.progress_detail,
        },
        "container_number": task.container_number,
        "final_recommendation": task.final_recommendation,
        "error": (
            {"code": task.error_code, "message": task.error_msg}
            if task.error_code else None
        ),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "photos": [
            {
                "photosId": photo.external_photo_id,
                "photo_id": photo.photo_id,
                "seq": photo.seq,
                "filename": photo.original_filename,
            }
            for photo in photos
        ],
    }


@router.post("/api/v1/embed-tokens")
async def issue_embed_token(
    body: EmbedTokenRequest,
    request: Request,
    _: None = Depends(require_embed_issuer_api_key),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """使用长期 API Key 换取只允许读取单个任务的短期 Token。"""
    task = await session.get(Task, body.task_id)
    if not task:
        raise HTTPException(404, "task_not_found")
    try:
        token, claims = create_embed_token(
            body.task_id,
            expires_in_seconds=body.expires_in_seconds,
        )
    except EmbedTokenError as exc:
        raise HTTPException(503, exc.code) from exc

    configured_base = os.environ.get("EMBED_PUBLIC_BASE_URL", "").strip().rstrip("/")
    base_url = configured_base or str(request.base_url).rstrip("/")
    # 使用 URL fragment，Token 不会被浏览器发送到 HTTP 访问日志或 Referer。
    embed_url = f"{base_url}/embed/audits/{body.task_id}#token={token}"
    return {
        "task_id": body.task_id,
        "embed_url": embed_url,
        "expires_at": datetime.fromtimestamp(claims.expires_at, timezone.utc).isoformat(),
        "expires_in_seconds": body.expires_in_seconds,
        "scope": "audit:read",
        "read_only": True,
    }


@router.get("/embed/audits/{task_id}", include_in_schema=False)
async def embed_page(task_id: str) -> HTMLResponse:
    """公开的无数据页面外壳；所有业务数据仍须任务 Token。"""
    html_path = _STATIC_DIR / "embed.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; img-src 'self' blob: data:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                "connect-src 'self'; frame-ancestors *; base-uri 'none'; form-action 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/v1/embed/audits/{task_id}")
async def get_embedded_audit(
    task_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """读取 Token 所绑定任务的状态、结果和证据索引。"""
    require_task_token(task_id, authorization)
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task_not_found")
    photos = (
        await session.execute(
            select(TaskPhoto).where(TaskPhoto.task_id == task_id).order_by(TaskPhoto.seq)
        )
    ).scalars().all()
    report_row = await session.get(AuditReport, task_id) if task.status == "succeeded" else None
    return {
        "task": _task_payload(task, list(photos)),
        "report": report_row.report if report_row else None,
    }


@router.get("/api/v1/embed/audits/{task_id}/photos/{photo_id}")
async def get_embedded_photo(
    task_id: str,
    photo_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """读取 Token 所绑定任务中的一张证据照片。"""
    require_task_token(task_id, authorization)
    photo = await session.get(TaskPhoto, photo_id)
    if not photo or photo.task_id != task_id:
        raise HTTPException(404, "photo_not_found")
    if not photo.url:
        raise HTTPException(404, "photo_url_empty")

    store = get_object_store()
    media_type = mimetypes.guess_type(photo.url)[0] or "image/jpeg"
    if isinstance(store, LocalObjectStore):
        root = store._root.resolve()
        path = (root / photo.url).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "file_not_found")
        content = path.read_bytes()
    else:
        try:
            import httpx

            url = store.presigned_url(photo.url, expires_hours=1)
            async with httpx.AsyncClient(timeout=30.0) as client:
                upstream = await client.get(url)
                upstream.raise_for_status()
            content = upstream.content
            media_type = upstream.headers.get("content-type", media_type)
        except Exception as exc:
            raise HTTPException(502, "failed_to_fetch_photo") from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
