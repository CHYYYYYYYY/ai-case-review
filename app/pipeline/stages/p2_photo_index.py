"""P2-A 照片索引 + P2-B 手写 Location 识别 + P2-C 货物识别.

P2-A: 并发识别 component_type + 富字段(v3.8: end_visible/is_interior/likely_location/
      has_damage/is_plate_info/component_code 等).
P2-B: 逐张识别手写 Location 编码 + 中文方向字 + 红色箱管批注(confidence>=0.40 采纳).
P2-C: 仅对箱内/含货照片预筛后调一次.
"""
from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from PIL import Image, ImageEnhance

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.pipeline.schemas import CargoInfo, HandwrittenMark, PhotoIndex, PipelineContext
from app.pipeline.stages.base import BaseStage, StageResult, Timer

_log = get_logger(__name__)

# P2-A 并发度
P2_A_CONCURRENCY = 2

# P2-B 并发度
P2_B_CONCURRENCY = 2

# P2-B confidence 采纳门槛(文档: P2-B 只识别不过滤, >=0.40 即采纳)
P2_B_CONFIDENCE_THRESHOLD = 0.40

# 红色箱管角标通常只占 640x480 照片顶部很小一块。给模型补一张放大的角标细节图，
# 避免整图缩放后把 8~12px 高的 Location 字符抹掉。
P2_B_CORNER_HEIGHT_RATIO = 0.34
P2_B_CORNER_WIDTH_RATIO = 0.64
P2_B_RED_PIXEL_MIN = 24
P2_B_DETAIL_TARGET_WIDTH = 1280

# P2-C 单次请求最多图片数(避免 prompt + image tokens 超过 LLM context 上限)
P2_C_MAX_IMAGES_PER_CALL = 5

_VALID_COMPONENT_TYPES = {"panel", "floor", "bottom", "structural", "cargo"}
_VALID_LIKELY_LOCATIONS = {
    "side_panel", "door_panel", "front_end", "floor", "bottom", "unknown",
}


def _local_path_from_image_ref(image_ref: str) -> Path | None:
    """Resolve a local/file:// image reference without touching remote URLs."""
    if image_ref.startswith("file://"):
        parsed = urlparse(image_ref)
        path = Path(unquote(parsed.path))
    elif image_ref.startswith(("http://", "https://", "data:")):
        return None
    else:
        path = Path(image_ref)
    return path if path.is_file() else None


def _red_pixel_count(image: Image.Image) -> int:
    """Count saturated red pixels typical of the printed container overlay."""
    rgb = image.convert("RGB")
    pixels = (
        rgb.get_flattened_data()
        if hasattr(rgb, "get_flattened_data")
        else rgb.getdata()
    )
    return sum(
        1
        for red, green, blue in pixels
        if red >= 105 and red - green >= 32 and red - blue >= 18
    )


