"""Orders, barista queue, ready board, and guest status router (Modules 1, 2, 3, 4)."""

import datetime
import time
import uuid
from zoneinfo import ZoneInfo
from typing import List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from fastapi.concurrency import run_in_threadpool
from sqlmodel import col, func, select

from .. import estimate
from ..auth import RequireBarista, RequireBoothLead, RequireCompletedAccess
from .. import database
from ..database import SessionDep
from ..models import (AuditLog, MenuItem, MenuItemOptions, Order, OrderItem, OrderRevision, ClosedDay, PickupSettings,
                      EstimateSettings, EstimateFormat, OrderClosure)
from ..schemas import (
    CancelledOrderResponse,
    GuestDrink,
    GuestStatusResponse,
    OrderCreate,
    OrderItemResponse,
    OrderResponse,
    OrderStatusUpdate,
    PickupLookupResponse,
    ReopenOrderRequest,
    WaitEstimateResponse,
)
from .events import broadcaster, guest_tag

router = APIRouter(prefix="/api/orders", tags=["orders"])

STATUS_RANKS = {
    "received": 1,
    "making": 2,
    "ready": 3,
    "collected": 4,
    # Final like Collected: the first final status wins.
    "discarded": 4,
    "cancelled": 4,
}
FINAL_STATUSES = ("collected", "discarded", "cancelled")
UNDO_SECONDS = 120  # Undo after a collect-on-scan (the tablet offers it for 10 s)


class UndoCollectRequest(BaseModel):
    status_revision: int = Field(ge=0)


def validate_order_items(session, items) -> None:
    """Reject drinks that cannot exist; the captured price stays as the POS billed it."""
    if not items:
        raise HTTPException(400, "Choose at least one item")
    for item in items:
        menu_item = session.exec(select(MenuItem).where(MenuItem.name == item.item_name)).first()
        if not menu_item:
            raise HTTPException(400, f"Unknown menu item '{item.item_name}'")
        options = session.get(MenuItemOptions, menu_item.id)
        takes_milk = options.takes_milk if options else True
        if not takes_milk and item.milk not in (None, "none"):
            raise HTTPException(400, f"{menu_item.name} is served without milk")


def _get_order_items(session: SessionDep, order_id: str) -> List[OrderItemResponse]:
    items = session.exec(
        select(OrderItem).where(OrderItem.order_id == order_id)
    ).all()
    return [
        OrderItemResponse(
            id=item.id,
            item_name=item.item_name,
            unit_price_cents=item.unit_price_cents,
            milk=item.milk,
            temperature=item.temperature,
            quantity=item.quantity,
        )
        for item in items
    ]


