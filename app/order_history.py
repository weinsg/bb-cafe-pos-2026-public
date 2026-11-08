"""Stage attribution saved atomically with order changes; no guest names."""
import datetime as dt
from .models import OrderStage


def record_stage(session, order, stage, actor, action_id, occurred_at, device_id=None):
    """Keep the current occurrence; the append-only audit keeps previous ones."""
    key = f"{order.id}:{stage}"
    row = session.get(OrderStage, key)
    values = dict(staff_username=actor.username, action_id=action_id, device_id=device_id,
                  occurred_at=occurred_at, synced_at=dt.datetime.now(dt.timezone.utc))
    if row is None:
        row = OrderStage(id=key, order_id=order.id, stage=stage, **values)
    else:
        for field, value in values.items():
            setattr(row, field, value)
    session.add(row)
