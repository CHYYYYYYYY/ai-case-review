"""流水线编排器(v3.8-GG, 10 阶段).

按 P1 → P2 → P3 → P4 → P5 → P6 顺序执行, 各阶段进度写 DB.
单阶段失败不阻断整体(致命错误除外), 最终报告里聚合所有错误.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import bind_task_context, get_logger
from app.db.models import AuditReport, Task, TaskPhoto, TaskStage
from app.db.session import get_sync_session
from app.llm.client import LLMClient
from app.pipeline.schemas import PipelineContext
from app.pipeline.stages.base import StageResult
from app.pipeline.stages.p1_nameplate import P1ManifestOCRStage, P1NameplateStage
from app.pipeline.stages.p2_photo_index import (
    P2CargoStage,
    P2HandwritingStage,
    P2PhotoIndexStage,
)
from app.pipeline.stages.p3_preprocess import P3PreprocessStage
from app.pipeline.stages.p4_a2_direction import P4A2DirectionStage
from app.pipeline.stages.p4_component_match import P4ComponentMatchStage
from app.pipeline.stages.p5_damage_audit import P5DamageAuditStage
from app.pipeline.stages.p6_report import P6ReportStage

_log = get_logger(__name__)


def _json_safe(obj: Any) -> Any:
    """递归把不可 JSON 序列化的对象转成基本类型, 写库前清洗 payload.

    处理: dataclass / Pydantic BaseModel / set / tuple / numpy 标量 / Decimal / Enum.
    未识别的对象转成 repr 字符串(避免 stage 写库失败导致整任务卡死).
    """
    import dataclasses
    import decimal
    from enum import Enum
    try:
        import numpy as np
    except ImportError:
        np = None  # type: ignore
    try:
        from pydantic import BaseModel
    except ImportError:
        BaseModel = None  # type: ignore

    if obj is None or isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
            return None  # NaN/Inf -> None, JSONB 不接受
        return obj
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if np is not None and isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in obj]
    if BaseModel is not None and isinstance(obj, BaseModel):
        return _json_safe(obj.model_dump(mode="json"))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _json_safe(dataclasses.asdict(obj))
    if hasattr(obj, "__dict__"):
        return _json_safe(vars(obj))
    return repr(obj)


@dataclass
class PipelineDefinition:
    """阶段定义."""
    stage_name: str
    stage_label: str
    factory: Any   # callable(ctx, llm) -> BaseStage


# v3.8-GG 完整 10 阶段流水线
PIPELINE_STAGES: list[PipelineDefinition] = [
    PipelineDefinition("p1_a", "P1-A 铭牌识别", P1NameplateStage),
    PipelineDefinition("p1_b", "P1-B 清单 OCR", P1ManifestOCRStage),
    PipelineDefinition("p2_a", "P2-A 照片索引", P2PhotoIndexStage),
    PipelineDefinition("p2_b", "P2-B 手写 Location", P2HandwritingStage),
    PipelineDefinition("p2_c", "P2-C 货物识别", P2CargoStage),
    PipelineDefinition("p3", "P3 清单预处理", P3PreprocessStage),
    PipelineDefinition("p4_a1", "P4-A1 部件匹配", P4ComponentMatchStage),
    PipelineDefinition("p4_a2", "P4-A2 方向确认", P4A2DirectionStage),
    PipelineDefinition("p5_b", "P5-B 损伤核验", P5DamageAuditStage),
    PipelineDefinition("p6", "P6 报告编译", P6ReportStage),
]

# 致命阶段: 失败即整单失败
FATAL_STAGES = {"p1_b"}   # 清单 OCR 失败 = 无法继续

# P2 阶段完成后同步 DB
P2_SYNC_STAGES = {"p2_a", "p2_b", "p2_c"}


class Orchestrator:
    """流水线编排器."""

    def __init__(self, ctx: PipelineContext):
        self.ctx = ctx
        self.llm = LLMClient()
        bind_task_context(ctx.task_id)

    async def execute(self, start_stage: str | None = None) -> None:
        """执行流水线；失败任务可从指定阶段继续，避免重复昂贵的前置识别。"""
        stage_names = [stage.stage_name for stage in PIPELINE_STAGES]
        if start_stage is not None and start_stage not in stage_names:
            raise ValueError(f"unknown start stage: {start_stage}")
        start_index = stage_names.index(start_stage) if start_stage else 0
        first_stage = PIPELINE_STAGES[start_index]
        _log.info("pipeline_start", task_id=self.ctx.task_id,
                  photos=len(self.ctx.photos), start_stage=first_stage.stage_name)

        self._update_task_status("running", current_stage=first_stage.stage_name)

        total = len(PIPELINE_STAGES)
        for idx, stage_def in enumerate(
            PIPELINE_STAGES[start_index:], start=start_index + 1
        ):
            if self._is_cancelled():
                _log.info("pipeline_cancelled", task_id=self.ctx.task_id)
                return

            stage = stage_def.factory(self.ctx, self.llm)
            stage.stage_name = stage_def.stage_name

            self._update_task_status(
                "running",
                current_stage=stage_def.stage_name,
                progress_detail=f"{stage_def.stage_label} ({idx}/{total})",
            )

            try:
                result: StageResult = await stage.run()
                self._persist_stage_result(result)
                if stage_def.stage_name in P2_SYNC_STAGES:
                    self._sync_photo_indexes_to_db()
                _log.info("stage_done",
                          stage=stage_def.stage_name,
                          success=result.success,
                          duration_ms=result.duration_ms,
                          tokens=result.tokens_used)

                if not result.success and stage_def.stage_name in FATAL_STAGES:
                    error_code = f"{stage_def.stage_name}_failed"
                    if result.error and result.error.startswith(
                        f"{stage_def.stage_name}_"
                    ):
                        error_code = result.error
                    self._fail_task(
                        error_code,
                        result.error or "unknown",
                    )
                    _log.error("pipeline_fatal", stage=stage_def.stage_name, error=result.error)
                    return

            except Exception as e:
                _log.exception("stage_exception", stage=stage_def.stage_name)
                self.ctx.errors.append(f"{stage_def.stage_name}: {type(e).__name__}: {e}")
                if stage_def.stage_name in FATAL_STAGES:
                    self._fail_task(
                        f"{stage_def.stage_name}_exception",
                        str(e),
                    )
                    return
                # 非致命, 继续

        # P6 一定执行, 报告必出
        self._finalize_task()
        _log.info("pipeline_done",
                  task_id=self.ctx.task_id,
                  tokens=self.ctx.total_tokens,
                  cost=self.ctx.total_cost_cny)

    # ----------------------- DB 状态更新 -----------------------

    def _is_cancelled(self) -> bool:
        session = get_sync_session()
        try:
            task = session.get(Task, self.ctx.task_id)
            return bool(task and task.status == "cancelled")
        finally:
            session.close()

    def _sync_photo_indexes_to_db(self) -> None:
        """把 P2 结果写回 task_photos."""
        session = get_sync_session()
        try:
            for photo in self.ctx.photos:
                row = session.get(TaskPhoto, photo.photo_id)
                if not row:
                    continue
                idx = self.ctx.photo_indexes.get(photo.photo_id)
                if idx:
                    row.component_type = idx.component_type
                    row.is_on_truck = idx.is_on_truck
                    row.is_interior = idx.is_interior
                    row.likely_location = idx.likely_location
                    row.has_damage = idx.has_damage
                    row.is_plate_info = idx.is_plate_info
                    row.stage_error = idx.stage_error
                mark = self.ctx.handwritten_marks.get(photo.photo_id)
                if mark:
                    row.handwritten_location = mark.corrected_to or mark.handwritten_location
                    row.chinese_direction = mark.chinese_direction
            if self.ctx.cargo_info and self.ctx.cargo_info.source_photo_ids:
                for pid in self.ctx.cargo_info.source_photo_ids:
                    row = session.get(TaskPhoto, pid)
                    if row:
                        row.is_door_open = True
                        row.cargo_name = self.ctx.cargo_info.cargo_name
            session.commit()
        finally:
            session.close()

    def _update_task_status(
        self, status: str, current_stage: str | None = None,
        progress_detail: str | None = None,
    ) -> None:
        session = get_sync_session()
        try:
            task = session.get(Task, self.ctx.task_id)
            if not task:
                return
            task.status = status
            if current_stage:
                task.current_stage = current_stage
            if progress_detail:
                task.progress_detail = progress_detail
            if self.ctx.container_number and not task.container_number:
                task.container_number = self.ctx.container_number
            session.commit()
        finally:
            session.close()

    def _persist_stage_result(self, result: StageResult) -> None:
        session = get_sync_session()
        try:
            stage = TaskStage(
                task_id=self.ctx.task_id,
                stage=result.stage,
                payload=_json_safe(result.payload),
                tokens_used=result.tokens_used or None,
                duration_ms=result.duration_ms or None,
            )
            session.add(stage)
            session.commit()
        finally:
            session.close()

    def _fail_task(self, code: str, msg: str) -> None:
        session = get_sync_session()
        try:
            task = session.get(Task, self.ctx.task_id)
            if task:
                task.status = "failed"
                task.error_code = code
                task.error_msg = msg
                from datetime import datetime, timezone
                task.finished_at = datetime.now(timezone.utc)
                session.commit()
        finally:
            session.close()

    def _finalize_task(self) -> None:
        session = get_sync_session()
        try:
            task = session.get(Task, self.ctx.task_id)
            if not task:
                return
            task.status = "succeeded"
            task.current_stage = "p6"
            task.final_recommendation = self.ctx.report.get("final_recommendation")
            task.cost_tokens = self.ctx.total_tokens
            task.cost_cny = self.ctx.total_cost_cny
            from datetime import datetime, timezone
            task.finished_at = datetime.now(timezone.utc)

            # 成功任务也允许重新触发流水线。已有报告必须原位更新，不能再次
            # INSERT 同一主键，否则生产纠错回放会在最后一步失败。
            report_row = session.get(AuditReport, self.ctx.task_id)
            if report_row:
                report_row.report = self.ctx.report
            else:
                session.add(AuditReport(
                    task_id=self.ctx.task_id,
                    report=self.ctx.report,
                ))
            session.commit()
        finally:
            session.close()
