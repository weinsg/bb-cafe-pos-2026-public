"""Administrative controls: Daily Reset and Audit Trail."""

import uuid
from typing import Annotated, List, Optional

from fastapi import APIRouter, Header, HTTPException, status
from sqlmodel import col, select

from ..auth import RequireBarista, RequireBoothLead, RequireAdmin, can_cancel, can_view_completed
from ..database import SessionDep
from ..models import (AuditLog, Order, ClosedDay, SyncReceipt, CompletedAccess, CancelAccess, User, BoardSettings,
                      QueueSettings, PickupSettings, CharityCounter, EstimateSettings, TicketSettings)
from .sync import lock_counter
from ..schemas import (AuditLogResponse, ResetDayRequest, CompletedAccessUpdate, UserResponse, BoardSettingsUpdate,
                       QueueSettingsUpdate, PickupSettingsUpdate, CharityCounterUpdate,
                       EstimateSettingsUpdate, CancelPlaceUpdate)
from .events import broadcaster

# Changes that other staff screens announce in the settings-change strip.
SETTING_ACTIONS = ("BOARD_LAYOUT_CHANGED", "CHARITY_COUNTER_CHANGED", "QUEUE_STEPS_CHANGED", "ESTIMATE_CHANGED", "CANCEL_PLACE_CHANGED",
                   "PICKUP_SETTINGS_CHANGED",
                   "COMPLETED_ACCESS_CHANGED", "CANCEL_ACCESS_CHANGED", "RESET_DAY")
ChangeId = Annotated[Optional[str], Header()]


def _change_id(value: Optional[str]) -> str:
    """The saving device sends its own change ID, so it can skip its own strip."""
    try:
        return str(uuid.UUID(value)) if value else str(uuid.uuid4())
    except ValueError:
        return str(uuid.uuid4())


async def _announce(event: str) -> None:
    await broadcaster.publish(event, {})
    # Public channel: the event type only; staff fetch who/what from /setting-changes.
    await broadcaster.publish("setting_changed", {})

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.patch("/board-settings")
async def update_board_settings(request: BoardSettingsUpdate, session: SessionDep, user: RequireAdmin,
                                x_change_id: ChangeId = None):
    """Only Admin can choose which side of every board shows Ready orders."""
    lock_counter(session, "board-settings")
    settings = session.get(BoardSettings, 1) or BoardSettings()
    previous = settings.ready_position
    settings.ready_position = request.ready_board_position
    session.add(settings)
    if previous != settings.ready_position:
        session.add(AuditLog(
            id=_change_id(x_change_id), staff_username=user.username,
            action="BOARD_LAYOUT_CHANGED",
            details=f"Ready board position changed from {previous} to {settings.ready_position}",
        ))
    session.commit()
    if previous != settings.ready_position:
        await _announce("board_settings_changed")
    else:
        await broadcaster.publish("board_settings_changed", {})
    return {"ready_board_position": settings.ready_position}


COUNTER_LABELS = {"off": "off", "cups": "cups", "cups_amount": "cups and amount",
                  "today": "today", "all": "all event days"}


@router.patch("/charity-counter")
async def update_charity_counter(request: CharityCounterUpdate, session: SessionDep, user: RequireAdmin,
                                 x_change_id: ChangeId = None):
    """Admin chooses what the ready board's charity counter shows (last save wins)."""
    lock_counter(session, "charity-counter")
    settings = session.get(CharityCounter, 1) or CharityCounter()
    before = (settings.show, settings.period)
    settings.show, settings.period = request.show, request.period
    session.add(settings)
    changed = before != (settings.show, settings.period)
    if changed:
        session.add(AuditLog(
            id=_change_id(x_change_id), staff_username=user.username, action="CHARITY_COUNTER_CHANGED",
            details=f"Charity counter: {COUNTER_LABELS[settings.show]}"
                    + ("" if settings.show == "off" else f", {COUNTER_LABELS[settings.period]}"),
        ))
    session.commit()
    if changed:
        await _announce("board_settings_changed")
    else:
        await broadcaster.publish("board_settings_changed", {})
    return {"show": settings.show, "period": settings.period}


@router.patch("/queue-settings")
async def update_queue_settings(request: QueueSettingsUpdate, session: SessionDep, user: RequireBoothLead,
                                x_change_id: ChangeId = None):
    """Booth Lead or Admin switches the optional Making step for every tablet."""
    lock_counter(session, "queue-settings")
    settings = session.get(QueueSettings, 1) or QueueSettings()
    previous = settings.use_making_step
    settings.use_making_step = request.use_making_step
    session.add(settings)
    if previous != settings.use_making_step:
        session.add(AuditLog(
            id=_change_id(x_change_id), staff_username=user.username,
            action="QUEUE_STEPS_CHANGED",
            details=f"Making step switched {'on' if settings.use_making_step else 'off'}",
        ))
    session.commit()
    if previous != settings.use_making_step:
        await _announce("changed")
    else:
        await broadcaster.publish("changed", {})
    return {"use_making_step": settings.use_making_step}


