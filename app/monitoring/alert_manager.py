import asyncio
import logging
import weakref
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.health_check import HealthCheck
from app.models.monitor import Monitor
from app.models.monitor_alert_state import MonitorAlertState
from app.monitoring.slack_alerts import SlackAlertService, slack_alert_service

logger = logging.getLogger(__name__)
_monitor_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


def _lock_for_monitor(monitor_id: int) -> asyncio.Lock:
    lock = _monitor_locks.get(monitor_id)
    if lock is None:
        lock = asyncio.Lock()
        _monitor_locks[monitor_id] = lock
    return lock


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _historical_failure_streak(
    db: AsyncSession,
    monitor_id: int,
) -> tuple[int, datetime | None]:
    latest_healthy_result = await db.execute(
        select(HealthCheck.checked_at, HealthCheck.id)
        .where(HealthCheck.monitor_id == monitor_id)
        .where(HealthCheck.is_healthy.is_(True))
        .order_by(HealthCheck.checked_at.desc(), HealthCheck.id.desc())
        .limit(1)
    )
    latest_healthy = latest_healthy_result.first()
    failure_filters = [
        HealthCheck.monitor_id == monitor_id,
        HealthCheck.is_healthy.is_(False),
    ]
    if latest_healthy is not None:
        latest_healthy_at, latest_healthy_id = latest_healthy
        failure_filters.append(
            or_(
                HealthCheck.checked_at > latest_healthy_at,
                and_(
                    HealthCheck.checked_at == latest_healthy_at,
                    HealthCheck.id > latest_healthy_id,
                ),
            )
        )
    result = await db.execute(
        select(func.count(HealthCheck.id), func.min(HealthCheck.checked_at)).where(
            *failure_filters
        )
    )
    failure_count, outage_started_at = result.one()
    return int(failure_count), _as_utc(outage_started_at)


async def process_monitor_result(
    db: AsyncSession,
    monitor: Monitor,
    result: dict[str, Any],
    *,
    notifier: SlackAlertService | None = None,
    failure_threshold: int | None = None,
) -> HealthCheck:
    """Persist one result, atomically update alert state, then deliver any transition."""
    threshold = (
        settings.ALERT_FAILURE_THRESHOLD
        if failure_threshold is None
        else failure_threshold
    )
    if threshold < 1:
        raise ValueError("failure_threshold must be at least 1.")

    notifier = notifier or slack_alert_service
    transition: tuple[str, datetime, datetime | None, int] | None = None

    async with _lock_for_monitor(monitor.id):
        locked_monitor = await db.scalar(
            select(Monitor).where(Monitor.id == monitor.id).with_for_update()
        )
        if locked_monitor is None:
            raise ValueError(f"Monitor {monitor.id} no longer exists.")
        locked_monitor_id = locked_monitor.id
        monitor_name = locked_monitor.name
        monitor_url = locked_monitor.url

        state = await db.get(MonitorAlertState, monitor.id)
        if state is None:
            historical_failures, historical_outage_start = (
                await _historical_failure_streak(db, monitor.id)
            )
            is_historical_outage = historical_failures >= threshold
            state = MonitorAlertState(
                monitor_id=monitor.id,
                status="unhealthy" if is_historical_outage else "healthy",
                consecutive_failures=historical_failures,
                failure_streak_started_at=(
                    None if is_historical_outage else historical_outage_start
                ),
                outage_started_at=(
                    historical_outage_start if is_historical_outage else None
                ),
            )
            db.add(state)
        elif state.status not in {"healthy", "unhealthy", "recovering"}:
            historical_failures, historical_outage_start = (
                await _historical_failure_streak(db, monitor.id)
            )
            state.status = (
                "unhealthy" if historical_failures >= threshold else "healthy"
            )
            state.consecutive_failures = historical_failures
            state.failure_streak_started_at = (
                historical_outage_start
                if historical_failures < threshold
                else None
            )
            state.outage_started_at = (
                historical_outage_start
                if state.status == "unhealthy"
                else None
            )
            state.recovery_started_at = None

        now = datetime.now(UTC)
        was_unhealthy = state.status == "unhealthy"
        if result["is_healthy"]:
            if was_unhealthy:
                outage_started_at = _as_utc(state.outage_started_at)
                transition = ("recovery", now, outage_started_at, 0)
                state.status = "recovering"
                state.recovery_started_at = now
                state.outage_started_at = None
            elif state.status == "recovering":
                state.status = "healthy"
                state.recovery_started_at = None
            state.consecutive_failures = 0
            state.failure_streak_started_at = None
        else:
            if state.status == "recovering":
                state.status = "healthy"
                state.recovery_started_at = None
                state.consecutive_failures = 0
                state.failure_streak_started_at = None
            if state.status == "healthy" and state.consecutive_failures == 0:
                state.failure_streak_started_at = now
            state.consecutive_failures += 1
            if not was_unhealthy and state.consecutive_failures >= threshold:
                state.status = "unhealthy"
                state.outage_started_at = state.failure_streak_started_at or now
                state.failure_streak_started_at = None
                transition = (
                    "outage",
                    now,
                    None,
                    state.consecutive_failures,
                )

        entry = HealthCheck(
            monitor_id=locked_monitor.id,
            status_code=result["status_code"],
            latency_ms=result["latency_ms"],
            is_healthy=result["is_healthy"],
            error_message=result["error"],
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)

    if transition is not None:
        kind, occurred_at, outage_started_at, detail = transition
        try:
            if kind == "outage":
                await notifier.send_outage(
                    monitor_name=monitor_name,
                    url=monitor_url,
                    detected_at=occurred_at,
                    consecutive_failures=detail,
                    status_code=result["status_code"],
                    error=result["error"],
                )
            else:
                duration = (
                    (occurred_at - outage_started_at).total_seconds()
                    if outage_started_at is not None
                    else None
                )
                await notifier.send_recovery(
                    monitor_name=monitor_name,
                    url=monitor_url,
                    recovered_at=occurred_at,
                    outage_duration_seconds=duration,
                )
        except Exception as exc:
            logger.error(
                "Alert delivery for monitor %s failed (%s).",
                locked_monitor_id,
                type(exc).__name__,
            )

    return entry
