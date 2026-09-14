from __future__ import annotations

import asyncio

from app.pipeline.schemas import (
    DamageResult,
    DirectionResult,
    ManifestItem,
    PhotoInfo,
    PipelineContext,
)
from app.pipeline.stages.p5_damage_audit import P5DamageAuditStage
from app.rules.iicl_codes import damage_type_matches_code


class _CachedDamageStage(P5DamageAuditStage):
    async def _audit_photo(self, photo_id: str) -> DamageResult | None:
        return self.ctx.photo_damage_cache.get(photo_id)


def _audit(damage_code: str, damage_type: str, direction_score: float) -> ManifestItem:
    ctx = PipelineContext(task_id="aud_test", manifest_image_url="manifest")
    ctx.photos = [PhotoInfo(photo_id="ph_1", seq=1, url="photo")]
    item = ManifestItem(
        item_no=1,
        component="PAA",
        damage_code=damage_code,
        core_photo_ids=["ph_1"],
    )
    ctx.manifest_items = [item]
    ctx.photo_damage_cache["ph_1"] = DamageResult(
        photo_id="ph_1",
        has_damage=True,
        damage_type=damage_type,
    )
    ctx.photo_direction_cache["ph_1"] = DirectionResult(
        photo_id="ph_1",
        score=direction_score,
    )
    stage = _CachedDamageStage(ctx, llm=None)
    asyncio.run(stage._audit_one(asyncio.Semaphore(1), item))
    return item


def test_damage_aliases_match_only_the_requested_damage() -> None:
    assert damage_type_matches_code("BR", "断裂")
    assert damage_type_matches_code("CO", "锈迹")
    assert not damage_type_matches_code("BR", "锈迹")
    assert not damage_type_matches_code("BR", "修补痕迹")


def test_unrelated_damage_cannot_verify_item() -> None:
    item = _audit("BR", "锈迹", 0.85)

    assert item.verification_status == "unsupported"
    assert item.photo_evidence_ids == []
    assert item.reference_photo_ids == ["ph_1"]
    assert "损伤不符" in item.auditor_notes
    assert "清单要求断裂" in item.auditor_notes


def test_matching_damage_and_direction_verifies_item() -> None:
    item = _audit("BR", "断裂", 0.85)

    assert item.verification_status == "verified"
    assert item.photo_evidence_ids == ["ph_1"]


def test_matching_damage_with_weak_direction_is_partial() -> None:
    item = _audit("BR", "断裂", 0.0)

    assert item.verification_status == "partial"
    assert item.photo_evidence_ids == []
    assert item.reference_photo_ids == ["ph_1"]
