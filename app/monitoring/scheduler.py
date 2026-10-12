import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.health_check import HealthCheck
from app.models.monitor import Monitor
from app.monitoring.checker import check_url
from app.monitoring.alert_manager import process_monitor_result
from app.monitoring.slack_alerts import SlackAlertService

logger = logging.getLogger(__name__)
MIN_MONITOR_INTERVAL_SECONDS = 10


async def run_monitor_check(
    db: AsyncSession,
    monitor: Monitor,
    *,
    notifier: SlackAlertService | None = None,
) -> HealthCheck:
    if monitor.url is None or monitor.url == "":
        raise ValueError(f"Monitor {monitor.id} has no URL configured")

    result = await check_url(monitor.url, timeout=5.0)
    entry = await process_monitor_result(db, monitor, result, notifier=notifier)
    logger.info(
        "Persisted scheduled check for monitor %s with status %s",
        monitor.id,
        result["status_code"],
    )
    return entry


class MonitorScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    @staticmethod
    def _job_id(monitor_id: int) -> str:
        return f"monitor-{monitor_id}"

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def normalize_interval(self, interval_seconds: int) -> int:
        if interval_seconds is None:
            raise ValueError("Monitor interval is required.")
        interval_seconds = int(interval_seconds)
        if interval_seconds < MIN_MONITOR_INTERVAL_SECONDS:
            raise ValueError(
                "Monitor interval must be at least "
                f"{MIN_MONITOR_INTERVAL_SECONDS} seconds."
            )
        return interval_seconds

    def register_monitor(self, monitor: Monitor) -> None:
        if monitor is None:
            return

        if not monitor.enabled:
            self.unschedule_monitor(monitor.id)
            return

        try:
            interval_seconds = self.normalize_interval(monitor.interval_seconds)
        except ValueError:
            logger.warning(
                "Skipping monitor %s because its interval is invalid: %s",
                monitor.id,
                monitor.interval_seconds,
            )
            self.unschedule_monitor(monitor.id)
            return

        job_id = self._job_id(monitor.id)
        self.scheduler.add_job(
            self._run_job,
            trigger="interval",
            args=[monitor.id],
            seconds=interval_seconds,
            id=job_id,
            name=f"monitor-{monitor.id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Scheduled monitor %s every %s seconds",
            monitor.id,
            interval_seconds,
        )

    def unschedule_monitor(self, monitor_id: int) -> None:
        job_id = self._job_id(monitor_id)
        if self.scheduler.get_job(job_id) is not None:
            self.scheduler.remove_job(job_id)
            logger.info("Removed scheduler job for monitor %s", monitor_id)

    async def sync_from_database(self) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database is not configured; cannot sync scheduler jobs.")

        async with SessionLocal() as db:
            result = await db.execute(select(Monitor).order_by(Monitor.id.asc()))
            monitors = result.scalars().all()

        reconciled_ids = {monitor.id for monitor in monitors}
        current_jobs = {
            int(job.id.replace("monitor-", "")) for job in self.scheduler.get_jobs()
        }
        for monitor in monitors:
            self.register_monitor(monitor)

        for stale_id in sorted(current_jobs - reconciled_ids):
            self.unschedule_monitor(stale_id)

    async def _run_job(self, monitor_id: int) -> None:
        if SessionLocal is None:
            raise RuntimeError("Database is not configured; cannot execute monitor job.")

        async with SessionLocal() as db:
            monitor = await db.get(Monitor, monitor_id)
            if monitor is None:
                self.unschedule_monitor(monitor_id)
                logger.warning("Monitor %s no longer exists; job removed.", monitor_id)
                return

            if not monitor.enabled:
                self.unschedule_monitor(monitor_id)
                logger.info("Monitor %s is disabled; job removed.", monitor_id)
                return

            try:
                await run_monitor_check(db, monitor)
            except Exception:
                logger.exception(
                    "Scheduled health check for monitor %s failed",
                    monitor.id,
                )
                await db.rollback()
                raise


monitor_scheduler = MonitorScheduler()
