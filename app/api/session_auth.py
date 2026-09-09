"""管理网页使用的短时 API 鉴权会话。"""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, HTTPException, Request, Response

from app.core.api_sessions import COOKIE_NAME, DEFAULT_TTL_SECONDS, create_api_session


router = APIRouter(prefix="/api/v1/session", tags=["网页鉴权"])


def _provided_api_key(request: Request) -> str:
    provided = request.headers.get("X-API-Key", "")
    if not provided:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
    return provided


@router.post("")
async def create_browser_session(request: Request, response: Response) -> dict:
    """校验一次 API Key，并为同源网页设置短时 HttpOnly Cookie。"""
    configured = os.environ.get("API_KEY", "").strip()
    provided = _provided_api_key(request)
    if not configured:
        raise HTTPException(503, "api_key_not_configured")
    if not provided or not hmac.compare_digest(provided, configured):
        raise HTTPException(401, "invalid_api_key")

    response.set_cookie(
        key=COOKIE_NAME,
        value=create_api_session(configured),
        max_age=DEFAULT_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )
    return {"authenticated": True, "expires_in": DEFAULT_TTL_SECONDS}


@router.delete("")
async def delete_browser_session(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"authenticated": False}
