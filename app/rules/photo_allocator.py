"""全局照片分配器: 关联优先 round-robin, 互斥分配, 含方向+部件匹配打分."""
from __future__ import annotations

from app.pipeline.schemas import DirectionResult, ManifestItem, PhotoMatchInfo, PipelineContext


MAX_CORE_PER_ITEM = 5
MAX_ROUNDS = 5


def _association_key(pid: str, ctx: PipelineContext) -> tuple:
    """同区域拍摄关联键(shotType/facing/lightDir)."""
    dr: DirectionResult | None = ctx.photo_direction_cache.get(pid)
    if not dr:
        return ("unknown", "unknown", "none")
    return (dr.shot_type, dr.facing, dr.light_direction)


def expected_side(item: ManifestItem) -> str | None:
    """Return the side encoded by a repair Location, when it is explicit."""
    location = (item.location_code or "").strip().upper()
    if location.startswith("L"):
        return "left_side"
    if location.startswith("R"):
        return "right_side"
    return None


def _photo_match_info(
    pid: str,
    item: ManifestItem,
    ctx: PipelineContext,
) -> PhotoMatchInfo:
    """获取照片对 Item 的完整匹配信息."""
    dr = ctx.photo_direction_cache.get(pid)
    dir_score = dr.score if dr else 0.0
    facing = dr.facing if dr else "unknown"
    side = dr.side if dr else "unknown"
    shot_type = dr.shot_type if dr else "unknown"

    matched = False
    side_ok = False

    for r in ctx.p4a1_results:
        if r.item_no == item.item_no and r.photo_id == pid:
            if r.component_match:
                matched = True

    # P4-A1 现在只负责部件识别；左右方向由每张照片只调用一次的 P4-A2
    # 统一输出，再按当前 Item 的 Location 通过代码确定，避免在 P4-A1 重复看图。
    item_side = expected_side(item)
    if item_side and dr:
        side_ok = dr.side == item_side

    # 部件匹配+方向匹配都命中 → 最高优先；只有部件 → 次优先；两者都没有 → 仅靠方向分
    if matched and side_ok:
        score = dir_score + 1.5
    elif matched:
        score = dir_score + 0.5
    elif side_ok:
        score = dir_score + 0.3
    else:
        score = dir_score

    # 构建标签文字
    labels: list[str] = []
    if matched:
        labels.append("部件匹配")
    if side_ok:
        labels.append("方向匹配")

    return PhotoMatchInfo(
        photo_id=pid,
        score=score,
        component_match=matched,
        side_match=side_ok,
        direction_score=dir_score,
        direction_facing=facing,
        direction_side=side,
        shot_type=shot_type,
        labels=labels,
    )


def allocate_photos(
    ctx: PipelineContext,
    items: list[ManifestItem],
) -> dict[int, list[PhotoMatchInfo]]:
    """关联优先 round-robin 互斥分配.

    - 每张照片只能分配给一条 Item
    - 每轮每 Item 最多拿 1 个关联组
    - 最多 5 轮 = 5 张照片/Item
    - 返回每张照片的完整匹配信息
    """
    if not items:
        return {}

    seq_map = {p.photo_id: p.seq for p in ctx.photos}

    # item_no → 匹配成功的候选照片(按 seq 排序)
    match_by_item: dict[int, list[str]] = {}
    for r in ctx.p4a1_results:
        if r.component_match:
            match_by_item.setdefault(r.item_no, []).append(r.photo_id)
    for v in match_by_item.values():
        v.sort(key=lambda pid: seq_map.get(pid, 9999))

    occupied: set[str] = set()
    allocation: dict[int, list[PhotoMatchInfo]] = {it.item_no: [] for it in items}

    sorted_items = sorted(items, key=lambda x: x.item_no)

    for _round in range(MAX_ROUNDS):
        for item in sorted_items:
            if len(allocation[item.item_no]) >= MAX_CORE_PER_ITEM:
                continue
            candidates = [
                pid for pid in match_by_item.get(item.item_no, [])
                if pid not in occupied
            ]
            if not candidates:
                continue

            # 按关联组 + 分数选最佳一组(每组 1 张代表)
            groups: dict[tuple, list[str]] = {}
            for pid in candidates:
                key = _association_key(pid, ctx)
                groups.setdefault(key, []).append(pid)

            # 选分数最高的组, 组内取 seq 最前
            best_group: list[str] | None = None
            best_score = -1.0
            for _key, pids in groups.items():
                pids.sort(key=lambda p: seq_map.get(p, 9999))
                info = _photo_match_info(pids[0], item, ctx)
                if info.score > best_score:
                    best_score = info.score
                    best_group = pids

            if best_group:
                chosen = best_group[0]
                info = _photo_match_info(chosen, item, ctx)
                allocation[item.item_no].append(info)
                occupied.add(chosen)

    return allocation
