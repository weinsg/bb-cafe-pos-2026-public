"""Shared, server-persisted presentation settings for the public ready board."""

from sqlalchemy import or_
from sqlmodel import Session, func, select

from .models import BoardSettings, CharityCounter, CharityPlace, Order, OrderClosure, OrderItem


def ready_board_position(session: Session) -> str:
    settings = session.get(BoardSettings, 1)
    return "right" if settings and settings.ready_position == "right" else "left"


def charity_settings(session: Session) -> CharityCounter:
    return session.get(CharityCounter, 1) or CharityCounter()


def charity_place(session: Session) -> str:
    """"top" (top line, default), "left" or "right" (a side panel on that side)."""
    row = session.get(CharityPlace, 1)
    if not row or row.place not in ("left", "right", "panel"):
        return "top"
    if row.place == "panel":  # saved before Left/Right existed: the side away from Ready, as it was shown then
        return "left" if ready_board_position(session) == "right" else "right"
    return row.place


def charity_counter(session: Session, today: str) -> dict | None:
    """Cups (and sales) on the board, counted by the daily sales rules (A15)."""
    settings = charity_settings(session)
    if settings.show == "off":
        return None
    query = (
        select(func.coalesce(func.sum(OrderItem.quantity), 0),
               func.coalesce(func.sum(OrderItem.unit_price_cents * OrderItem.quantity), 0))
        .join(Order, Order.id == OrderItem.order_id)
        .outerjoin(OrderClosure, OrderClosure.order_id == Order.id)
        .where(or_(Order.status != "cancelled", OrderClosure.money == "no_refund"))
    )
    if settings.period == "today":
        query = query.where(Order.event_day == today)
    cups, cents = session.exec(query).one()
    return {"cups": int(cups), "amount_cents": int(cents) if settings.show == "cups_amount" else None,
            "period": settings.period, "place": charity_place(session)}
