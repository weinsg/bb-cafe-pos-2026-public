"""Pydantic / SQLModel request and response schemas."""

import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# --- Auth Schemas ---
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    can_view_completed: bool = True
    can_cancel: bool = True


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    can_view_completed: bool = True
    can_cancel: bool = True


class CompletedAccessUpdate(BaseModel):
    user_ids: List[int] = Field(min_length=1, max_length=100)
    enabled: bool


class BoardSettingsUpdate(BaseModel):
    ready_board_position: Literal["left", "right"]


class CharityCounterUpdate(BaseModel):
    """What the ready board's charity counter shows, and over which days."""
    show: Literal["off", "cups", "cups_amount"]
    period: Literal["today", "all"]
    place: Optional[Literal["top", "left", "right"]] = None  # top line (default), left or right side panel


class QueueSettingsUpdate(BaseModel):
    use_making_step: bool


class EstimateSettingsUpdate(BaseModel):
    on_board: Optional[bool] = None
    on_guest: Optional[bool] = None
    display: Optional[Literal["range", "upper", "average", "average_upper"]] = None


class CancelPlaceUpdate(BaseModel):
    cancel_place: Literal["popup", "below", "both"]


class PickupSettingsUpdate(BaseModel):
    collect_on_scan: Optional[bool] = None
    reminders: Optional[bool] = None
    warn_minutes: Optional[int] = Field(default=None, ge=5, le=240)
    discard_minutes: Optional[int] = Field(default=None, ge=10, le=480)


CANCEL_REASONS = ("keyed_wrong", "duplicate", "changed_mind", "guest_left", "ran_out", "other")
CANCEL_MONEY = ("refunded", "not_paid", "no_refund")


# Upper bounds keep values inside PostgreSQL INTEGER and a sensible booth order.
MAX_PRICE_CENTS = 100_000  # S$1,000
MAX_QUANTITY = 20
MAX_ORDER_LINES = 50


# --- Menu Schemas ---
class MenuItemCreate(BaseModel):
    name: str = Field(max_length=80)
    category: str = "coffee"
    price_cents: int = Field(gt=0, le=MAX_PRICE_CENTS)
    is_sold_out: bool = False
    takes_milk: bool = True


class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    price_cents: Optional[int] = Field(default=None, gt=0, le=MAX_PRICE_CENTS)
    is_sold_out: Optional[bool] = None


class MenuItemResponse(BaseModel):
    id: int
    name: str
    category: str
    price_cents: int
    is_sold_out: bool
    takes_milk: bool = True


# --- Order Schemas ---
class OrderItemCreate(BaseModel):
    item_name: str = Field(max_length=80)
    unit_price_cents: int = Field(gt=0, le=MAX_PRICE_CENTS)  # Includes iced/oat add-ons
    milk: Optional[Literal["dairy", "oat", "none"]] = None
    temperature: Optional[Literal["hot", "iced"]] = None
    quantity: int = Field(default=1, gt=0, le=MAX_QUANTITY)


class OrderItemResponse(BaseModel):
    id: Optional[int] = None
    item_name: str
    unit_price_cents: int
    milk: Optional[str] = None
    temperature: Optional[str] = None
    quantity: int


class OrderCreate(BaseModel):
    id: Optional[str] = None  # UUID string if generated offline
    idempotency_key: str
    device_id: str = "tablet_A"
    first_name: str = Field(max_length=50)
    event_day: Optional[str] = None
    items: List[OrderItemCreate] = Field(max_length=MAX_ORDER_LINES)


class OrderStatusUpdate(BaseModel):
    status: str  # "making", "ready", "collected"
    status_revision: int = Field(default=0, ge=0)


class ReopenOrderRequest(BaseModel):
    confirm: bool = False
    status_revision: int = Field(ge=0)
    reason: Literal["accidental_tap", "guest_not_collected"]


