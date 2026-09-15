"""流水线各阶段数据结构.

按技术方案 3.2 节 + v3.8-GG 升级, 阶段间传递的强类型 dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.rules.mco_rules import MCOVerdict


@dataclass
class PhotoInfo:
    """输入照片基础信息."""
    photo_id: str
    seq: int
    url: str
    original_filename: str | None = None
    external_photo_id: str | None = None


@dataclass
class ManifestItem:
    """清单单条维修项(P1-B 提取 → P3 纠错后)."""
    item_no: int
    raw_text: str = ""
    raw_component: str | None = None
    raw_location_code: str | None = None
    raw_damage_code: str | None = None
    raw_repair_type: str | None = None
    parsed_size: str | None = None
    description: str | None = None
    # 估价单当前维修项的 Total 金额。None 表示原单未提供或无法可靠识别。
    total: float | None = None

    # P3 纠错后字段
    component: str | None = None          # 纠错后
    location_code: str | None = None      # 纠错后
    damage_code: str | None = None        # 纠错后
    component_suspicious: bool = False
    damage_suspicious: bool = False
    location_suspicious: bool = False

    # P3-B MCO 硬规则
    mco_verdict: MCOVerdict = MCOVerdict.NONE

    # P3-B PAA 标记
    is_paa: bool = False
    paa_panel_face: str | None = None

    # P3-C 手写 Location 强匹配 (v3.8-FF / v3.8-GG)
    strong_match: bool = False            # 手写编码精确/模糊命中 → 跳过 P4/P5
    match_source: str = "none"            # exact / fuzzy / none
    matched_photo_ids: list[str] = field(default_factory=list)  # 强匹配来源照片

    # P4/P5 输出
    core_photo_ids: list[str] = field(default_factory=list)  # 向后兼容: 仅 photo_id
    core_photo_matches: list[PhotoMatchInfo] = field(default_factory=list)  # v3.8-GG: 含匹配元数据
    photo_evidence_ids: list[str] = field(default_factory=list)   # P5-B: 有损伤且 score>=0.6
    reference_photo_ids: list[str] = field(default_factory=list)  # P5-B: 有损伤但 score<0.6
    verification_status: str = ""         # verified/partial/missing/unsupported
    auditor_notes: str = ""


@dataclass
class PhotoIndex:
    """P2-A 单张照片索引结果(v3.8 富字段)."""
    photo_id: str
    component_type: str       # panel / floor / bottom / structural / cargo
    photo_type: str = "close_up"
    is_on_truck: bool = False
    # v3.8 新增字段
    end_visible: bool = False
    is_interior: bool = False
    is_interior_confidence: float = 0.0
    has_cargo: bool = False
    has_caliper: bool = False
    has_damage: bool = False
    is_plate_info: bool = False
    likely_location: str = "unknown"   # side_panel/door_panel/front_end/floor/bottom/unknown
    component_code: str | None = None  # PAA/FPP/LBA 或 None
    stage_error: str | None = None


@dataclass
class HandwrittenMark:
    """P2-B 手写/箱管批注 Location 识别结果."""
    photo_id: str
    has_handwritten_location: bool = False
    handwritten_location: str | None = None
    all_locations: list[str] = field(default_factory=list)
    chinese_direction: str | None = None   # 左 / 右 / None
    confidence: float = 0.0
    # v3.8-FF/GG 处理结果
    filtered: bool = False        # FF: 无匹配清单 → 丢弃标记
    corrected_to: str | None = None  # GG: 模糊纠正后的编码
    stage_error: str | None = None


@dataclass
class DirectionResult:
    """P4-A2 方向确认结果: AI 观察事实 + 代码层计算."""
    photo_id: str
    # AI 输出的观察事实
    left_end: str = "unknown"        # door_end/front_end/side_panel/floor/unknown
    right_end: str = "unknown"
    far_end: str = "unknown"         # door_end/front_end/none/unknown
    light_direction: str = "none"    # left/right/none
    shot_type: str = "unknown"       # lateral/longitudinal/doorway/exterior/unknown
    reason: str = ""
    # 代码层计算结果(文档 4.1 节)
    facing: str = "unknown"          # front/rear/unknown
    side: str = "unknown"            # left_side/right_side/unknown
    score: float = 0.0               # 0.85 / 0.60 / 0.0
    stage_error: str | None = None


@dataclass
class CargoInfo:
    """P2-C 货物识别结果."""
    cargo_name: str | None
    cargo_name_confidence: float = 0.0
    cargo_desc: str = ""
    source_photo_ids: list[str] = field(default_factory=list)


@dataclass
class P4A1Result:
    """P4-A1 部件筛选单次结果(包含方向匹配)."""
    item_no: int
    photo_id: str
    component_match: bool
    component_type: str = ""
    observed_features: str = ""
    likely_location: str = "unknown"
    reason: str = ""
    # v3.9: 方向匹配
    side_match: bool = False       # AI 判断该图是否在正确的左右侧
    observed_side: str = ""         # AI 观察到的照片方向: left_side/right_side/unknown


@dataclass
class DamageResult:
    """P5-B 单张照片损伤核验结果(跨 Item 缓存)."""
    photo_id: str
    has_damage: bool = False
    damage_type: str = ""
    reason: str = ""
    stage_error: str | None = None


@dataclass
class PhotoMatchInfo:
    """P4 分配后每张照片的匹配元数据（用于报告展示）."""
    photo_id: str
    score: float = 0.0
    component_match: bool = False
    side_match: bool = False
    direction_score: float = 0.0
    direction_facing: str = "unknown"
    direction_side: str = "unknown"
    shot_type: str = "unknown"
    labels: list[str] = field(default_factory=list)  # 如 ["部件匹配", "方向匹配"]


@dataclass
class PipelineContext:
    """流水线上下文(Worker 进程内传递, 全程贯穿)."""
    task_id: str
    manifest_image_url: str
    photos: list[PhotoInfo] = field(default_factory=list)

    # P1 输出
    container_number: str | None = None
    container_number_source: str = "not_found"  # photo / manifest / not_found
    container_number_confidence: float | None = None
    container_source_photo_id: str | None = None
    # 估价单箱级移箱费 RepairMove。
    repair_move: float | None = None
    manifest_items: list[ManifestItem] = field(default_factory=list)

    # P2 输出
    photo_indexes: dict[str, PhotoIndex] = field(default_factory=dict)  # photo_id → index
    handwritten_marks: dict[str, HandwrittenMark] = field(default_factory=dict)  # photo_id → mark
    cargo_info: CargoInfo | None = None

    # P4 输出
    p4a1_results: list[P4A1Result] = field(default_factory=list)

    # 三大全局缓存(任务内跨 Item 复用, 对应 v3.8 全局缓存策略)
    photo_recognition_cache: dict[str, P4A1Result] = field(default_factory=dict)
    # key: f"{photo_id}:{component主码}"
    photo_direction_cache: dict[str, DirectionResult] = field(default_factory=dict)
    # key: photo_id
    photo_damage_cache: dict[str, DamageResult] = field(default_factory=dict)
    # key: photo_id

    # P6 输出
    report: dict[str, Any] = field(default_factory=dict)

    # 成本与日志
    total_tokens: int = 0
    total_cost_cny: float = 0.0
    errors: list[str] = field(default_factory=list)

    def get_photo(self, photo_id: str) -> PhotoInfo | None:
        for p in self.photos:
            if p.photo_id == photo_id:
                return p
        return None
