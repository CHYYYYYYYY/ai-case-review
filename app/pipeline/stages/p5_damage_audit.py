"""P5-B 损伤核验(v3.8-GG).

逐张核验 + photo_damage_cache.
有损伤且方向 score>=0.6 → photo_evidence; score<0.6 → reference_photos; 无损伤 → 丢弃.
"""
from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.pipeline.schemas import DamageResult
from app.pipeline.stages.base import BaseStage, StageResult, Timer
from app.rules.iicl_codes import DAMAGE_NAMES, damage_type_matches_code
from app.rules.mco_rules import is_mco_verdict_decided

_log = get_logger(__name__)

P5_B_CONCURRENCY = 3
P5_B_MAX_PER_ITEM = 5
EVIDENCE_SCORE_THRESHOLD = 0.6


class P5DamageAuditStage(BaseStage):
    """P5-B: 损伤核验 + 证据分级."""

    stage_name = "p5_b"

    async def run(self) -> StageResult:
        with Timer() as t:
            todo_items = [
                it for it in self.ctx.manifest_items
                if not is_mco_verdict_decided(it.mco_verdict)
                and not it.strong_match
                and it.core_photo_ids
            ]

            if not todo_items:
                self.log.info("p5_skip", reason="no_todo_items")
                for it in self.ctx.manifest_items:
                    if (
                        not is_mco_verdict_decided(it.mco_verdict)
                        and not it.strong_match
                        and not it.core_photo_ids
                        and not it.verification_status
                    ):
                        it.verification_status = "missing"
                        it.auditor_notes = "无匹配照片"
                return self._make_result(
                    success=True,
                    payload={"skipped": True},
                    duration_ms=t.elapsed_ms,
                )

            semaphore = asyncio.Semaphore(P5_B_CONCURRENCY)
            tasks = [self._audit_one(semaphore, it) for it in todo_items]
            await asyncio.gather(*tasks, return_exceptions=True)

            for it in self.ctx.manifest_items:
                if (
                    not is_mco_verdict_decided(it.mco_verdict)
                    and not it.strong_match
                    and not it.verification_status
                ):
                    it.verification_status = "missing"
                    it.auditor_notes = it.auditor_notes or "P5 未输出结论"

            self.log.info("p5_done", audited=len(todo_items))

        return self._make_result(
            success=True,
            payload={"audited_items": len(todo_items)},
            duration_ms=t.elapsed_ms,
        )

    async def _audit_one(self, semaphore: asyncio.Semaphore, item) -> None:
        async with semaphore:
            photo_ids = item.core_photo_ids[:P5_B_MAX_PER_ITEM]
            evidence: list[str] = []
            reference: list[str] = []
            compatible_reference: list[str] = []
            notes: list[str] = []

            for pid in photo_ids:
                dmg = await self._audit_photo(pid)
                if not dmg or not dmg.has_damage:
                    continue

                dir_score = 0.0
                dr = self.ctx.photo_direction_cache.get(pid)
                if dr:
                    dir_score = dr.score

                # 用照片序号（#N）替代 photo_id，便于稽核员对照原图
                photo = self.ctx.get_photo(pid)
                ref = f"#{photo.seq}" if photo else pid
                expected_damage = DAMAGE_NAMES.get(
                    (item.damage_code or "").strip().upper(),
                    item.damage_code or "未知损伤",
                )
                damage_matches = damage_type_matches_code(
                    item.damage_code,
                    dmg.damage_type,
                )

                if damage_matches and dir_score >= EVIDENCE_SCORE_THRESHOLD:
                    evidence.append(pid)
                    notes.append(f"{ref} 证据照({dmg.damage_type}, score={dir_score:.2f})")
                elif damage_matches:
                    reference.append(pid)
                    compatible_reference.append(pid)
                    notes.append(f"{ref} 参考照({dmg.damage_type}, score={dir_score:.2f})")
                else:
                    reference.append(pid)
                    notes.append(
                        f"{ref} 损伤不符(识别为{dmg.damage_type or '未知'}，"
                        f"清单要求{expected_damage})"
                    )

            item.photo_evidence_ids = evidence
            item.reference_photo_ids = reference

            if evidence:
                item.verification_status = "verified"
            elif compatible_reference:
                item.verification_status = "partial"
            elif photo_ids:
                item.verification_status = "unsupported"
                notes.append("候选照片未检出与清单损伤相符的有效证据")
            else:
                item.verification_status = "missing"

            item.auditor_notes = " | ".join(notes) if notes else item.auditor_notes

    async def _audit_photo(self, photo_id: str) -> DamageResult | None:
        if photo_id in self.ctx.photo_damage_cache:
            return self.ctx.photo_damage_cache[photo_id]

        photo = self.ctx.get_photo(photo_id)
        if not photo:
            return None

        prompt = render_prompt("p5_b_damage_audit", photo_id=photo_id)
        result = await self._call_llm(prompt, [photo.url])

        if result.error or not result.parsed:
            dmg = DamageResult(
                photo_id=photo_id,
                has_damage=False,
                stage_error=result.error or "empty",
            )
        else:
            data = result.parsed
            dmg = DamageResult(
                photo_id=photo_id,
                has_damage=bool(data.get("has_damage", False)),
                damage_type=str(data.get("damage_type", "")),
                reason=str(data.get("reason", "")),
            )

        self.ctx.photo_damage_cache[photo_id] = dmg
        return dmg
