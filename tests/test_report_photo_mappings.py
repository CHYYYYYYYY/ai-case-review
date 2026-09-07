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
