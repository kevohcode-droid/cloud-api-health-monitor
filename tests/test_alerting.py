import asyncio
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.config import settings
from app.models import HealthCheck, Monitor, MonitorAlertState
from app.monitoring.alert_manager import process_monitor_result
from app.monitoring.scheduler import run_monitor_check
from app.monitoring.slack_alerts import SlackAlertService


class RecordingNotifier(SlackAlertService):
    def __init__(self):
        super().__init__(webhook_url=None)
        self.outages = []
        self.recoveries = []

    async def send_outage(self, **kwargs):
        self.outages.append(kwargs)
        return True

    async def send_recovery(self, **kwargs):
        self.recoveries.append(kwargs)
        return True


class FailingNotifier(RecordingNotifier):
    async def send_outage(self, **kwargs):
        self.outages.append(kwargs)
        raise httpx.ReadTimeout("simulated timeout")


@pytest_asyncio.fixture
async def database(tmp_path):
    database_path = tmp_path / "alerts.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        monitor = Monitor(name="payments-api", url="http://127.0.0.1:8765/health")
        db.add(monitor)
        await db.commit()
        await db.refresh(monitor)
        monitor_id = monitor.id

    yield url, engine, factory, monitor_id
    await engine.dispose()


def _result(is_healthy, *, status_code=None, error=None):
    return {
        "url": "http://127.0.0.1:8765/health",
        "status_code": status_code,
        "latency_ms": 12.3,
        "is_healthy": is_healthy,
        "error": error,
    }


def _response_transport(status_codes, *, headers=None):
    calls = []

    async def handler(request):
        calls.append(request)
        status_code = status_codes.pop(0)
        return httpx.Response(status_code, request=request, headers=headers)

    return httpx.MockTransport(handler), calls


@pytest.mark.asyncio
async def test_successful_outage_and_recovery_alert_delivery():
    received_payloads = []

    async def handler(request):
        received_payloads.append(request.read())
        return httpx.Response(200, request=request, text="ok")

    service = SlackAlertService(
        "https://hooks.slack.test/services/secret",
        transport=httpx.MockTransport(handler),
    )
    occurred_at = datetime(2026, 10, 12, 1, 2, 3, tzinfo=UTC)

    assert await service.send_outage(
        monitor_name="billing <@channel>",
        url="https://api.example.test/health?a=1&b=2",
        detected_at=occurred_at,
        consecutive_failures=3,
        status_code=503,
        error=None,
    )
    assert await service.send_recovery(
        monitor_name="billing",
        url="https://api.example.test/health",
        recovered_at=occurred_at,
        outage_duration_seconds=61,
    )

    assert len(received_payloads) == 2
    outage = received_payloads[0].decode()
    assert "OUTAGE: billing &lt;@channel&gt;" in outage
    assert "Consecutive failures: 3" in outage
    assert "HTTP 503" in outage
    assert "Detected at (UTC): 2026-10-12T01:02:03+00:00" in outage
    recovery = received_payloads[1].decode()
    assert "RECOVERY: billing" in recovery
    assert "Previous outage duration: 61 seconds" in recovery


@pytest.mark.asyncio
async def test_missing_webhook_is_reported_without_request(caplog, monkeypatch):
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)
    service = SlackAlertService(webhook_url=None)

    delivered = await service.send_recovery(
        monitor_name="api",
        url="http://localhost",
        recovered_at=datetime.now(UTC),
        outage_duration_seconds=None,
    )

    assert delivered is False
    assert "SLACK_WEBHOOK_URL is not configured" in caplog.text


@pytest.mark.asyncio
async def test_timeout_is_retried_and_reported_without_raising(caplog):
    calls = 0
    sleeps = []

    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    async def sleep(delay):
        sleeps.append(delay)

    service = SlackAlertService(
        "https://hooks.slack.test/services/secret",
        max_retries=1,
        backoff_seconds=0.25,
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )

    assert not await service.send_recovery(
        monitor_name="api",
        url="http://localhost",
        recovered_at=datetime.now(UTC),
        outage_duration_seconds=None,
    )
    assert calls == 2
    assert sleeps == [0.25]
    assert "ReadTimeout" in caplog.text
    assert "hooks.slack.test" not in caplog.text


@pytest.mark.asyncio
async def test_transient_server_errors_use_exponential_backoff():
    transport, calls = _response_transport([500, 503, 200])
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    service = SlackAlertService(
        "https://hooks.slack.test/services/secret",
        max_retries=2,
        backoff_seconds=0.25,
        transport=transport,
        sleep=sleep,
    )

    assert await service.send_recovery(
        monitor_name="api",
        url="http://localhost",
        recovered_at=datetime.now(UTC),
        outage_duration_seconds=None,
    )
    assert len(calls) == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
