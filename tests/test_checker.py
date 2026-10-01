import sys
from pathlib import Path

import pytest
import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app
from app.monitoring.checker import check_url


@pytest.mark.asyncio
async def test_http_200_is_healthy():
    result = await check_url("https://example.com")

    assert result["url"] == "https://example.com"
    assert result["status_code"] == 200
    assert result["is_healthy"] is True
    assert result["error"] is None


@pytest.mark.asyncio
async def test_http_500_is_unhealthy():
    result = await check_url("https://httpbin.org/status/500")

    assert result["status_code"] == 500
    assert result["is_healthy"] is False
    assert result["error"] is None


@pytest.mark.asyncio
async def test_timeout():
    result = await check_url("https://httpbin.org/delay/10", timeout=0.1)

    assert result["status_code"] is None
    assert result["is_healthy"] is False
    assert result["error"] == "timeout"


@pytest.mark.asyncio
async def test_invalid_request():
    result = await check_url("https://nonexistent-example-domain.invalid")

    assert result["status_code"] is None
    assert result["is_healthy"] is False
    assert isinstance(result["error"], str)


@pytest.mark.asyncio
async def test_fastapi_health_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_fastapi_check_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/check", params={"url": "https://example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["url"] == "https://example.com"
    assert payload["status_code"] == 200
    assert payload["is_healthy"] is True
    assert payload["error"] is None
