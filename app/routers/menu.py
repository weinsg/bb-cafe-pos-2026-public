"""Menu controls router (Module 5)."""

import uuid
from typing import List

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from ..auth import RequireBoothLead
from ..database import SessionDep
from ..models import AuditLog, MenuItem, MenuItemOptions, MenuVersion
from ..schemas import MenuItemCreate, MenuItemResponse, MenuItemUpdate
from .events import broadcaster

router = APIRouter(prefix="/api/menu", tags=["menu"])


def menu_response(session, item: MenuItem) -> MenuItemResponse:
    options = session.get(MenuItemOptions, item.id)
    return MenuItemResponse(**item.model_dump(include=set(MenuItemResponse.model_fields)),
                            takes_milk=options.takes_milk if options else True)


@router.get("", response_model=List[MenuItemResponse])
def get_menu(session: SessionDep) -> List[MenuItemResponse]:
    items = session.exec(select(MenuItem).order_by(MenuItem.id)).all()
    removed = {v.key for v in session.exec(select(MenuVersion).where(MenuVersion.removed == True)).all()}
    return [menu_response(session, item) for item in items if f"item:{item.id}" not in removed]


@router.post("", response_model=MenuItemResponse, status_code=status.HTTP_201_CREATED)
async def create_menu_item(
    item_in: MenuItemCreate,
    session: SessionDep,
    user: RequireBoothLead,
) -> MenuItemResponse:
    existing = session.exec(
        select(MenuItem).where(MenuItem.name == item_in.name)
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Item with this name already exists",
        )

    item = MenuItem.model_validate(item_in.model_dump(exclude={"takes_milk"}))
    session.add(item)
    session.flush()
    session.add(MenuItemOptions(item_id=item.id, takes_milk=item_in.takes_milk))

    # Immutable Audit Log entry
    audit = AuditLog(
        id=str(uuid.uuid4()),
        staff_username=user.username,
        action="MENU_CREATE",
        details=f"Created item '{item.name}' (${item.price_cents / 100:.2f})",
    )
    session.add(audit)
    session.commit()
    session.refresh(item)

    await broadcaster.publish(
        "menu_updated",
        {"id": item.id, "name": item.name, "is_sold_out": item.is_sold_out},
    )
    return menu_response(session, item)


@router.patch("/{item_id}", response_model=MenuItemResponse)
async def update_menu_item(
    item_id: int,
    item_update: MenuItemUpdate,
    session: SessionDep,
    user: RequireBoothLead,
) -> MenuItemResponse:
    item = session.get(MenuItem, item_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Menu item not found"
        )

    changes = []
    if item_update.name is not None and item_update.name != item.name:
        changes.append(f"name: {item.name} -> {item_update.name}")
        item.name = item_update.name
    if (
        item_update.category is not None
        and item_update.category != item.category
    ):
        changes.append(f"category: {item.category} -> {item_update.category}")
        item.category = item_update.category
    if (
        item_update.price_cents is not None
        and item_update.price_cents != item.price_cents
    ):
        changes.append(
            f"price: ${item.price_cents / 100:.2f} -> ${item_update.price_cents / 100:.2f}"
        )
        item.price_cents = item_update.price_cents
    if (
        item_update.is_sold_out is not None
        and item_update.is_sold_out != item.is_sold_out
    ):
        changes.append(
            f"sold_out: {item.is_sold_out} -> {item_update.is_sold_out}"
        )
        item.is_sold_out = item_update.is_sold_out

    if changes:
        audit = AuditLog(
            id=str(uuid.uuid4()),
            staff_username=user.username,
            action="MENU_UPDATE",
            details=f"Updated item '{item.name}': {', '.join(changes)}",
        )
        session.add(audit)

    session.add(item)
    session.commit()
    session.refresh(item)

    await broadcaster.publish(
        "menu_updated",
        {"id": item.id, "name": item.name, "is_sold_out": item.is_sold_out},
    )
    return menu_response(session, item)
