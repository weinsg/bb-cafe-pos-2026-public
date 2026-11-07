"""Server-Sent Events (SSE) broadcaster for real-time order updates and ready alerts."""

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any, Dict, Set

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

router = APIRouter(prefix="/api/events", tags=["events"])


def guest_tag(order_id: str) -> str:
    """A short keyed tag of one order, so only that guest's page refetches on its change.

    The tag cannot be turned back into the order ID (keyed with the signing key), so the public
    stream still never carries a guest's tracking ID or name.
    """
    from ..auth import SECRET_KEY
    return hmac.new(SECRET_KEY.encode(), order_id.encode(), hashlib.sha256).hexdigest()[:16]


class EventBroadcaster:
    def __init__(self) -> None:
        self.subscribers: Set[asyncio.Queue] = set()
        # Every change publishes, so these counters tell the guest-page cache what is stale.
        self.version = 0  # any change
        self.order_versions: Dict[str, int] = {}  # changes to one order
        self.reset_epoch = 0  # a daily reset (changes many orders at once)

    async def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    async def publish(self, event_type: str, data: Dict[str, Any], order_id: str | None = None) -> None:
        self.version += 1
        if order_id:
            if len(self.order_versions) > 20000:
                self.order_versions.clear()
                self.reset_epoch += 1
            self.order_versions[order_id] = self.order_versions.get(order_id, 0) + 1
        if event_type == "daily_reset":
            self.reset_epoch += 1
        # The stream is public. Clients refetch their authorized/public resource.
        # Never expose every guest's random tracking ID or first name here.
        data = {"closed_event_day": data["closed_event_day"]} if event_type == "daily_reset" else {}
        if order_id:
            data["guest"] = guest_tag(order_id)
        payload = json.dumps({"type": event_type, "data": data})
        message = f"data: {payload}\n\n"
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self.subscribers.discard(queue)


broadcaster = EventBroadcaster()


async def event_generator(
    request: Request, queue: asyncio.Queue
) -> AsyncIterator[str]:
    try:
        # Initial greeting event
        yield "data: {\"type\": \"connected\"}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                # Wait for up to 15 seconds for a new event or send heartbeat
                message = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield message
            except asyncio.TimeoutError:
                # Keepalive ping
                yield ": ping\n\n"
    finally:
        broadcaster.unsubscribe(queue)


@router.get("", response_class=StreamingResponse)
async def subscribe_events(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for ready boards, barista tablets, and guest phones."""
    queue = await broadcaster.subscribe()
    return StreamingResponse(
        event_generator(request, queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

