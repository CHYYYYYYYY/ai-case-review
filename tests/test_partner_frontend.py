from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "partner_frontend"


def test_partner_frontend_is_standalone_json_renderer() -> None:
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    renderer = (FRONTEND / "report-renderer.js").read_text(encoding="utf-8")

    assert "report-renderer.js" in index
    assert "AuditReportRenderer.load" in index
    assert "AuditReportRenderer" in renderer
    assert "photosId" in renderer
    assert "photo_id" in renderer
    assert "item_verifications" in renderer
    assert "<iframe" not in index.lower()
    assert "Authorization" not in renderer


def test_partner_sample_report_matches_expected_mapping_shape() -> None:
    report = json.loads((FRONTEND / "sample-report.json").read_text(encoding="utf-8"))

    mapping = report["photo_mappings"][0]
    assert mapping["photosId"] == "COMPANY_PHOTO_001"
    assert mapping["photo_id"] == "ph_demo_001"
    assert report["item_verifications"][0]["matched_photos_detail"][0]["photosId"] == mapping["photosId"]
