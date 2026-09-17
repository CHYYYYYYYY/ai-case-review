from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.audits import retry_failed_audit


class _Session:
    def __init__(self, task) -> None:
        self.task = task
        self.committed = False

    async def get(self, _model, _task_id):
        return self.task

    async def commit(self) -> None:
        self.committed = True


async def test_retry_failed_p1_b_starts_from_p1_b(monkeypatch):
    task = SimpleNamespace(
        status="failed",
        current_stage="p1_b",
        progress_detail=None,
        error_code="p1_b_failed",
        error_msg="p1_b_json_parse_failed",
        finished_at=object(),
    )
    session = _Session(task)
    calls = []
    monkeypatch.setattr(
        "app.api.audits.run_audit_pipeline.delay",
        lambda *args: calls.append(args),
    )

    response = await retry_failed_audit("aud_test", session)

    assert response == {
        "task_id": "aud_test",
        "status": "pending",
        "start_stage": "p1_b",
    }
    assert calls == [("aud_test", "p1_b")]
    assert task.status == "pending"
    assert task.error_code is None
    assert task.finished_at is None
    assert session.committed is True


async def test_retry_rejects_non_failed_task():
    session = _Session(SimpleNamespace(status="running", current_stage="p1_b"))

    with pytest.raises(HTTPException) as exc_info:
        await retry_failed_audit("aud_test", session)

    assert exc_info.value.status_code == 409
