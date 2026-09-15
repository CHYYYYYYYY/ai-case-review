"""P6 报告编译(纯代码, 零 LLM 调用).

数学统计 + 文本汇总, 输出最终稽核报告 JSON(v3.8-GG).
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.pipeline.schemas import PipelineContext
from app.pipeline.stages.base import BaseStage, StageResult, Timer
from app.rules.iicl_codes import COMPONENT_NAMES, DAMAGE_NAMES
from app.storage.object_store import get_object_store

_log = get_logger(__name__)


_VALID_STATUSES = {"verified", "partial", "missing", "unsupported", "overclaimed"}


def _status_with_photo_evidence(item, available_photo_ids: set[str]) -> tuple[str, str]:
    """执行报告最终不变量：没有实际照片支撑的 Item 不能显示为通过。"""
    status = item.verification_status or "missing"
    if status not in _VALID_STATUSES:
        status = "missing"

    evidence_ids = {
        *item.matched_photo_ids,
        *item.photo_evidence_ids,
    } & available_photo_ids
    reference_ids = set(item.reference_photo_ids) & available_photo_ids

    if status == "verified" and not evidence_ids:
        return "missing", "未匹配到有效证据照片，不能判定为通过"
    if status == "partial" and not evidence_ids and not reference_ids:
        return "missing", "未匹配到对应照片，不能判定为部分通过"
    return status, ""


class P6ReportStage(BaseStage):
    """P6: 报告编译."""

    stage_name = "p6"

    async def run(self) -> StageResult:
        with Timer() as t:
            summary = {
                "verified": 0,
                "partial": 0,
                "missing": 0,
                "unsupported": 0,
                "overclaimed": 0,
            }

            store = get_object_store()
            photo_name_map = {
                p.photo_id: p.original_filename or f"photo_{p.seq}"
                for p in self.ctx.photos
            }
            external_photo_id_map = {
                p.photo_id: p.external_photo_id
                for p in self.ctx.photos
            }
            photo_url_map = {
                p.photo_id: store.proxy_url(self.ctx.task_id, p.photo_id)
                for p in self.ctx.photos
            }

            item_verifications = []
            available_photo_ids = {p.photo_id for p in self.ctx.photos}
            for item in self.ctx.manifest_items:
                status, status_correction = _status_with_photo_evidence(
                    item, available_photo_ids
                )
                summary[status] += 1
                auditor_notes = item.auditor_notes
                if status_correction:
                    auditor_notes = (
                        f"{auditor_notes} | {status_correction}"
                        if auditor_notes else status_correction
                    )

                comp_primary = (item.component or "").split("/", 1)[0].strip().upper()

                # 方向信息: 取 CORE 照片中最高分
                direction_info = None
                best_score = -1.0
                for pid in item.core_photo_ids:
                    dr = self.ctx.photo_direction_cache.get(pid)
                    if dr and dr.score > best_score:
                        best_score = dr.score
                        direction_info = {
                            "facing": dr.facing,
                            "side": dr.side,
                            "score": dr.score,
                            "shot_type": dr.shot_type,
                            "source_photo": photo_url_map.get(pid, pid),
                        }

                # 构建 core_photos: photo_id + 匹配标签 + 损伤信息 + 拍摄类型
                core_photos_detail = []
                shot_map = {
                    "lateral": "侧面横向",
                    "longitudinal": "纵向透视",
                    "doorway": "箱门视角",
                    "exterior": "箱外视角",
                    "top_down": "俯视",
                }
                for m in item.core_photo_matches:
                    photo_name = photo_name_map.get(m.photo_id, m.photo_id)
                    photo_url = photo_url_map.get(m.photo_id, m.photo_id)
                    # 方向中文标签
                    facing_cn = {"front": "前端", "rear": "后端", "unknown": ""}.get(m.direction_facing, m.direction_facing)
                    side_cn = {"left_side": "左侧", "right_side": "右侧", "unknown": ""}.get(m.direction_side, m.direction_side)
                    dir_label = facing_cn + (" · " + side_cn if side_cn else "")
                    # 获取损伤信息（优先从 photo_damage_cache，备用从 auditor_notes 提取）
                    dd = self.ctx.photo_damage_cache.get(m.photo_id)
                    has_damage = dd.has_damage if dd else False
                    damage_type = dd.damage_type if dd else ""
                    _log.warning(f"[P6-DEBUG] photo_id={m.photo_id}, dd={dd}, has_damage={has_damage}, damage_type={damage_type}, auditor_notes={item.auditor_notes}")
                    # 备用：从 auditor_notes 提取损伤类型（通过 photo_id 匹配序号）
                    if not damage_type and item.auditor_notes and item.auditor_notes.strip():
                        import re
                        notes = item.auditor_notes
                        # 获取该照片的序号
                        photo = None
                        for p in self.ctx.photos:
                            if p.photo_id == m.photo_id:
                                photo = p
                                break
                        photo_seq = photo.seq if photo else None
                        _log.warning(f"[P6-DEBUG] photo_seq={photo_seq}, ctx.photos_count={len(self.ctx.photos)}")
                        if photo_seq is not None:
                            # 匹配格式：#N 参考照(损伤类型1/..., score=...) 或 #N 证据照(...)
                            pattern = rf'#\s*{photo_seq}\s+\S+\(([^,)]+)'
                            dmg_match = re.search(pattern, notes)
                            _log.warning(f"[P6-DEBUG] pattern={pattern}, match={dmg_match}")
                            if dmg_match:
                                damage_type = dmg_match.group(1).split('/')[0]
                                has_damage = True
                                _log.warning(f"[P6-DEBUG] extracted damage_type={damage_type}")
                    # 拍摄类型中文
                    shot_cn = shot_map.get(m.shot_type, "") if m.shot_type and m.shot_type != "unknown" else ""
                    # 完整 labels（匹配标签 + 损伤 + 拍摄类型）
                    all_labels = list(m.labels) if m.labels else []
                    if has_damage and damage_type:
                        all_labels.append(f"有损伤-{damage_type}")
                    if shot_cn:
                        all_labels.append(shot_cn)
                    core_photos_detail.append({
                        "photosId": external_photo_id_map.get(m.photo_id),
                        "photo_id": m.photo_id,
                        "photo_name": photo_name,
                        "photo_url": photo_url,
                        "component_match": m.component_match,
                        "side_match": m.side_match,
                        "direction_score": m.direction_score,
                        "direction_label": dir_label,
                        "shot_type": m.shot_type,
                        "has_damage": has_damage,
                        "damage_type": damage_type,
                        "labels": all_labels,
                    })

                # P3-C 强匹配会直接跳过 P4/P5，因此过去报告里只有照片 ID，
                # 展开后看不到识别置信度和命中依据。这里保留强匹配当时已经存在的
                # P2-A/P2-B 事实，前端即可展示真实的 P3-C 核验明细。
                matched_photos_detail = []
                target_location = (item.location_code or "").strip().upper()
                for pid in item.matched_photo_ids:
                    mark = self.ctx.handwritten_marks.get(pid)
                    photo_index = self.ctx.photo_indexes.get(pid)

                    recognized_location = None
                    confidence = None
                    all_locations: list[str] = []
                    if mark:
                        confidence = mark.confidence
                        all_locations = [
                            str(loc).strip().upper()
                            for loc in mark.all_locations
                            if str(loc).strip()
                        ]
                        candidates = []
                        if mark.handwritten_location:
                            candidates.append(mark.handwritten_location.strip().upper())
                        candidates.extend(all_locations)
                        if target_location:
                            recognized_location = next(
                                (loc for loc in candidates if loc == target_location),
                                None,
                            )
                        if recognized_location is None and candidates:
                            recognized_location = candidates[0]

                    match_cn = "精确" if item.match_source == "exact" else "模糊"
                    labels = [f"P3-C Location {match_cn}匹配"]
                    if recognized_location or target_location:
                        labels.append(
                            f"识别 {recognized_location or '?'} → 清单 {target_location or '?'}"
                        )
                    if photo_index:
                        labels.append(f"P2-A部件识别-{photo_index.component_type}")
                        if photo_index.has_damage:
                            labels.append("P2-A有损伤")

                    matched_photos_detail.append({
                        "photosId": external_photo_id_map.get(pid),
                        "photo_id": pid,
                        "photo_name": photo_name_map.get(pid, pid),
                        "photo_url": photo_url_map.get(pid, pid),
                        "confidence": confidence,
                        "recognized_location": recognized_location,
                        "target_location": target_location or None,
                        "match_source": item.match_source,
                        "component_type": photo_index.component_type if photo_index else None,
                        "has_damage": photo_index.has_damage if photo_index else None,
                        "labels": labels,
                    })

                # 对外提供一个语义明确、可直接渲染的证据数组。过去普通 P5
                # 证据只出现在 photo_evidence（ID 数组）中，而强匹配照片位于
                # matched_photos_detail，容易让调用方误判为“没有证据照片”。
                core_detail_by_id = {
                    detail["photo_id"]: detail for detail in core_photos_detail
                }
                matched_detail_by_id = {
                    detail["photo_id"]: detail for detail in matched_photos_detail
                }

                def evidence_detail(photo_id: str, role: str) -> dict:
                    detail = dict(
                        matched_detail_by_id.get(photo_id)
                        or core_detail_by_id.get(photo_id)
                        or {
                            "photosId": external_photo_id_map.get(photo_id),
                            "photo_id": photo_id,
                            "photo_name": photo_name_map.get(photo_id, photo_id),
                            "photo_url": photo_url_map.get(photo_id, photo_id),
                            "labels": [],
                        }
                    )
                    detail["evidence_role"] = role
                    return detail

                evidence_photo_ids = list(dict.fromkeys(
                    [*item.matched_photo_ids, *item.photo_evidence_ids]
                ))
                evidence_photos_detail = [
                    evidence_detail(pid, "evidence") for pid in evidence_photo_ids
                ]
                reference_photos_detail = [
                    evidence_detail(pid, "reference")
                    for pid in dict.fromkeys(item.reference_photo_ids)
                ]

                item_verifications.append({
                    "item_no": item.item_no,
                    "component": item.component,
                    "component_name": COMPONENT_NAMES.get(comp_primary, ""),
                    "component_suspicious": item.component_suspicious,
                    "is_paa": item.is_paa,
                    "paa_panel_face": item.paa_panel_face,
                    "location_code": item.location_code,
                    "damage_code": item.damage_code,
                    "damage_name": DAMAGE_NAMES.get((item.damage_code or "").upper(), ""),
                    "description": item.description or "",
                    "total": item.total,
                    "verification_status": status,
                    "strong_match": item.strong_match,
                    "match_source": item.match_source,
                    "matched_photos": item.matched_photo_ids,
                    "matched_photos_detail": matched_photos_detail,
                    "core_photos": [m.photo_id for m in item.core_photo_matches],
                    "core_photos_detail": core_photos_detail,
                    "photo_evidence": item.photo_evidence_ids,
                    "evidence_photos_detail": evidence_photos_detail,
                    "reference_photos": item.reference_photo_ids,
                    "reference_photos_detail": reference_photos_detail,
                    "auditor_notes": auditor_notes,
                    "direction": direction_info,
                    "mco_verdict": item.mco_verdict.value,
                })

            total = len(self.ctx.manifest_items)
            recommendation = self._recommendation(summary, total)

            cargo_info = None
            if self.ctx.cargo_info:
                cargo_info = {
                    "cargo_name": self.ctx.cargo_info.cargo_name,
                    "confidence": self.ctx.cargo_info.cargo_name_confidence,
                    "cargo_desc": self.ctx.cargo_info.cargo_desc,
                    "source_photos": [photo_url_map.get(pid, pid) for pid in self.ctx.cargo_info.source_photo_ids],
                }

            report = {
                "task_id": self.ctx.task_id,
                "container_number": self.ctx.container_number,
                "container_recognition": self._container_recognition(
                    photo_name_map, photo_url_map, external_photo_id_map
                ),
                "repair_move": self.ctx.repair_move,
                "final_recommendation": recommendation,
                "verification_summary": summary,
                "total_list_items": total,
                "item_verifications": item_verifications,
                "cargo_info": cargo_info,
                # v3.8-GG: 照片 ID 列表，按上传顺序
                "photo_id_list": [p.photo_id for p in self.ctx.photos],
                "photo_mappings": [
                    {
                        "photosId": p.external_photo_id,
                        "photo_id": p.photo_id,
                        "seq": p.seq,
                        "filename": p.original_filename,
                        "photo_url": photo_url_map.get(p.photo_id, p.photo_id),
                    }
                    for p in self.ctx.photos
                ],
                "photos": [
                    {
                        "photosId": p.external_photo_id,
                        "photo_id": p.photo_id,
                        "seq": p.seq,
                        "filename": p.original_filename,
                        "photo_url": photo_url_map.get(p.photo_id, p.photo_id),
                    }
                    for p in self.ctx.photos
                ],
                "photo_name_map": photo_name_map,
                "photo_url_map": photo_url_map,
                "allocation_strategy": "v3.8-GG(四级候选池+方向确认+关联优先round-robin)",
                "pipeline_version": "v3.8-GG",
                "cost": {
                    "total_tokens": self.ctx.total_tokens,
                    "estimated_cost_cny": round(self.ctx.total_cost_cny, 4),
                },
                "errors": self.ctx.errors,
            }

            self.ctx.report = report
            self.log.info("p6_done",
                          recommendation=recommendation,
                          summary=summary,
                          total=total)

        return self._make_result(
            success=True,
            payload=report,
            duration_ms=t.elapsed_ms,
        )

    def _container_recognition(
        self,
        photo_name_map: dict[str, str],
        photo_url_map: dict[str, str],
        external_photo_id_map: dict[str, str | None],
    ) -> dict:
        """返回箱号值和精确来源，避免用含糊的“第 N 张”描述照片。"""
        photo_id = self.ctx.container_source_photo_id
        source_photo = None
        if photo_id:
            photo = self.ctx.get_photo(photo_id)
            source_photo = {
                "photosId": external_photo_id_map.get(photo_id),
                "photo_id": photo_id,
                "seq": photo.seq if photo else None,
                "filename": photo_name_map.get(photo_id),
                "photo_url": photo_url_map.get(photo_id),
            }
        return {
            "container_number": self.ctx.container_number,
            "source": self.ctx.container_number_source,
            "confidence": self.ctx.container_number_confidence,
            "source_photo": source_photo,
        }

    @staticmethod
    def _recommendation(summary: dict[str, int], total: int) -> str:
        """根据统计给最终建议."""
        if total == 0:
            return "MISSING"
        if summary["missing"] == 0 and summary["unsupported"] == 0:
            return "VERIFIED"
        ok = summary["verified"] + summary["partial"]
        if ok >= total / 2:
            return "PARTIAL"
        return "MISSING"
