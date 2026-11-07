"""Main FastAPI application entry point for Beans&Beats Queue Management System."""

import datetime
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from .auth import hash_password
from .database import SessionDep, create_db_and_tables, engine
from .board import charity_place, charity_settings, ready_board_position
from .models import (Counter, EstimateSettings, EstimateFormat, TicketSettings, MenuItem, MenuItemOptions, MenuVersion, PickupSettings, QueueSettings,
                     Surcharge, User)
from .routers import admin, auth, events, menu, orders, reports, sync


def seed_default_data(session: Session) -> None:
    # Starting accounts come only from .env: no password is written in the code. Add more staff with
    # python -m app.manage add-staff <username> <role> (the password is typed in).
    for username, role, variable in (("admin", "admin", "ADMIN_PASSWORD"), ("barista", "barista", "BARISTA_PASSWORD"),
                                     ("booth_lead", "booth_lead", "BOOTH_LEAD_PASSWORD")):
        password = os.environ.get(variable)
        if password and not session.exec(select(User).where(User.username == username)).first():
            session.add(User(username=username, password_hash=hash_password(password), role=role))

    # 2. Owner's menu. Applied once per database, so later
    # Booth Lead edits are never overwritten on restart.
    if not session.get(Counter, OWNER_MENU_MARKER):
        apply_owner_menu(session)
    for name in ("iced", "oat"):
        if not session.get(Surcharge, name):
            session.add(Surcharge(name=name, price_cents=100))

    session.commit()


OWNER_MENU_MARKER = "seed:owner-menu-2026-10-28"
# (name, price in cents, takes milk). Order keeps the original item IDs 1-4.
OWNER_MENU = [
    ("Espresso", 300, False),
    ("Americano", 600, False),
    ("Flat White", 800, True),
    ("Latte", 800, True),
    ("Pourover", 1000, True),
]


def apply_owner_menu(session: Session) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    wanted = {name for name, _, _ in OWNER_MENU}
    for name, price, takes_milk in OWNER_MENU:
        item = session.exec(select(MenuItem).where(MenuItem.name == name)).first() or MenuItem(name=name, price_cents=price)
        item.category, item.price_cents, item.is_sold_out = "coffee", price, False
        session.add(item)
        session.flush()
        options = session.get(MenuItemOptions, item.id) or MenuItemOptions(item_id=item.id)
        options.takes_milk = takes_milk
        session.add(options)
        clock = session.get(MenuVersion, f"item:{item.id}")
        if clock and clock.removed:
            clock.removed = False
            session.add(clock)
    # Items outside the owner's menu are removed the same way as a Booth Lead removal,
    # so past orders and reports keep their rows.
    for item in session.exec(select(MenuItem)).all():
        if item.name not in wanted:
            clock = session.get(MenuVersion, f"item:{item.id}") or MenuVersion(
                key=f"item:{item.id}", occurred_at=now, action_id=OWNER_MENU_MARKER)
            clock.removed = True
            session.add(clock)
    session.add(Counter(id=OWNER_MENU_MARKER, value=1))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    create_db_and_tables()
    with Session(engine) as session:
        seed_default_data(session)
    yield


app = FastAPI(
    title="Beans&Beats Queue Management System",
    description="Charity coffee booth queue system for Christmas Together 2026",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for tablet and mobile browsers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(auth.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(events.router)
app.include_router(sync.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "event": "Christmas Together 2026"}


@app.get("/api/config")
def public_config(session: SessionDep):
    surcharges = {row.name: row.price_cents for row in session.exec(select(Surcharge)).all()}
    pickup = session.get(PickupSettings, 1) or PickupSettings()
    return {
        "guest_base_url": os.environ.get("GUEST_BASE_URL", ""),
        "ready_board_position": ready_board_position(session),
        # The board's charity counter choice (Settings → Ready board).
        **{f"charity_{key}": value for key, value in charity_settings(session).model_dump(include={"show", "period"}).items()},
        "charity_place": charity_place(session),
        "iced_surcharge_cents": surcharges.get("iced", 100),
        "oat_surcharge_cents": surcharges.get("oat", 100),
        "use_making_step": (session.get(QueueSettings, 1) or QueueSettings()).use_making_step,
        # The estimate on the ready board / guest's phone, Settings → Queue steps.
        **{f"estimate_{key}": value for key, value in
           (session.get(EstimateSettings, 1) or EstimateSettings()).model_dump(include={"on_board", "on_guest"}).items()},
        "estimate_display": (session.get(EstimateFormat, 1) or EstimateFormat()).display,
        # Where Cancel order shows on every tablet.
        "cancel_place": (session.get(TicketSettings, 1) or TicketSettings()).cancel_place,
        # The live server never shows demo usernames or passwords on its sign-in page.
        "show_demo_logins": os.environ.get("APP_ENV") != "production",
        # Pickup settings, shared by every tablet.
        **{key: getattr(pickup, key) for key in ("collect_on_scan", "reminders", "warn_minutes", "discard_minutes")},
    }


# Explicit browser routes leave missing API and asset URLs as real 404s.
@app.get("/board", include_in_schema=False)
@app.get("/guest/{order_id}", include_in_schema=False)
def frontend_page(order_id: str = ""):
    return FileResponse("static/index.html")


# Mount static directory for frontend if it exists
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")