def _format_order_response(order: Order, items: List[OrderItemResponse], session=None) -> OrderResponse:
    revision = session.get(OrderRevision, order.id) if session is not None else None
    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        device_id=order.device_id,
        first_name=order.first_name,
        status=order.status,
        status_revision=revision.value if revision else 0,
        total_price_cents=order.total_price_cents,
        event_day=order.event_day,
        created_at=order.created_at,
        making_at=order.making_at,
        ready_at=order.ready_at,
        collected_at=order.collected_at,
        items=items,
    )


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_in: OrderCreate,
    session: SessionDep,
    user: RequireBarista,
) -> OrderResponse:
    # 1. Idempotency Check (ensures zero duplicate orders during offline replay)
    existing_order = session.exec(
        select(Order).where(Order.idempotency_key == order_in.idempotency_key)
    ).first()
    if existing_order:
        items = _get_order_items(session, existing_order.id)
        return _format_order_response(existing_order, items, session)

    # 2. Validate First Name (PDPA required)
    clean_name = order_in.first_name.strip()
    if not clean_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Guest first name is required",
        )

    # 3. Check Items and Sold-Out Status
    validate_order_items(session, order_in.items)
    for item_data in order_in.items:
        menu_item = session.exec(
            select(MenuItem).where(MenuItem.name == item_data.item_name)
        ).first()
        if menu_item and menu_item.is_sold_out:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Item '{item_data.item_name}' is currently sold out",
            )

    # 4. Determine Event Day and Order Number
    today_str = (
        order_in.event_day
        if order_in.event_day
        else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    )
    # Tablet prefix: extract first letter from device_id e.g. "tablet_A" -> "A", default "A"
    prefix = (
        order_in.device_id.replace("tablet_", "")[0].upper()
        if order_in.device_id
        else "A"
    )

    # Count today's orders for this prefix to create sequential numbers: A001, A002...
    count = session.exec(
        select(func.count(Order.id))
        .where(Order.event_day == today_str)
        .where(col(Order.order_number).startswith(prefix))
    ).one()
    seq = count + 1
    order_number = f"{prefix}{seq:03d}"

    order_id = order_in.id if order_in.id else str(uuid.uuid4())
    total_cents = sum(item.unit_price_cents * item.quantity for item in order_in.items)

    order = Order(
        id=order_id,
        idempotency_key=order_in.idempotency_key,
        order_number=order_number,
        device_id=order_in.device_id,
        first_name=clean_name,
        status="received",
        total_price_cents=total_cents,
        event_day=today_str,
    )
    session.add(order)

    item_responses = []
    for item_data in order_in.items:
        order_item = OrderItem(
            order_id=order.id,
            item_name=item_data.item_name,
            unit_price_cents=item_data.unit_price_cents,
            milk=item_data.milk,
            temperature=item_data.temperature,
            quantity=item_data.quantity,
        )
        session.add(order_item)
        item_responses.append(
            OrderItemResponse(
                item_name=order_item.item_name,
                unit_price_cents=order_item.unit_price_cents,
                milk=order_item.milk,
                temperature=order_item.temperature,
                quantity=order_item.quantity,
            )
        )

    # Audit Trail (Staff username + Order #; NO guest name per PDPA)
    audit = AuditLog(
        id=str(uuid.uuid4()),
        staff_username=user.username,
        action="CREATE_ORDER",
        order_number=order.order_number,
        details=f"Created order with {len(order_in.items)} item(s), total ${total_cents / 100:.2f}",
    )
    session.add(audit)
    session.commit()
    session.refresh(order)

    # Broadcast SSE event
    await broadcaster.publish(
        "order_created",
        {
            "id": order.id,
            "order_number": order.order_number,
            "first_name": order.first_name,
            "status": order.status,
        },
        order_id=order.id,
    )

    return _format_order_response(order, item_responses, session)


@router.get("/active", response_model=List[OrderResponse])
def get_active_orders(
    session: SessionDep,
    user: RequireBarista,
) -> List[OrderResponse]:
    """Chronological queue of active tickets (oldest first)."""
    orders = session.exec(
        select(Order)
        .where(col(Order.status).in_(["received", "making", "ready"]))
        .order_by(Order.created_at)
    ).all()

    result = []
    for o in orders:
        items = _get_order_items(session, o.id)
        result.append(_format_order_response(o, items, session))
    return result


@router.get("/wait-estimate", response_model=WaitEstimateResponse)
def get_wait_estimate(session: SessionDep, user: RequireBarista) -> WaitEstimateResponse:
    """Today's usual gap between Ready taps and the estimate for a new order."""
    from .sync import today
    day = today()
    stats = estimate.day_stats(session, day)
    waiting = estimate.waiting_count(session, day)
    low, high = estimate.minutes(waiting, stats)
    return WaitEstimateResponse(event_day=day, low_s=stats.low_s, high_s=stats.high_s,
                                measured_gaps=stats.measured_gaps, average_wait_min=stats.average_wait_min,
                                waiting=waiting, new_order_low_min=low, new_order_high_min=high)


def _board_estimate(session, day: str) -> dict | None:
    if not (session.get(EstimateSettings, 1) or EstimateSettings()).on_board:
        return None
    low, high = estimate.minutes(estimate.waiting_count(session, day), estimate.day_stats(session, day))
    return {"low_min": low, "high_min": high, "display": (session.get(EstimateFormat, 1) or EstimateFormat()).display}


@router.get("/ready-board")
def get_ready_board(session: SessionDep):
    """Public board showing Ready and Making orders."""
    from ..board import charity_counter, ready_board_position
    from .sync import today

    ready_orders = session.exec(
        select(Order)
        .where(Order.status == "ready")
        .order_by(Order.ready_at.desc())
    ).all()
    making_orders = session.exec(
        select(Order)
        .where(Order.status == "making")
        .order_by(Order.making_at.desc())
    ).all()

    return {
        "ready_board_position": ready_board_position(session),
        # One request per check; the page purges names of closed days from this too.
        "closed_days": _closed_days(session),
        # Cups (and sales, if chosen) for today or all event days; None when off.
        "charity_counter": charity_counter(session, today()),
        # "Order now: ready in about X–Y min"; None when switched off for the board.
        "estimate": _board_estimate(session, today()),
        "ready": [
            {"order_number": o.order_number, "first_name": o.first_name}
            for o in ready_orders
        ],
        "making": [
            {"order_number": o.order_number, "first_name": o.first_name}
            for o in making_orders
        ],
    }


