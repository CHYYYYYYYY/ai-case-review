"""P1-A 铭牌识别 + P1-B 清单 OCR.

P1-A: 逐张调 LLM 识别箱号, 命中完整 11 位即停(省成本).
P1-B: 单次调 LLM 提取清单表格数据行.
"""
from __future__ import annotations

import re

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.pipeline.schemas import ManifestItem, PipelineContext
from app.pipeline.stages.base import BaseStage, StageResult, Timer

_log = get_logger(__name__)

# 11 位箱号正则: 4 字母 + 7 数字
_CONTAINER_NO_RE = re.compile(r"^[A-Z]{4}\d{7}$")


class P1NameplateStage(BaseStage):
    """P1-A: 铭牌识别(逐张到完整箱号即停)."""

    stage_name = "p1_a"

    async def run(self) -> StageResult:
        with Timer() as t:
            container_number: str | None = None
            tried_photos: list[dict] = []

            # 逐张调, 命中即停
            for photo in self.ctx.photos:
                prompt = render_prompt("p1_a_nameplate", photo_id=photo.photo_id)
                result = await self._call_llm(prompt, [photo.url])
                parsed = result.parsed or {}
                tried_photos.append({
                    "photo_id": photo.photo_id,
                    "raw": parsed,
                })

                if result.error:
                    self.log.warning("p1_a_photo_error", photo_id=photo.photo_id, error=result.error)
                    continue

                has_plate = parsed.get("has_plate", False)
                number = parsed.get("container_number")
                if has_plate and number and _CONTAINER_NO_RE.match(str(number).upper()):
                    container_number = str(number).upper()
                    self.log.info("p1_a_hit", photo_id=photo.photo_id, container_number=container_number)
                    break

            self.ctx.container_number = container_number
            if not container_number:
                self.log.warning("p1_a_no_container_found",
                                 tried_count=len(tried_photos))
                # 不阻断流程, P6 报告里标注"箱号未识别"

        return self._make_result(
            success=True,
            payload={
                "container_number": container_number,
                "tried_photos": tried_photos,
            },
            duration_ms=t.elapsed_ms,
        )


class P1ManifestOCRStage(BaseStage):
    """P1-B: 清单 OCR(提取表格数据行)."""

    stage_name = "p1_b"

    async def run(self) -> StageResult:
        with Timer() as t:
            prompt = render_prompt("p1_b_manifest_ocr")
            result = await self._call_llm(prompt, [self.ctx.manifest_image_url])

            if result.error or not result.parsed:
                self.log.error("p1_b_failed", error=result.error)
                return self._make_result(
                    success=False,
                    error=result.error or "p1_b_empty_response",
                    duration_ms=t.elapsed_ms,
                )

            data = result.parsed
            raw_items = data.get("items", []) or []

            # 校验条数, 异常标 suspicious 但不阻断
            if len(raw_items) > 15:
                self.log.warning("p1_b_too_many_items", count=len(raw_items))

            if not raw_items:
                return self._make_result(
                    success=False,
                    error="manifest_parse_empty",
                    payload={"raw": data},
                    duration_ms=t.elapsed_ms,
                )

            # 转 ManifestItem
            items: list[ManifestItem] = []
            for raw in raw_items:
                try:
                    item_no = int(raw.get("item_no", len(items) + 1))
                except (TypeError, ValueError):
                    item_no = len(items) + 1
                items.append(ManifestItem(
                    item_no=item_no,
                    raw_text=str(raw.get("raw_text", "")),
                    raw_component=raw.get("raw_component"),
                    raw_location_code=raw.get("raw_location_code"),
                    raw_damage_code=raw.get("raw_damage_code"),
                    raw_repair_type=raw.get("raw_repair_type"),
                    parsed_size=raw.get("parsed_size"),
                    description=raw.get("description"),
                ))

            items.sort(key=lambda x: x.item_no)
            self.ctx.manifest_items = items

            # 如果 P1-A 未识别箱号, 用清单上的兜底
            if not self.ctx.container_number:
                manifest_cn = data.get("container_number")
                if manifest_cn and _CONTAINER_NO_RE.match(str(manifest_cn).upper()):
                    self.ctx.container_number = str(manifest_cn).upper()

            self.log.info("p1_b_done", items=len(items),
                          container_number=self.ctx.container_number)

        return self._make_result(
            success=True,
            payload={"items_count": len(items), "raw_items": raw_items},
            duration_ms=t.elapsed_ms,
        )