async def test_rate_limit_retries_after_retry_after_delay():
    transport, calls = _response_transport([429, 200], headers={"Retry-After": "2"})
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    service = SlackAlertService(
        "https://hooks.slack.test/services/secret",
        max_retries=1,
        transport=transport,
        sleep=sleep,
    )

    assert await service.send_recovery(
        monitor_name="api",
        url="http://localhost",
        recovered_at=datetime.now(UTC),
        outage_duration_seconds=None,
    )
    assert len(calls) == 2
    assert sleeps == [2]


@pytest.mark.asyncio
async def test_consecutive_failures_suppress_duplicates_then_recover(database):
    _, _, factory, monitor_id = database
    notifier = RecordingNotifier()

    for _ in range(4):
        async with factory() as db:
            monitor = await db.get(Monitor, monitor_id)
            await process_monitor_result(
                db,
                monitor,
                _result(False, status_code=503),
                notifier=notifier,
                failure_threshold=3,
            )

    assert len(notifier.outages) == 1
    assert notifier.outages[0]["consecutive_failures"] == 3
    async with factory() as db:
        monitor = await db.get(Monitor, monitor_id)
        await process_monitor_result(
            db,
            monitor,
            _result(True, status_code=200),
            notifier=notifier,
            failure_threshold=3,
        )
    assert len(notifier.recoveries) == 1
    assert notifier.recoveries[0]["outage_duration_seconds"] >= 0

    async with factory() as db:
        monitor = await db.get(Monitor, monitor_id)
        await process_monitor_result(
            db,
            monitor,
            _result(True, status_code=200),
            notifier=notifier,
            failure_threshold=3,
        )
        state = await db.get(MonitorAlertState, monitor_id)
    assert state.status == "healthy"
    assert len(notifier.outages) == len(notifier.recoveries) == 1


@pytest.mark.asyncio
async def test_alert_state_survives_database_engine_restart(database):
    url, engine, factory, monitor_id = database
    notifier = RecordingNotifier()

    async with factory() as db:
        monitor = await db.get(Monitor, monitor_id)
        await process_monitor_result(
            db,
            monitor,
            _result(False, status_code=500),
            notifier=notifier,
            failure_threshold=2,
        )
    await engine.dispose()

    restarted_engine = create_async_engine(url)
    restarted_factory = async_sessionmaker(restarted_engine, expire_on_commit=False)
    async with restarted_factory() as db:
        monitor = await db.get(Monitor, monitor_id)
        await process_monitor_result(
            db,
            monitor,
            _result(False, status_code=500),
            notifier=notifier,
            failure_threshold=2,
        )
        state = await db.get(MonitorAlertState, monitor_id)
    assert state.status == "unhealthy"
    assert state.consecutive_failures == 2
    assert len(notifier.outages) == 1
    await restarted_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_checks_only_emit_one_outage_alert(database):
    _, _, factory, monitor_id = database
    notifier = RecordingNotifier()

    async def fail_once():
        async with factory() as db:
            monitor = await db.get(Monitor, monitor_id)
            await process_monitor_result(
                db,
                monitor,
                _result(False, status_code=503),
                notifier=notifier,
                failure_threshold=2,
            )

    await asyncio.gather(fail_once(), fail_once(), fail_once())

    async with factory() as db:
        state = await db.get(MonitorAlertState, monitor_id)
    assert state.status == "unhealthy"
    assert state.consecutive_failures == 3
    assert len(notifier.outages) == 1


@pytest.mark.asyncio
async def test_scheduler_survives_alert_delivery_failure(database, monkeypatch):
    _, _, factory, monitor_id = database
    from app.monitoring import scheduler

    async def unhealthy(url, timeout):
        assert timeout == 5.0
        result = _result(False, status_code=503)
        result["url"] = url
        return result

    monkeypatch.setattr(scheduler, "check_url", unhealthy)
    async with factory() as db:
        monitor = await db.get(Monitor, monitor_id)
        for _ in range(3):
            entry = await run_monitor_check(
                db,
                monitor,
                notifier=FailingNotifier(),
            )
    assert entry.is_healthy is False
    async with factory() as db:
        state = await db.get(MonitorAlertState, monitor_id)
    assert state.status == "unhealthy"


@pytest.mark.asyncio
async def test_missing_state_reconstructs_outage_from_check_history(database):
    _, _, factory, monitor_id = database
    notifier = RecordingNotifier()
    async with factory() as db:
        db.add_all(
            [
                HealthCheck(
                    monitor_id=monitor_id,
                    status_code=503,
                    latency_ms=5,
                    is_healthy=False,
                )
                for _ in range(3)
            ]
        )
        await db.commit()
        monitor = await db.get(Monitor, monitor_id)
        await process_monitor_result(
            db,
            monitor,
            _result(False, status_code=503),
            notifier=notifier,
            failure_threshold=3,
        )
        state = await db.get(MonitorAlertState, monitor_id)
    assert state.status == "unhealthy"
    assert state.consecutive_failures == 4
    assert not notifier.outages
