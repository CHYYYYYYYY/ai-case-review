"""IICL 标准代码表.

来源: 原文档第六章. 用于 P3 校验、P4 匹配、P6 报告.
"""
from __future__ import annotations

# Component 标准代码 (60+ 项)
COMPONENT_CODES: frozenset[str] = frozenset({
    "CPA", "CPS", "CPO", "CPI", "CPJ", "CFG", "RCI", "DAH", "DAA", "PAA",
    "LBA", "LBR", "LBC", "LBH", "LBG", "LBL", "GTO", "GTA", "GRS", "DHC",
    "DHR", "DRT", "DSB", "DSC", "DSH", "DST", "MPD", "HGH", "RLA", "RLF",
    "RUF", "RUP", "RLG", "VRA", "PSC", "RAA", "HEP", "FPP", "FSP", "FPB",
    "FLP", "CMU", "CMA", "CMA'", "FSA", "FTP", "CML", "FLL", "FLT", "FLA",
    "TUB", "TUP", "LSB", "LSR", "RBO", "RBH", "INP", "RBS", "RCK", "CPL",
    "HGP", "DHL", "DHB", "FHS", "TUA", "DPA", "DRN", "MCO",
})

# Damage 标准代码 (20 项) + 扩展码 (OS/DB 等, 业务中常见但非核心 20 项)
DAMAGE_CODES: frozenset[str] = frozenset({
    "BT", "BW", "BR", "BN", "CK", "CO", "CT", "CU", "DT", "DY",
    "HO", "IR", "LO", "MS", "ML", "PF", "SA", "WT", "FZ", "GD",
    # 扩展码
    "OS",   # Other Stain / 标记残留
    "DB",   # Dangerous cargo mark / 危标残留
})

# Location 面代码 (6 个)
LOCATION_FACE_CODES: dict[str, str] = {
    "L": "Left 左",
    "R": "Right 右",
    "T": "Top 顶",
    "B": "Bottom 底",
    "D": "Door 门端",
    "F": "Front 前端",
}

# Component 中文对照(常见项)
COMPONENT_NAMES: dict[str, str] = {
    "PAA": "波纹面板(侧板/门板/前板/顶板)",
    "FPP": "木地板",
    "FSP": "钢地板",
    "FPB": "地板支撑",
    "CMU": "横梁",
    "CMA": "横梁",
    "MCO": "清洁",
    "CPA": "角柱",
    "CPS": "角柱",
    "DHC": "箱门铰链",
    "DHR": "箱门铰链",
    "DRT": "门锁杆",
    "DSB": "门锁杆",
    "DSC": "门锁杆",
    "DSH": "门把手",
    "DST": "门把手",
    "MPD": "门胶条",
    "HGH": "门铰链",
    "RLA": "门锁",
    "RLF": "门锁",
    "RUF": "门锁",
    "RUP": "门锁",
    "LBA": "铭牌",
    "LBR": "铭牌",
    "LBC": "铭牌",
    "VRA": "通风器",
    "PSC": "标志",
    "INP": "内衬",
    "TUB": "管",
    "TUP": "管",
    "FLP": "地板",
    "FLL": "地板",
    "FLT": "地板",
    "FLA": "地板",
    "FSA": "地板",
    "FTP": "地板",
}

# Damage 中文对照
DAMAGE_NAMES: dict[str, str] = {
    "BT": "弯曲",
    "BW": "弓形/波浪变形",
    "BR": "断裂",
    "BN": "烧损",
    "CK": "裂纹",
    "CO": "锈蚀",
    "CT": "污染",
    "CU": "切割",
    "DT": "凹损/凹陷",
    "DY": "脏污",
    "HO": "破洞",
    "IR": "划伤",
    "LO": "松动",
    "MS": "缺失",
    "ML": "标记",
    "PF": "漆膜失效",
    "SA": "刮擦",
    "WT": "磨损",
    "FZ": "冻结",
    "GD": "凹陷",
    # 扩展码 (业务中常见, 非核心 20 项)
    "OS": "标记残留/其他污渍",
    "DB": "危标残留",
}


def is_valid_component(code: str | None) -> bool:
    return bool(code) and code in COMPONENT_CODES


def is_valid_damage(code: str | None) -> bool:
    return bool(code) and code in DAMAGE_CODES


def is_valid_location_face(code: str | None) -> bool:
    return bool(code) and len(code) >= 1 and code[0] in LOCATION_FACE_CODES
