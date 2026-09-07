"""P3-C 手写 Location 匹配(纯代码, 零 API 成本).

v3.8 精确匹配: 手写 BL1N == 清单 BL1N → 强匹配, 跳过 P4/P5, 直接 VERIFIED.
v3.8-FF 无效标记过滤: 手写 BL41(无匹配清单) → 删除标记 → 走 AI 流程.
v3.8-GG 模糊匹配纠正: 手写 BL4N(无精确匹配) → 模糊匹配 BL1N(1 字符差异
        + 混淆对) → 纠正为 BL1N → 按精确匹配处理.
"""
from __future__ import annotations

from dataclasses import dataclass

# v3.8-GG 支持的手写混淆对(无向): 1↔4, 1↔7, 1↔I, 1↔L, 4↔A, 4↔H,
# 0↔O, 0↔D, 5↔S, 6↔G, 8↔B, N↔M, N↔H, L↔4, L↔1
_CONFUSION_PAIRS: list[tuple[str, str]] = [
    ("1", "4"),
    ("1", "7"),
    ("1", "I"),
    ("1", "L"),
    ("4", "A"),
    ("4", "H"),
    ("0", "O"),
    ("0", "D"),
    ("5", "S"),
    ("6", "G"),
    ("8", "B"),
    ("N", "M"),
    ("N", "H"),
    ("L", "4"),
]

# 展开为对称集合, O(1) 查询
_CONFUSION_SET: set[frozenset[str]] = {frozenset(p) for p in _CONFUSION_PAIRS}


def is_confusable(a: str, b: str) -> bool:
    """两个字符是否为手写混淆对."""
    if a == b:
        return True
    return frozenset((a.upper(), b.upper())) in _CONFUSION_SET


@dataclass
class HandwritingMatchResult:
    """手写编码与清单 Location 的匹配结果."""
    matched: bool
    match_source: str            # exact / fuzzy / none
    matched_code: str | None     # 命中的清单 Location 编码
    corrected: str | None = None  # GG: 模糊纠正后的编码(=matched_code)


def _normalize(code: str | None) -> str:
    return (code or "").strip().upper()


def _fuzzy_equal(handwritten: str, list_code: str) -> bool:
    """1 字符差异 + 混淆对 → 模糊相等(v3.8-GG)."""
    if len(handwritten) != len(list_code):
        return False
    diff_positions = [
        (a, b) for a, b in zip(handwritten, list_code) if a != b
    ]
    if len(diff_positions) != 1:
        return False
    a, b = diff_positions[0]
    return is_confusable(a, b)


def match_location(
    handwritten: str | None, list_codes: list[str | None]
) -> HandwritingMatchResult:
    """手写 Location 编码与清单 Location 列表匹配.

    优先级:
      1. 精确匹配(含 3 位简写命中 4 位清单码前缀, 如 BR1 → BR1V)
      2. v3.8-GG 模糊纠正(同长度 1 字符差异 + 混淆对, 唯一候选才纠正)
      3. 无匹配 → v3.8-FF 过滤(matched=False)

    Args:
        handwritten: P2-B 识别的手写编码
        list_codes: 清单所有 Item 的 Location 编码

    Returns:
        HandwritingMatchResult
    """
    hw = _normalize(handwritten)
    if not hw:
        return HandwritingMatchResult(matched=False, match_source="none", matched_code=None)

    codes = [_normalize(c) for c in list_codes if _normalize(c)]

    # 1. 精确匹配
    for code in codes:
        if hw == code:
            return HandwritingMatchResult(
                matched=True, match_source="exact", matched_code=code,
            )

    # 1b. 3 位简写命中 4 位清单码(省略了底梁后缀 N/M/V)
    for code in codes:
        if len(code) == len(hw) + 1 and code.startswith(hw) and code[-1] in "NMV":
            return HandwritingMatchResult(
                matched=True, match_source="exact", matched_code=code,
            )

    # 2. v3.8-GG 模糊纠正: 收集全部候选, 唯一时才采纳(避免歧义误纠)
    fuzzy_hits = [code for code in dict.fromkeys(codes) if _fuzzy_equal(hw, code)]
    if len(fuzzy_hits) == 1:
        return HandwritingMatchResult(
            matched=True,
            match_source="fuzzy",
            matched_code=fuzzy_hits[0],
            corrected=fuzzy_hits[0],
        )

    # 3. v3.8-FF: 无匹配 → 过滤
    return HandwritingMatchResult(matched=False, match_source="none", matched_code=None)
