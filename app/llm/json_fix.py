"""LLM 输出 JSON 兜底修复.

大模型偶尔返回带 markdown 代码块、多余文字、尾随逗号的非合法 JSON.
本模块尽量修复, 修复失败返回 None.
"""
from __future__ import annotations

import json
import re
from typing import Any

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def extract_and_parse(text: str) -> dict[str, Any] | None:
    """从 LLM 文本中提取 JSON 对象并解析.

    尝试顺序:
      1. 直接 json.loads
      2. 去 markdown 代码块后解析
      3. 正则提取首个 {...} 后修复尾随逗号解析
    全部失败返回 None.
    """
    if not text:
        return None

    text = text.strip()

    # 1. 直接解析
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 2. 去代码块
    m = _CODE_FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            text = candidate

    # 3. 正则提取 + 修复
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return None
    candidate = m.group(0)
    candidate = _TRAILING_COMMA_RE.sub(r"\1", candidate)
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        return None
    return None
