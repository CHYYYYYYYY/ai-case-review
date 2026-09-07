"""P4-A1 部件筛选(v3.8-GG).

四级候选池 + 硬排除 + photo_recognition_cache, 每 Item 最多验证 15 张.
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.pipeline.schemas import P4A1Result
from app.pipeline.stages.base import BaseStage, StageResult, Timer
from app.rules.candidate_pool import build_candidate_pool, _comp_primary
from app.rules.mco_rules import is_mco_verdict_decided

_log = get_logger(__name__)

P4_A1_CONCURRENCY = 4
P4_A1_MAX_PER_ITEM = 15


class P4ComponentMatchStage(BaseStage):
    """P4-A1: 部件筛选 + 识别缓存."""

    stage_name = "p4_a1"

    async def run(self) -> StageResult:
        with Timer() as t:
            todo_items = [
                it for it in self.ctx.manifest_items
                if not is_mco_verdict_decided(it.mco_verdict)
                and not it.strong_match
            ]

            if not todo_items or not self.ctx.photos:
                self.log.info("p4_a1_skip", reason="no_todo_items_or_photos")
                for it in todo_items:
                    if not it.verification_status:
                        it.verification_status = "missing"
                        it.auditor_notes = "无可用照片"
                return self._make_result(
                    success=True,
                    payload={"skipped": True},
                    duration_ms=t.elapsed_ms,
                )

            p4a1_results, batch_stats = await self._run_p4a1_batch(todo_items)
            self.ctx.p4a1_results = p4a1_results

            self.log.info("p4_a1_done",
                          items=len(todo_items),
                          results=len(p4a1_results),
                          **batch_stats)

        comp_map = {i.item_no: (i.component or "") for i in todo_items}
        return self._make_result(
            success=True,
            payload={
                "p4a1_count": len(p4a1_results),
                "optimization": batch_stats,
                "details": [
                    {
                        "item_no": r.item_no,
                        "component": comp_map.get(r.item_no, ""),
                        "photo_id": r.photo_id,
                        "match": r.component_match,
                        "component_type": r.component_type,
                        "reason": r.reason,
                    }
                    for r in p4a1_results
                ],
            },
            duration_ms=t.elapsed_ms,
        )

    async def _run_p4a1_batch(
        self,
        items: list,
    ) -> tuple[list[P4A1Result], dict[str, int]]:
        """Recognize every unique photo/component pair once, then fan it out.

        The old implementation created all item/photo tasks at once.  Parallel
        tasks checked the cache before any peer had populated it, so five PAA
        items could trigger five identical vision calls for one photo.
        """
        semaphore = asyncio.Semaphore(P4_A1_CONCURRENCY)
        assignments: dict[tuple[str, str], list] = {}
        requested_pairs = 0

        for item in items:
            candidates, pool_level = build_candidate_pool(
                self.ctx, item, max_photos=P4_A1_MAX_PER_ITEM,
            )
            self.log.info("p4_a1_pool",
                          item_no=item.item_no,
                          pool=pool_level,
                          count=len(candidates))
            for photo_id in candidates:
                requested_pairs += 1
                key = (photo_id, _comp_primary(item))
                assignments.setdefault(key, []).append(item)

        stats = {
            "requested_pairs": requested_pairs,
            "unique_pairs": len(assignments),
            "deduplicated_pairs": requested_pairs - len(assignments),
            "p2_reused": 0,
            "cache_reused": 0,
            "llm_calls": 0,
        }

        async def recognize_one(
            photo_id: str,
            comp_primary: str,
            representative,
        ) -> P4A1Result:
            cache_key = f"{photo_id}:{comp_primary}"

            if cache_key in self.ctx.photo_recognition_cache:
                stats["cache_reused"] += 1
                return self.ctx.photo_recognition_cache[cache_key]

            photo = self.ctx.get_photo(photo_id)
            if not photo:
                return P4A1Result(
                    item_no=0,
                    photo_id=photo_id,
                    component_match=False,
                    reason="photo_not_found",
                )

            # P2-A 的 component_code 只在模型能明确判断具体 IICL 部件时输出。
            # 完全一致属于可靠正向证据，可直接复用，避免同图再做一次部件识别。
            index = self.ctx.photo_indexes.get(photo_id)
            indexed_code = (index.component_code or "").strip().upper() if index else ""
            if indexed_code and indexed_code == comp_primary:
                stats["p2_reused"] += 1
                out = P4A1Result(
                    item_no=0,
                    photo_id=photo_id,
                    component_match=True,
                    component_type=index.component_type,
                    observed_features="P2-A component_code 精确命中",
                    likely_location=index.likely_location,
                    reason=f"复用 P2-A 部件识别: {indexed_code}",
                )
                self.ctx.photo_recognition_cache[cache_key] = out
                return out

            async with semaphore:
                prompt = render_prompt(
                    "p4_a1_component_filter",
                    item_no=representative.item_no,
                    component_code=representative.component or "",
                    photo_id=photo_id,
                )
                stats["llm_calls"] += 1
                result = await self._call_llm(prompt, [photo.url])

            if result.error or not result.parsed:
                out = P4A1Result(
                    item_no=0,
                    photo_id=photo_id,
                    component_match=False,
                    reason=f"LLM 调用失败: {result.error or 'empty'}",
                )
            else:
                data = result.parsed
                out = P4A1Result(
                    item_no=0,
                    photo_id=photo_id,
                    component_match=bool(data.get("component_match", False)),
                    component_type=str(data.get("component_type", "")),
                    observed_features=str(data.get("observed_features", "")),
                    likely_location=str(data.get("likely_location", "unknown")),
                    reason=str(data.get("reason", "")),
                )

            self.ctx.photo_recognition_cache[cache_key] = out
            return out

        keys = list(assignments)
        tasks = [
            asyncio.create_task(
                recognize_one(photo_id, comp_primary, assignments[key][0])
            )
            for key in keys
            for photo_id, comp_primary in [key]
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: list[P4A1Result] = []
        for key, r in zip(keys, results):
            if isinstance(r, Exception):
                self.log.error("p4a1_exception", error=str(r))
                continue
            photo_id, _comp_primary_code = key
            for item in assignments[key]:
                out.append(P4A1Result(
                    item_no=item.item_no,
                    photo_id=photo_id,
                    component_match=r.component_match,
                    component_type=r.component_type,
                    observed_features=r.observed_features,
                    likely_location=r.likely_location,
                    reason=r.reason,
                ))
        return out, stats
