"""P3-A IICL 编码纠错.

针对常见 OCR 错误做映射修正, 修正后仍校验是否在 IICL 标准表内.
不在表内的标记 code_suspicious=True, 但保留参与下游(报告中标注).
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from app.rules.iicl_codes import (
    COMPONENT_CODES,
    DAMAGE_CODES,
    LOCATION_FACE_CODES,
    is_valid_component,
    is_valid_damage,
)


# Component 常见 OCR 错误映射
COMPONENT_FIX: dict[str, str] = {
    # MCO 系列
    "MCQ": "MCO",
    "MC0": "MCO",
    "MCO.": "MCO",
    "MCO ": "MCO",
    "MCO@": "MCO",
    # PAA 系列
    "PAA ": "PAA",
    "PAA@": "PAA",
    "PAA.": "PAA",
    "PA4": "PAA",
    "P44": "PAA",
    # CMA' 系列
    "CMA'": "CMA'",
    "CMA`": "CMA'",
    "CMA\"": "CMA'",
    "CMA": "CMA",
    # FPP 系列
    "FPP ": "FPP",
    "FPP.": "FPP",
    "FPP@": "FPP",
    "FPF": "FPP",
    # CMU 系列
    "CMU ": "CMU",
    "CMU.": "CMU",
    "CNW": "CMU",
    # CPA 系列
    "CP4": "CPA",
    "CPI": "CPI",
    # 其他常见混淆
    "DHC ": "DHC",
    "DHL ": "DHL",
}

# Damage 常见 OCR 错误映射
DAMAGE_FIX: dict[str, str] = {
    # BR 断裂
    "BRT": "BR",
    "BRK": "BR",
    "BR1": "BR",
    # DT 凹损
    "DTH": "DT",
    "DNT": "DT",
    "DT.": "DT",
    # CK 裂纹
    "CK.": "CK",
    "GK": "CK",
    # HO 破洞
    "HO.": "HO",
    "H0": "HO",
    # CO 锈蚀
    "C0": "CO",
    "CO.": "CO",
    # SA 刮擦
    "5A": "SA",
    "SA.": "SA",
    # WT 磨损
    "WT.": "WT",
    "W7": "WT",
    # LO 松动
    "L0": "LO",
    # MS 缺失
    "M5": "MS",
}

# Location 面代码混淆
LOCATION_FACE_FIX: dict[str, str] = {
    # 数字 0 误读为字母 O
    "O": "L",   # 默认按 Left 处理, 标 suspicious
    "0": "L",
    # 大小写
    "l": "L",
    "r": "R",
    "t": "T",
    "b": "B",
    "d": "D",
    "f": "F",
}

# Location 的中间数字位常见 OCR 混淆。这里仅在编码结构明确表明该段应为
# 数字时替换，避免把部件/方向字母误改掉。例如 BLSN -> BL5N。
LOCATION_DIGIT_FIX = str.maketrans({
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "B": "8",
})

_LOCATION_PLACEHOLDER_RE = re.compile(r"^[A-Z]XXX$")
_LOCATION_WITH_N_SUFFIX_RE = re.compile(
    r"^([A-Z]{2})([0-9OQDILZSGB]+)(N)$"
)
_LOCATION_NUMERIC_TAIL_RE = re.compile(
    r"^([A-Z]{2})([0-9OQDILZSGB]{2,})$"
)


@dataclass
class FixResult:
    """纠错结果."""
    original: str
    fixed: str | None
    suspicious: bool   # 修正后仍不在标准表内
    changed: bool      # 是否发生过修正

    @property
    def output(self) -> str | None:
        """最终输出: 修正后的编码, 严重可疑则为 None."""
        return self.fixed


def _split_dual_code(code: str) -> tuple[str, str | None]:
    """拆分 CEDEX 双码格式 'PAA/PAA' -> ('PAA', 'PAA').

    清单 OCR 常输出 '部件码/部件码' 格式(同部件内外侧代码),
    取第一段作为主码参与校验/匹配, 第二段保留用于报告展示.
    """
    if "/" in code:
        parts = [p.strip() for p in code.split("/", 1)]
        primary = parts[0]
        secondary = parts[1] if len(parts) > 1 and parts[1] else None
        return primary, secondary
    return code, None


def fix_component(raw: str | None) -> FixResult:
    """修正 Component 编码.

    兼容 CEDEX 双码格式 'PAA/PAA': 拆分后对主码做纠错,
    输出仍保留双码格式(报告可读性更好).
    """
    if not raw:
        return FixResult(original="", fixed=None, suspicious=False, changed=False)
    code = raw.strip().upper()
    original = code

    # 拆分双码 'PAA/PAA' -> 主码 'PAA'
    primary, secondary = _split_dual_code(code)

    # 1. 主码直接命中标准表
    if primary in COMPONENT_CODES:
        # 保留双码格式(若原输入是双码), 主码合法即可
        return FixResult(original=original, fixed=code, suspicious=False, changed=False)

    # 2. 主码查纠错表
    if primary in COMPONENT_FIX:
        fixed_primary = COMPONENT_FIX[primary]
        # 重组: fixed_primary / secondary (若存在)
        fixed = f"{fixed_primary}/{secondary}" if secondary else fixed_primary
        suspicious = fixed_primary not in COMPONENT_CODES
        return FixResult(
            original=original,
            fixed=fixed,
            suspicious=suspicious,
            changed=fixed != original,
        )

    # 3. 修不掉, 标可疑但保留原值
    return FixResult(original=original, fixed=code, suspicious=True, changed=False)


def fix_damage(raw: str | None) -> FixResult:
    """修正 Damage 编码."""
    if not raw:
        return FixResult(original="", fixed=None, suspicious=False, changed=False)
    code = raw.strip().upper()
    original = code

    if code in DAMAGE_CODES:
        return FixResult(original=original, fixed=code, suspicious=False, changed=False)

    if code in DAMAGE_FIX:
        fixed = DAMAGE_FIX[code]
        return FixResult(
            original=original,
            fixed=fixed,
            suspicious=fixed not in DAMAGE_CODES,
            changed=fixed != original,
        )

    return FixResult(original=original, fixed=code, suspicious=True, changed=False)


def fix_location_code(raw: str | None) -> FixResult:
    """按 Location 编码结构修正常见 OCR 混淆。

    除了历史的首字母纠错，还会修正确定处于数字段中的字母，例如
    ``BLSN``（估价单实际为 ``BL5N``）和 ``UXI5``（``UX15``）。
    ``IXXX`` 这类业务占位值保持不变。
    """
    if not raw:
        return FixResult(original="", fixed=None, suspicious=False, changed=False)
    code = re.sub(r"\s+", "", raw.strip().upper())
    original = code
    if not code:
        return FixResult(original="", fixed=None, suspicious=False, changed=False)
    if _LOCATION_PLACEHOLDER_RE.fullmatch(code):
        return FixResult(original=original, fixed=code, suspicious=False, changed=False)

    head = code[0]
    rest = code[1:]

    if head in LOCATION_FACE_FIX:
        head = LOCATION_FACE_FIX[head]
        code = head + rest

    match = _LOCATION_WITH_N_SUFFIX_RE.fullmatch(code)
    if match:
        code = f"{match.group(1)}{match.group(2).translate(LOCATION_DIGIT_FIX)}{match.group(3)}"
    else:
        match = _LOCATION_NUMERIC_TAIL_RE.fullmatch(code)
        if match:
            code = f"{match.group(1)}{match.group(2).translate(LOCATION_DIGIT_FIX)}"

    return FixResult(
        original=original,
        fixed=code,
        suspicious=head not in LOCATION_FACE_CODES,
        changed=code != original,
    )


def fix_location_face(raw: str | None) -> FixResult:
    """兼容旧调用名；新逻辑会校正完整 Location 编码。"""
    return fix_location_code(raw)
