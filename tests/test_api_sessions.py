from __future__ import annotations

from app.core.api_sessions import create_api_session, verify_api_session


def test_api_session_round_trip() -> None:
    token = create_api_session("test-key", ttl_seconds=60)

    assert verify_api_session(token, "test-key") is True
    assert verify_api_session(token, "wrong-key") is False


def test_api_session_rejects_tampering_and_expiry() -> None:
    token = create_api_session("test-key", ttl_seconds=60)
    expires_text, nonce, signature = token.split(".", 2)

    assert verify_api_session(f"{expires_text}.{nonce}x.{signature}", "test-key") is False
    assert verify_api_session(token, "test-key", now=int(expires_text) + 1) is False
    assert verify_api_session("invalid", "test-key") is False
