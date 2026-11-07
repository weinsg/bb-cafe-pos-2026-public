"""Waiting-time estimate.

estimate = (orders ahead + 1) x the usual gap between Ready taps while a queue is waiting,
shown from the median gap (low) to the 80th-percentile gap (high). No running-late flag.
"""

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from .models import Order

DEFAULT_LOW_S = 150  # 2.5 min until enough gaps are measured today
DEFAULT_HIGH_S = 210  # 3.5 min
MIN_GAPS = 10
LAST_GAPS = 50
MIN_FOR_AVERAGE = 5
WAITING = ("received", "making")


@dataclass(frozen=True)
class DayStats:
    low_s: float
    high_s: float
    measured_gaps: int
    average_wait_min: int | None


def utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    below, above = math.floor(k), math.ceil(k)
    return ordered[below] + (ordered[above] - ordered[below]) * (k - below)


def compute_stats(rows: list[tuple[dt.datetime, dt.datetime]]) -> DayStats:
    """rows: (created_at, ready_at) of the day's orders that reached Ready."""
    rows = sorted(((utc(c), utc(r)) for c, r in rows), key=lambda row: row[1])
    gaps = [
        (ready - previous_ready).total_seconds()
        for (_, previous_ready), (created, ready) in zip(rows, rows[1:])
        # Counted only while a queue was waiting: the next order existed at the previous Ready tap.
        if created <= previous_ready
    ][-LAST_GAPS:]
    if len(gaps) >= MIN_GAPS:
        low, high = statistics.median(gaps), _percentile(gaps, 0.8)
    else:
        low, high = DEFAULT_LOW_S, DEFAULT_HIGH_S
    waits = [(ready - created).total_seconds() for created, ready in rows]
    average = round(statistics.mean(waits) / 60) if len(waits) >= MIN_FOR_AVERAGE else None
    return DayStats(low, high, len(gaps), average)


_cache: dict[str, tuple[tuple, DayStats]] = {}


def day_stats(session: Session, event_day: str) -> DayStats:
    """Cached per day until another order reaches Ready (one cheap query per guest check)."""
    ready_today = (Order.event_day == event_day) & col(Order.ready_at).is_not(None)
    key = tuple(session.exec(select(func.count(), func.max(Order.ready_at))
                             .where(ready_today)).one())
    cached = _cache.get(event_day)
    if cached and cached[0] == key:
        return cached[1]
    rows = session.exec(select(Order.created_at, Order.ready_at).where(ready_today)).all()
    stats = compute_stats(list(rows))
    _cache.clear()  # only one day is ever needed
    _cache[event_day] = (key, stats)
    return stats


def minutes(ahead: int, stats: DayStats) -> tuple[int, int]:
    """(low, high) whole minutes for an order with `ahead` orders before it; leans high."""
    low = max(1, math.floor((ahead + 1) * stats.low_s / 60))
    high = max(low + 1, math.ceil((ahead + 1) * stats.high_s / 60))
    return low, high


def orders_ahead(session: Session, order: Order) -> int:
    return session.exec(select(func.count()).where(
        Order.event_day == order.event_day, col(Order.status).in_(WAITING), Order.id != order.id,
        Order.created_at < order.created_at)).one()


def waiting_count(session: Session, event_day: str) -> int:
    return session.exec(select(func.count()).where(
        Order.event_day == event_day, col(Order.status).in_(WAITING))).one()
