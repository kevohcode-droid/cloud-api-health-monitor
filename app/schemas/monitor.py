from datetime import datetime

from pydantic import BaseModel, Field


class MonitorCreate(BaseModel):
    name: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    interval_seconds: int = Field(default=60, ge=10)
    enabled: bool = True


class MonitorRead(MonitorCreate):
    id: int
    created_at: datetime


class HealthCheckWrite(BaseModel):
    status_code: int | None = None
    latency_ms: float | None = None
    is_healthy: bool
    error_message: str | None = None


class HealthCheckRead(HealthCheckWrite):
    id: int
    monitor_id: int
    checked_at: datetime
