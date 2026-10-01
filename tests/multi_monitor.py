import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.monitoring.checker import check_url


async def monitor_urls(cases):
    """
    Start all URL checks at the same time, wait for all results, and print them.
    """
    print("Starting concurrent URL checks...\n")

    results = await asyncio.gather(*[check_url(url) for _, url in cases])

    for (label, url), result in zip(cases, results):
        print("-" * 60)
        print(f"Case: {label}")
        print(f"URL: {url}")
        print(f"HTTP status: {result['status_code']}")
        print(f"Latency: {result['latency_ms']} ms")
        print(f"Healthy: {result['is_healthy']}")
        print(f"Error: {result['error']}")

    print("\nAll monitoring checks finished.")
    return results


async def main():
    cases = [
        ("Successful response", "https://example.com"),
        ("200 response", "https://httpbin.org/status/200"),
        ("500 response", "https://httpbin.org/status/500"),
        ("Invalid domain", "https://nonexistent-example-domain.invalid"),
        ("Timeout", "https://httpbin.org/delay/10"),
    ]

    await monitor_urls(cases)


if __name__ == "__main__":
    asyncio.run(main())