def build_red_overlay_detail(image_ref: str) -> str | None:
    """Return an enlarged data-URI crop for a red top-corner overlay, if present.

    Container-management systems normally print compact red metadata in either
    top corner.  The final row contains the Location code (for example LB2N,
    RB3N or RX14).  Selecting the corner with more red pixels keeps the extra
    vision-token cost bounded to one detail image per source photo.
    """
    path = _local_path_from_image_ref(image_ref)
    if path is None:
        return None

    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
            width, height = image.size
            if width < 32 or height < 32:
                return None

            crop_height = max(1, int(height * P2_B_CORNER_HEIGHT_RATIO))
            crop_width = max(1, int(width * P2_B_CORNER_WIDTH_RATIO))
            corners = [
                image.crop((0, 0, crop_width, crop_height)),
                image.crop((width - crop_width, 0, width, crop_height)),
            ]
            scored = [(_red_pixel_count(crop), crop) for crop in corners]
            red_count, detail = max(scored, key=lambda pair: pair[0])
            if red_count < P2_B_RED_PIXEL_MIN:
                return None

            scale = max(2, (P2_B_DETAIL_TARGET_WIDTH + detail.width - 1) // detail.width)
            detail = detail.resize(
                (detail.width * scale, detail.height * scale),
                Image.Resampling.LANCZOS,
            )
            detail = ImageEnhance.Sharpness(detail).enhance(1.6)

            buffer = io.BytesIO()
            detail.save(buffer, format="JPEG", quality=94, optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
    except (OSError, ValueError):
        return None


def should_scan_handwriting(
    index: PhotoIndex | None,
    *,
    has_red_overlay: bool,
) -> bool:
    """Conservatively decide whether P2-B needs another vision call.

    A detected red overlay always wins.  When P2-A succeeded, obvious scene
    photos and plain photos without damage/measurement evidence cannot contain
    a useful repair Location under the P2-A contract, so reprocessing them in
    P2-B only adds latency and tokens.
    """
    if has_red_overlay:
        return True
    if index is None or index.stage_error:
        return True
    if index.is_on_truck or index.is_plate_info or index.has_cargo:
        return False
    return bool(index.has_damage or index.has_caliper)


class P2PhotoIndexStage(BaseStage):
    """P2-A: 照片索引(并发=2, v3.8 富字段)."""

    stage_name = "p2_a"

    async def run(self) -> StageResult:
        with Timer() as t:
            semaphore = asyncio.Semaphore(P2_A_CONCURRENCY)

            async def index_one(photo_id: str, url: str) -> PhotoIndex:
                async with semaphore:
                    prompt = render_prompt("p2_a_photo_index", photo_id=photo_id)
                    result = await self._call_llm(prompt, [url])
                    if result.error or not result.parsed:
                        return PhotoIndex(
                            photo_id=photo_id,
                            component_type="panel",  # 兜底
                            stage_error=result.error or "p2_a_empty",
                        )
                    data = result.parsed
                    comp = str(data.get("component_type", "panel")).lower()
                    if comp not in _VALID_COMPONENT_TYPES:
                        comp = "panel"
                    likely = str(data.get("likely_location", "unknown")).lower()
                    if likely not in _VALID_LIKELY_LOCATIONS:
                        likely = "unknown"
                    comp_code = data.get("component_code")
                    if comp_code in ("null", "", None):
                        comp_code = None
                    try:
                        interior_conf = float(data.get("is_interior_confidence", 0.0))
                    except (TypeError, ValueError):
                        interior_conf = 0.0
                    return PhotoIndex(
                        photo_id=photo_id,
                        component_type=comp,
                        photo_type=str(data.get("photo_type", "close_up")),
                        is_on_truck=bool(data.get("is_on_truck", False)),
                        end_visible=bool(data.get("end_visible", False)),
                        is_interior=bool(data.get("is_interior", False)),
                        is_interior_confidence=interior_conf,
                        has_cargo=bool(data.get("has_cargo", False)),
                        has_caliper=bool(data.get("has_caliper", False)),
                        has_damage=bool(data.get("has_damage", False)),
                        is_plate_info=bool(data.get("is_plate_info", False)),
                        likely_location=likely,
                        component_code=str(comp_code).upper() if comp_code else None,
                    )

            tasks = [index_one(p.photo_id, p.url) for p in self.ctx.photos]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    self.log.error("p2_a_exception", error=str(r))
                    continue
                self.ctx.photo_indexes[r.photo_id] = r

            self.log.info("p2_a_done", indexed=len(self.ctx.photo_indexes))

        return self._make_result(
            success=True,
            payload={"photo_indexes": [pi.__dict__ for pi in self.ctx.photo_indexes.values()]},
            duration_ms=t.elapsed_ms,
        )


class P2HandwritingStage(BaseStage):
    """P2-B: 手写 Location 编码识别(并发=2, confidence>=0.40 采纳)."""

    stage_name = "p2_b"

    async def run(self) -> StageResult:
        with Timer() as t:
            semaphore = asyncio.Semaphore(P2_B_CONCURRENCY)

            # 预筛: 红色角标始终扫描；其余只保留 P2-A 发现损伤/测量痕迹的照片。
            # 普通无损伤照、拖车照、铭牌照、货物照不再重复进入视觉模型。
            candidates = []
            detail_refs: dict[str, str] = {}
            skipped_scene = 0
            skipped_plain = 0
            for p in self.ctx.photos:
                idx = self.ctx.photo_indexes.get(p.photo_id)
                detail_ref = build_red_overlay_detail(p.url)
                if detail_ref:
                    detail_refs[p.photo_id] = detail_ref
                if not should_scan_handwriting(idx, has_red_overlay=bool(detail_ref)):
                    if idx and (idx.is_on_truck or idx.is_plate_info or idx.has_cargo):
                        skipped_scene += 1
                    else:
                        skipped_plain += 1
                    continue
                candidates.append(p)

            async def recognize_one(photo) -> HandwrittenMark:
                async with semaphore:
                    prompt = render_prompt("p2_b_handwriting", photo_id=photo.photo_id)
                    image_refs = [photo.url]
                    detail_ref = detail_refs.get(photo.photo_id)
                    if detail_ref:
                        prompt += (
                            "\n\n图像说明：第1张是完整原图；第2张（如有）是同一照片顶部"
                            "红色箱管批注的放大细节。请优先逐字符读取细节图最后一行的"
                            "Location 编码。"
                        )
                        image_refs.append(detail_ref)
                    result = await self._call_llm(prompt, image_refs, temperature=0.0)
                    if result.error or not result.parsed:
                        return HandwrittenMark(
                            photo_id=photo.photo_id,
                            stage_error=result.error or "p2_b_empty",
                        )
                    data = result.parsed
                    try:
                        conf = float(data.get("confidence", 0.0))
                    except (TypeError, ValueError):
                        conf = 0.0

                    loc = data.get("handwritten_location")
                    if loc in ("null", "", None):
                        loc = None
                    all_locs_raw = data.get("all_locations") or []
                    all_locs = [
                        str(x).strip().upper() for x in all_locs_raw
                        if isinstance(x, (str, int)) and str(x).strip()
                    ]

                    # 模型偶尔会返回 Location 字符串但漏置布尔标记，或只放进
                    # all_locations。只在存在唯一候选且达到 confidence 门槛时兜底，
                    # 后续 P3 仍会用清单 Location 做严格匹配，避免误报直通。
                    if loc is None and len(all_locs) == 1:
                        loc = all_locs[0]
                    has_loc = loc is not None and conf >= P2_B_CONFIDENCE_THRESHOLD
                    if not has_loc:
                        loc = None

                    direction = data.get("chinese_direction")
                    if direction not in ("左", "右"):
                        direction = None

                    return HandwrittenMark(
                        photo_id=photo.photo_id,
                        has_handwritten_location=has_loc,
                        handwritten_location=str(loc).strip().upper() if loc else None,
                        all_locations=all_locs,
                        chinese_direction=direction,
                        confidence=conf,
                    )

            tasks = [recognize_one(p) for p in candidates]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            found = 0
            for r in results:
                if isinstance(r, Exception):
                    self.log.error("p2_b_exception", error=str(r))
                    continue
                self.ctx.handwritten_marks[r.photo_id] = r
                if r.has_handwritten_location:
                    found += 1

            self.log.info("p2_b_done",
                          scanned=len(candidates),
                          with_location=found,
                          skipped_scene=skipped_scene,
                          skipped_plain=skipped_plain)

        return self._make_result(
            success=True,
            payload={
                "scanned": len(candidates),
                "with_location": found,
                "skipped_scene": skipped_scene,
                "skipped_plain": skipped_plain,
                "marks": [m.__dict__ for m in self.ctx.handwritten_marks.values()],
            },
            duration_ms=t.elapsed_ms,
        )


class P2CargoStage(BaseStage):
    """P2-C: 货物识别(对箱内/含货照片批量调一次)."""

    stage_name = "p2_c"

    async def run(self) -> StageResult:
        with Timer() as t:
            # 预筛候选照片: 箱内场景或已检出货物
            candidates = [
                self.ctx.get_photo(pid)
                for pid, idx in self.ctx.photo_indexes.items()
                if (idx.is_interior or idx.has_cargo or idx.component_type == "cargo")
                and not idx.stage_error
            ]
            candidates = [c for c in candidates if c is not None]

            if not candidates:
                self.log.info("p2_c_no_candidates, skip")
                self.ctx.cargo_info = CargoInfo(cargo_name=None)
                return self._make_result(
                    success=True,
                    payload={"skipped": True, "reason": "no_interior_photos"},
                    duration_ms=t.elapsed_ms,
                )

            # 分批调用, 避免单次请求 prompt + image tokens 超限
            batch_size = max(1, P2_C_MAX_IMAGES_PER_CALL)
            batches = [candidates[i:i + batch_size] for i in range(0, len(candidates), batch_size)]

            results_list: list[dict[str, Any]] = []
            first_error: str | None = None
            for batch in batches:
                batch_block_lines = [
                    f"- photo_id={c.photo_id}, seq={c.seq}" for c in batch
                ]
                batch_photos_block = "\n".join(batch_block_lines)
                prompt = render_prompt("p2_c_cargo", photos_block=batch_photos_block)
                image_refs = [c.url for c in batch]
                result = await self._call_llm(prompt, image_refs)
                if result.error or not result.parsed:
                    if first_error is None:
                        first_error = result.error or "p2_c_empty"
                    self.log.warning("p2_c_batch_failed",
                                     batch_size=len(batch),
                                     error=result.error)
                    continue
                batch_results = result.parsed.get("results") or []
                results_list.extend([r for r in batch_results if isinstance(r, dict)])

            if not results_list:
                self.log.warning("p2_c_all_batches_failed", error=first_error)
                self.ctx.cargo_info = CargoInfo(cargo_name=None)
                return self._make_result(
                    success=True,
                    payload={"skipped": True, "reason": "llm_failed"},
                    duration_ms=t.elapsed_ms,
                )

            cargo_name: str | None = None
            cargo_conf = 0.0
            cargo_desc = ""
            source_ids: list[str] = []

            for item in results_list:
                if not isinstance(item, dict):
                    continue
                if item.get("is_door_open"):
                    name = item.get("cargo_name")
                    if name:
                        cargo_name = str(name)
                        cargo_conf = float(item.get("cargo_name_confidence", 0.0))
                        cargo_desc = str(item.get("cargo_desc", ""))
                        source_ids.append(str(item.get("photo_id", "")))
                        break  # 取第一个识别成功的

            self.ctx.cargo_info = CargoInfo(
                cargo_name=cargo_name,
                cargo_name_confidence=cargo_conf,
                cargo_desc=cargo_desc,
                source_photo_ids=source_ids,
            )
            self.log.info("p2_c_done", cargo=cargo_name, conf=cargo_conf)

        return self._make_result(
            success=True,
            payload={
                "candidates_count": len(candidates),
                "cargo_name": cargo_name,
                "confidence": cargo_conf,
            },
            duration_ms=t.elapsed_ms,
        )
