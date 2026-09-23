from __future__ import annotations

from types import SimpleNamespace

from app.db.models import AuditReport, Task
from app.pipeline.orchestrator import Orchestrator
from app.pipeline.schemas import PipelineContext


def test_finalize_rerun_updates_existing_report(monkeypatch) -> None:
    task = SimpleNamespace()
    old_report = SimpleNamespace(report={"location": "BLSN"})

    class FakeSession:
        added = []
        committed = False

        def get(self, model, key):
            if model is Task:
                return task
            if model is AuditReport:
                return old_report
            return None

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.committed = True

        def close(self):
            pass

    session = FakeSession()
    monkeypatch.setattr(
        "app.pipeline.orchestrator.get_sync_session",
        lambda: session,
    )
    ctx = PipelineContext(task_id="aud_existing", manifest_image_url="manifest.jpg")
    ctx.report = {
        "final_recommendation": "PARTIAL",
        "location": "BL5N",
    }
    orchestrator = object.__new__(Orchestrator)
    orchestrator.ctx = ctx

    orchestrator._finalize_task()

    assert old_report.report == ctx.report
    assert session.added == []
    assert session.committed
    assert task.status == "succeeded"
    assert task.final_recommendation == "PARTIAL"
