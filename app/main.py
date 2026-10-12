from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, get_db_optional, init_db
from app.models.health_check import HealthCheck
from app.models.monitor import Monitor
from app.monitoring.checker import check_url
from app.monitoring.alert_manager import process_monitor_result
from app.monitoring.scheduler import monitor_scheduler, run_monitor_check
from app.schemas.monitor import HealthCheckRead, MonitorCreate, MonitorRead


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    monitor_scheduler.start()
    await monitor_scheduler.sync_from_database()
    try:
        yield
    finally:
        monitor_scheduler.shutdown()


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Return a simple health signal to confirm the API is running."""
    return {"status": "ok"}


@app.get("/check")
async def check_url_endpoint(
    url: str,
    timeout: float = 5.0,
    monitor_id: int | None = None,
    db: AsyncSession | None = Depends(get_db_optional),
):
    """
    Check a URL and return the result from the existing async monitoring logic.

    Query parameters:
    - url: the URL to inspect
    - timeout: maximum time in seconds before treating the request as a timeout
    - monitor_id: optional monitor to associate with this result in PostgreSQL
    """
    result = await check_url(url, timeout=timeout)

    if db is not None and monitor_id is not None:
        monitor = await db.get(Monitor, monitor_id)
        if monitor is None:
            raise HTTPException(status_code=404, detail="Monitor not found")

        await process_monitor_result(db, monitor, result)

    return result


@app.post("/monitors", response_model=MonitorRead)
async def create_monitor(payload: MonitorCreate, db: AsyncSession = Depends(get_db)):
    monitor = Monitor(**payload.model_dump())
    db.add(monitor)
    await db.commit()
    await db.refresh(monitor)
    monitor_scheduler.register_monitor(monitor)
    return monitor


@app.get("/monitors", response_model=list[MonitorRead])
async def list_monitors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Monitor).order_by(Monitor.created_at.desc()))
    return result.scalars().all()


@app.post("/monitors/{monitor_id}/checks", response_model=HealthCheckRead)
async def save_monitor_check(
    monitor_id: int,
    db: AsyncSession = Depends(get_db),
):
    monitor = await db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    return await run_monitor_check(db, monitor)


@app.get("/monitors/{monitor_id}/checks", response_model=list[HealthCheckRead])
async def list_monitor_checks(monitor_id: int, db: AsyncSession = Depends(get_db)):
    monitor = await db.get(Monitor, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")

    result = await db.execute(
        select(HealthCheck)
        .where(HealthCheck.monitor_id == monitor_id)
        .order_by(HealthCheck.checked_at.desc())
    )
    return result.scalars().all()
