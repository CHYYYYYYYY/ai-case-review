"""JSON 兜底修复测试."""
from __future__ import annotations

from app.llm.json_fix import extract_and_parse


def test_plain_json():
    assert extract_and_parse('{"a": 1}') == {"a": 1}


def test_markdown_code_block():
    text = '```json\n{"a": 1}\n```'
    assert extract_and_parse(text) == {"a": 1}


def test_code_block_no_lang():
    text = '```\n{"a": 1}\n```'
    assert extract_and_parse(text) == {"a": 1}


def test_text_with_json():
    text = '好的, 这是结果:\n{"a": 1, "b": 2}\n以上是答案'
    assert extract_and_parse(text) == {"a": 1, "b": 2}


def test_trailing_comma():
    text = '{"a": 1, "b": [1, 2, 3,]}'
    assert extract_and_parse(text) == {"a": 1, "b": [1, 2, 3]}


def test_empty():
    assert extract_and_parse("") is None
    assert extract_and_parse("   ") is None


def test_garbage():
    assert extract_and_parse("hello world no json here") is None
