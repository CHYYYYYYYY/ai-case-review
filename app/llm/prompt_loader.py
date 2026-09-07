"""Prompt 加载器.

从 prompts/*.md 读取模板, 支持 {variable} 占位符注入.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache(maxsize=32)
def _load_raw(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, **variables: object) -> str:
    """加载 Prompt 并填充占位符.

    Args:
        name: Prompt 名(不含扩展名), 如 p1_a_nameplate
        **variables: 占位符变量, 会替换 {key}

    Returns:
        渲染后的 Prompt 字符串
    """
    template = _load_raw(name)
    if not variables:
        return template
    # 安全替换, 缺失的占位符保留原样
    return template.format_map(_SafeDict(variables))


class _SafeDict(dict):
    """format_map 时, 缺失的 key 保留 {key} 原样."""

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return "{" + key + "}"