@router.get("/completed", response_model=List[OrderResponse])
def get_completed_orders(session: SessionDep, user: RequireCompletedAccess, event_day: str | None = None):
    """Staff-only completed tickets; names follow the existing day-reset policy."""
    day = event_day or datetime.datetime.now(ZoneInfo("Asia/Singapore")).date().isoformat()
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Choose a valid event day")
    orders = session.exec(select(Order).where(Order.event_day == day, Order.status == "collected")
                          .order_by(Order.collected_at.desc(), Order.created_at.desc())).all()
    return [_format_order_response(order, _get_order_items(session, order.id), session) for order in orders]


@router.get("/cancelled", response_model=List[CancelledOrderResponse])
def get_cancelled_orders(session: SessionDep, user: RequireCompletedAccess, event_day: str | None = None):
    """Queue's Cancelled tab: the day's cancelled orders with reason, money, who and when."""
    day = event_day or datetime.datetime.now(ZoneInfo("Asia/Singapore")).date().isoformat()
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Choose a valid event day")
    rows = session.exec(select(Order, OrderClosure).join(OrderClosure, OrderClosure.order_id == Order.id)
                        .where(Order.event_day == day, Order.status == "cancelled", OrderClosure.kind == "cancelled")
                        .order_by(OrderClosure.closed_at.desc())).all()
    return [CancelledOrderResponse(**_format_order_response(order, _get_order_items(session, order.id), session).model_dump(),
                                   reason=closure.reason, money=closure.money, cancelled_by=closure.staff_username,
                                   cancelled_at=closure.closed_at) for order, closure in rows]


@router.post("/{order_id}/reopen", response_model=OrderResponse)
async def reopen_order(order_id: str, request: ReopenOrderRequest,
                       session: SessionDep, user: RequireBoothLead):
    """Explicit, audited correction; normal status updates remain forward-only."""
    from .sync import lock_counter
    if not request.confirm:
        raise HTTPException(400, "Confirm that this collection was a mistake")
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    lock_counter(session, f"day:{order.event_day}")
    session.refresh(order, with_for_update=True)
    if session.get(ClosedDay, order.event_day):
        raise HTTPException(409, "A closed event day cannot be reopened")
    revision = session.get(OrderRevision, order.id) or OrderRevision(id=order.id)
    if order.status != "collected" or request.status_revision != revision.value:
        raise HTTPException(409, "This order has changed. Refresh the queue before correcting it.")
    revision.value += 1
    previous_collection = order.collected_at.isoformat() if order.collected_at else "not recorded"
    order.status = "ready"
    # Ready again now, so it returns to the top of the board's Ready list.
    order.ready_at = datetime.datetime.now(datetime.timezone.utc)
    order.collected_at = None
    session.add(order)
    session.add(revision)
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username=user.username, action="COLLECTION_CORRECTED",
                         order_number=order.order_number,
                         details=f"Collected -> ready; reason={request.reason}; previous collection={previous_collection}; revision={revision.value}"))
    session.commit()
    session.refresh(order)
    await broadcaster.publish("order_corrected", {}, order_id=order.id)
    return _format_order_response(order, _get_order_items(session, order.id), session)


@router.post("/{order_id}/undo-collect", response_model=OrderResponse)
async def undo_collect(order_id: str, request: UndoCollectRequest, session: SessionDep, user: RequireBarista):
    """Any staff member undoes a collection made seconds ago (a mistaken scan); audited."""
    from .sync import lock_counter
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    lock_counter(session, f"day:{order.event_day}")
    session.refresh(order, with_for_update=True)
    if session.get(ClosedDay, order.event_day):
        raise HTTPException(409, "A closed event day cannot be changed")
    revision = session.get(OrderRevision, order.id) or OrderRevision(id=order.id)
    if order.status != "collected" or request.status_revision != revision.value:
        raise HTTPException(409, "This order has changed. Refresh the queue before undoing.")
    collected = order.collected_at
    if collected and collected.tzinfo is None:
        collected = collected.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    if not collected or (now - collected).total_seconds() > UNDO_SECONDS:
        raise HTTPException(409, "Too late to undo. Ask a Booth Lead to reopen it.")
    revision.value += 1
    order.status = "ready"
    order.collected_at = None
    session.add(order)
    session.add(revision)
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username=user.username, action="COLLECTION_UNDONE",
                         order_number=order.order_number,
                         details=f"Collected -> ready (Undo after scan); revision={revision.value}"))
    session.commit()
    session.refresh(order)
    await broadcaster.publish("order_corrected", {}, order_id=order.id)
    return _format_order_response(order, _get_order_items(session, order.id), session)