@router.patch("/estimate-settings")
async def update_estimate_settings(request: EstimateSettingsUpdate, session: SessionDep, user: RequireBoothLead,
                                   x_change_id: ChangeId = None):
    """Booth Lead or Admin switches the estimate on the ready board and/or the guest's phone."""
    lock_counter(session, "estimate-settings")
    settings = session.get(EstimateSettings, 1) or EstimateSettings()
    changes = []
    for key, place in (("on_board", "on the ready board"), ("on_guest", "on the guest's phone")):
        value = getattr(request, key)
        if value is not None and value != getattr(settings, key):
            setattr(settings, key, value)
            changes.append(f"Waiting-time estimate {place} switched {'on' if value else 'off'}")
    session.add(settings)
    if changes:
        session.add(AuditLog(id=_change_id(x_change_id), staff_username=user.username, action="ESTIMATE_CHANGED",
                             details="; ".join(changes)))
    session.commit()
    if changes:
        # Guest pages refetch for this event (spread over 3 s); tablets and boards refetch on any event.
        await _announce("guest_settings_changed")
    return {"on_board": settings.on_board, "on_guest": settings.on_guest}


CANCEL_PLACES = {"popup": "in the QR popup", "below": "below the main button", "both": "in the QR popup and below the main button"}


@router.patch("/cancel-place")
async def update_cancel_place(request: CancelPlaceUpdate, session: SessionDep, user: RequireBoothLead,
                              x_change_id: ChangeId = None):
    """Where every tablet shows Cancel order."""
    lock_counter(session, "ticket-settings")
    settings = session.get(TicketSettings, 1) or TicketSettings()
    changed = settings.cancel_place != request.cancel_place
    settings.cancel_place = request.cancel_place
    session.add(settings)
    if changed:
        session.add(AuditLog(id=_change_id(x_change_id), staff_username=user.username, action="CANCEL_PLACE_CHANGED",
                             details=f"Cancel button {CANCEL_PLACES[settings.cancel_place]}"))
    session.commit()
    if changed:
        await _announce("changed")
    return {"cancel_place": settings.cancel_place}


def pickup_settings(session) -> PickupSettings:
    return session.get(PickupSettings, 1) or PickupSettings()


@router.patch("/pickup-settings")
async def update_pickup_settings(request: PickupSettingsUpdate, session: SessionDep, user: RequireBoothLead,
                                 x_change_id: ChangeId = None):
    """Booth Lead or Admin: Collect on scan and pickup reminders, shared and audited."""
    lock_counter(session, "pickup-settings")
    settings = pickup_settings(session)
    changes = []
    if request.collect_on_scan is not None and request.collect_on_scan != settings.collect_on_scan:
        settings.collect_on_scan = request.collect_on_scan
        changes.append(f"Collect on scan switched {'on' if settings.collect_on_scan else 'off'}")
    if request.reminders is not None and request.reminders != settings.reminders:
        settings.reminders = request.reminders
        changes.append(f"Pickup reminders switched {'on' if settings.reminders else 'off'}")
    warn = request.warn_minutes if request.warn_minutes is not None else settings.warn_minutes
    discard = request.discard_minutes if request.discard_minutes is not None else settings.discard_minutes
    if discard <= warn:
        raise HTTPException(400, "The discard time must be later than the warning time")
    if (warn, discard) != (settings.warn_minutes, settings.discard_minutes):
        settings.warn_minutes, settings.discard_minutes = warn, discard
        changes.append(f"Pickup reminder times: {warn} and {discard} min")
    session.add(settings)
    if changes:
        session.add(AuditLog(id=_change_id(x_change_id), staff_username=user.username,
                             action="PICKUP_SETTINGS_CHANGED", details="; ".join(changes)))
    session.commit()
    if changes:
        await _announce("changed")
    return {key: getattr(settings, key) for key in ("collect_on_scan", "reminders", "warn_minutes", "discard_minutes")}


@router.get("/setting-changes")
def setting_changes(session: SessionDep, user: RequireBarista):
    """Signed-in staff only: the latest booth-setting changes for the strip."""
    rows = session.exec(select(AuditLog).where(col(AuditLog.action).in_(SETTING_ACTIONS))
                        .order_by(AuditLog.timestamp.desc()).limit(10)).all()
    return [{"id": row.id, "staff_username": row.staff_username, "details": row.details,
             "timestamp": row.timestamp} for row in rows]


