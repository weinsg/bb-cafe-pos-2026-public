"""Durable device actions: a receipt and its business change commit together."""

from ..order_history import record_stage
import datetime as dt
import json
import uuid
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from ..auth import RequireBarista, can_cancel
from ..database import SessionDep
from ..models import (AuditLog, ClosedDay, Counter, Ingredient, MenuItem, MenuItemOptions,
                      MenuVersion, Order, OrderClosure, OrderItem, OrderRevision, Surcharge, SyncReceipt, Tablet)
from ..schemas import CANCEL_MONEY, CANCEL_REASONS, MAX_PRICE_CENTS, MenuItemCreate, MenuItemUpdate, OrderCreate
from .events import broadcaster
from .orders import FINAL_STATUSES, STATUS_RANKS, _format_order_response, _get_order_items, validate_order_items

router = APIRouter(prefix="/api/sync", tags=["sync"])


def today():
    return dt.datetime.now(ZoneInfo("Asia/Singapore")).date().isoformat()


def insert_once(session, model, values):
    dialect_insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    statement = dialect_insert(model).values(**values).on_conflict_do_nothing().returning(model.id)
    # psycopg does not reliably provide rowcount for this statement. RETURNING
    # gives the inserted key on both PostgreSQL and SQLite, or no row on replay.
    return session.exec(statement).first() is not None


def lock_counter(session, key):
    insert_once(session, Counter, {"id": key, "value": 0})
    return session.exec(select(Counter).where(Counter.id == key).with_for_update()).one()


class RegisterTablet(BaseModel):
    id: str = Field(min_length=1, max_length=100)


@router.post("/tablets")
def register_tablet(request: RegisterTablet, session: SessionDep, user: RequireBarista):
    counter = lock_counter(session, "tablet-registry")
    tablet = session.get(Tablet, request.id)
    if not tablet:
        used = {t.prefix for t in session.exec(select(Tablet)).all()}
        # Reserve prefixes used by the original API too.
        used.update(o.order_number.rstrip("0123456789") for o in session.exec(select(Order)).all())
        while True:
            n = counter.value
            prefix = ""
            while True:
                prefix = chr(65 + n % 26) + prefix
                n = n // 26 - 1
                if n < 0:
                    break
            counter.value += 1
            if prefix not in used:
                break
        tablet = Tablet(id=request.id, prefix=prefix)
        session.add(tablet)
        session.add(counter)
    session.commit()
    sequence = session.get(Counter, f"orders:{today()}:{tablet.id}")
    return {"id": tablet.id, "prefix": tablet.prefix, "sequence": sequence.value if sequence else 0, "event_day": today()}


@router.get("/state")
def sync_state(session: SessionDep):
    # Public dates let guest browsers purge cached names after a missed reset.
    return {"event_day": today(), "closed_days": [c.day for c in session.exec(select(ClosedDay)).all()]}


@router.get("/ingredients")
def ingredients(session: SessionDep):
    return [{"name": name, "is_sold_out": bool(row and row.is_sold_out)}
            for name in ("dairy", "oat", "none") for row in [session.get(Ingredient, name)]]


