"""可选 API Key 鉴权."""
from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


class APIKeyMiddleware(BaseHTTPMiddleware):
    """当环境变量 API_KEY 非空时, 要求请求携带 X-API-Key 或 Authorization: Bearer."""

    EXEMPT_PATHS = {
        "/health", "/docs", "/redoc", "/openapi.json", "/",
        "/api/v1/embed-tokens",
    }

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ):
        api_key = os.environ.get("API_KEY", "").strip()
        if not api_key:
            return await call_next(request)

        path = request.url.path
        if (
            path in self.EXEMPT_PATHS
            or path.startswith("/static")
            or path.startswith("/embed/")
            or path.startswith("/api/v1/embed/")
        ):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if not provided:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()

        if provided != api_key:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

        return await call_next(request)