@router.get("/{order_id}/pickup", response_model=PickupLookupResponse)
def lookup_pickup(order_id: str, session: SessionDep, user: RequireBarista) -> PickupLookupResponse:
    """Staff lookup for a scanned pickup QR when the order is not on the tablet."""
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not a Beans&Beats order")
    return PickupLookupResponse(
        order=_format_order_response(order, _get_order_items(session, order.id), session),
        day_closed=session.get(ClosedDay, order.event_day) is not None,
    )


def _guest_drink(item: OrderItem) -> GuestDrink:
    """Plain words for guests, e.g. "2 × Latte" then "Iced · Oat milk"."""
    name = item.item_name if item.quantity == 1 else f"{item.quantity} × {item.item_name}"
    details = []
    if item.temperature:
        details.append(item.temperature.capitalize())
    if item.milk and item.milk.lower() != "none":
        details.append(f"{item.milk.capitalize()} milk")
    return GuestDrink(name=name, details=" · ".join(details))


def _closed_days(session) -> list[str]:
    return [day for day in session.exec(select(ClosedDay.day)).all()]


# Guest pages are the server's heaviest traffic (300 phones). Their data is cached in memory and
# dropped when a change is published: per order (its tag) for the order itself, and per change for the
# figures every page shares. Every write publishes after it commits; 15 s is a safety limit.
CACHE_SECONDS = 15
_shared_cache: dict[str, tuple] = {}
_order_cache: dict[str, tuple] = {}


def reset_guest_cache() -> None:
    _shared_cache.clear()
    _order_cache.clear()


def _fresh(hit, key) -> bool:
    return bool(hit) and hit[0] == key and time.monotonic() - hit[1] < CACHE_SECONDS


def _cached(order_id: str) -> bool:
    """True when the order and its day's shared figures are both cached and fresh."""
    hit = _order_cache.get(order_id)
    if not _fresh(hit, (broadcaster.order_versions.get(order_id, 0), broadcaster.reset_epoch)):
        return False
    return _fresh(_shared_cache.get(hit[2]["event_day"]), (broadcaster.version, broadcaster.reset_epoch))


def _shared_figures(session, event_day: str, peek: bool = False) -> dict | None:
    key = (broadcaster.version, broadcaster.reset_epoch)  # read before the database, so a race only re-reads
    now = time.monotonic()
    hit = _shared_cache.get(event_day)
    if _fresh(hit, key):
        return hit[2]
    if peek:
        return None
    pickup = session.get(PickupSettings, 1) or PickupSettings()
    waiting = session.exec(select(Order.id, Order.created_at).where(
        Order.event_day == event_day, col(Order.status).in_(estimate.WAITING))).all()
    value = {
        "warn_minutes": pickup.warn_minutes if pickup.reminders else None,
        "discard_minutes": pickup.discard_minutes if pickup.reminders else None,
        "closed_days": _closed_days(session),
        "stats": estimate.day_stats(session, event_day),
        "show_estimate": (session.get(EstimateSettings, 1) or EstimateSettings()).on_guest,
        "estimate_display": (session.get(EstimateFormat, 1) or EstimateFormat()).display,
        "waiting": [(order_id, estimate.utc(created)) for order_id, created in waiting],
    }
    if len(_shared_cache) > 8:
        _shared_cache.clear()
    _shared_cache[event_day] = (key, now, value)
    return value


def _order_snapshot(session, order_id: str, peek: bool = False) -> dict | None:
    key = (broadcaster.order_versions.get(order_id, 0), broadcaster.reset_epoch)
    now = time.monotonic()
    hit = _order_cache.get(order_id)
    if _fresh(hit, key):
        return hit[2]
    if peek:
        return None
    order = session.get(Order, order_id)
    if not order:
        return None
    items = session.exec(select(OrderItem).where(OrderItem.order_id == order.id)).all()
    value = {
        "id": order.id, "order_number": order.order_number, "first_name": order.first_name,
        "status": order.status, "created_at": order.created_at, "ready_at": order.ready_at,
        "event_day": order.event_day, "items": [_guest_drink(i) for i in items], "tag": guest_tag(order.id),
    }
    if len(_order_cache) > 5000:
        _order_cache.clear()
    _order_cache[order_id] = (key, now, value)
    return value


