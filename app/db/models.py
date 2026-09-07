"""SQLAlchemy ORM 模型.

对应技术方案第五章数据模型:
  - tasks           稽核任务主表
  - task_photos     任务照片
  - task_stages     各阶段原始输出(用于调试与回放)
  - audit_reports   最终报告(1:1)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # status: pending / running / succeeded / failed / cancelled
    current_stage: Mapped[Optional[str]] = mapped_column(String(16))
    # current_stage: p1_a / p1_b / p2_a / p2_b / p2_c / p3 / p4_a1 / p4_a2 / p5_b / p6
    progress_detail: Mapped[Optional[str]] = mapped_column(Text)
    container_number: Mapped[Optional[str]] = mapped_column(String(11))

    manifest_url: Mapped[str] = mapped_column(Text, nullable=False)
    callback_url: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column("metadata", JSONB)
    priority: Mapped[int] = mapped_column(Integer, default=0)

    final_recommendation: Mapped[Optional[str]] = mapped_column(String(16))
    # VERIFIED / PARTIAL / MISSING

    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    error_msg: Mapped[Optional[str]] = mapped_column(Text)

    cost_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    cost_cny: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    photos: Mapped[List["TaskPhoto"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    stages: Mapped[List["TaskStage"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    report: Mapped[Optional["AuditReport"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", uselist=False
    )


class TaskPhoto(Base):
    __tablename__ = "task_photos"

    photo_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))  # 用户上传时的原始文件名
    # 客户上传时提供的业务照片 ID；历史任务和旧客户端允许为空。
    external_photo_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # P2-A 输出
    component_type: Mapped[Optional[str]] = mapped_column(String(16))
    is_on_truck: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_interior: Mapped[Optional[bool]] = mapped_column(Boolean)
    likely_location: Mapped[Optional[str]] = mapped_column(String(16))
    has_damage: Mapped[Optional[bool]] = mapped_column(Boolean)
    is_plate_info: Mapped[Optional[bool]] = mapped_column(Boolean)

    # P2-B 输出(手写 Location)
    handwritten_location: Mapped[Optional[str]] = mapped_column(String(16))
    chinese_direction: Mapped[Optional[str]] = mapped_column(String(8))

    # P2-C 输出
    is_door_open: Mapped[Optional[bool]] = mapped_column(Boolean)
    cargo_name: Mapped[Optional[str]] = mapped_column(String(64))

    stage_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="photos")
    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_photos_task_seq"),
        UniqueConstraint(
            "task_id",
            "external_photo_id",
            name="uq_task_photos_task_external_photo_id",
        ),
    )


class TaskStage(Base):
    __tablename__ = "task_stages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    # stage: p1_a / p1_b / p2_a / p2_b / p2_c / p3 / p4_a1 / p4_a2 / p5_b / p6
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="stages")


class AuditReport(Base):
    __tablename__ = "audit_reports"

    task_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    report: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="report")
