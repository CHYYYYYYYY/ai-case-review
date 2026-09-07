"""P4-A2 方向确认 + 全局照片分配器(v3.8-GG)."""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.pipeline.stages.base import BaseStage, StageResult, Timer
from app.rules.direction_calc import compute_direction
from app.rules.mco_rules import is_mco_verdict_decided
from app.rules.photo_allocator import allocate_photos

_log = get_logger(__name__)

P4_A2_CONCURRENCY = 4
P4_A2_MAX_PER_ITEM = 15


class P4A2DirectionStage(BaseStage):
    """P4-A2: 方向确认 + 关联优先 round-robin 互斥分配."""

    stage_name = "p4_a2"

    async def run(self) -> StageResult:
        with Timer() as t:
            todo_items = [
                it for it in self.ctx.manifest_items
                if not is_mco_verdict_decided(it.mco_verdict)
                and not it.strong_match
            ]

            if not todo_items:
                self.log.info("p4_a2_skip", reason="no_todo_items")
                return self._make_result(
                    success=True,
                    payload={"skipped": True},
                    duration_ms=t.elapsed_ms,
                )

            photo_ids: set[str] = set()
            for r in self.ctx.p4a1_results:
                if r.component_match:
                    photo_ids.add(r.photo_id)

            await self._run_direction_batch(list(photo_ids))

            allocation = allocate_photos(self.ctx, todo_items)
            for item in todo_items:
                matches = allocation.get(item.item_no, [])
                item.core_photo_matches = matches
                item.core_photo_ids = [m.photo_id for m in matches]
                if not matches and not item.verification_status:
                    item.verification_status = "missing"
                    item.auditor_notes = "无匹配照片"

            self.log.info("p4_a2_done",
                          items=len(todo_items),
                          allocated=sum(len(v) for v in allocation.values()))

        # 转 JSON 可序列化格式（避免 PhotoMatchInfo 无法序列化）
        alloc_serializable = {
            item_no: [
                {
                    "photo_id": m.photo_id,
                    "score": m.score,
                    "component_match": m.component_match,
                    "side_match": m.side_match,
                    "direction_score": m.direction_score,
                    "direction_facing": m.direction_facing,
                    "direction_side": m.direction_side,
                    "shot_type": m.shot_type,
                    "labels": m.labels,
                }
                for m in matches
            ]
            for item_no, matches in allocation.items()
        }

        return self._make_result(
            success=True,
            payload={"allocation": alloc_serializable},
            duration_ms=t.elapsed_ms,
        )

    async def _run_direction_batch(self, photo_ids: list[str]) -> None:
        """对候选照片跑 P4-A2(带全局 direction cache)."""
        # 去重
        unique_ids = list(dict.fromkeys(photo_ids))
        semaphore = asyncio.Semaphore(P4_A2_CONCURRENCY)

        async def direction_one(photo_id: str) -> None:
            if photo_id in self.ctx.photo_direction_cache:
                return
            photo = self.ctx.get_photo(photo_id)
            if not photo:
                return

            async with semaphore:
                prompt = render_prompt("p4_a2_direction", photo_id=photo_id)
                result = await self._call_llm(prompt, [photo.url])

            if result.error or not result.parsed:
                self.ctx.photo_direction_cache[photo_id] = compute_direction(
                    photo_id,
                    left_end="unknown",
                    right_end="unknown",
                    far_end="unknown",
                    light_direction="none",
                    shot_type="unknown",
                    reason=result.error or "empty",
                )
                return

            data = result.parsed
            dr = compute_direction(
                photo_id,
                left_end=str(data.get("left_end", "unknown")),
                right_end=str(data.get("right_end", "unknown")),
                far_end=str(data.get("far_end", "unknown")),
                light_direction=str(data.get("light_direction", "none")),
                shot_type=str(data.get("shot_type", "unknown")),
                reason=str(data.get("reason", "")),
            )
            self.ctx.photo_direction_cache[photo_id] = dr
            self.log.info("p4_a2_detail",
                          photo_id=photo_id,
                          facing=dr.facing,
                          side=dr.side,
                          score=dr.score)

        tasks = [direction_one(pid) for pid in unique_ids]
        await asyncio.gather(*tasks, return_exceptions=True)
