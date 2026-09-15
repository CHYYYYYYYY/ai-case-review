from __future__ import annotations

import asyncio

from app.llm.client import LLMCallResult
from app.pipeline.schemas import ManifestItem, PhotoInfo, PipelineContext
from app.pipeline.stages.p1_nameplate import (
    P1ManifestOCRStage,
    P1NameplateStage,
    _optional_number,
)
from app.pipeline.stages.p6_report import _status_with_photo_evidence
from app.rules.mco_rules import MCOVerdict


class FakeLLM:
    def __init__(self, payloads: list[dict]):
        self.payloads = iter(payloads)

    async def chat_vision(self, prompt, image_refs, temperature=None):
        return LLMCallResult(
            content="",
            parsed=next(self.payloads),
            tokens_prompt=0,
            tokens_completion=0,
            duration_ms=0,
            provider="test",
            model="test",
        )


def test_verified_item_without_evidence_is_missing_even_for_mco_rule() -> None:
    item = ManifestItem(
        item_no=1,
        verification_status="verified",
        mco_verdict=MCOVerdict.PASS,
    )

    status, reason = _status_with_photo_evidence(item, {"ph_1"})

    assert status == "missing"
    assert "不能判定为通过" in reason


def test_verified_item_with_real_evidence_remains_verified() -> None:
    item = ManifestItem(
        item_no=1,
        verification_status="verified",
        photo_evidence_ids=["ph_1"],
    )

    assert _status_with_photo_evidence(item, {"ph_1"}) == ("verified", "")
    assert _status_with_photo_evidence(item, {"ph_other"})[0] == "missing"


def test_partial_item_requires_at_least_a_reference_photo() -> None:
    item = ManifestItem(
        item_no=1,
        verification_status="partial",
        reference_photo_ids=["ph_ref"],
    )

    assert _status_with_photo_evidence(item, {"ph_ref"}) == ("partial", "")
    assert _status_with_photo_evidence(item, set())[0] == "missing"


def test_manifest_ocr_extracts_total_and_repair_move() -> None:
    ctx = PipelineContext(task_id="aud_test", manifest_image_url="manifest.jpg")
    stage = P1ManifestOCRStage(
        ctx,
        FakeLLM([
            {
                "container_number": "CSGU2457701",
                "repair_move": "CNY 35.50",
                "items": [
                    {
                        "item_no": 1,
                        "raw_component": "PAA/PAA",
                        "total": "1,200.25",
                    }
                ],
            }
        ]),
    )

    result = asyncio.run(stage.run())

    assert result.success
    assert ctx.repair_move == 35.5
    assert ctx.manifest_items[0].total == 1200.25
    assert result.payload["repair_move"] == 35.5
    assert ctx.container_number_source == "manifest"


def test_nameplate_result_keeps_complete_source_photo_mapping() -> None:
    ctx = PipelineContext(
        task_id="aud_test",
        manifest_image_url="manifest.jpg",
        photos=[
            PhotoInfo("ph_1", 1, "one.jpg", "one.jpg", "CLIENT_1"),
            PhotoInfo("ph_2", 2, "two.jpg", "two.jpg", "CLIENT_2"),
        ],
    )
    stage = P1NameplateStage(
        ctx,
        FakeLLM([
            {"has_plate": False, "container_number": None},
            {
                "has_plate": True,
                "container_number": "OOLU0464523",
                "container_number_confidence": 0.9,
            },
        ]),
    )

    result = asyncio.run(stage.run())

    assert ctx.container_source_photo_id == "ph_2"
    assert ctx.container_number_source == "photo"
    assert ctx.container_number_confidence == 0.9
    assert result.payload["tried_photos"][1] == {
        "photo_id": "ph_2",
        "photosId": "CLIENT_2",
        "seq": 2,
        "filename": "two.jpg",
        "raw": {
            "has_plate": True,
            "container_number": "OOLU0464523",
            "container_number_confidence": 0.9,
        },
    }


def test_optional_number_does_not_invent_missing_values() -> None:
    assert _optional_number(None) is None
    assert _optional_number("--") is None
    assert _optional_number("unreadable") is None
