"""Atomic admission control for audit submissions.

The Celery list length alone is subject to a check-then-enqueue race.  A short
lived Redis reservation closes that gap: each accepted HTTP request reserves
one queue slot before files are stored, then hands that slot over to Celery.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from redis import asyncio as redis_async

from app.core.config import AppConfig

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_RESERVE_SCRIPT = """
local queue_key = KEYS[1]
local reservation_key = KEYS[2]
local daily_key = KEYS[3]
local max_pending = tonumber(ARGV[1])
local max_daily = tonumber(ARGV[2])
local initial_daily = tonumber(ARGV[3])
local daily_ttl = tonumber(ARGV[4])
local reservation_ttl = tonumber(ARGV[5])

if redis.call('EXISTS', daily_key) == 0 then
  redis.call('SET', daily_key, initial_daily, 'EX', daily_ttl, 'NX')
end

local queued = redis.call('LLEN', queue_key)
local reserved = tonumber(redis.call('GET', reservation_key) or '0')
local daily = tonumber(redis.call('GET', daily_key) or '0')
local pending = queued + reserved

if max_pending > 0 and pending >= max_pending then
  return {0, 1, pending, daily}
end
if max_daily > 0 and daily >= max_daily then
  return {0, 2, pending, daily}
end

redis.call('INCR', reservation_key)
redis.call('EXPIRE', reservation_key, reservation_ttl)
redis.call('INCR', daily_key)
return {1, 0, pending + 1, daily + 1}
"""

_RELEASE_SCRIPT = """
local reservation_key = KEYS[1]
local daily_key = KEYS[2]
local accepted = tonumber(ARGV[1])
local reserved = tonumber(redis.call('GET', reservation_key) or '0')
if reserved > 0 then
  redis.call('DECR', reservation_key)
end
if accepted == 0 then
  local daily = tonumber(redis.call('GET', daily_key) or '0')
  if daily > 0 then
    redis.call('DECR', daily_key)
  end
end
return 1
"""


@dataclass(frozen=True)
class AdmissionReservation:
    reservation_key: str
    daily_key: str


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    reason: str | None
    pending_count: int
    daily_count: int
    reservation: AdmissionReservation | None = None


def shanghai_day_window(now: datetime | None = None) -> tuple[datetime, datetime, str, int]:
    local_now = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    ttl = max(60, int((end - local_now).total_seconds()) + 60)
    return start, end, start.date().isoformat(), ttl


async def reserve_audit_slot(
    cfg: AppConfig,
    *,
    initial_daily_count: int,
    day_key: str,
    daily_ttl_seconds: int,
) -> AdmissionDecision:
    """Atomically reserve capacity or return a stable rejection reason."""
    queue_key = cfg.redis.queue_name
    reservation_key = f"{queue_key}:admission:reservations"
    daily_key = f"{queue_key}:admission:daily:{day_key}"
    client = redis_async.from_url(cfg.redis.url(), decode_responses=True)
    try:
        raw = await client.eval(
            _RESERVE_SCRIPT,
            3,
            queue_key,
            reservation_key,
            daily_key,
            cfg.admission.max_pending_tasks,
            cfg.admission.max_daily_tasks,
            max(0, initial_daily_count),
            daily_ttl_seconds,
            cfg.admission.reservation_ttl_seconds,
        )
    finally:
        await client.aclose()

    accepted, reason_code, pending_count, daily_count = [int(value) for value in raw]
    reason = {1: "queue_full", 2: "daily_limit"}.get(reason_code)
    reservation = (
        AdmissionReservation(reservation_key=reservation_key, daily_key=daily_key)
        if accepted
        else None
    )
    return AdmissionDecision(
        accepted=bool(accepted),
        reason=reason,
        pending_count=pending_count,
        daily_count=daily_count,
        reservation=reservation,
    )


async def release_audit_slot(
    cfg: AppConfig,
    reservation: AdmissionReservation,
    *,
    accepted: bool,
) -> None:
    """Release the temporary queue reservation.

    ``accepted`` means a durable task was created.  Failed submissions also
    give back their daily allowance.
    """
    client = redis_async.from_url(cfg.redis.url(), decode_responses=True)
    try:
        await client.eval(
            _RELEASE_SCRIPT,
            2,
            reservation.reservation_key,
            reservation.daily_key,
            1 if accepted else 0,
        )
    finally:
        await client.aclose()
