import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.monitoring.checker import check_url


async def show_case(label: str, url: str, timeout: float = 5.0):
    result = await check_url(url, timeout=timeout)
    print(f"\n=== {label} ===")
    print(f"URL: {result['url']}")
    print(f"Status code: {result['status_code']}")
    print(f"Latency (ms): {result['latency_ms']}")
    print(f"Healthy: {result['is_healthy']}")
    print(f"Error: {result['error']}")


async def main():
    await show_case("1. Successful URL", "https://example.com")
    await show_case("2. 500 response", "https://httpbin.org/status/500")
    await show_case("3. Invalid domain", "https://nonexistent-example-domain.invalid")
    await show_case("4. Timeout", "https://httpbin.org/delay/10", timeout=1.0)


if __name__ == "__main__":
    asyncio.run(main())