class DeviceAction(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    device_id: str = Field(min_length=1, max_length=100)
    occurred_at: dt.datetime
    kind: Literal["order.create", "order.status", "menu.create", "menu.update", "menu.remove", "ingredient.update",
                  "surcharge.update", "auth.logout"]
    payload: dict[str, Any]


def utc(value):
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


@router.post("")
async def sync_action(action: DeviceAction, session: SessionDep, user: RequireBarista):
    if action.kind.startswith(("menu.", "ingredient.", "surcharge.")) and user.role not in ("booth_lead", "admin"):
        raise HTTPException(403, "Booth lead access required")
    if not session.get(Tablet, action.device_id):
        raise HTTPException(400, "Register this tablet online first")
    if utc(action.occurred_at) > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise HTTPException(400, "Tablet time is ahead of server time")
    claimed = insert_once(session, SyncReceipt, {
        "id": action.id, "staff_username": user.username, "device_id": action.device_id,
        "occurred_at": utc(action.occurred_at), "synced_at": dt.datetime.now(dt.timezone.utc), "result": "{}",
    })
    if not claimed:
        receipt = session.get(SyncReceipt, action.id)
        if receipt.staff_username != user.username or receipt.device_id != action.device_id:
            raise HTTPException(409, "Action belongs to another session")
        result = json.loads(receipt.result)
        order_id = result.pop("order_id", None)
        if order_id:
            order = session.get(Order, order_id)
            if order:
                result["order"] = _format_order_response(order, _get_order_items(session, order.id), session).model_dump(mode="json")
        return result
    payload = action.payload
    number = None
    result = {"applied": True}
    audit_action = action.kind.upper().replace(".", "_")
    details = "Staff signed out"
    try:
        if action.kind == "order.create":
            data = OrderCreate.model_validate(payload)
            if not data.items or not data.first_name.strip() or len(data.first_name.strip()) > 50:
                raise HTTPException(400, "Enter a first name and at least one item")
            day = data.event_day or today()
            dt.date.fromisoformat(day)
            # Serialize creation against a reset for the same day.
            lock_counter(session, f"day:{day}")
            if session.get(ClosedDay, day):
                result = {"applied": False, "closed_day": day}
                details = f"Discarded a delayed order for closed day {day}"
            else:
                tablet = session.get(Tablet, action.device_id)
                sequence = payload.get("sequence")
                if not isinstance(sequence, int) or sequence < 1:
                    raise HTTPException(400, "A positive tablet sequence is required")
                number = f"{tablet.prefix}{sequence:03d}"
                existing = session.exec(select(Order).where(Order.idempotency_key == data.idempotency_key)).first()
                if existing:
                    result["order"] = _format_order_response(existing, _get_order_items(session, existing.id), session).model_dump(mode="json")
                else:
                    duplicate = session.exec(select(Order).where(Order.event_day == day, Order.order_number == number)).first()
                    if duplicate:
                        raise HTTPException(409, "Order number is already used. Keep this action and contact the booth lead.")
                    validate_order_items(session, data.items)
                    counter = lock_counter(session, f"orders:{day}:{tablet.id}")
                    counter.value = max(counter.value, sequence)
                    session.add(counter)
                    order = Order(id=data.id or str(uuid.uuid4()), idempotency_key=data.idempotency_key,
                                  order_number=number, device_id=action.device_id, first_name=data.first_name.strip(),
                                  event_day=day, created_at=utc(action.occurred_at),
                                  total_price_cents=sum(i.unit_price_cents * i.quantity for i in data.items))
                    session.add(order)
                    session.flush()
                    record_stage(session, order, "received", user, action.id, utc(action.occurred_at), action.device_id)
                    for item in data.items:
                        session.add(OrderItem(order_id=order.id, **item.model_dump()))
                    session.flush()
                    result["order"] = _format_order_response(order, _get_order_items(session, order.id), session).model_dump(mode="json")
                audit_action = "CREATE_ORDER"
                details = f"Saved {len(data.items)} item(s) from tablet; prices captured at ordering"
        elif action.kind == "order.status":
            order = session.get(Order, payload.get("id"))
            if not order:
                raise HTTPException(404, "Order not found")
            lock_counter(session, f"day:{order.event_day}")
            session.refresh(order, with_for_update=True)
            number = order.order_number
            target = payload.get("status")
            if target not in STATUS_RANKS:
                raise HTTPException(400, "Invalid order status")
            revision = session.get(OrderRevision, order.id)
            current_revision = revision.value if revision else 0
            # A tap on a step already reached (e.g. by a second device) is recorded, not counted as a change.
            audit_action = "STATUS_UNCHANGED"
            if payload.get("status_revision", 0) != current_revision:
                result["applied"] = False
                details = f"Ignored status from an older order revision; current revision {current_revision}"
            elif target in ("discarded", "cancelled") and order.status in FINAL_STATUSES:
                # The first final status wins.
                details = f"Requested {target}; already {order.status}, no change"
            elif target in ("discarded", "cancelled"):
                if target == "discarded" and order.status != "ready":
                    raise HTTPException(409, f"Only a Ready drink can be discarded; {number} is {order.status}")
                if target == "cancelled" and not can_cancel(session, user):
                    raise HTTPException(403, "Your cancel right is switched off. Ask the Booth Lead.")
                reason, money = payload.get("reason"), payload.get("money")
                if target == "cancelled" and (reason not in CANCEL_REASONS or money not in CANCEL_MONEY):
                    raise HTTPException(400, "Choose a cancel reason and the money status")
                if target == "discarded":
                    reason, money = "hygiene", None
                previous = order.status
                order.status = target
                session.add(order)
                session.add(OrderClosure(order_id=order.id, kind=target, reason=reason, money=money,
                                         staff_username=user.username, closed_at=utc(action.occurred_at)))
                record_stage(session, order, target, user, action.id, utc(action.occurred_at), action.device_id)
                audit_action = "ORDER_CANCELLED" if target == "cancelled" else "ORDER_DISCARDED"
                details = (f"{previous} -> {target}; reason={reason}" + (f"; money={money}" if money else ""))
            elif target == "collected" and order.status in ("received", "making"):
                # Only a Ready drink can be handed over.
                raise HTTPException(409, f"Order {number} is not ready yet and cannot be collected")
            elif STATUS_RANKS[target] > STATUS_RANKS[order.status]:
                order.status = target
                record_stage(session, order, target, user, action.id, utc(action.occurred_at), action.device_id)
                setattr(order, f"{target}_at", utc(action.occurred_at))
                session.add(order)
                audit_action = "STATUS_CHANGE"
                details = f"Requested {target}; resulting status {order.status}"
            else:
                details = f"Requested {target}; already {order.status}, no change"
            result["order"] = _format_order_response(order, _get_order_items(session, order.id), session).model_dump(mode="json")
        elif action.kind.startswith(("menu.", "ingredient.", "surcharge.")):
            # Lock serializes concurrent edits. The device timestamp then action ID break ties.
            lock_counter(session, "menu-edits")
            if action.kind == "ingredient.update":
                name = payload.get("name")
                if name not in ("dairy", "oat", "none") or not isinstance(payload.get("is_sold_out"), bool):
                    raise HTTPException(400, "Invalid milk availability")
                key = f"ingredient:{name}"
            elif action.kind == "surcharge.update":
                name = payload.get("name")
                price = payload.get("price_cents")
                if name not in ("iced", "oat") or not isinstance(price, int) or not 0 < price <= MAX_PRICE_CENTS:
                    raise HTTPException(400, "Invalid add-on price")
                key = f"surcharge:{name}"
            else:
                key = f"item:{payload.get('id')}" if action.kind != "menu.create" else f"create:{action.id}"
            clock = session.get(MenuVersion, key)
            incoming = (utc(action.occurred_at), action.id)
            if clock and incoming <= (utc(clock.occurred_at), clock.action_id):
                result["applied"] = False
                details = "Older menu change ignored"
            else:
                if action.kind == "ingredient.update":
                    row = session.get(Ingredient, name) or Ingredient(name=name)
                    row.is_sold_out = payload["is_sold_out"]
                    session.add(row)
                    details = f"{name} milk {'sold out' if row.is_sold_out else 'back in stock'}"
                elif action.kind == "surcharge.update":
                    row = session.get(Surcharge, name) or Surcharge(name=name)
                    details = f"{name} add-on: {row.price_cents} -> {price} cents"
                    row.price_cents = price
                    session.add(row)
                elif action.kind == "menu.create":
                    data = MenuItemCreate.model_validate(payload)
                    if not data.name.strip() or data.category not in ("coffee", "non-coffee", "pastry"):
                        raise HTTPException(400, "Enter an item name and valid category")
                    item = session.exec(select(MenuItem).where(MenuItem.name == data.name.strip())).first()
                    item_clock = item and session.get(MenuVersion, f"item:{item.id}")
                    if item and not (item_clock and item_clock.removed):
                        raise HTTPException(409, "An item with this name already exists")
                    if item:
                        # The removed row is kept for history; adding the name again brings it back.
                        item_clock.removed = False
                        item_clock.occurred_at = utc(action.occurred_at)
                        item_clock.action_id = action.id
                        session.add(item_clock)
                        verb = "Re-added"
                    else:
                        item = MenuItem(name=data.name.strip(), category=data.category, price_cents=data.price_cents)
                        verb = "Added"
                    item.category, item.price_cents, item.is_sold_out = data.category, data.price_cents, data.is_sold_out
                    session.add(item)
                    session.flush()
                    options = session.get(MenuItemOptions, item.id) or MenuItemOptions(item_id=item.id)
                    options.takes_milk = data.takes_milk
                    session.add(options)
                    result["item_id"] = item.id
                    details = f"{verb} menu item {item.id}: {item.name}, {item.price_cents} cents, {'takes milk' if data.takes_milk else 'no milk'}"
                else:
                    item = session.get(MenuItem, payload.get("id"))
                    if not item:
                        raise HTTPException(404, "Menu item not found")
                    if action.kind == "menu.remove":
                        details = f"Removed menu item {item.id}: {item.name}"
                    else:
                        data = MenuItemUpdate.model_validate(payload)
                        changes = data.model_dump(exclude_none=True)
                        if "name" in changes or "category" in changes:
                            raise HTTPException(400, "Use price and availability controls for existing items")
                        details = f"Menu item {item.id}: " + "; ".join(f"{k}: {getattr(item, k)} -> {v}" for k, v in changes.items())
                        for field, value in changes.items():
                            setattr(item, field, value)
                        session.add(item)
                clock = clock or MenuVersion(key=key, occurred_at=utc(action.occurred_at), action_id=action.id)
                clock.occurred_at = utc(action.occurred_at)
                clock.action_id = action.id
                clock.removed = action.kind == "menu.remove" or bool(clock.removed)
                session.add(clock)
        session.add(AuditLog(id=action.id, staff_username=user.username, action=audit_action,
                             order_number=number, details=details))
        receipt = session.get(SyncReceipt, action.id)
        # Receipts retain only an order reference, never a second copy of a guest name.
        stored = {key: value for key, value in result.items() if key != "order"}
        if "order" in result:
            stored["order_id"] = result["order"]["id"]
        receipt.result = json.dumps(stored)
        session.add(receipt)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except (ValueError, TypeError, KeyError):
        session.rollback()
        raise HTTPException(400, "Invalid action data")
    # Do not broadcast guest names or order IDs on the public SSE channel.
    await broadcaster.publish("changed", {}, order_id=result["order"]["id"] if "order" in result else None)
    return result
