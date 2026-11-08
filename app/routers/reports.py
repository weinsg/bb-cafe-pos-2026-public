"""Daily sales report and CSV export router (Module 6)."""

from ..reporting import enrich, summary, breakdown, details_csv, stamp
import csv
import datetime
import io
import json
import uuid
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import or_
from sqlmodel import col, func, select

from ..auth import RequireBoothLead
from ..database import SessionDep
from ..models import Order, OrderClosure, OrderItem, AuditLog, OrderStage, SyncReceipt
from ..schemas import DailyDrinkTotal, DailyReportResponse, DailySalesItem, ReportOrder, ReportOrdersResponse

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _calculate_daily_summary(
    session: SessionDep, event_day: str, days: list[str] | None = None
) -> DailyReportResponse:
    """One day, or several days (all event days) when `days` is given."""
    days = days or [event_day]
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
        .where(col(Order.event_day).in_(days))
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
        .where(col(Order.event_day).in_(days), col(Order.status).in_(["cancelled", "discarded"]))
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
    scope: Literal["day", "all"] = "day",
) -> DailyReportResponse:
    days = _days(session, event_day, scope)
    report = _calculate_daily_summary(session, "all" if scope == "all" else days[0], days)
    orders = _report_orders(session, days, "all")
    report.summary = summary(orders)
    report.breakdown = breakdown(orders)
    report.generated_at = stamp(datetime.datetime.now(datetime.timezone.utc))
    return report


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


# The owner's views and exports — cancelled (with the refund tally), discarded and all orders,
# for one day or all event days. No guest names anywhere; staff logins are shown.
SGT = ZoneInfo("Asia/Singapore")
REASONS = {"keyed_wrong": "Keyed wrong", "duplicate": "Duplicate order", "changed_mind": "Guest changed their mind",
           "guest_left": "Guest left", "ran_out": "Drink or milk ran out", "other": "Other", "hygiene": "Waited too long"}
MONEY = {"refunded": "Refunded on POS", "not_paid": "Not paid yet", "no_refund": "Paid, no refund"}


def _aware(value):
    return value.replace(tzinfo=datetime.timezone.utc) if value and value.tzinfo is None else value


def _all_days(session) -> list[str]:
    return sorted(session.exec(select(Order.event_day).distinct()).all())


def _days(session, event_day: Optional[str], scope: str) -> list[str]:
    if scope == "all":
        return _all_days(session)
    day = event_day or datetime.datetime.now(SGT).date().isoformat()
    try:
        datetime.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Choose a valid event day")
    return [day]


def _local(value) -> str:
    if not value:
        return ""
    local = _aware(value).astimezone(SGT)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def _drinks_text(drinks) -> str:
    parts = []
    for d in drinks:
        milk = {"oat": "Oat", "dairy": "Dairy"}.get(d.milk or "", "")
        options = ", ".join(x for x in ((d.temperature or "").capitalize(), milk) if x)
        parts.append(f"{d.quantity} × {d.item_name}" + (f" ({options})" if options else ""))
    return "; ".join(parts)


