"""结构化日志配置(structlog).

每条日志含 task_id/stage/photo_id 字段, 便于按任务追踪.
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import get_config


def setup_logging() -> None:
    cfg = get_config()
    level = getattr(logging, cfg.log_level.upper(), logging.INFO)

    # 标准库 logging 兜底
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    timestamper = structlog.processors.TimeStamper(fmt="iso")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer() if cfg.env == "development" else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def bind_task_context(task_id: str, stage: str | None = None) -> None:
    """绑定任务级上下文, 后续该任务的所有日志都自动带 task_id."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(task_id=task_id)
    if stage:
        structlog.contextvars.bind_contextvars(stage=stage)


def bind_photo_context(photo_id: str) -> None:
    structlog.contextvars.bind_contextvars(photo_id=photo_id)
