"""用于只读嵌入页的短期、任务级签名 Token。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any


TOKEN_VERSION = "e1"
TOKEN_SCOPE = "audit:read"
TOKEN_AUDIENCE = "audit-embed"
DEFAULT_TTL_SECONDS = 30 * 60
MIN_TTL_SECONDS = 60
MAX_TTL_SECONDS = 60 * 60


class EmbedTokenError(ValueError):
    """Token 无效、过期或越权。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EmbedTokenClaims:
    task_id: str
    issued_at: int
    expires_at: int
    token_id: str


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:
        raise EmbedTokenError("invalid_token") from exc


def _secret() -> bytes:
    value = os.environ.get("EMBED_TOKEN_SECRET", "").strip()
    if len(value) < 32:
        raise EmbedTokenError("embed_token_secret_not_configured")
    return value.encode("utf-8")


def create_embed_token(
    task_id: str,
    *,
    expires_in_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
    token_id: str | None = None,
) -> tuple[str, EmbedTokenClaims]:
    """签发仅能读取一个任务的 HMAC-SHA256 Token。"""
    if not task_id:
        raise EmbedTokenError("task_id_required")
    if not MIN_TTL_SECONDS <= expires_in_seconds <= MAX_TTL_SECONDS:
        raise EmbedTokenError("invalid_token_ttl")

    issued_at = int(time.time() if now is None else now)
    if token_id is None:
        token_id = _b64encode(os.urandom(12))
    payload: dict[str, Any] = {
        "aud": TOKEN_AUDIENCE,
        "exp": issued_at + expires_in_seconds,
        "iat": issued_at,
        "jti": token_id,
        "scope": TOKEN_SCOPE,
        "task_id": task_id,
    }
    encoded_payload = _b64encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{TOKEN_VERSION}.{encoded_payload}".encode("ascii")
    signature = _b64encode(hmac.new(_secret(), signing_input, hashlib.sha256).digest())
    claims = EmbedTokenClaims(
        task_id=task_id,
        issued_at=issued_at,
        expires_at=issued_at + expires_in_seconds,
        token_id=token_id,
    )
    return f"{TOKEN_VERSION}.{encoded_payload}.{signature}", claims


def verify_embed_token(
    token: str,
    *,
    expected_task_id: str,
    now: int | None = None,
) -> EmbedTokenClaims:
    """校验签名、有效期、用途和任务绑定。"""
    try:
        version, encoded_payload, provided_signature = token.split(".")
    except ValueError as exc:
        raise EmbedTokenError("invalid_token") from exc
    if version != TOKEN_VERSION:
        raise EmbedTokenError("invalid_token")

    signing_input = f"{version}.{encoded_payload}".encode("ascii")
    expected_signature = _b64encode(hmac.new(_secret(), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise EmbedTokenError("invalid_token")

    try:
        payload = json.loads(_b64decode(encoded_payload))
        task_id = str(payload["task_id"])
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
        token_id = str(payload["jti"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EmbedTokenError("invalid_token") from exc

    current_time = int(time.time() if now is None else now)
    if payload.get("aud") != TOKEN_AUDIENCE or payload.get("scope") != TOKEN_SCOPE:
        raise EmbedTokenError("invalid_token_scope")
    if issued_at > current_time + 60:
        raise EmbedTokenError("invalid_token")
    if expires_at <= current_time:
        raise EmbedTokenError("token_expired")
    if not hmac.compare_digest(task_id, expected_task_id):
        raise EmbedTokenError("task_access_denied")

    return EmbedTokenClaims(
        task_id=task_id,
        issued_at=issued_at,
        expires_at=expires_at,
        token_id=token_id,
    )