def _report_orders(session, days: list[str], kind: str) -> list[ReportOrder]:
    query = (select(Order, OrderClosure).outerjoin(OrderClosure, OrderClosure.order_id == Order.id)
             .where(col(Order.event_day).in_(days)))
    if kind in ("cancelled", "discarded"):
        query = query.where(Order.status == kind).order_by(col(OrderClosure.closed_at).desc())
    else:
        query = query.order_by(Order.event_day, Order.created_at)
    rows = session.exec(query).all()
    ids = [order.id for order, _ in rows]
    items: dict[str, list] = {}
    if ids:
        for item in session.exec(select(OrderItem).where(col(OrderItem.order_id).in_(ids))).all():
            items.setdefault(item.order_id, []).append(item)
    # Batch stage/receipt reads so a four-day report does not query once per order.
    stages_by_id, logs_by_id = {}, {}
    if ids:
        for row in session.exec(select(OrderStage).where(col(OrderStage.order_id).in_(ids))).all():
            stages_by_id.setdefault(row.order_id, []).append(row)
        numbers = list({o.order_number for o, _ in rows})
        wanted = set(ids)
        for log, receipt in session.exec(select(AuditLog, SyncReceipt).join(SyncReceipt, SyncReceipt.id == AuditLog.id)
                                        .where(col(AuditLog.order_number).in_(numbers))).all():
            try:
                order_id = json.loads(receipt.result).get("order_id")
            except (ValueError, TypeError):
                continue
            if order_id in wanted:
                logs_by_id.setdefault(order_id, []).append((log, receipt))
    result = []
    for order, closure in rows:
        drinks = items.get(order.id, [])
        waited = None
        if closure and order.status == "discarded" and order.ready_at:
            waited = max(0, int((_aware(closure.closed_at) - _aware(order.ready_at)).total_seconds() // 60))
        result.append(ReportOrder(
            event_day=order.event_day, order_number=order.order_number, time=_local(order.created_at),
            status=order.status, drinks=_drinks_text(drinks),
            lines=[{"line_id": d.id, "item": d.item_name, "temperature": d.temperature or "", "milk": d.milk or "", "quantity": d.quantity,
                    "unit_price_cents": d.unit_price_cents} for d in drinks],
            amount_cents=order.total_price_cents,
            counts_in_sales=order.status != "cancelled" or (closure is not None and closure.money == "no_refund"),
            reason=REASONS.get(closure.reason, closure.reason) if closure and closure.reason else None,
            money=MONEY.get(closure.money) if closure and closure.money else None,
            by=closure.staff_username if closure else None,
            closed_time=_local(closure.closed_at) if closure else None,
            waited_min=waited,
            **enrich(session, order, closure, drinks, stages_by_id.get(order.id, []), logs_by_id.get(order.id, [])),
        ))
    return result


def _orders_response(session, days, kind) -> ReportOrdersResponse:
    orders = _report_orders(session, days, kind) if days else []
    cancelled = [o for o in (orders if kind == "cancelled" else _report_orders(session, days, "cancelled") if days else [])]
    tally = {key: sum(o.amount_cents for o in cancelled if o.money == label) for key, label in MONEY.items()}
    final = _calculate_daily_summary(session, days[0], days).total_revenue_cents if days else 0
    return ReportOrdersResponse(scope_days=days, kind=kind, orders=orders, refunded_cents=tally["refunded"],
                                not_paid_cents=tally["not_paid"], no_refund_cents=tally["no_refund"], final_sales_cents=final,
                                summary=summary(orders), breakdown=breakdown(orders), generated_at=stamp(datetime.datetime.now(datetime.timezone.utc)))


@router.get("/orders", response_model=ReportOrdersResponse)
def get_report_orders(session: SessionDep, user: RequireBoothLead, kind: Literal["cancelled", "discarded", "all"] = "all",
                      event_day: Optional[str] = None, scope: Literal["day", "all"] = "day") -> ReportOrdersResponse:
    """Cancelled / discarded / all orders for one day or all event days (no guest names)."""
    return _orders_response(session, _days(session, event_day, scope), kind)


CSV_LABELS = {"complete": "complete sales report", "sales": "sales by item", "cancelled": "cancelled orders", "discarded": "discarded drinks", "all": "all orders"}
CSV_FILES = {"complete": "complete_sales", "sales": "sales", "cancelled": "cancelled", "discarded": "discarded", "all": "all_orders"}


def _cents(cents: int) -> str:
    return f"{cents / 100:.2f}"


@router.get("/csv")
def export_report_csv(session: SessionDep, user: RequireBoothLead,
                      kind: Literal["sales", "complete", "cancelled", "discarded", "all"] = "sales",
                      event_day: Optional[str] = None, scope: Literal["day", "all"] = "day") -> Response:
    """Sales by item, cancelled orders (with the refund tally), discarded drinks or all orders; no guest names."""
    days = _days(session, event_day, scope)
    out = io.StringIO()
    w = csv.writer(out)
    if kind == "sales":
        w.writerow(["date", "item", "temperature", "milk", "cups", "unit_price", "total"])
        grand_cups = grand_cents = 0
        for day in days:
            report = _calculate_daily_summary(session, day)
            for item in report.items:
                w.writerow([day, item.item_name, item.temperature or "", item.milk or "", item.cups_sold,
                            _cents(item.unit_price_cents), _cents(item.total_revenue_cents)])
            w.writerow([day, "TOTAL", "", "", report.total_cups, "-", _cents(report.total_revenue_cents)])
            grand_cups += report.total_cups
            grand_cents += report.total_revenue_cents
        if scope == "all":
            w.writerow(["ALL EVENT DAYS", "GRAND TOTAL", "", "", grand_cups, "-", _cents(grand_cents)])
    else:
        master = _report_orders(session, days, "all") if days else []
        selected = [o for o in master if o.status == kind] if kind in ("cancelled", "discarded") else master
        out = io.StringIO(details_csv(selected, days, kind, summary(master)))
    label = "all-days" if scope == "all" else (days[0] if days else "none")
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username=user.username, action="CSV_EXPORT",
                         details=f"Exported {CSV_LABELS[kind]} for {'all event days' if scope == 'all' else label}"))
    session.commit()
    return Response(content=out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=beansbeats_{CSV_FILES[kind]}_{label}.csv"})

