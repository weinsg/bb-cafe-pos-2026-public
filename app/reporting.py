"""Shared, guest-name-free report records, reconciliation and detailed CSVs."""
import csv
import datetime as dt
import io
import json
from zoneinfo import ZoneInfo
from sqlmodel import select
from .models import OrderStage, SyncReceipt, AuditLog

SGT = ZoneInfo('Asia/Singapore')
STAGES = ('received', 'making', 'ready', 'collected', 'cancelled', 'discarded')


def aware(value):
    return value.replace(tzinfo=dt.timezone.utc) if value and value.tzinfo is None else value


def stamp(value):
    return aware(value).astimezone(SGT).isoformat(timespec='seconds') if value else None


def enrich(session, order, closure, lines, stage_rows=None, receipt_logs=None):
    """Never join by order number alone: those repeat across event days."""
    stages = {r.stage: r for r in (stage_rows if stage_rows is not None else session.exec(select(OrderStage).where(OrderStage.order_id == order.id)).all())}
    times = dict(received=order.created_at, making=order.making_at, ready=order.ready_at,
                 collected=order.collected_at, cancelled=closure.closed_at if closure and order.status == 'cancelled' else None,
                 discarded=closure.closed_at if closure and order.status == 'discarded' else None)
    meta = {}
    for stage in STAGES:
        value = times[stage]
        row = stages.get(stage)
        # Ignore superseded stage attribution after Undo or collection correction.
        valid = row and value and aware(row.occurred_at) == aware(value)
        actor = row.staff_username if valid else closure.staff_username if stage in ('cancelled', 'discarded') and value and closure else None
        meta[stage] = {'at': stamp(value), 'by': actor, 'device_id': row.device_id if valid else None,
                       'action_id': row.action_id if valid else None,
                       'synced_at': stamp(row.synced_at) if valid else None}
    # Older synced actions have an unambiguous order ID in their receipt. Reconstruct only
    # events whose occurrence matches the current stage timestamp; never guess REST actors.
    logs = receipt_logs if receipt_logs is not None else session.exec(select(AuditLog, SyncReceipt).join(SyncReceipt, SyncReceipt.id == AuditLog.id)
                        .where(AuditLog.order_number == order.order_number)).all()
    for log, receipt in logs:
        try:
            result = json.loads(receipt.result)
        except (ValueError, TypeError):
            continue
        if result.get('order_id') != order.id or result.get('applied') is False:
            continue
        for stage, value in times.items():
            if meta[stage]['by'] or not value or aware(value) != aware(receipt.occurred_at):
                continue
            accepted = (stage == 'received' and log.action == 'CREATE_ORDER') or (
                log.action == 'STATUS_CHANGE' and stage in ('making', 'ready', 'collected') and
                (f'resulting status {stage}' in log.details or f"'{stage}'" in log.details))
            if accepted:
                meta[stage].update(by=receipt.staff_username, device_id=receipt.device_id,
                                   action_id=receipt.id, synced_at=stamp(receipt.synced_at))
        if closure and log.action == ('ORDER_DISCARDED' if order.status == 'discarded' else 'ORDER_CANCELLED') and aware(closure.closed_at) == aware(receipt.occurred_at):
            meta[order.status].update(device_id=receipt.device_id, action_id=receipt.id, synced_at=stamp(receipt.synced_at))
    def duration(start, end):
        if not times[start] or not times[end]:
            return None
        seconds = (aware(times[end]) - aware(times[start])).total_seconds()
        return round(seconds / 60, 2) if seconds >= 0 else None
    final = order.status if order.status in ('collected', 'cancelled', 'discarded') else None
    return dict(order_id=order.id, device_id=order.device_id, cups=sum(d.quantity for d in lines),
                stages=meta, received_to_ready_min=duration('received', 'ready'),
                ready_to_collected_min=duration('ready', 'collected'), ready_to_discarded_min=duration('ready', 'discarded'),
                received_to_final_min=duration('received', final) if final else None,
                making_step='Used' if order.making_at else 'Skipped' if order.ready_at and meta['ready']['by'] else 'Not recorded' if order.ready_at else '—')


def summary(orders):
    def tally(rows):
        return dict(orders=len(rows), cups=sum(o.cups for o in rows), amount_cents=sum(o.amount_cents for o in rows))
    groups = {'recorded': orders, 'refunded': [o for o in orders if o.status == 'cancelled' and o.money == 'Refunded on POS'],
              'unpaid': [o for o in orders if o.status == 'cancelled' and o.money == 'Not paid yet'],
              'no_refund': [o for o in orders if o.status == 'cancelled' and o.money == 'Paid, no refund'],
              'discarded': [o for o in orders if o.status == 'discarded'],
              'cancelled': [o for o in orders if o.status == 'cancelled'],
              'final': [o for o in orders if o.counts_in_sales]}
    return {key: tally(rows) for key, rows in groups.items()}


def breakdown(orders):
    groups = {}
    for o in orders:
        category = 'discarded' if o.status == 'discarded' else {'Refunded on POS':'refunded', 'Not paid yet':'unpaid', 'Paid, no refund':'no_refund'}.get(o.money, 'sales')
        for line in o.lines:
            key = (line['item'], line['temperature'], line['milk'], line['unit_price_cents'], category)
            row = groups.setdefault(key, dict(item=line['item'], temperature=line['temperature'], milk=line['milk'],
                unit_price_cents=line['unit_price_cents'], category=category, cups=0, amount_cents=0))
            row['cups'] += line['quantity']
            row['amount_cents'] += line['quantity'] * line['unit_price_cents']
    return list(groups.values())


