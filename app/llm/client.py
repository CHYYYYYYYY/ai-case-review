"""统一 LLM 客户端.

封装 OpenAI 兼容协议调用, 提供:
  - 限流(令牌桶)
  - 重试(指数退避)
  - 多模态图片输入(URL 或 base64)
  - JSON 输出兜底修复
  - 成本与耗时统计
"""
from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI, APIConnectionError, RateLimitError, APITimeoutError

from app.core.config import get_config
from app.core.logging import get_logger
from app.llm.json_fix import extract_and_parse_detailed
from app.llm.limiter import get_limiter

_log = get_logger(__name__)


class MockMode(str, Enum):
    OFF = "off"
    FIXED = "fixed"   # 返回固定 stub


@dataclass
class LLMCallResult:
    """单次 LLM 调用结果."""
    content: str
    parsed: dict[str, Any] | None
    tokens_prompt: int
    tokens_completion: int
    duration_ms: int
    provider: str
    model: str
    raw_response: Any = None
    error: str | None = None
    retries: int = 0
    parse_error: str | None = None
    finish_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.tokens_prompt + self.tokens_completion


class LLMClient:
    """OpenAI 兼容协议统一客户端."""

    def __init__(self, provider: str | None = None, model: str | None = None):
        cfg = get_config()
        self._cfg = cfg
        self.provider = provider or cfg.llm.resolve_provider()
        provider_cfg = cfg.llm.providers.get(self.provider)
        if provider_cfg is None:
            raise ValueError(f"未配置 LLM provider: {self.provider}")
        api_key = cfg.llm.get_api_key(self.provider)
        if not api_key and not cfg.llm.mock and not cfg.llm.allow_empty_api_key:
            raise ValueError(
                f"环境变量 {provider_cfg.api_key_env} 未设置, 无法调用 LLM"
            )
        self._api_key = api_key or "none"
        self._base_url = provider_cfg.base_url
        self._qps = provider_cfg.qps
        self._timeout = provider_cfg.timeout
        self.model = model or cfg.llm.default_model
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
        )

    async def chat_vision(
        self,
        prompt: str,
        image_refs: list[str],
        temperature: float | None = None,
        extra_retry_hint: bool = False,
    ) -> LLMCallResult:
        """调用多模态视觉模型.

        Args:
            prompt: 文本 Prompt
            image_refs: 图片引用列表, 元素可为:
                        - http(s):// URL(公网可访问)
                        - file:/// 本地路径(转 base64)
                        - data:image/...;base64,... (直接传)
            temperature: 温度, 默认用配置
            extra_retry_hint: 重试时附"必须返回合法 JSON"提示

        Returns:
            LLMCallResult, parsed 字段为解析后的 dict(失败为 None)
        """
        cfg = get_config()

        # Mock 模式: 调试代码逻辑时跳过真实调用
        if cfg.llm.mock:
            return self._mock_result(prompt, image_refs)

        # 构造消息
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for ref in image_refs:
            url = await self._to_image_url(ref)
            content.append({"type": "image_url", "image_url": {"url": url}})

        limiter = get_limiter(self.provider, self._qps)
        last_error: str | None = None
        last_retries = 0
        last_content = ""
        last_raw_response: Any = None
        last_parse_error: str | None = None
        last_finish_reason: str | None = None
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_duration_ms = 0

        if extra_retry_hint:
            content[0]["text"] = self._with_json_hint(prompt)

        for attempt in range(cfg.llm.retry_max):
            if attempt > 0:
                # 重试时强化 JSON 提示
                content[0]["text"] = self._with_json_hint(prompt)
                backoff = cfg.llm.retry_backoff_base ** attempt
                await asyncio.sleep(backoff)
                last_retries = attempt

            await limiter.acquire()
            t0 = time.monotonic()
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": content}],
                    temperature=temperature if temperature is not None else cfg.llm.temperature,
                )
                duration_ms = int((time.monotonic() - t0) * 1000)
                content_text = resp.choices[0].message.content or ""
                usage = resp.usage
                raw_response = resp.model_dump() if hasattr(resp, "model_dump") else None
                finish_reason = getattr(resp.choices[0], "finish_reason", None)
                parse_result = extract_and_parse_detailed(content_text)
                parsed = parse_result.parsed
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                total_duration_ms += duration_ms
                last_content = content_text
                last_raw_response = raw_response
                last_parse_error = parse_result.error
                last_finish_reason = finish_reason
                result = LLMCallResult(
                    content=content_text,
                    parsed=parsed,
                    tokens_prompt=total_prompt_tokens,
                    tokens_completion=total_completion_tokens,
                    duration_ms=total_duration_ms,
                    provider=self.provider,
                    model=self.model,
                    raw_response=raw_response,
                    retries=last_retries,
                    parse_error=parse_result.error,
                    finish_reason=finish_reason,
                )
                if parsed is None:
                    last_error = "llm_json_parse_failed"
                    _log.warning(
                        "llm_json_parse_failed",
                        provider=self.provider,
                        model=self.model,
                        attempt=attempt + 1,
                        max_attempts=cfg.llm.retry_max,
                        parse_error=parse_result.error,
                        finish_reason=finish_reason,
                        content_length=len(content_text),
                        content_preview=content_text[:500],
                    )
                    # 模型已经响应但 JSON 非法也属于可重试错误。旧逻辑在这里直接返回，
                    # 导致偶发的截断/转义错误立即终止整个稽核任务。
                    if attempt + 1 < cfg.llm.retry_max:
                        continue
                    result.error = last_error
                return result

            except (RateLimitError, APITimeoutError, APIConnectionError) as e:
                last_error = f"{type(e).__name__}: {e}"
                _log.warning("llm_retryable_error", attempt=attempt, error=last_error)
                continue
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                _log.error("llm_fatal_error", error=last_error)
                break

        # 全部失败
        return LLMCallResult(
            content=last_content,
            parsed=None,
            tokens_prompt=total_prompt_tokens,
            tokens_completion=total_completion_tokens,
            duration_ms=total_duration_ms,
            provider=self.provider,
            model=self.model,
            raw_response=last_raw_response,
            error=last_error,
            retries=last_retries,
            parse_error=last_parse_error,
            finish_reason=last_finish_reason,
        )

    async def _to_image_url(self, ref: str) -> str:
        """把不同来源的图片引用转为 LLM 接受的 URL 或 data URI."""
        # data URI 直接返回
        if ref.startswith("data:"):
            return ref

        # 本地文件路径(含 file://)转 base64
        if ref.startswith("file://"):
            ref = ref[len("file://"):]
        if not ref.startswith(("http://", "https://")):
            path = Path(ref)
            if path.exists():
                return self._file_to_data_uri(path)
            return ref

        # http(s) URL: 若是本地地址(localhost/127.0.0.1), 云端 LLM 访问不到,
        # 自动下载转 base64; 否则原样返回让 LLM 自己拉
        lowered = ref.lower()
        if "://localhost" in lowered or "://127.0.0.1" in lowered:
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    resp = await c.get(ref)
                    resp.raise_for_status()
                    data = resp.content
                # 从 URL 或 content-type 推断 mime
                mime = "image/jpeg"
                if ".png" in lowered:
                    mime = "image/png"
                elif ".webp" in lowered:
                    mime = "image/webp"
                elif resp.headers.get("content-type", "").startswith("image/"):
                    mime = resp.headers["content-type"].split(";")[0]
                b64 = base64.b64encode(data).decode("ascii")
                return f"data:{mime};base64,{b64}"
            except Exception as e:
                _log.warning("local_image_download_failed", url=ref, error=str(e))
                return ref

        return ref

    @staticmethod
    def _file_to_data_uri(path: Path) -> str:
        """本地文件转 data URI."""
        suffix = path.suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "jpeg")
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/{mime};base64,{b64}"

    @staticmethod
    def _with_json_hint(prompt: str) -> str:
        if "必须输出合法 JSON" in prompt:
            return prompt
        return prompt + "\n\n注意: 必须输出合法 JSON, 不要 markdown 代码块, 不要解释文字."

    def _mock_result(self, prompt: str, image_refs: list[str]) -> LLMCallResult:
        """调试模式: 返回空 dict, 让上游降级处理."""
        _log.info("llm_mock_call", prompt_len=len(prompt), image_count=len(image_refs))
        return LLMCallResult(
            content="{}",
            parsed={},
            tokens_prompt=10,
            tokens_completion=10,
            duration_ms=1,
            provider=self.provider,
            model=self.model + "(mock)",
        )


# 模型计价(元/千 token), 用于成本估算, 实际以阿里云账单为准
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    # (input 元/千token, output 元/千token)
    "qwen3-vl": (0.0, 0.0),
    "qwen3.7-plus": (0.0008, 0.002),
    "qwen-plus": (0.0008, 0.002),
    "qwen-turbo": (0.0003, 0.0006),
    "moonshot-v1-8k": (0.012, 0.012),
}


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """估算单次调用成本(元)."""
    p = _PRICE_TABLE.get(model, (0.001, 0.002))
    return round(prompt_tokens / 1000 * p[0] + completion_tokens / 1000 * p[1], 6)
