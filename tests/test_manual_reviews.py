from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.audits import (
    _enrich_report_manual_reviews,
    _normalize_manual_review_reason,
)


def test_manual_review_is_attached_to_matching_report_item() -> None:
    now = datetime.now(timezone.utc)
    report = {
        "item_verifications": [
            {"item_no": 1, "verification_status": "verified"},
            {"item_no": 2, "verification_status": "missing"},
        ]
    }
    reviews = [
        SimpleNamespace(
            item_no=2,
            ai_verification_status="missing",
            is_ai_correct=False,
            reason="证据照片已包含该维修位置",
            created_at=now,
            updated_at=now,
        )
    ]

    enriched = _enrich_report_manual_reviews(report, reviews)

    assert "manual_review" not in report["item_verifications"][0]
    assert enriched["item_verifications"][0]["manual_review"] is None
    assert enriched["item_verifications"][1]["manual_review"]["is_ai_correct"] is False
    assert enriched["item_verifications"][1]["manual_review"]["reason"] == "证据照片已包含该维修位置"
    assert enriched["manual_reviews"][0]["item_no"] == 2


def test_incorrect_review_requires_reason() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _normalize_manual_review_reason(False, "  ")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "AI 审核错误时必须填写原因"


def test_correct_review_discards_reason() -> None:
    assert _normalize_manual_review_reason(True, "不应保留") is None
