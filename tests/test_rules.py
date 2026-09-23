"""规则模块单元测试(v3.8-GG)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.pipeline.schemas import (
    HandwrittenMark,
    ManifestItem,
    P4A1Result,
    PhotoIndex,
    PhotoInfo,
    PipelineContext,
)
from app.rules.candidate_pool import build_candidate_pool, is_hard_excluded
from app.rules.code_fix import fix_component, fix_damage, fix_location_code
from app.rules.container_number import (
    is_valid_container_number,
    normalize_container_number,
)
from app.rules.direction_calc import compute_direction
from app.rules.handwriting_match import match_location
from app.rules.iicl_codes import is_valid_component, is_valid_damage
from app.rules.mco_rules import MCOVerdict, check_mco, is_mco_verdict_decided
from app.rules.paa_rules import mark_paa
from app.rules.photo_allocator import allocate_photos
from app.pipeline.stages.p2_photo_index import (
    build_red_overlay_detail,
    should_scan_handwriting,
)
from app.pipeline.stages.p4_component_match import P4ComponentMatchStage


def test_component_fix_mco():
    r = fix_component("MCQ")
    assert r.fixed == "MCO"
    assert r.changed


def test_location_fix_repairs_digit_five_misread_as_s():
    result = fix_location_code("BLSN")
    assert result.fixed == "BL5N"
    assert result.changed
    assert not result.suspicious


def test_location_fix_repairs_numeric_tail_but_preserves_placeholder():
    assert fix_location_code("UXI5").fixed == "UX15"
    assert fix_location_code("IXXX").fixed == "IXXX"


def test_container_number_normalizes_position_specific_ocr_confusion():
    assert normalize_container_number("00LU O464523") == "OOLU0464523"


def test_container_number_rejects_bad_iso_check_digit():
    assert is_valid_container_number("CBHU4391461")
    assert normalize_container_number("CBHU4391462") is None


def test_handwriting_exact_match():
    r = match_location("BL1N", ["BL1N", "LX25"])
    assert r.matched
    assert r.match_source == "exact"
    assert r.matched_code == "BL1N"


def test_handwriting_fuzzy_gg():
    """BL4N → BL1N (1↔4 混淆)."""
    r = match_location("BL4N", ["BL1N"])
    assert r.matched
    assert r.match_source == "fuzzy"
    assert r.matched_code == "BL1N"


def test_handwriting_ff_filter():
    """无匹配清单 → 过滤."""
    r = match_location("BL41", ["BL1N", "LX25"])
    assert not r.matched
    assert r.match_source == "none"


def test_direction_facing_rear():
    dr = compute_direction(
        "ph1",
        left_end="side_panel",
        right_end="floor",
        far_end="door_end",
        light_direction="left",
        shot_type="longitudinal",
    )
    assert dr.facing == "rear"
    assert dr.side == "right_side"
    assert dr.score == 0.85


def test_direction_score_none_far_end():
    dr = compute_direction(
        "ph1",
        left_end="side_panel",
        right_end="floor",
        far_end="none",
        light_direction="none",
        shot_type="longitudinal",
    )
    assert dr.facing == "unknown"
    assert dr.side == "left_side"
    assert dr.score == 0.60


def test_hard_exclude_plate():
    idx = PhotoIndex(photo_id="p1", component_type="panel", is_plate_info=True)
    assert is_hard_excluded(idx)


def test_candidate_pool_type_match():
    ctx = PipelineContext(task_id="t1", manifest_image_url="x")
    ctx.photos = [PhotoInfo(photo_id="p1", seq=1, url="u1")]
    ctx.photo_indexes["p1"] = PhotoIndex(photo_id="p1", component_type="panel")
    item = ManifestItem(item_no=1, component="PAA", location_code="LX25")
    ids, level = build_candidate_pool(ctx, item)
    assert "p1" in ids
    assert level == "type_match"


def test_candidate_pool_excludes_photo_claimed_by_strong_match():
    ctx = PipelineContext(task_id="t1", manifest_image_url="x")
    ctx.photos = [
        PhotoInfo(photo_id="claimed", seq=1, url="u1"),
        PhotoInfo(photo_id="free", seq=2, url="u2"),
    ]
    ctx.photo_indexes["claimed"] = PhotoIndex(
        photo_id="claimed", component_type="panel"
    )
    ctx.photo_indexes["free"] = PhotoIndex(photo_id="free", component_type="panel")
    strong = ManifestItem(
        item_no=1,
        component="PAA",
        location_code="LB2N",
        strong_match=True,
        matched_photo_ids=["claimed"],
    )
    pending = ManifestItem(item_no=2, component="PAA", location_code="LB3N")
    ctx.manifest_items = [strong, pending]

    ids, level = build_candidate_pool(ctx, pending)
    assert ids == ["free"]
    assert level == "type_match"


class _CountingP4Stage(P4ComponentMatchStage):
    def __init__(self, ctx):
        super().__init__(ctx, llm=None)
        self.call_count = 0

    async def _call_llm(self, prompt, image_refs, *, temperature=None):
        self.call_count += 1
        return SimpleNamespace(
            error=None,
            parsed={
                "component_match": True,
                "component_type": "panel",
                "observed_features": "波纹面板",
                "likely_location": "side_panel",
                "reason": "主体为面板",
            },
        )


def _p4_dedup_context(*, component_code=None):
    ctx = PipelineContext(task_id="t1", manifest_image_url="x")
    ctx.photos = [PhotoInfo(photo_id="p1", seq=1, url="u1")]
    ctx.photo_indexes["p1"] = PhotoIndex(
        photo_id="p1",
        component_type="panel",
        component_code=component_code,
        has_damage=True,
    )
    ctx.manifest_items = [
        ManifestItem(item_no=1, component="PAA", location_code="LX14"),
        ManifestItem(item_no=2, component="PAA", location_code="RX14"),
    ]
    return ctx


def test_p4_deduplicates_concurrent_photo_component_pairs():
    ctx = _p4_dedup_context()
    stage = _CountingP4Stage(ctx)
    results, stats = asyncio.run(stage._run_p4a1_batch(ctx.manifest_items))

    assert len(results) == 2
    assert stage.call_count == 1
    assert stats["requested_pairs"] == 2
    assert stats["unique_pairs"] == 1
    assert stats["deduplicated_pairs"] == 1
    assert stats["llm_calls"] == 1


def test_p4_reuses_exact_p2_component_code():
    ctx = _p4_dedup_context(component_code="PAA")
    stage = _CountingP4Stage(ctx)
    results, stats = asyncio.run(stage._run_p4a1_batch(ctx.manifest_items))

    assert len(results) == 2
    assert all(result.component_match for result in results)
    assert stage.call_count == 0
    assert stats["p2_reused"] == 1
    assert stats["llm_calls"] == 0


def test_allocator_mutual_exclusive():
    ctx = PipelineContext(task_id="t1", manifest_image_url="x")
    ctx.photos = [
        PhotoInfo(photo_id="p1", seq=1, url="u1"),
        PhotoInfo(photo_id="p2", seq=2, url="u2"),
    ]
    ctx.p4a1_results = [
        P4A1Result(item_no=1, photo_id="p1", component_match=True),
        P4A1Result(item_no=2, photo_id="p1", component_match=True),
        P4A1Result(item_no=2, photo_id="p2", component_match=True),
    ]
    items = [
        ManifestItem(item_no=1, component="PAA"),
        ManifestItem(item_no=2, component="PAA"),
    ]
    alloc = allocate_photos(ctx, items)
    all_assigned = alloc[1] + alloc[2]
    assert len(all_assigned) == len({match.photo_id for match in all_assigned})


def test_allocator_derives_side_match_from_p4_a2_direction():
    ctx = PipelineContext(task_id="t1", manifest_image_url="x")
    ctx.photos = [PhotoInfo(photo_id="p1", seq=1, url="u1")]
    ctx.p4a1_results = [
        P4A1Result(item_no=1, photo_id="p1", component_match=True)
    ]
    ctx.photo_direction_cache["p1"] = compute_direction(
        "p1",
        left_end="side_panel",
        right_end="floor",
        far_end="front_end",
        light_direction="none",
        shot_type="longitudinal",
    )
    item = ManifestItem(item_no=1, component="PAA", location_code="LX14")

    match = allocate_photos(ctx, [item])[1][0]
    assert match.side_match
    assert "方向匹配" in match.labels


def test_handwriting_prefilter_skips_plain_photo_but_keeps_red_overlay():
    plain = PhotoIndex(photo_id="p1", component_type="panel", has_damage=False)
    assert not should_scan_handwriting(plain, has_red_overlay=False)
    assert should_scan_handwriting(plain, has_red_overlay=True)


def test_handwriting_prefilter_keeps_damage_and_measurement_photos():
    damaged = PhotoIndex(photo_id="p1", component_type="panel", has_damage=True)
    measured = PhotoIndex(photo_id="p2", component_type="panel", has_caliper=True)
    assert should_scan_handwriting(damaged, has_red_overlay=False)
    assert should_scan_handwriting(measured, has_red_overlay=False)


def test_mco_pass():
    assert check_mco("MCO", "sweep the floor") == MCOVerdict.PASS


def test_mco_water_wash_pass():
    assert (
        check_mco("MCO/MCO", "Water wash entire interior 20' container or wash detergent")
        == MCOVerdict.PASS
    )


def test_red_overlay_detail_crop(tmp_path):
    from PIL import Image, ImageDraw

    path = tmp_path / "red-overlay.jpg"
    image = Image.new("RGB", (640, 480), "#2579ad")
    draw = ImageDraw.Draw(image)
    draw.text((500, 8), "RX14", fill=(240, 20, 20))
    draw.rectangle((500, 22, 570, 27), fill=(240, 20, 20))
    image.save(path)

    detail = build_red_overlay_detail(str(path))
    assert detail is not None
    assert detail.startswith("data:image/jpeg;base64,")


def test_no_red_overlay_has_no_detail_crop(tmp_path):
    from PIL import Image

    path = tmp_path / "plain.jpg"
    Image.new("RGB", (640, 480), "#2579ad").save(path)
    assert build_red_overlay_detail(str(path)) is None


def test_paa_mark():
    is_paa, face = mark_paa("PAA/PAA", "LX25")
    assert is_paa


def test_iicl_codes_valid():
    assert is_valid_component("PAA")
    assert is_valid_damage("DT")
