"""稽核任务 API.

  GET    /api/v1/audits                 历史任务列表
  POST   /api/v1/audits                 提交任务, 异步返回 task_id
  GET    /api/v1/audits/{task_id}        查询任务状态
  GET    /api/v1/audits/{task_id}/report 获取报告
  GET    /api/v1/audits/{task_id}/photos/{photo_id}  照片中间结果(调试)
  POST   /api/v1/audits/{task_id}/retry  从失败阶段重试任务
  POST   /api/v1/audits/{task_id}/cancel 取消任务
"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.photo_ids import normalize_external_photo_ids
from app.core.config import get_config
from app.core.logging import get_logger
from app.db.models import AuditReport, ManualReview, Task, TaskPhoto, TaskStage
from app.db.session import get_async_session
from app.rules.iicl_codes import damage_type_matches_code
from app.storage.object_store import get_object_store, LocalObjectStore
from app.worker.tasks import run_audit_pipeline

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/audits", tags=["稽核任务"])


class ManualReviewRequest(BaseModel):
    """人工复核 AI 对单个 Item 的判断。"""

    is_ai_correct: bool
    reason: str | None = Field(default=None, max_length=1000)


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _ext_from_filename(name: str) -> str:
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return "jpg"


def _photo_mapping(photo: TaskPhoto, *, include_url: bool = False) -> dict:
    mapping = {
        "photosId": photo.external_photo_id,
        "photo_id": photo.photo_id,
        "seq": photo.seq,
        "filename": photo.original_filename,
    }
    if include_url:
        mapping["photo_url"] = (
            f"/api/v1/audits/{photo.task_id}/photos/{photo.photo_id}/image"
        )
    return mapping


def _enrich_container_recognition(
    report: dict,
    photos: list[TaskPhoto],
    p1_payload: dict | None,
) -> dict:
    """给历史报告补齐箱号识别来源；新报告中的原生字段保持不变。"""
    enriched = deepcopy(report)
    if isinstance(enriched.get("container_recognition"), dict):
        return enriched

    photo_by_id = {photo.photo_id: photo for photo in photos}
    source_photo = None
    confidence = None
    source = "not_found"
    target_number = str(enriched.get("container_number") or "").strip().upper()
    payload = p1_payload if isinstance(p1_payload, dict) else {}
    for tried in payload.get("tried_photos") or []:
        if not isinstance(tried, dict):
            continue
        raw = tried.get("raw") if isinstance(tried.get("raw"), dict) else {}
        recognized = str(raw.get("container_number") or "").strip().upper()
        if target_number and recognized == target_number and raw.get("has_plate"):
            photo_id = tried.get("photo_id")
            photo = photo_by_id.get(photo_id)
            if photo:
                source = "photo"
                raw_confidence = raw.get("container_number_confidence")
                confidence = (
                    float(raw_confidence)
                    if isinstance(raw_confidence, (int, float))
                    else None
                )
                source_photo = _photo_mapping(photo, include_url=True)
            break

    if source == "not_found" and target_number:
        # P1-A 没有照片命中而最终报告有箱号时，来源只能是估价单 P1-B。
        source = "manifest"
    enriched["container_recognition"] = {
        "container_number": enriched.get("container_number"),
        "source": source,
        "confidence": confidence,
        "source_photo": source_photo,
    }
    return enriched


def _enrich_report_photo_mappings(report: dict, photos: list[TaskPhoto]) -> dict:
    """让新旧报告响应都包含客户 photosId；不修改数据库中的原始 JSON。"""
    enriched = deepcopy(report)
    external_by_photo_id = {photo.photo_id: photo.external_photo_id for photo in photos}
    enriched["photo_mappings"] = [
        _photo_mapping(photo, include_url=True) for photo in photos
    ]

    report_photos = enriched.get("photos")
    if isinstance(report_photos, list):
        for photo in report_photos:
            if isinstance(photo, dict):
                photo["photosId"] = external_by_photo_id.get(photo.get("photo_id"))

    for item in enriched.get("item_verifications") or []:
        if not isinstance(item, dict):
            continue
        for key in (
            "matched_photos_detail",
            "core_photos_detail",
            "evidence_photos_detail",
            "reference_photos_detail",
        ):
            for detail in item.get(key) or []:
                if isinstance(detail, dict):
                    detail["photosId"] = external_by_photo_id.get(detail.get("photo_id"))

        # 兼容历史报告：过去 P5 会把“任意损伤”当成当前清单项的证据，
        # 且没有统一的证据明细字段。读取时只校正响应副本，不改历史原始 JSON。
        if not item.get("evidence_photos_detail"):
            core_details = {
                detail.get("photo_id"): detail
                for detail in item.get("core_photos_detail") or []
                if isinstance(detail, dict) and detail.get("photo_id")
            }
            matched_details = {
                detail.get("photo_id"): detail
                for detail in item.get("matched_photos_detail") or []
                if isinstance(detail, dict) and detail.get("photo_id")
            }
            strong_ids = list(dict.fromkeys(item.get("matched_photos") or []))
            raw_p5_ids = list(dict.fromkeys(item.get("photo_evidence") or []))
            valid_p5_ids = [
                photo_id
                for photo_id in raw_p5_ids
                if damage_type_matches_code(
                    item.get("damage_code"),
                    (core_details.get(photo_id) or {}).get("damage_type"),
                )
            ]

            evidence_ids = list(dict.fromkeys([*strong_ids, *valid_p5_ids]))
            evidence_details = []
            for photo_id in evidence_ids:
                detail = dict(
                    matched_details.get(photo_id)
                    or core_details.get(photo_id)
                    or {"photo_id": photo_id}
                )
                detail["photosId"] = external_by_photo_id.get(photo_id)
                detail["evidence_role"] = "evidence"
                evidence_details.append(detail)
            item["evidence_photos_detail"] = evidence_details
            item["photo_evidence"] = valid_p5_ids

            reference_ids = list(dict.fromkeys([
                *(item.get("reference_photos") or []),
                *(photo_id for photo_id in raw_p5_ids if photo_id not in valid_p5_ids),
            ]))
            reference_details = []
            for photo_id in reference_ids:
                detail = dict(core_details.get(photo_id) or {"photo_id": photo_id})
                detail["photosId"] = external_by_photo_id.get(photo_id)
                detail["evidence_role"] = "reference"
                reference_details.append(detail)
            item["reference_photos"] = reference_ids
            item["reference_photos_detail"] = reference_details

            if (
                item.get("verification_status") == "verified"
                and not evidence_ids
                and not item.get("strong_match")
            ):
                correction = "历史报告校正：原证据损伤类型与清单要求不符，不能判定为通过"
                old_note = str(item.get("auditor_notes") or "").strip()
                item["auditor_notes"] = f"{old_note} | {correction}" if old_note else correction

        # 最终显示层的统一安全规则：无论是 MCO 硬规则、普通识别还是历史数据，
        # “通过”都必须能指向本任务中真实存在的证据照片。
        existing_photo_ids = set(external_by_photo_id)
        evidence_ids = {
            detail.get("photo_id")
            for detail in item.get("evidence_photos_detail") or []
            if isinstance(detail, dict) and detail.get("photo_id")
        }
        evidence_ids.update(item.get("matched_photos") or [])
        evidence_ids.update(item.get("photo_evidence") or [])
        evidence_ids &= existing_photo_ids
        reference_ids = {
            detail.get("photo_id")
            for detail in item.get("reference_photos_detail") or []
            if isinstance(detail, dict) and detail.get("photo_id")
        }
        reference_ids.update(item.get("reference_photos") or [])
        reference_ids &= existing_photo_ids

        correction = ""
        if item.get("verification_status") == "verified" and not evidence_ids:
            item["verification_status"] = "missing"
            correction = "无有效证据照片，不能判定为通过"
        elif (
            item.get("verification_status") == "partial"
            and not evidence_ids
            and not reference_ids
        ):
            item["verification_status"] = "missing"
            correction = "无对应照片，不能判定为部分通过"
        if correction:
            old_note = str(item.get("auditor_notes") or "").strip()
            if correction not in old_note:
                item["auditor_notes"] = (
                    f"{old_note} | {correction}" if old_note else correction
                )

    # 某个历史 Item 被校正后，汇总和总体结论必须与逐项结果保持一致。
    report_items = enriched.get("item_verifications") or []
    summary = {key: 0 for key in ("verified", "partial", "missing", "unsupported", "overclaimed")}
    for item in report_items:
        if isinstance(item, dict):
            status_key = str(item.get("verification_status") or "missing")
            summary[status_key if status_key in summary else "missing"] += 1
    enriched["verification_summary"] = summary
    total = len(report_items)
    if total == 0:
        enriched["final_recommendation"] = "MISSING"
    elif summary["missing"] == 0 and summary["unsupported"] == 0:
        enriched["final_recommendation"] = "VERIFIED"
    elif summary["verified"] + summary["partial"] >= total / 2:
        enriched["final_recommendation"] = "PARTIAL"
    else:
        enriched["final_recommendation"] = "MISSING"
    return enriched


def _manual_review_payload(review: ManualReview) -> dict:
    return {
        "item_no": review.item_no,
        "ai_verification_status": review.ai_verification_status,
        "is_ai_correct": review.is_ai_correct,
        "reason": review.reason,
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
    }


def _normalize_manual_review_reason(is_ai_correct: bool, reason: str | None) -> str | None:
    normalized = (reason or "").strip()
    if not is_ai_correct and not normalized:
        raise HTTPException(400, "AI 审核错误时必须填写原因")
    return None if is_ai_correct else normalized


def _enrich_report_manual_reviews(
    report: dict,
    reviews: list[ManualReview],
) -> dict:
    """把人工复核附加到响应；原始 AI 报告 JSON 保持只读。"""
    enriched = deepcopy(report)
    review_by_item = {review.item_no: review for review in reviews}
    enriched["manual_reviews"] = [_manual_review_payload(review) for review in reviews]
    for item in enriched.get("item_verifications") or []:
        if not isinstance(item, dict):
            continue
        try:
            item_no = int(item.get("item_no"))
        except (TypeError, ValueError):
            continue
        review = review_by_item.get(item_no)
        item["manual_review"] = _manual_review_payload(review) if review else None
    return enriched


@router.get("", response_model=list[dict])
async def list_audits(
    limit: int = 50,
    session: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """历史任务列表（按创建时间倒序）."""
    stmt = (
        select(Task)
        .order_by(Task.created_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()

    items = []
    for t in rows:
        # 历史列表也使用与详情页相同的证据校正规则，避免列表显示旧的“通过”。
        summary = {}
        final_recommendation = t.final_recommendation
        if t.status == "succeeded":
            rep = await session.get(AuditReport, t.task_id)
            if rep:
                photos = (
                    await session.execute(
                        select(TaskPhoto)
                        .where(TaskPhoto.task_id == t.task_id)
                        .order_by(TaskPhoto.seq)
                    )
                ).scalars().all()
                corrected = _enrich_report_photo_mappings(rep.report, list(photos))
                summary = corrected.get("verification_summary") or {}
                final_recommendation = corrected.get("final_recommendation")

        items.append({
            "task_id": t.task_id,
            "status": t.status,
            "current_stage": t.current_stage,
            "container_number": t.container_number,
            "final_recommendation": final_recommendation,
            "verification_summary": summary,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        })
    return items


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_audit(
    manifest_image: UploadFile = File(..., description="维修清单图片"),
    photos: list[UploadFile] = File(..., description="修箱照片(1-30 张)"),
    photos_ids: list[str] | None = Form(
        None,
        alias="photosIds",
        description="客户照片 ID，顺序须与 photos 一致；旧客户端可不传",
    ),
    callback_url: str | None = Form(None, description="完成回调 URL"),
    metadata: str | None = Form(None, description="应用系统透传字段(JSON)"),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """提交稽核任务."""
    cfg = get_config()

    # 校验文件数
    if not photos:
        raise HTTPException(400, "至少上传 1 张修箱照片")
    if len(photos) > cfg.upload.max_photo_count:
        raise HTTPException(400, f"最多 {cfg.upload.max_photo_count} 张修箱照片")
    try:
        normalized_photo_ids = normalize_external_photo_ids(
            photos_ids,
            expected_count=len(photos),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # 校验文件类型
    allowed = set(cfg.upload.allowed_types)
    for f in [manifest_image, *photos]:
        if f.content_type not in allowed:
            raise HTTPException(400, f"不支持的文件类型: {f.content_type}")

    # 解析 metadata
    metadata_dict: dict[str, Any] | None = None
    if metadata:
        try:
            metadata_dict = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(400, "metadata 必须是合法 JSON")

    # 上传到对象存储
    store = get_object_store()
    manifest_data = await manifest_image.read()
    if len(manifest_data) > cfg.upload.max_file_size_mb * 1024 * 1024:
        raise HTTPException(400, f"清单图片超过 {cfg.upload.max_file_size_mb}MB")
    manifest_key = store.upload_image(manifest_data, ext=_ext_from_filename(manifest_image.filename or "manifest.jpg"))

    photo_records: list[TaskPhoto] = []
    for seq, (photo, external_photo_id) in enumerate(
        zip(photos, normalized_photo_ids),
        start=1,
    ):
        data = await photo.read()
        if len(data) > cfg.upload.max_file_size_mb * 1024 * 1024:
            raise HTTPException(400, f"照片 {seq} 超过 {cfg.upload.max_file_size_mb}MB")
        key = store.upload_image(data, ext=_ext_from_filename(photo.filename or f"photo_{seq}.jpg"))
        photo_records.append(TaskPhoto(
            photo_id=_gen_id("ph"),
            task_id="",  # 稍后填
            seq=seq,
            url=key,
            original_filename=photo.filename or f"photo_{seq}",
            external_photo_id=external_photo_id,
        ))

    # 创建任务
    task_id = _gen_id("aud")
    task = Task(
        task_id=task_id,
        status="pending",
        current_stage=None,
        manifest_url=manifest_key,
        callback_url=callback_url,
        metadata_=metadata_dict,
    )
    session.add(task)
    await session.flush()
    for ph in photo_records:
        ph.task_id = task_id
        session.add(ph)
    await session.commit()

    # 投递任务到队列
    run_audit_pipeline.delay(task_id)
    _log.info("audit_submitted", task_id=task_id,
              photos=len(photo_records),
              has_callback=bool(callback_url))

    return {
        "task_id": task_id,
        "status": "pending",
        "created_at": task.created_at.isoformat() if task.created_at else None,
        # v3.8-GG: 返回照片 ID 列表，便于前后端对应
        "photo_id_list": [ph.photo_id for ph in photo_records],
        # 客户 photosId ↔ 系统 photo_id 的完整一一映射。
        "photo_mappings": [_photo_mapping(ph) for ph in photo_records],
    }


@router.get("/{task_id}")
async def get_audit(
    task_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """查询任务状态与进度."""
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task_not_found")

    # 计算阶段进度(v3.8-GG 共 10 阶段)
    total_stages = 10
    stage_order = [
        "p1_a", "p1_b", "p2_a", "p2_b", "p2_c",
        "p3", "p4_a1", "p4_a2", "p5_b", "p6",
    ]
    stage_idx = stage_order.index(task.current_stage) + 1 if task.current_stage in stage_order else 0

    return {
        "task_id": task.task_id,
        "status": task.status,
        "current_stage": task.current_stage,
        "progress": {
            "stage": f"{stage_idx}/{total_stages}" if stage_idx else "0/10",
            "detail": task.progress_detail,
        },
        "container_number": task.container_number,
        "final_recommendation": task.final_recommendation,
        "error": {"code": task.error_code, "message": task.error_msg} if task.error_code else None,
        "cost": {
            "tokens": task.cost_tokens,
            "cny": float(task.cost_cny) if task.cost_cny else None,
        },
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "metadata": task.metadata_,
        # v3.8-GG: 照片 ID 列表（seq 从 1 开始）
        "photos": [
            _photo_mapping(p)
            for p in (
                await session.execute(
                    select(TaskPhoto).where(TaskPhoto.task_id == task_id).order_by(TaskPhoto.seq)
                )
            ).scalars().all()
        ],
    }


@router.get("/{task_id}/report")
async def get_audit_report(
    task_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """获取稽核报告(任务完成后可查)."""
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task_not_found")
    if task.status != "succeeded":
        raise HTTPException(409, "task_not_ready", headers={"X-Task-Status": task.status})

    report_row = await session.get(AuditReport, task_id)
    if not report_row:
        raise HTTPException(404, "report_not_found")

    photos = (
        await session.execute(
            select(TaskPhoto).where(TaskPhoto.task_id == task_id).order_by(TaskPhoto.seq)
        )
    ).scalars().all()
    reviews = (
        await session.execute(
            select(ManualReview)
            .where(ManualReview.task_id == task_id)
            .order_by(ManualReview.item_no)
        )
    ).scalars().all()
    p1_stage = (
        await session.execute(
            select(TaskStage)
            .where(TaskStage.task_id == task_id, TaskStage.stage == "p1_a")
            .order_by(TaskStage.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    enriched = _enrich_report_photo_mappings(report_row.report, list(photos))
    enriched = _enrich_container_recognition(
        enriched,
        list(photos),
        p1_stage.payload if p1_stage else None,
    )
    return _enrich_report_manual_reviews(enriched, list(reviews))


@router.put("/{task_id}/items/{item_no}/manual-review")
async def upsert_manual_review(
    task_id: str,
    item_no: int,
    body: ManualReviewRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """保存或更新单个维修项目的人工复核结果。"""
    if item_no < 1:
        raise HTTPException(400, "item_no 必须大于 0")

    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task_not_found")
    if task.status != "succeeded":
        raise HTTPException(409, "task_not_ready", headers={"X-Task-Status": task.status})

    report_row = await session.get(AuditReport, task_id)
    if not report_row:
        raise HTTPException(404, "report_not_found")

    report_item = next(
        (
            item
            for item in report_row.report.get("item_verifications", [])
            if isinstance(item, dict) and str(item.get("item_no")) == str(item_no)
        ),
        None,
    )
    if report_item is None:
        raise HTTPException(404, "item_not_found")

    reason = _normalize_manual_review_reason(body.is_ai_correct, body.reason)

    review = (
        await session.execute(
            select(ManualReview).where(
                ManualReview.task_id == task_id,
                ManualReview.item_no == item_no,
            )
        )
    ).scalar_one_or_none()
    if review is None:
        review = ManualReview(
            task_id=task_id,
            item_no=item_no,
            ai_verification_status=str(report_item.get("verification_status") or "missing"),
            is_ai_correct=body.is_ai_correct,
            reason=reason,
        )
        session.add(review)
    else:
        review.ai_verification_status = str(
            report_item.get("verification_status") or "missing"
        )
        review.is_ai_correct = body.is_ai_correct
        review.reason = reason

    await session.commit()
    await session.refresh(review)
    _log.info(
        "manual_review_saved",
        task_id=task_id,
        item_no=item_no,
        is_ai_correct=body.is_ai_correct,
    )
    return _manual_review_payload(review)


@router.get("/{task_id}/photos/{photo_id}")
async def get_audit_photo(
    task_id: str,
    photo_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """获取照片中间结果(调试)."""
    photo = await session.get(TaskPhoto, photo_id)
    if not photo or photo.task_id != task_id:
        raise HTTPException(404, "photo_not_found")

    # 优先用代理接口 URL（浏览器可直接访问，不受跨域/鉴权限制）
    # LocalObjectStore: /api/v1/audits/{task_id}/photos/{photo_id}/image
    # MinIO: presigned_url 有限期，fallback 到原始 url
    store = get_object_store()
    photo_public_url = None
    if hasattr(store, 'proxy_url'):
        photo_public_url = store.proxy_url(task_id, photo_id)
    else:
        url = photo.url
        if url and not url.startswith("http"):
            photo_public_url = store.presigned_url(url)
        elif url:
            photo_public_url = url

    return {
        "photosId": photo.external_photo_id,
        "photo_id": photo.photo_id,
        "task_id": photo.task_id,
        "seq": photo.seq,
        "original_filename": photo.original_filename,
        "url": photo_public_url or photo.url,
        "object_key": photo.url,
        "component_type": photo.component_type,
        "is_on_truck": photo.is_on_truck,
        "is_interior": photo.is_interior,
        "likely_location": photo.likely_location,
        "has_damage": photo.has_damage,
        "is_plate_info": photo.is_plate_info,
        "handwritten_location": photo.handwritten_location,
        "chinese_direction": photo.chinese_direction,
        "is_door_open": photo.is_door_open,
        "cargo_name": photo.cargo_name,
        "stage_error": photo.stage_error,
    }


@router.get("/{task_id}/photos/{photo_id}/image")
async def get_audit_photo_image(
    task_id: str,
    photo_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """获取照片图片二进制(绕过预签名/ file:// 浏览器无法访问问题)."""
    photo = await session.get(TaskPhoto, photo_id)
    if not photo or photo.task_id != task_id:
        raise HTTPException(404, "photo_not_found")

    if not photo.url:
        raise HTTPException(404, "photo_url_empty")

    object_name = photo.url if photo.url.startswith("http") else photo.url
    store = get_object_store()

    if isinstance(store, LocalObjectStore):
        path = store._root / object_name
        if not path.exists():
            raise HTTPException(404, "file_not_found")
        return Response(
            content=path.read_bytes(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    else:
        # MinIO: 走 presigned_url 重定向，或直接 fetch 二进制
        try:
            url = store.presigned_url(object_name, expires_hours=1)
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            return Response(
                content=resp.content,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=3600"},
            )
        except Exception as e:
            _log.error("fetch_photo_image_failed", photo_id=photo_id, error=str(e))
            raise HTTPException(500, "failed_to_fetch_photo")


@router.post("/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_audit(
    task_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """取消任务(仅 pending/running 可取消)."""
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task_not_found")
    if task.status not in ("pending", "running"):
        raise HTTPException(409, f"task_already_{task.status}")

    task.status = "cancelled"
    task.finished_at = datetime.now(timezone.utc)
    await session.commit()
    _log.info("audit_cancelled", task_id=task_id)

    return {"task_id": task_id, "status": "cancelled"}


@router.post("/{task_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_failed_audit(
    task_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """从 P1-B 重试 OCR 失败任务，不重复执行已经完成的 P1-A 照片扫描。"""
    task = await session.get(Task, task_id)
    if not task:
        raise HTTPException(404, "task_not_found")
    if task.status != "failed":
        raise HTTPException(409, f"task_not_failed:{task.status}")
    if task.current_stage != "p1_b":
        raise HTTPException(409, f"retry_stage_not_supported:{task.current_stage}")

    task.status = "pending"
    task.current_stage = "p1_b"
    task.progress_detail = "等待重试 P1-B 清单 OCR"
    task.error_code = None
    task.error_msg = None
    task.finished_at = None
    await session.commit()

    run_audit_pipeline.delay(task_id, "p1_b")
    _log.info("audit_retry_submitted", task_id=task_id, start_stage="p1_b")
    return {"task_id": task_id, "status": "pending", "start_stage": "p1_b"}
