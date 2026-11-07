"""SQLModel database models for the Beans&Beats Queue Management System."""

import datetime
from typing import Optional
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    role: str = Field(default="barista", index=True)  # "barista", "booth_lead", or "admin"
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class MenuItem(SQLModel, table=True):
    __tablename__ = "menu_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    category: str = Field(default="coffee")  # "coffee", "non-coffee", "pastry"
    price_cents: int = Field(gt=0)
    is_sold_out: bool = Field(default=False)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: str = Field(primary_key=True)  # Client-generated or server UUID string
    idempotency_key: str = Field(unique=True, index=True)
    order_number: str = Field(index=True)  # e.g. "A001", "B042"
    device_id: str = Field(default="tablet_A")
    first_name: Optional[str] = Field(
        default=None, index=True
    )  # PDPA: cleared on daily reset
    status: str = Field(
        default="received", index=True
    )  # "received", "making", "ready", "collected", "discarded", "cancelled"
    total_price_cents: int = Field(default=0)
    event_day: str = Field(index=True)  # e.g. "2026-11-26"
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    making_at: Optional[datetime.datetime] = None
    ready_at: Optional[datetime.datetime] = None
    collected_at: Optional[datetime.datetime] = None


class OrderItem(SQLModel, table=True):
    __tablename__ = "order_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: str = Field(foreign_key="orders.id", index=True)
    item_name: str
    unit_price_cents: int
    milk: Optional[str] = Field(default=None)  # "oat", "dairy", "none"
    temperature: Optional[str] = Field(default=None)  # "hot", "iced"
    quantity: int = Field(default=1, gt=0)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: str = Field(primary_key=True)  # UUID string
    timestamp: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc), index=True
    )
    staff_username: str = Field(index=True)  # Login username only; NEVER guest names (PDPA)
    action: str  # "CREATE_ORDER", "STATUS_CHANGE", "STATUS_UNCHANGED", "MENU_UPDATE", "RESET_DAY"
    order_number: Optional[str] = None
    details: str


# Separate tables keep existing PostgreSQL volumes compatible (no ALTER needed).
class Tablet(SQLModel, table=True):
    __tablename__ = "tablets"
    id: str = Field(primary_key=True)
    prefix: str = Field(unique=True)


class Counter(SQLModel, table=True):
    __tablename__ = "counters"
    id: str = Field(primary_key=True)
    value: int = 0


class OrderRevision(SQLModel, table=True):
    __tablename__ = "order_revisions"
    id: str = Field(primary_key=True)
    value: int = 0


class CompletedAccess(SQLModel, table=True):
    __tablename__ = "completed_access"
    user_id: int = Field(primary_key=True)
    enabled: bool = True


class BoardSettings(SQLModel, table=True):
    __tablename__ = "board_settings"
    id: int = Field(default=1, primary_key=True)
    ready_position: str = "left"


# Charity counter on the ready board, in its own table (no ALTER on existing volumes).
class CharityCounter(SQLModel, table=True):
    __tablename__ = "charity_counter"
    id: int = Field(default=1, primary_key=True)
    show: str = "cups"  # off | cups | cups_amount
    period: str = "today"  # today | all


# Own table so existing PostgreSQL volumes need no ALTER.
class QueueSettings(SQLModel, table=True):
    __tablename__ = "queue_settings"
    id: int = Field(default=1, primary_key=True)
    use_making_step: bool = True


# Where the counter goes: "top" (top line, default), "left" or "right" (a 1/3 side panel).
# Own table, so existing volumes need no ALTER.
class CharityPlace(SQLModel, table=True):
    __tablename__ = "charity_place"
    id: int = Field(default=1, primary_key=True)
    place: str = "top"


# The waiting-time estimate on the ready board and on the guest's phone can each be
# switched off; staff screens always show it. Own table, no ALTER.
class EstimateSettings(SQLModel, table=True):
    __tablename__ = "estimate_settings"
    id: int = Field(default=1, primary_key=True)
    on_board: bool = True
    on_guest: bool = True


# How the estimate shows on the guest's phone and the board: "range" (default), "upper",
# "average" or "average_upper". Own table, so existing volumes need no ALTER.
class EstimateFormat(SQLModel, table=True):
    __tablename__ = "estimate_format"
    id: int = Field(default=1, primary_key=True)
    display: str = "range"


# Where Cancel order shows: "popup" (QR popups), "below" (under the main button), "both".
class TicketSettings(SQLModel, table=True):
    __tablename__ = "ticket_settings"
    id: int = Field(default=1, primary_key=True)
    cancel_place: str = "popup"


# Pickup booth settings in their own table (no ALTER on existing volumes).
class PickupSettings(SQLModel, table=True):
    __tablename__ = "pickup_settings"
    id: int = Field(default=1, primary_key=True)
    collect_on_scan: bool = True
    reminders: bool = True
    warn_minutes: int = 30
    discard_minutes: int = 60


# A barista's cancel right (Booth Lead and Admin can always cancel).
class CancelAccess(SQLModel, table=True):
    __tablename__ = "cancel_access"
    user_id: int = Field(primary_key=True)
    enabled: bool = True


# Why and when an order ended as Cancelled or Discarded.
class OrderClosure(SQLModel, table=True):
    __tablename__ = "order_closures"
    order_id: str = Field(primary_key=True)
    kind: str  # "cancelled" or "discarded"
    reason: Optional[str] = None
    money: Optional[str] = None  # cancelled only: "refunded", "not_paid", "no_refund"
    staff_username: str
    closed_at: datetime.datetime


class ClosedDay(SQLModel, table=True):
    __tablename__ = "closed_days"
    day: str = Field(primary_key=True)


class Ingredient(SQLModel, table=True):
    __tablename__ = "ingredients"
    name: str = Field(primary_key=True)
    is_sold_out: bool = False


class MenuItemOptions(SQLModel, table=True):
    __tablename__ = "menu_item_options"
    item_id: int = Field(primary_key=True)
    takes_milk: bool = True  # False for black drinks (Espresso, Americano)


class Surcharge(SQLModel, table=True):
    __tablename__ = "surcharges"
    name: str = Field(primary_key=True)  # "iced", "oat"
    price_cents: int = 100


class MenuVersion(SQLModel, table=True):
    __tablename__ = "menu_versions"
    key: str = Field(primary_key=True)
    occurred_at: datetime.datetime
    action_id: str
    removed: bool = False


class SyncReceipt(SQLModel, table=True):
    __tablename__ = "sync_receipts"
    id: str = Field(primary_key=True)
    staff_username: str
    device_id: str
    occurred_at: datetime.datetime
    synced_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    result: str = "{}"

