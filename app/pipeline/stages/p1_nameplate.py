"""P1-A 铭牌识别 + P1-B 清单 OCR.

P1-A: 逐张调 LLM 识别箱号, 命中完整 11 位即停(省成本).
P1-B: 单次调 LLM 提取清单表格数据行.
"""
from __future__ import annotations

import re
from math import isfinite

from app.core.logging import get_logger
from app.llm.prompt_loader import render_prompt
from app.pipeline.schemas import ManifestItem, PipelineContext
from app.pipeline.stages.base import BaseStage, StageResult, Timer
from app.rules.container_number import normalize_container_number

_log = get_logger(__name__)

def _optional_number(value: object) -> float | None:
    """把 OCR 金额安全转成数值；无法可靠解析时保留为空。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if isfinite(number) else None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"null", "none", "n/a", "-", "--"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    number = float(match.group())
    return number if isfinite(number) else None


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
                    "photosId": photo.external_photo_id,
                    "seq": photo.seq,
                    "filename": photo.original_filename,
                    "raw": parsed,
                })

                if result.error:
                    self.log.warning("p1_a_photo_error", photo_id=photo.photo_id, error=result.error)
                    continue

                has_plate = parsed.get("has_plate", False)
                number = normalize_container_number(parsed.get("container_number"))
                if has_plate and number:
                    container_number = number
                    self.ctx.container_number_source = "photo"
                    self.ctx.container_source_photo_id = photo.photo_id
                    confidence = _optional_number(
                        parsed.get("container_number_confidence")
                    )
                    self.ctx.container_number_confidence = (
                        max(0.0, min(1.0, confidence))
                        if confidence is not None else None
                    )
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
                error = result.error or "p1_b_empty_response"
                if error == "llm_json_parse_failed":
                    error = "p1_b_json_parse_failed"
                diagnostic = {
                    # 完整保留模型文本，便于确定是尾部截断、转义错误还是结构错误。
                    "raw_content": result.content,
                    "parse_error": result.parse_error,
                    "finish_reason": result.finish_reason,
                    "retries": result.retries,
                    "provider": result.provider,
                    "model": result.model,
                    "tokens_prompt": result.tokens_prompt,
                    "tokens_completion": result.tokens_completion,
                }
                self.log.error(
                    "p1_b_failed",
                    error=error,
                    parse_error=result.parse_error,
                    finish_reason=result.finish_reason,
                    retries=result.retries,
                )
                return self._make_result(
                    success=False,
                    error=error,
                    payload={"llm_diagnostic": diagnostic},
                    duration_ms=t.elapsed_ms,
                )

            data = result.parsed
            raw_items = data.get("items", []) or []
            self.ctx.repair_move = _optional_number(data.get("repair_move"))

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
                    total=_optional_number(raw.get("total")),
                ))

            items.sort(key=lambda x: x.item_no)
            self.ctx.manifest_items = items

            # 如果 P1-A 未识别箱号, 用清单上的兜底
            if not self.ctx.container_number:
                manifest_cn = normalize_container_number(data.get("container_number"))
                if manifest_cn:
                    self.ctx.container_number = manifest_cn
                    self.ctx.container_number_source = "manifest"
                    self.ctx.container_number_confidence = None
                    self.ctx.container_source_photo_id = None

            self.log.info("p1_b_done", items=len(items),
                          container_number=self.ctx.container_number)

        return self._make_result(
            success=True,
            payload={
                "items_count": len(items),
                "repair_move": self.ctx.repair_move,
                "raw_items": raw_items,
            },
            duration_ms=t.elapsed_ms,
        )