@router.post("/reset-day")
async def reset_between_event_days(
    request: ResetDayRequest,
    session: SessionDep,
    user: RequireBoothLead,
    x_change_id: ChangeId = None,
):
    """Close an event day and apply the first-name purge policy."""
    if not request.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset confirmation flag must be explicitly set to True",
        )

    import datetime
    try:
        datetime.date.fromisoformat(request.closed_event_day)
    except ValueError:
        raise HTTPException(400, "Choose a valid event day")
    lock_counter(session, f"day:{request.closed_event_day}")
    if not session.get(ClosedDay, request.closed_event_day):
        session.add(ClosedDay(day=request.closed_event_day))

    # 1. Fetch closed day orders
    orders = session.exec(
        select(Order).where(Order.event_day == request.closed_event_day)
    ).all()

    sanitized_count = 0
    closed_in_queue_count = 0

    for order in orders:
        # Apply the project's daily-reset policy, not a universal PDPA deadline.
        if order.first_name is not None:
            order.first_name = None
            sanitized_count += 1

        # Clear remaining open items from active board/queue
        if order.status in ["received", "making", "ready"]:
            order.status = "collected"
            closed_in_queue_count += 1

        session.add(order)

    # 2. Add Immutable Audit Log entry
    audit = AuditLog(
        id=_change_id(x_change_id),
        staff_username=user.username,
        action="RESET_DAY",
        details=(
            f"Reset day '{request.closed_event_day}': sanitized {sanitized_count} guest names, "
            f"cleared {closed_in_queue_count} open orders from queue"
        ),
    )
    session.add(audit)
    session.commit()

    # 3. Broadcast SSE signal so all tablets reset their local view
    await broadcaster.publish(
        "daily_reset",
        {"closed_event_day": request.closed_event_day},
    )
    await broadcaster.publish("setting_changed", {})

    return {
        "status": "success",
        "message": f"Day '{request.closed_event_day}' closed and reset successfully.",
        "names_sanitized": sanitized_count,
        "queue_orders_cleared": closed_in_queue_count,
    }


@router.get("/audit-logs", response_model=List[AuditLogResponse])
def get_audit_logs(
    session: SessionDep,
    user: RequireAdmin,
) -> List[AuditLogResponse]:
    """Audit records are available only to Admin."""
    logs = session.exec(
        select(AuditLog).order_by(AuditLog.timestamp.desc())
    ).all()
    receipts = {r.id: r for r in session.exec(select(SyncReceipt)).all()}
    return [
        AuditLogResponse(
            id=log.id,
            timestamp=log.timestamp,
            staff_username=log.staff_username,
            action=log.action,
            order_number=log.order_number,
            details=log.details,
            device_id=receipts[log.id].device_id if log.id in receipts else None,
            occurred_at=receipts[log.id].occurred_at if log.id in receipts else log.timestamp,
            synced_at=receipts[log.id].synced_at if log.id in receipts else log.timestamp,
        )
        for log in logs
    ]


@router.get("/staff-permissions", response_model=List[UserResponse])
def staff_permissions(session: SessionDep, user: RequireBoothLead):
    staff = session.exec(select(User).where(User.role == "barista").order_by(User.username)).all()
    return [UserResponse(id=staff.id, username=staff.username, role=staff.role,
                         can_view_completed=can_view_completed(session, staff),
                         can_cancel=can_cancel(session, staff)) for staff in staff]


def _baristas(session, user_ids):
    ids = sorted(set(user_ids))
    targets = [session.get(User, user_id) for user_id in ids]
    if any(target is None or target.role != "barista" for target in targets):
        raise HTTPException(400, "Select barista accounts only")
    return targets


@router.patch("/staff-permissions", response_model=List[UserResponse])
async def update_staff_permissions(request: CompletedAccessUpdate, session: SessionDep, user: RequireBoothLead,
                                   x_change_id: ChangeId = None):
    targets = _baristas(session, request.user_ids)
    lock_counter(session, "completed-permissions")
    for target in targets:
        access = session.get(CompletedAccess, target.id) or CompletedAccess(user_id=target.id)
        access.enabled = request.enabled
        session.add(access)
    names = ", ".join(target.username for target in targets)
    session.add(AuditLog(id=_change_id(x_change_id), staff_username=user.username, action="COMPLETED_ACCESS_CHANGED",
                         details=f"Completed-orders access {'on' if request.enabled else 'off'} for {names}"))
    session.commit()
    await _announce("permissions_changed")
    return staff_permissions(session, user)


@router.patch("/cancel-permissions", response_model=List[UserResponse])
async def update_cancel_permissions(request: CompletedAccessUpdate, session: SessionDep, user: RequireBoothLead,
                                    x_change_id: ChangeId = None):
    """Switch a barista's cancel right off or on (Booth Lead and Admin can always cancel)."""
    targets = _baristas(session, request.user_ids)
    lock_counter(session, "cancel-permissions")
    for target in targets:
        access = session.get(CancelAccess, target.id) or CancelAccess(user_id=target.id)
        access.enabled = request.enabled
        session.add(access)
    names = ", ".join(target.username for target in targets)
    session.add(AuditLog(id=_change_id(x_change_id), staff_username=user.username, action="CANCEL_ACCESS_CHANGED",
                         details=f"Cancel right {'on' if request.enabled else 'off'} for {names}"))
    session.commit()
    await _announce("permissions_changed")
    return staff_permissions(session, user)
