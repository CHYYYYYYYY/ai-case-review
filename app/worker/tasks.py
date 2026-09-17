"""Celery 任务定义.

入口任务: run_audit_pipeline(task_id)
  1. 从 DB 加载 task + photos
  2. 构造 PipelineContext
  3. 调用 Orchestrator.execute()
  4. 失败兜底: 状态置 failed, 错误写 DB
  5. 成功: 触发 webhook 回调(若有)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
from celery import Task as CeleryTask

from app.core.config import get_config
from app.core.logging import bind_task_context, get_logger, setup_logging
from app.db.models import Task as AuditTaskModel, TaskPhoto, TaskStage
from app.db.session import get_sync_session
from app.pipeline.orchestrator import Orchestrator
from app.pipeline.schemas import PhotoInfo, PipelineContext
from app.storage.object_store import get_object_store
from app.worker.celery_app import celery_app

setup_logging()
_log = get_logger(__name__)


class AuditTask(CeleryTask):
    """任务基类: 失败时统一更新 DB 状态."""

    def on_failure(self, exc, task_id, args, kwargs, info):
        task_id_arg = args[0] if args else kwargs.get("task_id")
        if task_id_arg:
            _log.exception("audit_task_failed", task_id=task_id_arg, exc=exc)
            session = get_sync_session()
            try:
                t = session.get(AuditTaskModel, task_id_arg)
                if t and t.status not in ("failed", "succeeded", "cancelled"):
                    t.status = "failed"
                    t.error_code = "worker_exception"
                    t.error_msg = f"{type(exc).__name__}: {exc}"
                    t.finished_at = datetime.now(timezone.utc)
                    session.commit()
            finally:
                session.close()
        super().on_failure(exc, task_id, args, kwargs, info)


@celery_app.task(name="app.worker.tasks.run_audit_pipeline", base=AuditTask, bind=True)
def run_audit_pipeline(
    self, task_id: str, start_stage: str | None = None
) -> dict:
    """执行稽核流水线.

    Args:
        task_id: 任务 ID
        start_stage: 可选恢复阶段。目前生产重试使用 p1_b，跳过已完成的铭牌识别。

    Returns:
        包含 status 与 report 摘要的 dict
    """
    bind_task_context(task_id)
    _log.info("audit_task_start", task_id=task_id)

    ctx = _load_context(task_id)
    if ctx is None:
        _log.error("audit_task_load_failed", task_id=task_id)
        return {"task_id": task_id, "status": "failed", "error": "task_not_found"}

    # 取消检查
    session = get_sync_session()
    try:
        task = session.get(AuditTaskModel, task_id)
        if task and task.status == "cancelled":
            _log.info("audit_task_cancelled", task_id=task_id)
            return {"task_id": task_id, "status": "cancelled"}
    finally:
        session.close()

    # 运行异步流水线
    orchestrator = Orchestrator(ctx)
    try:
        asyncio.run(orchestrator.execute(start_stage=start_stage))
    except Exception as e:
        _log.exception("pipeline_execute_failed", task_id=task_id)
        # 兜底置 failed
        session = get_sync_session()
        try:
            t = session.get(AuditTaskModel, task_id)
            if t and t.status not in ("failed", "cancelled"):
                t.status = "failed"
                t.error_code = "pipeline_exception"
                t.error_msg = f"{type(e).__name__}: {e}"
                t.finished_at = datetime.now(timezone.utc)
                session.commit()
        finally:
            session.close()
        return {"task_id": task_id, "status": "failed", "error": str(e)}

    # 读取最终状态
    session = get_sync_session()
    try:
        task = session.get(AuditTaskModel, task_id)
        status = task.status if task else "unknown"
        callback_url = task.callback_url if task else None
    finally:
        session.close()

    # 异步触发 webhook
    if callback_url and status == "succeeded":
        _fire_webhook(callback_url, task_id, status)

    return {"task_id": task_id, "status": status}


def _load_context(task_id: str) -> PipelineContext | None:
    """从 DB 加载任务数据, 构造 PipelineContext."""
    session = get_sync_session()
    try:
        task = session.get(AuditTaskModel, task_id)
        if not task:
            return None

        photos_q = (
            session.query(TaskPhoto)
            .filter(TaskPhoto.task_id == task_id)
            .order_by(TaskPhoto.seq)
            .all()
        )

        store = get_object_store()
        photos = []
        for ph in photos_q:
            # url 字段存的是 object_name, 转成可访问 URL
            url = store.presigned_url(ph.url) if not ph.url.startswith("http") else ph.url
            photos.append(PhotoInfo(
                photo_id=ph.photo_id,
                seq=ph.seq,
                url=url,
                original_filename=ph.original_filename,
                external_photo_id=ph.external_photo_id,
            ))

        manifest_url = (
            store.presigned_url(task.manifest_url)
            if not task.manifest_url.startswith("http")
            else task.manifest_url
        )

        ctx = PipelineContext(
            task_id=task_id,
            manifest_image_url=manifest_url,
            photos=photos,
            container_number=task.container_number,
        )

        # 从最新的 P1-A 结果恢复箱号来源信息。这样从 P1-B 继续执行时，
        # 最终报告仍能正确展示来源照片和置信度，而不需要再次扫描全部照片。
        if task.container_number:
            p1_stage = (
                session.query(TaskStage)
                .filter_by(task_id=task_id, stage="p1_a")
                .order_by(TaskStage.id.desc())
                .first()
            )
            payload = p1_stage.payload if p1_stage and isinstance(p1_stage.payload, dict) else {}
            target = task.container_number.strip().upper()
            for tried in payload.get("tried_photos") or []:
                raw = tried.get("raw") if isinstance(tried, dict) else None
                recognized = str((raw or {}).get("container_number") or "").strip().upper()
                if recognized != target or not (raw or {}).get("has_plate"):
                    continue
                ctx.container_number_source = "photo"
                ctx.container_source_photo_id = tried.get("photo_id")
                confidence = (raw or {}).get("container_number_confidence")
                if isinstance(confidence, (int, float)):
                    ctx.container_number_confidence = max(0.0, min(1.0, float(confidence)))
                break
            if ctx.container_number_source == "not_found":
                ctx.container_number_source = "manifest"
        return ctx
    finally:
        session.close()


def _fire_webhook(callback_url: str, task_id: str, status: str) -> None:
    """触发 webhook 回调(失败重试 3 次, 指数退避)."""
    cfg = get_config()
    secret = __import__("os").environ.get("WEBHOOK_SECRET", "")

    payload = {
        "task_id": task_id,
        "event": f"task.{status}",
        "status": status,
        "report_url": f"/api/v1/audits/{task_id}/report",
    }
    body = json.dumps(payload).encode()

    import hmac
    import hashlib
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else ""

    for attempt in range(cfg.webhook.max_retries):
        try:
            headers = {"Content-Type": "application/json"}
            if signature:
                headers["X-Audit-Signature"] = f"sha256={signature}"
            with httpx.Client(timeout=10) as client:
                resp = client.post(callback_url, content=body, headers=headers)
            if 200 <= resp.status_code < 300:
                _log.info("webhook_ok", task_id=task_id, attempt=attempt)
                return
            _log.warning("webhook_non_2xx", task_id=task_id,
                         attempt=attempt, status=resp.status_code)
        except Exception as e:
            _log.warning("webhook_error", task_id=task_id, attempt=attempt, error=str(e))
        import time
        backoff = cfg.webhook.backoff_seconds[min(attempt, len(cfg.webhook.backoff_seconds) - 1)]
        time.sleep(backoff)

    _log.error("webhook_giveup", task_id=task_id, callback_url=callback_url)


@celery_app.task(name="app.worker.tasks.cleanup_expired_tasks")
def cleanup_expired_tasks() -> dict:
    """保留兼容任务名，但禁止自动删除历史证据照片。"""
    _log.warning("cleanup_skipped", reason="history_retention_required")
    return {"cleaned": 0, "disabled": True, "reason": "history_retention_required"}