def _guest_parts(order_id: str) -> tuple[dict | None, dict | None]:
    """Cache hits need no database; a miss opens one session in a worker thread."""
    order = _order_snapshot(None, order_id, peek=True)
    shared = _shared_figures(None, order["event_day"], peek=True) if order else None
    if order and shared:
        return order, shared
    with database.new_session() as session:
        order = _order_snapshot(session, order_id)
        return order, (_shared_figures(session, order["event_day"]) if order else None)


@router.get("/{order_id}/status", response_model=GuestStatusResponse)
async def get_guest_status(order_id: str) -> GuestStatusResponse:
    """Public guest endpoint accessed via unguessable UUID."""
    order, shared = _guest_parts(order_id) if _cached(order_id) else await run_in_threadpool(_guest_parts, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    lines = order["items"]
    items_summary = [f"{line.name} · {line.details}" if line.details else line.name for line in lines]
    stats = shared["stats"]
    ahead = None
    shown = shared["show_estimate"]  # Booth Lead or Admin can switch it off on the guest's phone
    if shown and order["status"] in estimate.WAITING:
        created = estimate.utc(order["created_at"])
        ahead = sum(1 for other, when in shared["waiting"] if other != order["id"] and when < created)
    low, high = estimate.minutes(ahead, stats) if ahead is not None else (None, None)

    return GuestStatusResponse(
        id=order["id"],
        order_number=order["order_number"],
        first_name=order["first_name"],
        status=order["status"],
        created_at=order["created_at"],
        ready_at=order["ready_at"],
        items_summary=items_summary,
        items=lines,
        warn_minutes=shared["warn_minutes"],
        discard_minutes=shared["discard_minutes"],
        closed_days=shared["closed_days"],
        event_tag=order["tag"],
        estimate_low_min=low,
        estimate_display=shared["estimate_display"],
        estimate_high_min=high,
        orders_ahead=ahead,
        average_wait_min=stats.average_wait_min if shown else None,
    )


@router.patch("/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: str,
    status_in: OrderStatusUpdate,
    session: SessionDep,
    user: RequireBarista,
) -> OrderResponse:
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    target_status = status_in.status.lower()
    from .sync import lock_counter
    lock_counter(session, f"day:{order.event_day}")
    session.refresh(order, with_for_update=True)
    revision = session.get(OrderRevision, order.id)
    if status_in.status_revision != (revision.value if revision else 0):
        raise HTTPException(409, "This order was corrected. Refresh it before changing its status.")
    if target_status not in STATUS_RANKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status '{target_status}'. Must be one of {list(STATUS_RANKS.keys())}",
        )

    if target_status in ("discarded", "cancelled"):
        # These need a reason and go through the tablet action.
        raise HTTPException(400, "Cancel or discard the order from the queue")

    # Strictly monotonic progression: cannot move backwards!
    current_rank = STATUS_RANKS.get(order.status, 0)
    target_rank = STATUS_RANKS[target_status]
    if target_rank < current_rank:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot move status backwards from '{order.status}' to '{target_status}'",
        )

    # Only a Ready drink can be handed over.
    if target_status == "collected" and order.status in ("received", "making"):
        raise HTTPException(409, f"Order {order.order_number} is not ready yet and cannot be collected")

    changed = target_rank > current_rank
    now = datetime.datetime.now(datetime.timezone.utc)
    if target_status == "making" and not order.making_at:
        order.making_at = now
    elif target_status == "ready" and not order.ready_at:
        order.ready_at = now
    elif target_status == "collected" and not order.collected_at:
        order.collected_at = now

    order.status = target_status
    session.add(order)

    # Audit Trail
    audit = AuditLog(
        id=str(uuid.uuid4()),
        staff_username=user.username,
        action="STATUS_CHANGE" if changed else "STATUS_UNCHANGED",
        order_number=order.order_number,
        details=f"Status updated to '{target_status}'" if changed else f"Requested '{target_status}'; already '{order.status}', no change",
    )
    session.add(audit)
    session.commit()
    session.refresh(order)

    # Broadcast SSE update
    await broadcaster.publish(
        "order_status_updated",
        {
            "id": order.id,
            "order_number": order.order_number,
            "first_name": order.first_name,
            "status": order.status,
        },        order_id=order.id,
    )

    items = _get_order_items(session, order.id)
    return _format_order_response(order, items, session)

