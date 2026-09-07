"""Celery 应用配置."""
from __future__ import annotations

from celery import Celery

from app.core.config import get_config

cfg = get_config()

celery_app = Celery(
    "audit_service",
    broker=cfg.redis.url(),
    backend=cfg.redis.url(),
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    # 队列
    task_queues={"audit": {"exchange": "audit", "routing_key": "audit"}},
    task_default_queue=cfg.redis.queue_name,
    task_default_exchange=cfg.redis.queue_name,
    task_default_routing_key=cfg.redis.queue_name,
    # 并发与超时
    worker_concurrency=cfg.worker.concurrency,
    task_soft_time_limit=cfg.worker.task_soft_time_limit,
    task_time_limit=cfg.worker.task_time_limit,
    # 可靠性
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
)

# 历史稽核结果及证据照片需要长期保留。删除仅允许由运维人员后台人工执行，
# 因此不注册任何自动清理计划。
celery_app.conf.beat_schedule = {}
