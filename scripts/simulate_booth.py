"""Booth load simulator.

Runs a busy booth through the real system, the way the tablets do (POST /api/sync):
- orders arrive at random (Poisson) at --cups-per-hour (default 200) for --minutes (default 5);
- each order has 1-4 cups: 1 cup 50%, 2 cups 30%, 3 cups 12%, 4 cups 8%; random drink, milk and hot/iced;
- --baristas (default 2) make the orders in turn: Making, then Ready after 45-90 s per cup;
- each guest collects 1-5 minutes after Ready.
--speed 5 runs five times faster (for the demo video). Orders count in daily sales, so the live server is
refused unless --live is given. Made-up first names only.

Usage (needs httpx):
  python scripts/simulate_booth.py http://127.0.0.1:8000 [--minutes 5] [--cups-per-hour 200] [--speed 1]
Set BB_SIM_USER (default `barista`) and BB_SIM_PASSWORD to a staff login on that server.
"""
import argparse
import asyncio
import datetime as dt
import os
import random
import statistics
import sys
import time
import uuid
from collections import Counter

import httpx

CUP_WEIGHTS = {1: 50, 2: 30, 3: 12, 4: 8}
NAMES = ["Aisha", "Ben", "Chloe", "Darren", "Elaine", "Farid", "Grace", "Hui Min", "Ivan", "Jia Hui", "Kumar",
         "Li Ting", "Marcus", "Nur", "Oliver", "Priya", "Qi Xuan", "Rachel", "Siti", "Tom", "Uma", "Victor",
         "Wei Jie", "Xin Yi", "Yusuf", "Zoe"]


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Booth:
    def __init__(self, client, base, args):
        self.client, self.base, self.args = client, base, args
        self.latencies, self.errors = [], []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.cups_per_order = Counter()
        self.placed = self.collected = 0
        self.tasks = []

    async def call(self, method, path, **kwargs):
        started = time.perf_counter()
        try:
            response = await self.client.request(method, f"{self.base}{path}", timeout=20, **kwargs)
        except httpx.HTTPError as error:
            self.errors.append(f"{path}: {type(error).__name__}")
            return None
        finally:
            self.latencies.append((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            self.errors.append(f"{path}: {response.status_code} {response.text[:120]}")
            return None
        return response.json()

    async def setup(self):
        config = (await self.client.get(f"{self.base}/api/config")).json()
        if not config.get("show_demo_logins", True) and not self.args.live:
            sys.exit("This is the live server: its orders would count in daily sales. Add --live to run anyway.")
        self.iced = config.get("iced_surcharge_cents", 100)
        self.oat = config.get("oat_surcharge_cents", 100)
        self.making_step = config.get("use_making_step", True)
        login = await self.client.post(f"{self.base}/api/auth/login", json={
            "username": os.environ.get("BB_SIM_USER", "barista"),
            "password": os.environ.get("BB_SIM_PASSWORD", "")})
        if login.status_code != 200:
            sys.exit(f"Sign-in failed ({login.status_code}). Set BB_SIM_USER and BB_SIM_PASSWORD.")
        self.client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        self.menu = [i for i in (await self.client.get(f"{self.base}/api/menu")).json() if not i["is_sold_out"]]
        sold = {m["name"] for m in (await self.client.get(f"{self.base}/api/sync/ingredients")).json() if m["is_sold_out"]}
        self.milks = [m for m in ("dairy", "oat") if m not in sold]
        if not self.menu:
            sys.exit("Every menu item is sold out.")
        self.device = str(uuid.uuid4())
        tablet = await self.call("POST", "/api/sync/tablets", json={"id": self.device})
        if not tablet:
            sys.exit(f"Could not register the simulator tablet: {self.errors[-1]}")
        self.day, self.sequence, self.prefix = tablet["event_day"], tablet["sequence"], tablet["prefix"]

    def drink(self):
        item = random.choice(self.menu)
        takes_milk = item.get("takes_milk", True) and item["category"] != "pastry"
        milk = (random.choices(self.milks, weights=[75, 25][:len(self.milks)])[0] if takes_milk and self.milks
                else None)
        temperature = None if item["category"] == "pastry" else random.choices(["hot", "iced"], [65, 35])[0]
        price = item["price_cents"] + (self.iced if temperature == "iced" else 0) + (self.oat if milk == "oat" else 0)
        return item["name"], milk, temperature, price

    async def action(self, kind, payload):
        return await self.call("POST", "/api/sync", json={
            "id": str(uuid.uuid4()), "device_id": self.device, "occurred_at": now_iso(), "kind": kind,
            "payload": payload})

    async def place(self):
        cups = random.choices(list(CUP_WEIGHTS), weights=list(CUP_WEIGHTS.values()))[0]
        lines = Counter(self.drink() for _ in range(cups))
        self.sequence += 1
        order_id = str(uuid.uuid4())
        result = await self.action("order.create", {
            "id": order_id, "idempotency_key": order_id, "device_id": self.device, "first_name": random.choice(NAMES),
            "event_day": self.day, "sequence": self.sequence, "created_at": now_iso(),
            "order_number": f"{self.prefix}{self.sequence:03d}",
            "items": [{"item_name": name, "milk": milk, "temperature": temperature, "unit_price_cents": price,
                       "quantity": quantity} for (name, milk, temperature, price), quantity in lines.items()]})
        if result:
            self.placed += 1
            self.cups_per_order[cups] += 1
            await self.queue.put((result["order"], cups))

    async def barista(self):
        while True:
            order, cups = await self.queue.get()
            if self.making_step:
                await self.action("order.status", {"id": order["id"], "status": "making"})
            await asyncio.sleep(sum(random.uniform(45, 90) for _ in range(cups)) / self.args.speed)
            await self.action("order.status", {"id": order["id"], "status": "ready"})
            self.tasks.append(asyncio.create_task(self.guest_collects(order)))
            self.queue.task_done()

    async def guest_collects(self, order):
        await asyncio.sleep(random.uniform(60, 300) / self.args.speed)
        if await self.action("order.status", {"id": order["id"], "status": "collected"}):
            self.collected += 1

    async def run(self):
        await self.setup()
        mean_cups = sum(k * w for k, w in CUP_WEIGHTS.items()) / sum(CUP_WEIGHTS.values())
        orders_per_second = self.args.cups_per_hour / 3600 / mean_cups
        baristas = [asyncio.create_task(self.barista()) for _ in range(self.args.baristas)]
        end = time.monotonic() + self.args.minutes * 60 / self.args.speed
        print(f"Simulating {self.args.cups_per_hour} cups/hour for {self.args.minutes} min "
              f"(x{self.args.speed} speed) as tablet {self.prefix} on {self.base} ...")
        while True:
            await asyncio.sleep(random.expovariate(orders_per_second) / self.args.speed)
            if time.monotonic() >= end:
                break
            await self.place()
        await self.queue.join()  # the baristas finish what was ordered
        await asyncio.gather(*self.tasks)
        for task in baristas:
            task.cancel()
        figures = await self.call("GET", "/api/orders/wait-estimate")
        cups = sum(k * n for k, n in self.cups_per_order.items())
        print(f"orders={self.placed} cups={cups} collected={self.collected} errors={len(self.errors)}")
        print("cups per order:", dict(sorted(self.cups_per_order.items())))
        if self.latencies:
            ordered = sorted(self.latencies)
            print(f"API p50={statistics.median(ordered):.0f} ms  p95={ordered[int(0.95 * (len(ordered) - 1))]:.0f} ms")
        if figures:
            print(f"estimate for a new order now: {figures['new_order_low_min']}-{figures['new_order_high_min']} min; "
                  f"average wait today: {figures['average_wait_min']} min; gaps measured: {figures['measured_gaps']}")
        for error in self.errors[:10]:
            print("error:", error)
        return not self.errors


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--cups-per-hour", type=float, default=200)
    parser.add_argument("--baristas", type=int, default=2)
    parser.add_argument("--speed", type=float, default=1, help="run this many times faster (demo)")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--live", action="store_true", help="allow the live server (orders count in daily sales)")
    args = parser.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    async with httpx.AsyncClient(headers={"User-Agent": "beansbeats-booth-simulator"}) as client:
        ok = await Booth(client, args.base_url.rstrip("/"), args).run()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
