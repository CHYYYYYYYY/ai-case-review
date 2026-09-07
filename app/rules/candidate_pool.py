"""P4 四级候选池 + 硬排除(零 API)."""
from __future__ import annotations

from app.pipeline.schemas import ManifestItem, PhotoIndex, PipelineContext


# Component 主码 → 期望 P2-A component_type
_COMPONENT_TO_PHOTO_TYPE: dict[str, set[str]] = {
    "PAA": {"panel"},
    "FPP": {"floor", "bottom"},
    "FSP": {"floor", "bottom"},
    "FPB": {"floor", "bottom"},
    "CMU": {"bottom", "structural"},
    "CMA": {"bottom", "structural"},
    "CMA'": {"bottom", "structural"},
    "LBA": {"structural", "panel"},
}

# likely_location 兜底映射
_SURFACE_FALLBACK: dict[str, set[str]] = {
    "PAA": {"side_panel", "door_panel", "front_end", "unknown"},
    "FPP": {"floor", "bottom", "unknown"},
    "FSP": {"floor", "bottom", "unknown"},
    "CMU": {"bottom", "structural", "unknown"},
    "CMA": {"bottom", "structural", "unknown"},
}


def _comp_primary(item: ManifestItem) -> str:
    return (item.component or "").split("/", 1)[0].strip().upper()


def _norm_loc(code: str | None) -> str:
    return (code or "").strip().upper()


def is_hard_excluded(idx: PhotoIndex | None) -> bool:
    """第一层硬排除: 拖车/铭牌/货物照."""
    if not idx or idx.stage_error:
        return True
    return bool(idx.is_on_truck or idx.is_plate_info or idx.has_cargo)


def build_candidate_pool(
    ctx: PipelineContext,
    item: ManifestItem,
    *,
    max_photos: int = 15,
) -> tuple[list[str], str]:
    """四级候选池, 返回 (photo_ids, pool_level).

    1. 精确 Location 匹配(手写/批注)
    2. component_type 匹配
    3. likely_location 兜底
    4. 全量保底(硬排除后)
    """
    item_loc = _norm_loc(item.location_code)
    comp = _comp_primary(item)
    expected_types = _COMPONENT_TO_PHOTO_TYPE.get(comp, set())
    surface_types = _SURFACE_FALLBACK.get(comp, {"unknown"})

    seq_map = {p.photo_id: p.seq for p in ctx.photos}
    all_ids = sorted(
        [p.photo_id for p in ctx.photos],
        key=lambda pid: seq_map.get(pid, 9999),
    )

    # P3-C 已经用明确 Location 强匹配并核实的照片属于确定证据，不能再进入
    # 其他未匹配 Item 的 P4 候选池。过去这些照片会被 P4/P5 再处理一遍，
    # 还可能被错误分配给另一条清单项。
    strong_claimed_ids = {
        pid
        for manifest_item in ctx.manifest_items
        if manifest_item.strong_match
        for pid in manifest_item.matched_photo_ids
    }

    def eligible(pid: str) -> bool:
        return (
            pid not in strong_claimed_ids
            and not is_hard_excluded(ctx.photo_indexes.get(pid))
        )

    # Level 1: 精确 Location(手写/批注)
    if item_loc:
        level1: list[str] = []
        for pid in all_ids:
            if not eligible(pid):
                continue
            mark = ctx.handwritten_marks.get(pid)
            if mark and not mark.filtered:
                hw = _norm_loc(mark.corrected_to or mark.handwritten_location)
                if hw == item_loc:
                    level1.append(pid)
        if level1:
            return level1[:max_photos], "exact_location"

    # Level 2: type 匹配
    if expected_types:
        level2 = [
            pid for pid in all_ids
            if eligible(pid)
            and (ctx.photo_indexes[pid].component_type in expected_types)
        ]
        if level2:
            return level2[:max_photos], "type_match"

    # Level 3: surface 兜底
    level3 = [
        pid for pid in all_ids
        if eligible(pid)
        and ctx.photo_indexes.get(pid)
        and ctx.photo_indexes[pid].likely_location in surface_types
    ]
    if level3:
        return level3[:max_photos], "surface_fallback"

    # Level 4: 全量保底
    level4 = [pid for pid in all_ids if eligible(pid)]
    return level4[:max_photos], "full_fallback"