class OrderResponse(BaseModel):
    id: str
    order_number: str
    device_id: str
    first_name: Optional[str] = None
    status: str
    status_revision: int = 0
    total_price_cents: int
    event_day: str
    created_at: datetime.datetime
    making_at: Optional[datetime.datetime] = None
    ready_at: Optional[datetime.datetime] = None
    collected_at: Optional[datetime.datetime] = None
    items: List[OrderItemResponse] = []


class CancelledOrderResponse(OrderResponse):
    """Queue's Cancelled tab: the order with its cancel record."""
    reason: Optional[str] = None
    money: Optional[str] = None
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[datetime.datetime] = None


class GuestDrink(BaseModel):
    name: str
    details: str = ""


class GuestStatusResponse(BaseModel):
    id: str
    order_number: str
    first_name: Optional[str] = None
    status: str
    created_at: datetime.datetime
    ready_at: Optional[datetime.datetime] = None
    items_summary: List[str] = []
    items: List[GuestDrink] = []
    # Minutes after Ready for the guest warning and the discard notice (None when off).
    warn_minutes: Optional[int] = None
    discard_minutes: Optional[int] = None
    # Closed event days (so one request per check is enough) and this order's live-update tag.
    closed_days: List[str] = []
    event_tag: str = ""
    # Waiting-time estimate while Received or Making (None otherwise), average wait today.
    estimate_low_min: Optional[int] = None
    estimate_high_min: Optional[int] = None
    estimate_display: str = "range"  # range / upper / average / average_upper
    orders_ahead: Optional[int] = None
    average_wait_min: Optional[int] = None


class WaitEstimateResponse(BaseModel):
    """The day's figures for the Queue; the tablet works out each order's range from them."""
    event_day: str
    low_s: float
    high_s: float
    measured_gaps: int
    average_wait_min: Optional[int] = None
    waiting: int
    new_order_low_min: int
    new_order_high_min: int


# --- Report & Reset Schemas ---
class DailySalesItem(BaseModel):
    item_name: str
    temperature: Optional[str] = None
    milk: Optional[str] = None
    cups_sold: int
    unit_price_cents: int
    total_revenue_cents: int


class DailyDrinkTotal(BaseModel):
    item_name: str
    cups_sold: int
    total_revenue_cents: int


class DailyReportResponse(BaseModel):
    event_day: str
    total_cups: int
    total_revenue_cents: int
    iced_cups: int = 0
    oat_cups: int = 0
    cancelled_orders: int = 0
    refunded_cents: int = 0
    discarded_orders: int = 0
    drinks: List[DailyDrinkTotal] = []  # One row per drink
    items: List[DailySalesItem]  # One row per drink + options + unit price (CSV rows)


class ReportOrder(BaseModel):
    """One order in the report's Cancelled / Discarded / All orders views (no guest name)."""
    event_day: str
    order_number: str
    time: str
    status: str
    drinks: str
    lines: List[dict] = []
    amount_cents: int
    counts_in_sales: bool
    reason: Optional[str] = None
    money: Optional[str] = None
    by: Optional[str] = None
    closed_time: Optional[str] = None
    waited_min: Optional[int] = None


class ReportOrdersResponse(BaseModel):
    scope_days: List[str]
    kind: str
    orders: List[ReportOrder]
    refunded_cents: int = 0
    not_paid_cents: int = 0
    no_refund_cents: int = 0
    final_sales_cents: int = 0


class ResetDayRequest(BaseModel):
    closed_event_day: str
    confirm: bool = False


# --- Audit Schemas ---
class AuditLogResponse(BaseModel):
    id: str
    timestamp: datetime.datetime
    staff_username: str
    action: str
    order_number: Optional[str] = None
    details: str
    device_id: Optional[str] = None
    occurred_at: Optional[datetime.datetime] = None
    synced_at: Optional[datetime.datetime] = None


class PickupLookupResponse(BaseModel):
    """Staff pickup scan: the scanned order and whether its day is closed."""
    order: OrderResponse
    day_closed: bool
