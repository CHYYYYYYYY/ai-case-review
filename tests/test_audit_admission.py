from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import audits
from app.core.admission import (
    AdmissionDecision,
    AdmissionReservation,
    shanghai_day_window,
)


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _Session:
    def __init__(self) -> None:
        self.results = iter((7, 2))

    async def execute(self, _query: object) -> _ScalarResult:
        return _ScalarResult(next(self.results))


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        admission=SimpleNamespace(
            enabled=True,
            max_pending_tasks=3,
            max_daily_tasks=20,
            retry_after_seconds=600,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "expected_code", "current"),
    [
        ("queue_full", "AI_AUDIT_BUSY", 10),
        ("daily_limit", "AI_AUDIT_DAILY_LIMIT_REACHED", 20),
    ],
)
async def test_submission_limit_returns_stable_business_code(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    expected_code: str,
    current: int,
) -> None:
    async def reject(*_args: object, **_kwargs: object) -> AdmissionDecision:
        return AdmissionDecision(
            accepted=False,
            reason=reason,
            pending_count=10,
            daily_count=20,
        )

    monkeypatch.setattr(audits, "get_config", _config)
    monkeypatch.setattr(audits, "reserve_audit_slot", reject)

    with pytest.raises(HTTPException) as raised:
        await audits._reserve_submission_capacity(_Session())  # type: ignore[arg-type]

    assert raised.value.status_code == 429
    assert raised.value.detail["code"] == expected_code
    assert raised.value.detail["current"] == current
    assert raised.value.detail["limit"] == (3 if reason == "queue_full" else 20)
    assert raised.value.detail["retryable"] is True
    assert raised.value.headers == {"Retry-After": "600"}


@pytest.mark.asyncio
async def test_accepted_submission_returns_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservation = AdmissionReservation("audit:reservations", "audit:daily:2026-09-18")
    seen: dict[str, object] = {}

    async def accept(*_args: object, **_kwargs: object) -> AdmissionDecision:
        seen.update(_kwargs)
        return AdmissionDecision(
            accepted=True,
            reason=None,
            pending_count=4,
            daily_count=8,
            reservation=reservation,
        )

    monkeypatch.setattr(audits, "get_config", _config)
    monkeypatch.setattr(audits, "reserve_audit_slot", accept)

    assert await audits._reserve_submission_capacity(_Session()) == reservation  # type: ignore[arg-type]
    assert seen["initial_daily_count"] == 7
    assert seen["initial_active_count"] == 2


def test_daily_window_uses_shanghai_calendar_day() -> None:
    start, end, day_key, ttl = shanghai_day_window()
    assert start.tzinfo is not None
    assert start.hour == start.minute == start.second == 0
    assert end > start
    assert day_key == start.date().isoformat()
    assert ttl >= 60