def details_csv(orders, days, kind, final_summary):
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(['REPORT', kind, 'Original order event dates', ', '.join(days)])
    w.writerow(['Generated at', stamp(dt.datetime.now(dt.timezone.utc)), 'Timezone', 'Asia/Singapore'])
    w.writerow(['Date basis', 'Original order event date, not cancellation/discard date'])
    w.writerow(['Amounts', 'SGD; sum line_total / included_line_sales, not repeated order_total'])
    w.writerow(['Limitations', 'Staff-recorded whole-order refund status; POS transactions not verified; missing historical metadata is Not recorded'])
    w.writerow([])
    w.writerow(['SUMMARY', 'orders', 'cups', 'amount_sgd', 'sales_treatment'])
    totals = summary(orders)
    for key in ('recorded','refunded','unpaid','final','no_refund','discarded','cancelled'):
        t = final_summary[key] if kind == 'complete' else totals[key]
        w.writerow([key, t['orders'], t['cups'], f"{t['amount_cents']/100:.2f}",
                    'Excluded' if key in ('refunded','unpaid') else 'Included subset' if key in ('no_refund','discarded') else ''])
    w.writerow(['period_final_sales', final_summary['final']['orders'], final_summary['final']['cups'], f"{final_summary['final']['amount_cents']/100:.2f}", 'Full selected period, including other statuses'])
    w.writerow([])
    w.writerow(['DRINK BREAKDOWN', 'temperature', 'milk', 'unit_price_sgd', 'category', 'cups', 'amount_sgd'])
    for b in breakdown(orders):
        w.writerow([b['item'], b['temperature'], b['milk'], f"{b['unit_price_cents']/100:.2f}", b['category'], b['cups'], f"{b['amount_cents']/100:.2f}"])
    if kind == 'complete':
        w.writerow([])
        w.writerow(['SALES BY DRINK', 'cups_in_sales', 'sales_sgd', 'discarded_cups_included', 'discarded_value_included'])
        names = sorted({line['item'] for o in orders for line in o.lines})
        for name in names:
            sold = [(o, l) for o in orders if o.counts_in_sales for l in o.lines if l['item'] == name]
            w.writerow([name, sum(l['quantity'] for _, l in sold), f"{sum(l['quantity']*l['unit_price_cents'] for _,l in sold)/100:.2f}",
                sum(l['quantity'] for o,l in sold if o.status=='discarded'), f"{sum(l['quantity']*l['unit_price_cents'] for o,l in sold if o.status=='discarded')/100:.2f}"])
    w.writerow([])
    stage_headers = [f'{s}_{field}' for s in STAGES for field in ('at_sgt','by','device_id','action_id','synced_at_sgt')]
    headers = ['date','order','order_id','line_id','status','device_id','item','temperature','milk','quantity','unit_price','line_total',
               'order_total_repeated_do_not_sum','order_cups_repeated','counted_in_sales','included_line_sales','money']
    if kind != 'discarded': headers += ['reason']
    headers += ['making_step'] + stage_headers + ['received_to_ready_min','ready_to_collected_min','ready_to_discarded_min','received_to_final_min']
    def write_rows(rows, omit_reason=False):
        w.writerow([h for h in headers if not (omit_reason and h == "reason")])
        for o in rows:
            stage_values = []
            for stage in STAGES:
                m = o.stages[stage]
                stage_values += [(m.get(field) or ('Not recorded' if m['at'] else '—')) for field in ('at','by','device_id','action_id','synced_at')]
            for index, line in enumerate(o.lines):
                amount = line['quantity'] * line['unit_price_cents']
                row = [o.event_day,o.order_number,o.order_id,line.get('line_id', index+1),o.status,o.device_id,line['item'],line['temperature'],line['milk'],line['quantity'],
                       f"{line['unit_price_cents']/100:.2f}",f'{amount/100:.2f}',f'{o.amount_cents/100:.2f}',o.cups,'yes' if o.counts_in_sales else 'no',
                       f'{(amount if o.counts_in_sales else 0)/100:.2f}',o.money or 'No cancellation/refund recorded']
                if kind != 'discarded' and not omit_reason: row += [o.reason or '—']
                row += [o.making_step] + stage_values + [getattr(o,k) if getattr(o,k) is not None else '—' for k in ('received_to_ready_min','ready_to_collected_min','ready_to_discarded_min','received_to_final_min')]
                # Spreadsheet-safe text: do not let staff-controlled item names/logins become formulas.
                w.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) and v != '—' else v) for v in row])
    if kind == 'complete':
        for section, rows in [('ALL ORDERS',orders),('CANCELLED',[o for o in orders if o.status=='cancelled']),('DISCARDED',[o for o in orders if o.status=='discarded'])]:
            w.writerow([section]); write_rows(rows, omit_reason=section == "DISCARDED"); w.writerow([])
    else:
        w.writerow(['ORDER DETAILS']); write_rows(orders)
    return out.getvalue()
