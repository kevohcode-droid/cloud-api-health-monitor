import time
import httpx


async def check_url(url: str, timeout: float = 5.0) -> dict:
    """
    Asynchronously check a URL and return a health result dictionary.

    This function is designed to be safe in a monitoring loop: even if one URL
    fails or times out, it returns a normal result instead of crashing the entire
    app.
    """
    start = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            latency_ms = round((time.perf_counter() - start) * 1000, 2)

            return {
                "url": url,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "is_healthy": 200 <= response.status_code < 400,
                "error": None,
            }

    except httpx.TimeoutException:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "url": url,
            "status_code": None,
            "latency_ms": latency_ms,
            "is_healthy": False,
            "error": "timeout",
        }

    except httpx.RequestError as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "url": url,
            "status_code": None,
            "latency_ms": latency_ms,
            "is_healthy": False,
            "error": str(exc),
        }

    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "url": url,
            "status_code": None,
            "latency_ms": latency_ms,
            "is_healthy": False,
            "error": f"unexpected_error: {exc}",
        }
