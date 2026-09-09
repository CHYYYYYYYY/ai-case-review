"""为同源管理网页签发短时、无状态的 API 会话令牌。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time


COOKIE_NAME = "audit_api_session"
DEFAULT_TTL_SECONDS = 8 * 60 * 60


def create_api_session(api_key: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    expires_at = int(time.time()) + ttl_seconds
    nonce = secrets.token_urlsafe(18)
    payload = f"{expires_at}.{nonce}"
    signature = hmac.new(
        api_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_api_session(token: str, api_key: str, *, now: int | None = None) -> bool:
    if not token or not api_key:
        return False
    try:
        expires_text, nonce, signature = token.split(".", 2)
        expires_at = int(expires_text)
    except (TypeError, ValueError):
        return False
    if not nonce or expires_at < (int(time.time()) if now is None else now):
        return False
    payload = f"{expires_at}.{nonce}"
    expected = hmac.new(
        api_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
