"""P3 清单预处理(纯代码, 零 API 成本).

- P3-A: IICL 编码纠错(Component / Damage / Location 面代码)
- P3-B: PAA 判断 + MCO 硬规则(直接 PASS/FAIL 跳过 P4/P5)
- P3-C: 手写 Location 精确匹配(强匹配 → 跳过 P4/P5 直接 VERIFIED)
        + v3.8-FF 无效标记过滤 + v3.8-GG 模糊匹配纠正
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.stages.base import BaseStage, StageResult, Timer
from app.rules.code_fix import fix_component, fix_damage, fix_location_face
from app.rules.handwriting_match import match_location
from app.rules.mco_rules import (
    MCOVerdict,
    MCO_FAIL_KEYWORDS,
    MCO_PASS_KEYWORDS,
    check_mco,
    is_mco_verdict_decided,
)
from app.rules.paa_rules import mark_paa

_log = get_logger(__name__)


class P3PreprocessStage(BaseStage):
    """P3: 清单预处理(纯代码)."""

    stage_name = "p3"

    async def run(self) -> StageResult:
        with Timer() as t:
            decided_count = 0

            for item in self.ctx.manifest_items:
                # P3-A: 纠错
                comp_fix = fix_component(item.raw_component)
                item.component = comp_fix.output
                item.component_suspicious = comp_fix.suspicious

                dmg_fix = fix_damage(item.raw_damage_code)
                item.damage_code = dmg_fix.output
                item.damage_suspicious = dmg_fix.suspicious

                loc_fix = fix_location_face(item.raw_location_code)
                item.location_code = loc_fix.output
                item.location_suspicious = loc_fix.suspicious

                # P3-B: PAA 标记
                is_paa, panel_face = mark_paa(item.component, item.location_code)
                item.is_paa = is_paa
                item.paa_panel_face = panel_face

                # P3-B: MCO 硬规则
                verdict = check_mco(item.component, item.description)
                item.mco_verdict = verdict
                if is_mco_verdict_decided(verdict):
                    decided_count += 1
                    if verdict == MCOVerdict.PASS:
                        item.verification_status = "verified"
                        # 找出实际命中的 PASS 关键词, 写进备注
                        desc_lower = (item.description or "").lower()
                        hit = next(
                            (kw for kw in MCO_PASS_KEYWORDS
                             if kw.lower() in desc_lower),
                            None,
                        )
                        item.auditor_notes = (
                            f"MCO 硬规则命中 PASS ({hit})"
                            if hit
                            else "MCO 硬规则命中 PASS"
                        )
                    else:
                        item.verification_status = "unsupported"
                        desc_lower = (item.description or "").lower()
                        hit = next(
                            (kw for kw in MCO_FAIL_KEYWORDS
                             if kw.lower() in desc_lower),
                            None,
                        )
                        item.auditor_notes = (
                            f"MCO 硬规则命中 FAIL ({hit})"
                            if hit
                            else "MCO 硬规则命中 FAIL"
                        )
                    self.log.info("p3_mco_decided",
                                  item_no=item.item_no, verdict=verdict.value)

            # P3-C: 手写 Location 精确匹配 + FF 过滤 + GG 模糊纠正
            strong_match_count = self._run_p3c()

            self.log.info("p3_done",
                          items=len(self.ctx.manifest_items),
                          mco_decided=decided_count,
                          strong_matched=strong_match_count)

        return self._make_result(
            success=True,
            payload={
                "items_count": len(self.ctx.manifest_items),
                "mco_decided": decided_count,
                "strong_matched": strong_match_count,
                "handwriting_marks": [
                    {
                        "photo_id": m.photo_id,
                        "handwritten_location": m.handwritten_location,
                        "corrected_to": m.corrected_to,
                        "filtered": m.filtered,
                    }
                    for m in self.ctx.handwritten_marks.values()
                    if m.handwritten_location
                ],
                "items": [
                    {
                        "item_no": it.item_no,
                        "component": it.component,
                        "component_suspicious": it.component_suspicious,
                        "damage": it.damage_code,
                        "location": it.location_code,
                        "is_paa": it.is_paa,
                        "paa_panel_face": it.paa_panel_face,
                        "mco_verdict": it.mco_verdict.value,
                        "strong_match": it.strong_match,
                        "match_source": it.match_source,
                    }
                    for it in self.ctx.manifest_items
                ],
            },
            duration_ms=t.elapsed_ms,
        )

    def _run_p3c(self) -> int:
        """P3-C: 手写 Location 匹配(纯代码).

        - 精确/模糊命中清单 Location → 对应 Item 强匹配, 直接 VERIFIED,
          跳过 P4/P5, 照片计入 CORE.
        - 无任何命中(v3.8-FF) → 标记 filtered=True, 照片正常走 AI 流程.

        Returns:
            强匹配的 Item 数
        """
        # 未被 MCO 决定的 Item 的 Location 池
        open_items = [
            it for it in self.ctx.manifest_items
            if not is_mco_verdict_decided(it.mco_verdict)
        ]
        list_codes = [it.location_code for it in open_items]

        strong_items: set[int] = set()

        for mark in self.ctx.handwritten_marks.values():
            location_candidates = []
            for code in [mark.handwritten_location, *mark.all_locations]:
                normalized = (code or "").strip().upper()
                if normalized and normalized not in location_candidates:
                    location_candidates.append(normalized)
            if not location_candidates:
                continue

            # 优先精确命中；只有没有精确结果时才接受模糊纠正。
            results = [match_location(code, list_codes) for code in location_candidates]
            result = next(
                (candidate for candidate in results if candidate.match_source == "exact"),
                next((candidate for candidate in results if candidate.matched), results[0]),
            )
            recognized_location = next(
                (
                    code for code, candidate in zip(location_candidates, results)
                    if candidate is result
                ),
                location_candidates[0],
            )

            if not result.matched:
                # v3.8-FF: 无效标记 → 过滤, 照片走 AI 流程
                mark.filtered = True
                self.log.info("p3c_mark_filtered",
                              photo_id=mark.photo_id,
                              handwritten=recognized_location)
                continue

            if result.match_source == "fuzzy":
                # v3.8-GG: 模糊纠正
                mark.corrected_to = result.corrected
                self.log.info("p3c_mark_corrected",
                              photo_id=mark.photo_id,
                              handwritten=recognized_location,
                              corrected=result.corrected)

            matched_code = result.matched_code
            for item in open_items:
                if (item.location_code or "").strip().upper() != (matched_code or ""):
                    continue
                item.strong_match = True
                if item.match_source == "none" or result.match_source == "exact":
                    item.match_source = result.match_source
                if mark.photo_id not in item.matched_photo_ids:
                    item.matched_photo_ids.append(mark.photo_id)
                if mark.photo_id not in item.core_photo_ids:
                    item.core_photo_ids.append(mark.photo_id)
                item.verification_status = "verified"
                src_txt = "精确" if result.match_source == "exact" else "模糊纠正"
                note = (
                    f"P3-C 手写 Location {src_txt}匹配"
                    f"(识别 {recognized_location} → 清单 {matched_code})"
                )
                item.auditor_notes = (
                    f"{item.auditor_notes} | {note}" if item.auditor_notes else note
                )
                strong_items.add(item.item_no)
                self.log.info("p3c_strong_match",
                              item_no=item.item_no,
                              photo_id=mark.photo_id,
                              source=result.match_source)

        return len(strong_items)
