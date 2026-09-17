from __future__ import annotations

from app.llm.client import LLMCallResult
from app.pipeline.schemas import PipelineContext
from app.pipeline.stages.p1_nameplate import P1ManifestOCRStage


class _InvalidJSONLLM:
    async def chat_vision(self, *_args, **_kwargs) -> LLMCallResult:
        return LLMCallResult(
            content='{"items":[',
            parsed=None,
            tokens_prompt=20,
            tokens_completion=8,
            duration_ms=10,
            provider="local",
            model="qwen3-vl",
            error="llm_json_parse_failed",
            retries=2,
            parse_error="Expecting value at line 1 column 11 (char 10)",
            finish_reason="length",
        )


async def test_p1_b_persists_json_parse_diagnostics():
    ctx = PipelineContext(task_id="aud_test", manifest_image_url="manifest.png")
    stage = P1ManifestOCRStage(ctx, _InvalidJSONLLM())

    result = await stage.run()

    assert result.success is False
    assert result.error == "p1_b_json_parse_failed"
    diagnostic = result.payload["llm_diagnostic"]
    assert diagnostic["raw_content"] == '{"items":['
    assert diagnostic["finish_reason"] == "length"
    assert diagnostic["retries"] == 2
