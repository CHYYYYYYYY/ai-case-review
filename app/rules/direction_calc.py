"""P4-A2 方向计算(纯代码, 文档 4.1 节).

AI 只描述视觉事实, facing/side/score 由代码层 100% 确定.
"""
from __future__ import annotations

from app.pipeline.schemas import DirectionResult


def compute_direction(
    photo_id: str,
    *,
    left_end: str,
    right_end: str,
    far_end: str,
    light_direction: str,
    shot_type: str,
    reason: str = "",
) -> DirectionResult:
    """根据 AI 观察事实计算 facing / side / score."""
    facing = _compute_facing(far_end)
    side = _compute_side(facing, shot_type, left_end, right_end, far_end)
    score = _compute_score(far_end, side)
    return DirectionResult(
        photo_id=photo_id,
        left_end=left_end or "unknown",
        right_end=right_end or "unknown",
        far_end=far_end or "unknown",
        light_direction=light_direction or "none",
        shot_type=shot_type or "unknown",
        reason=reason,
        facing=facing,
        side=side,
        score=score,
    )


def _compute_facing(far_end: str) -> str:
    if far_end == "door_end":
        return "rear"
    if far_end == "front_end":
        return "front"
    return "unknown"


def _side_panel_on_left(left_end: str, right_end: str) -> bool | None:
    """侧板主体在画面左侧 → True, 右侧 → False, 无法判断 → None."""
    left_is_panel = left_end == "side_panel"
    right_is_panel = right_end == "side_panel"
    if left_is_panel and not right_is_panel:
        return True
    if right_is_panel and not left_is_panel:
        return False
    return None


def _compute_side(
    facing: str,
    shot_type: str,
    left_end: str,
    right_end: str,
    far_end: str = "unknown",
) -> str:
    """facing + 侧板画面位置 → side(仅 longitudinal 有效)."""
    if shot_type != "longitudinal":
        return "unknown"

    panel_left = _side_panel_on_left(left_end, right_end)
    if panel_left is None:
        return "unknown"

    if facing == "rear":
        return "right_side" if panel_left else "left_side"
    if facing == "front":
        return "left_side" if panel_left else "right_side"

    # far_end=none/unknown 时仍可根据侧板画面位置推断 side(文档 score=0.60 分支)
    if far_end in ("none", "unknown", ""):
        return "left_side" if panel_left else "right_side"

    return "unknown"


def _compute_score(far_end: str, side: str) -> float:
    side_ok = side not in ("unknown", "")
    if far_end in ("door_end", "front_end") and side_ok:
        return 0.85
    if far_end in ("none", "unknown", "") and side_ok:
        return 0.60
    return 0.0
