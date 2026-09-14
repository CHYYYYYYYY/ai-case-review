"""IICL 标准代码表.

来源: 原文档第六章. 用于 P3 校验、P4 匹配、P6 报告.
"""
from __future__ import annotations

import re

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

# P5 视觉模型使用自然语言描述损伤。只有观察到的损伤与清单 Damage
# 编码相符时，照片才可以作为“通过”证据；其他损伤只能作为参考。
# 这里同时兼容历史模型已经输出过的常见同义词。
DAMAGE_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "BT": ("弯曲", "弯折", "bent", "bend"),
    "BW": ("弓形", "波浪变形", "变形", "bowed", "wave"),
    "BR": ("断裂", "破裂", "折断", "broken", "break"),
    "BN": ("烧损", "烧蚀", "火烧", "burn"),
    "CK": ("裂纹", "裂缝", "开裂", "crack"),
    "CO": ("锈蚀", "锈迹", "腐蚀", "corrosion", "rust"),
    "CT": ("污染", "污染物", "contamination"),
    "CU": ("切割", "切痕", "cut"),
    "DT": ("凹损", "凹陷", "凹痕", "dent"),
    "DY": ("脏污", "污垢", "dirty", "dirt"),
    "HO": ("破洞", "孔洞", "洞", "hole"),
    "IR": ("划伤", "擦伤", "scratch"),
    "LO": ("松动", "松脱", "loose"),
    "MS": ("缺失", "丢失", "missing"),
    "ML": ("inspector标记", "检查员标记", "标记", "marking"),
    "PF": ("漆膜失效", "漆面脱落", "涂层失效", "paint failure"),
    "SA": ("刮擦", "擦痕", "scuff"),
    "WT": ("磨损", "磨耗", "wear"),
    "FZ": ("冻结", "冻损", "frozen", "freeze"),
    "GD": ("凹陷", "凹痕", "凹损", "dent"),
    "OS": ("标记残留", "其他污渍", "残留", "residue"),
    "DB": ("危标残留", "危险品标识残留", "placard residue"),
}


def damage_type_matches_code(damage_code: str | None, damage_type: str | None) -> bool:
    """判断视觉模型识别的损伤类型能否证明清单中的 Damage 编码。"""
    aliases = DAMAGE_TYPE_ALIASES.get((damage_code or "").strip().upper())
    observed = (damage_type or "").strip().lower()
    if not aliases or not observed or observed in {"无", "none", "no damage"}:
        return False

    tokens = [
        re.sub(r"[\s_-]+", "", token)
        for token in re.split(r"[/、,，;；|]+", observed)
        if token.strip()
    ]
    normalized_aliases = [re.sub(r"[\s_-]+", "", alias.lower()) for alias in aliases]
    return any(
        token == alias or alias in token
        for token in tokens
        for alias in normalized_aliases
    )


def is_valid_component(code: str | None) -> bool:
    return bool(code) and code in COMPONENT_CODES


def is_valid_damage(code: str | None) -> bool:
    return bool(code) and code in DAMAGE_CODES


def is_valid_location_face(code: str | None) -> bool:
    return bool(code) and len(code) >= 1 and code[0] in LOCATION_FACE_CODES
