"""ISO 6346 集装箱号归一化与校验。"""
from __future__ import annotations

import re

_CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")

_LETTER_VALUES = {
    letter: value
    for letter, value in zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        (10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24,
         25, 26, 27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38),
    )
}

# 只按字符所在位置纠错：前四位必须是字母，后七位必须是数字。
_OCR_TO_LETTER = str.maketrans({
    "0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B",
})
_OCR_TO_DIGIT = str.maketrans({
    "O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
    "Z": "2", "S": "5", "G": "6", "B": "8",
})


def is_valid_container_number(value: str | None) -> bool:
    """校验格式及 ISO 6346 校验位。"""
    if not value:
        return False
    code = value.strip().upper()
    if not _CONTAINER_RE.fullmatch(code):
        return False

    total = 0
    for index, char in enumerate(code[:10]):
        number = _LETTER_VALUES[char] if char.isalpha() else int(char)
        total += number * (2 ** index)
    expected_check_digit = (total % 11) % 10
    return expected_check_digit == int(code[-1])


def normalize_container_number(value: object) -> str | None:
    """修正常见 OCR 混淆，并拒绝校验位不正确的箱号。"""
    if value is None:
        return None
    compact = re.sub(r"[\s_-]+", "", str(value).strip().upper())
    if len(compact) != 11:
        return None

    normalized = (
        compact[:4].translate(_OCR_TO_LETTER)
        + compact[4:].translate(_OCR_TO_DIGIT)
    )
    return normalized if is_valid_container_number(normalized) else None
