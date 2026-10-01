import asyncio
from app.monitoring.checker import check_url

URLS = [
    "https://example.com",  # Should return 200 (Healthy)
    "https://httpbin.org/status/500",  # Should return 500 (Unhealthy)
    "https://httpbin.org/delay/6",  # Should trigger a timeout
]


async def main():
    print("Testing asynchronous health checks...\n")
    results = await asyncio.gather(*(check_url(url, timeout=3.0) for url in URLS))

    for result in results:
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
