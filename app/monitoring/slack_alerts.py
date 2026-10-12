import asyncio
import logging
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _safe_text(value: object) -> str:
    text = str(value)
    text = "".join(
        character
        for character in text
        if character in "\t " or ord(character) >= 32
    )
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds")


class SlackAlertService:
    def __init__(
        self,
        webhook_url: str | None = None,
        *,
        timeout_seconds: float = 5.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        max_retry_after_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.webhook_url = (
            settings.SLACK_WEBHOOK_URL if webhook_url is None else webhook_url
        )
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.max_retry_after_seconds = max_retry_after_seconds
        self.transport = transport
        self.sleep = sleep
        self._missing_webhook_logged = False

    async def send_outage(
        self,
        *,
        monitor_name: str,
        url: str,
        detected_at: datetime,
        consecutive_failures: int,
        status_code: int | None,
        error: str | None,
    ) -> bool:
        latest_result = (
            f"HTTP {status_code}" if status_code is not None else (error or "No response")
        )
        message = (
            f"OUTAGE: {_safe_text(monitor_name)}\n"
            f"URL: {_safe_text(url)}\n"
            f"Detected at (UTC): {_utc_isoformat(detected_at)}\n"
            f"Consecutive failures: {consecutive_failures}\n"
            f"Latest result: {_safe_text(latest_result)}"
        )
        return await self._send(message)

    async def send_recovery(
        self,
        *,
        monitor_name: str,
        url: str,
        recovered_at: datetime,
        outage_duration_seconds: float | None,
    ) -> bool:
        message = (
            f"RECOVERY: {_safe_text(monitor_name)}\n"
            f"URL: {_safe_text(url)}\n"
            f"Recovered at (UTC): {_utc_isoformat(recovered_at)}"
        )
        if outage_duration_seconds is not None and outage_duration_seconds >= 0:
            message += f"\nPrevious outage duration: {outage_duration_seconds:.0f} seconds"
        return await self._send(message)

    async def _send(self, message: str) -> bool:
        if not self.webhook_url:
            if not self._missing_webhook_logged:
                logger.warning(
                    "Slack alerts are disabled because SLACK_WEBHOOK_URL is not configured."
                )
                self._missing_webhook_logged = True
            return False

        payload = {
            "text": message,
            "mrkdwn": False,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(self.webhook_url, json=payload)
                except httpx.TimeoutException as exc:
                    if attempt < self.max_retries:
                        await self.sleep(self._backoff(attempt))
                        continue
                    logger.error(
                        "Slack alert delivery timed out after %s attempts (%s).",
                        attempt + 1,
                        type(exc).__name__,
                    )
                    return False
                except httpx.TransportError as exc:
                    if attempt < self.max_retries:
                        await self.sleep(self._backoff(attempt))
                        continue
                    logger.error(
                        "Slack alert delivery failed after %s attempts (%s).",
                        attempt + 1,
                        type(exc).__name__,
                    )
                    return False

                if 200 <= response.status_code < 300:
                    return True

                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt >= self.max_retries:
                    logger.error(
                        "Slack alert delivery failed with HTTP status %s after %s attempts.",
                        response.status_code,
                        attempt + 1,
                    )
                    return False

                retry_after = self._retry_after(response)
                await self.sleep(
                    retry_after if retry_after is not None else self._backoff(attempt)
                )

        return False

    def _backoff(self, attempt: int) -> float:
        return min(
            self.backoff_seconds * (2**attempt),
            self.max_retry_after_seconds,
        )

    def _retry_after(self, response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            delay = float(value)
        except ValueError:
            try:
                retry_time = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_time.tzinfo is None:
                retry_time = retry_time.replace(tzinfo=UTC)
            delay = (retry_time - datetime.now(UTC)).total_seconds()
        if not math.isfinite(delay):
            return None
        return min(max(0.0, delay), self.max_retry_after_seconds)


slack_alert_service = SlackAlertService()
