"""Guest-page load test: N guest pages open at once, each checking like the real page.

Each simulated page keeps one live-update stream (/api/events) open and checks its order like the guest
page does since the 7 Nov load fix: one request (/api/orders/<id>/status) every 20 s while the stream is
connected (--every 20, the default). --every 5 checks every 5 s, as a page does when live push drops.
--legacy makes the old page's two requests (/api/sync/state too). Reports p50/p95/p99 and errors.

Usage (run the client in Docker, not WSL: WSL networking caps the client; needs httpx):
  python scripts/load_guest_pages.py https://<host>/guest/<order id> --pages 300 --minutes 10 [--every 5]
Point it at a disposable stack, or at the live server only when no event is running.
"""
import argparse
import asyncio
import random
import statistics
import time
from urllib.parse import urlparse

import httpx


async def stream(client, base, stop):
    while not stop.is_set():
        try:
            async with client.stream("GET", f"{base}/api/events", timeout=None) as response:
                async for _ in response.aiter_lines():
                    if stop.is_set():
                        return
        except (httpx.HTTPError, asyncio.CancelledError):
            await asyncio.sleep(1)


async def page(client, base, order_id, stop, latencies, errors, every, legacy):
    await asyncio.sleep(random.uniform(0, every))  # pages open at different moments
    paths = (["/api/sync/state"] if legacy else []) + [f"/api/orders/{order_id}/status"]
    while not stop.is_set():
        for path in paths:
            started = time.perf_counter()
            try:
                response = await client.get(f"{base}{path}", timeout=10)
                if response.status_code != 200:
                    errors.append(response.status_code)
            except httpx.HTTPError as error:
                errors.append(type(error).__name__)
            latencies.append((time.perf_counter() - started) * 1000)
        await asyncio.sleep(every)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("guest_url")
    parser.add_argument("--pages", type=int, default=300)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--every", type=float, default=20, help="seconds between checks (20 live, 5 push down)")
    parser.add_argument("--legacy", action="store_true", help="the old page: two requests per check")
    parser.add_argument("--no-stream", action="store_true", help="skip the live-update streams (diagnosis)")
    args = parser.parse_args()
    url = urlparse(args.guest_url)
    base, order_id = f"{url.scheme}://{url.netloc}", url.path.rstrip("/").split("/")[-1]
    latencies, errors, stop = [], [], asyncio.Event()
    limits = httpx.Limits(max_connections=args.pages * 2 + 20, max_keepalive_connections=args.pages * 2)
    async with httpx.AsyncClient(limits=limits, headers={"User-Agent": "beansbeats-load-test"}) as client:
        tasks = [] if args.no_stream else [asyncio.create_task(stream(client, base, stop)) for _ in range(args.pages)]
        tasks += [asyncio.create_task(page(client, base, order_id, stop, latencies, errors, args.every, args.legacy)) for _ in range(args.pages)]
        await asyncio.sleep(args.minutes * 60)
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    ordered = sorted(latencies)
    pick = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]
    print(f"pages={args.pages} minutes={args.minutes} every={args.every}s legacy={args.legacy} "
          f"requests={len(ordered)} errors={len(errors)}")
    if ordered:
        print(f"p50={pick(0.5):.0f} ms  p95={pick(0.95):.0f} ms  p99={pick(0.99):.0f} ms  "
              f"mean={statistics.mean(ordered):.0f} ms  max={ordered[-1]:.0f} ms")
    if errors:
        print("error kinds:", {kind: errors.count(kind) for kind in set(errors)})
    passed = ordered and not errors and pick(0.95) < 500
    print("", "PASS" if passed else "FAIL", "(p95 < 500 ms, no errors)")


if __name__ == "__main__":
    asyncio.run(main())
