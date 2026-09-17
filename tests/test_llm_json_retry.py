from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.client import LLMClient


class _Limiter:
    async def acquire(self) -> None:
        return None


class _Response:
    def __init__(self, content: str, *, finish_reason: str = "stop") -> None:
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
        self.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2)

    def model_dump(self) -> dict:
        return {"choices": [{"finish_reason": self.choices[0].finish_reason}]}


class _Completions:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _client(responses: list[_Response]) -> tuple[LLMClient, _Completions]:
    completions = _Completions(responses)
    client = object.__new__(LLMClient)
    client.provider = "local"
    client.model = "qwen3-vl"
    client._qps = 5
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


@pytest.fixture
def retry_config(monkeypatch):
    cfg = SimpleNamespace(
        llm=SimpleNamespace(
            mock=False,
            retry_max=3,
            retry_backoff_base=0,
            temperature=0.1,
        )
    )
    monkeypatch.setattr("app.llm.client.get_config", lambda: cfg)
    monkeypatch.setattr("app.llm.client.get_limiter", lambda *_args: _Limiter())
    return cfg


async def test_invalid_json_is_retried_and_token_usage_is_accumulated(retry_config):
    client, completions = _client([
        _Response('{"items":[', finish_reason="length"),
        _Response('{"items":[]}'),
    ])

    result = await client.chat_vision("OCR", [])

    assert result.parsed == {"items": []}
    assert result.error is None
    assert result.retries == 1
    assert result.tokens_prompt == 20
    assert result.tokens_completion == 4
    assert len(completions.calls) == 2
    retry_prompt = completions.calls[1]["messages"][0]["content"][0]["text"]
    assert "必须输出合法 JSON" in retry_prompt


async def test_last_invalid_response_keeps_diagnostics(retry_config):
    client, completions = _client([
        _Response('{"items":[', finish_reason="length"),
        _Response('{"items":[', finish_reason="length"),
        _Response('{"items":[', finish_reason="length"),
    ])

    result = await client.chat_vision("OCR", [])

    assert result.parsed is None
    assert result.error == "llm_json_parse_failed"
    assert result.content == '{"items":['
    assert result.finish_reason == "length"
    assert result.parse_error
    assert result.retries == 2
    assert len(completions.calls) == 3
