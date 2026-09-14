from __future__ import annotations

from types import SimpleNamespace

from app.api.audits import _enrich_report_photo_mappings


def test_report_contains_customer_and_internal_photo_ids() -> None:
    report = {
        "photos": [{"photo_id": "ph_1", "seq": 1, "filename": "a.jpg"}],
        "item_verifications": [
            {
                "matched_photos_detail": [{"photo_id": "ph_1"}],
                "core_photos_detail": [{"photo_id": "ph_1"}],
                "evidence_photos_detail": [{"photo_id": "ph_1"}],
                "reference_photos_detail": [{"photo_id": "ph_1"}],
            }
        ],
    }
    photos = [
        SimpleNamespace(
            external_photo_id="COMPANY_001",
            photo_id="ph_1",
            task_id="aud_1",
            seq=1,
            original_filename="a.jpg",
        )
    ]

    enriched = _enrich_report_photo_mappings(report, photos)

    assert report["photos"][0].get("photosId") is None
    assert enriched["photo_mappings"] == [
        {
            "photosId": "COMPANY_001",
            "photo_id": "ph_1",
            "seq": 1,
            "filename": "a.jpg",
            "photo_url": "/api/v1/audits/aud_1/photos/ph_1/image",
        }
    ]
    assert enriched["photos"][0]["photosId"] == "COMPANY_001"
    item = enriched["item_verifications"][0]
    assert item["matched_photos_detail"][0]["photosId"] == "COMPANY_001"
    assert item["core_photos_detail"][0]["photosId"] == "COMPANY_001"
    assert item["evidence_photos_detail"][0]["photosId"] == "COMPANY_001"
    assert item["reference_photos_detail"][0]["photosId"] == "COMPANY_001"


def test_old_report_returns_null_customer_photo_id() -> None:
    report = {"photos": [{"photo_id": "ph_old"}], "item_verifications": []}
    photos = [
        SimpleNamespace(
            external_photo_id=None,
            photo_id="ph_old",
            task_id="aud_old",
            seq=1,
            original_filename="old.jpg",
        )
    ]

    enriched = _enrich_report_photo_mappings(report, photos)

    assert enriched["photo_mappings"][0]["photosId"] is None
    assert enriched["photos"][0]["photosId"] is None


def test_legacy_report_downgrades_unrelated_damage_evidence() -> None:
    report = {
        "final_recommendation": "VERIFIED",
        "verification_summary": {"verified": 1},
        "item_verifications": [
            {
                "item_no": 3,
                "damage_code": "BR",
                "verification_status": "verified",
                "strong_match": False,
                "mco_verdict": "none",
                "photo_evidence": ["ph_rust"],
                "reference_photos": [],
                "core_photos": ["ph_rust"],
                "core_photos_detail": [
                    {
                        "photo_id": "ph_rust",
                        "damage_type": "锈迹",
                        "direction_score": 0.85,
                    }
                ],
                "auditor_notes": "#2 证据照(锈迹, score=0.85)",
            }
        ],
    }
    photos = [
        SimpleNamespace(
            external_photo_id="COMPANY_RUST",
            photo_id="ph_rust",
            task_id="aud_old",
            seq=2,
            original_filename="rust.jpg",
        )
    ]

    enriched = _enrich_report_photo_mappings(report, photos)
    item = enriched["item_verifications"][0]

    assert report["item_verifications"][0]["verification_status"] == "verified"
    assert item["verification_status"] == "unsupported"
    assert item["photo_evidence"] == []
    assert item["evidence_photos_detail"] == []
    assert item["reference_photos_detail"][0]["photosId"] == "COMPANY_RUST"
    assert "历史报告校正" in item["auditor_notes"]
    assert enriched["verification_summary"]["unsupported"] == 1
    assert enriched["final_recommendation"] == "MISSING"
