"""阶段基类 + 通用工具."""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.llm.client import LLMClient, LLMCallResult, estimate_cost_cny
from app.pipeline.schemas import PipelineContext


@dataclass
class StageResult:
    """阶段执行结果."""
    stage: str
    success: bool
    duration_ms: int
    tokens_used: int = 0
    cost_cny: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class BaseStage(abc.ABC):
    """阶段基类."""

    stage_name: str = ""

    def __init__(self, ctx: PipelineContext, llm: LLMClient):
        self.ctx = ctx
        self.llm = llm
        self.log = get_logger(self.__class__.__name__)
        self._tokens_used = 0

    @abc.abstractmethod
    async def run(self) -> StageResult:
        ...

    async def _call_llm(
        self, prompt: str, image_refs: list[str], *, temperature: float | None = None
    ) -> LLMCallResult:
        """统一 LLM 调用, 自动累加成本."""
        result = await self.llm.chat_vision(prompt, image_refs, temperature=temperature)
        self._tokens_used += result.total_tokens
        self.ctx.total_tokens += result.total_tokens
        self.ctx.total_cost_cny += estimate_cost_cny(
            result.model, result.tokens_prompt, result.tokens_completion
        )
        if result.error:
            self.ctx.errors.append(f"{self.stage_name}: {result.error}")
        return result

    def _make_result(
        self,
        success: bool,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
        duration_ms: int = 0,
        tokens: int | None = None,
    ) -> StageResult:
        return StageResult(
            stage=self.stage_name,
            success=success,
            duration_ms=duration_ms,
            tokens_used=tokens if tokens is not None else self._tokens_used,
            cost_cny=0.0,
            payload=payload or {},
            error=error,
        )

    @staticmethod
    def _timer() -> "Timer":
        return Timer()


class Timer:
    """简单计时器."""

    def __init__(self) -> None:
        self._t0 = 0.0

    def __enter__(self) -> "Timer":
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.monotonic() - self._t0) * 1000)

    @property
    def elapsed_ms(self) -> int:
        return getattr(self, "ms", 0)
