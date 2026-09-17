"""LLM 输出 JSON 兜底修复.

大模型偶尔返回带 markdown 代码块、多余文字、尾随逗号的非合法 JSON.
本模块尽量修复, 修复失败返回 None.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


@dataclass(frozen=True)
class JSONParseResult:
    """JSON 提取结果，并保留最后一次解析失败的精确位置。"""

    parsed: dict[str, Any] | None
    error: str | None = None


def _loads_dict(candidate: str) -> JSONParseResult:
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return JSONParseResult(
            parsed=None,
            error=(
                f"{exc.msg} at line {exc.lineno} column {exc.colno} "
                f"(char {exc.pos})"
            ),
        )
    if not isinstance(obj, dict):
        return JSONParseResult(
            parsed=None,
            error=f"top-level JSON must be object, got {type(obj).__name__}",
        )
    return JSONParseResult(parsed=obj)


def extract_and_parse_detailed(text: str) -> JSONParseResult:
    """解析 LLM JSON，并返回可落库、可检索的失败原因。"""
    if not text:
        return JSONParseResult(parsed=None, error="empty response")

    text = text.strip()
    if not text:
        return JSONParseResult(parsed=None, error="blank response")

    # 1. 直接解析
    result = _loads_dict(text)
    if result.parsed is not None:
        return result
    last_error = result.error

    # 2. 去代码块
    m = _CODE_FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        result = _loads_dict(candidate)
        if result.parsed is not None:
            return result
        text = candidate
        last_error = result.error

    # 3. 正则提取 + 修复尾随逗号
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return JSONParseResult(
            parsed=None,
            error=last_error or "no complete JSON object found",
        )
    candidate = _TRAILING_COMMA_RE.sub(r"\1", m.group(0))
    result = _loads_dict(candidate)
    if result.parsed is not None:
        return result
    return JSONParseResult(parsed=None, error=result.error or last_error)


def extract_and_parse(text: str) -> dict[str, Any] | None:
    """从 LLM 文本中提取 JSON 对象并解析.

    尝试顺序:
      1. 直接 json.loads
      2. 去 markdown 代码块后解析
      3. 正则提取首个 {...} 后修复尾随逗号解析
    全部失败返回 None.
    """
    return extract_and_parse_detailed(text).parsed
