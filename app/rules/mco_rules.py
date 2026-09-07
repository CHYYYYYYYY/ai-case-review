"""P3-B MCO 硬规则.

零 LLM 调用: 关键词命中即直接判定 PASS / FAIL, 跳过 P4/P5.
"""
from __future__ import annotations

from enum import Enum


class MCOVerdict(str, Enum):
    """MCO 判定结果."""
    PASS = "pass"        # 直接标 VERIFIED, 跳过 P4/P5
    FAIL = "fail"        # 直接标 UNSUPPORTED/MISSING, 跳过 P4/P5
    NONE = "none"        # 非 MCO 项目或关键词未命中, 走标准流程


# MCO 直接通过的关键词(描述中包含即 PASS)
MCO_PASS_KEYWORDS: list[str] = [
    "sweep",
    "brush",
    "clean",
    "wash",
    "detergent",
    "清洗",
    "清扫",
    "刷洗",
    # 危标清除类(MCO + DB 典型场景, 描述常为 "Remove marking of dangerous cargo")
    "remove marking",
    "remove of marking",
    "removal of marking",
    "dangerous cargo",
    "dangerous mark",
    "去除危标",
    "清除危标",
    "拆除标记",
]

# MCO 直接不通过的关键词
MCO_FAIL_KEYWORDS: list[str] = [
    "remove residue",
    "lashing wire",
    "remove lashing",
    "removal of lashing",
    "去除绑扎",
    "清理绑扎",
    "拆除绑扎",
]


def check_mco(component_code: str | None, description: str | None) -> MCOVerdict:
    """判断 MCO 项目是否命中硬规则.

    Args:
        component_code: 清单项的 Component 编码(已纠错后)
        description: 清单项的描述文本

    Returns:
        MCOVerdict.PASS / FAIL / NONE
    """
    if not component_code:
        return MCOVerdict.NONE

    # 兼容 CEDEX 双码格式 'MCO/MCO': 取第一段判断
    primary = component_code.split("/", 1)[0].strip().upper()

    if primary != "MCO":
        return MCOVerdict.NONE

    desc = (description or "").lower()

    # 先判 FAIL(更严格), 避免被 PASS 关键词误覆盖
    for kw in MCO_FAIL_KEYWORDS:
        if kw.lower() in desc:
            return MCOVerdict.FAIL

    for kw in MCO_PASS_KEYWORDS:
        if kw.lower() in desc:
            return MCOVerdict.PASS

    return MCOVerdict.NONE


def is_mco_verdict_decided(verdict: MCOVerdict) -> bool:
    """是否已硬性决定(无需走 P4/P5)."""
    return verdict in (MCOVerdict.PASS, MCOVerdict.FAIL)
