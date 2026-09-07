from __future__ import annotations

import pytest

from app.core.embed_tokens import EmbedTokenError, create_embed_token, verify_embed_token


@pytest.fixture(autouse=True)
def token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_TOKEN_SECRET", "test-secret-that-is-at-least-32-characters-long")


def test_token_is_bound_to_one_task() -> None:
    token, claims = create_embed_token("aud_one", now=1_000, token_id="fixed-id")

    verified = verify_embed_token(token, expected_task_id="aud_one", now=1_001)

    assert verified.task_id == "aud_one"
    assert verified.expires_at == 2_800
    assert claims.token_id == "fixed-id"
    with pytest.raises(EmbedTokenError, match="task_access_denied"):
        verify_embed_token(token, expected_task_id="aud_two", now=1_001)


def test_expired_token_is_rejected() -> None:
    token, _ = create_embed_token("aud_one", expires_in_seconds=60, now=1_000)

    with pytest.raises(EmbedTokenError, match="token_expired"):
        verify_embed_token(token, expected_task_id="aud_one", now=1_060)


def test_tampered_token_is_rejected() -> None:
    token, _ = create_embed_token("aud_one", now=1_000)
    parts = token.split(".")
    parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")

    with pytest.raises(EmbedTokenError, match="invalid_token"):
        verify_embed_token(".".join(parts), expected_task_id="aud_one", now=1_001)


def test_secret_must_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBED_TOKEN_SECRET")

    with pytest.raises(EmbedTokenError, match="embed_token_secret_not_configured"):
        create_embed_token("aud_one")


@pytest.mark.parametrize("ttl", [0, 59, 3601])
def test_ttl_is_bounded(ttl: int) -> None:
    with pytest.raises(EmbedTokenError, match="invalid_token_ttl"):
        create_embed_token("aud_one", expires_in_seconds=ttl)
