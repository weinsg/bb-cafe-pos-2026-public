"""Daily sales report and CSV export router (Module 6)."""

import csv
import datetime
import io
import uuid
from typing import List, Optional

from fastapi import APIRouter, Response
from sqlalchemy import or_
from sqlmodel import col, func, select

from ..auth import RequireBoothLead
from ..database import SessionDep
from ..models import Order, OrderClosure, OrderItem, AuditLog
from ..schemas import DailyDrinkTotal, DailyReportResponse, DailySalesItem

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _calculate_daily_summary(
    session: SessionDep, event_day: str
) -> DailyReportResponse:
    # One row per drink + options + captured unit price (which includes add-ons).
    # Note: Only non-cancelled orders count towards daily sales
    query = (
        select(
            OrderItem.item_name,
            OrderItem.temperature,
            OrderItem.milk,
            func.sum(OrderItem.quantity).label("cups_sold"),
            OrderItem.unit_price_cents,
            func.sum(OrderItem.unit_price_cents * OrderItem.quantity).label(
                "total_revenue"
            ),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .outerjoin(OrderClosure, OrderClosure.order_id == Order.id)
        .where(Order.event_day == event_day)
        # A cancelled order counts only if it was paid and not refunded, so the total matches the POS.
        .where(or_(Order.status != "cancelled", OrderClosure.money == "no_refund"))
        .group_by(OrderItem.item_name, OrderItem.temperature, OrderItem.milk, OrderItem.unit_price_cents)
        .order_by(OrderItem.item_name, OrderItem.temperature, OrderItem.milk, OrderItem.unit_price_cents)
    )

    rows = session.exec(query).all()

    items: List[DailySalesItem] = []
    drinks: dict[str, DailyDrinkTotal] = {}
    total_cups = total_revenue_cents = iced_cups = oat_cups = 0

    for row in rows:
        item_name, temperature, milk, cups, unit_price, total_rev = row
        cups_int = int(cups) if cups else 0
        total_rev_int = int(total_rev) if total_rev else 0
        total_cups += cups_int
        total_revenue_cents += total_rev_int
        iced_cups += cups_int if temperature == "iced" else 0
        oat_cups += cups_int if milk == "oat" else 0
        drink = drinks.setdefault(item_name, DailyDrinkTotal(item_name=item_name, cups_sold=0, total_revenue_cents=0))
        drink.cups_sold += cups_int
        drink.total_revenue_cents += total_rev_int
        items.append(
            DailySalesItem(
                item_name=item_name,
                temperature=temperature,
                milk=milk,
                cups_sold=cups_int,
                unit_price_cents=unit_price,
                total_revenue_cents=total_rev_int,
            )
        )

    closures = session.exec(
        select(Order.status, OrderClosure.money, Order.total_price_cents)
        .join(OrderClosure, OrderClosure.order_id == Order.id)
        .where(Order.event_day == event_day, col(Order.status).in_(["cancelled", "discarded"]))
    ).all()
    cancelled = [row for row in closures if row[0] == "cancelled"]

    return DailyReportResponse(
        event_day=event_day,
        cancelled_orders=len(cancelled),
        refunded_cents=sum(row[2] for row in cancelled if row[1] == "refunded"),
        discarded_orders=sum(1 for row in closures if row[0] == "discarded"),
        total_cups=total_cups,
        total_revenue_cents=total_revenue_cents,
        iced_cups=iced_cups,
        oat_cups=oat_cups,
        drinks=list(drinks.values()),
        items=items,
    )


@router.get("/daily", response_model=DailyReportResponse)
def get_daily_sales_report(
    session: SessionDep,
    user: RequireBoothLead,
    event_day: Optional[str] = None,
) -> DailyReportResponse:
    target_day = (
        event_day
        if event_day
        else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    )
    return _calculate_daily_summary(session, target_day)


@router.get("/daily/csv")
def export_daily_sales_csv(
    session: SessionDep,
    user: RequireBoothLead,
    event_day: Optional[str] = None,
) -> Response:
    """Exports daily sales to CSV matching organizer POS. Zero guest names included (PDPA)."""
    target_day = (
        event_day
        if event_day
        else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    )
    report = _calculate_daily_summary(session, target_day)

    output = io.StringIO()
    writer = csv.writer(output)
    # Header: date, item, temperature, milk, cups, unit_price, total
    writer.writerow(["date", "item", "temperature", "milk", "cups", "unit_price", "total"])

    for item in report.items:
        writer.writerow(
            [
                report.event_day,
                item.item_name,
                item.temperature or "",
                item.milk or "",
                item.cups_sold,
                f"{item.unit_price_cents / 100:.2f}",
                f"{item.total_revenue_cents / 100:.2f}",
            ]
        )

    # Summary row
    writer.writerow(
        [
            report.event_day,
            "TOTAL",
            "",
            "",
            report.total_cups,
            "-",
            f"{report.total_revenue_cents / 100:.2f}",
        ]
    )

    csv_data = output.getvalue()
    filename = f"beansbeats_sales_{target_day}.csv"
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username=user.username,
                         action="CSV_EXPORT", details=f"Exported sales for {target_day}"))
    session.commit()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

