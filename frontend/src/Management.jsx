import React, { useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import {
  api,
  applyClosedDays,
  changeMenu,
  db,
  eventDay,
  money,
  post,
  surcharges,
  synchronize,
  saveSetting,
} from "./data";
import { optionText } from "./Orders";
import { useResource } from "./useResource";
import { Dialog, Empty, Icon, Message } from "./ui";

// Whole cents from a typed S$ price; 1.15 * 100 is 114.999..., so round.
const toCents = (value) => Math.round(Number(value) * 100);
function MenuRow({ item, onError }) {
  const [price, setPrice] = useState((item.price_cents / 100).toFixed(2));
  const [busy, setBusy] = useState(false);
  const [remove, setRemove] = useState(false);
  async function action(kind, payload) {
    setBusy(true);
    try {
      await changeMenu(kind, { id: item.id, ...payload });
      setRemove(false);
    } catch (error) {
      onError(error.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <tr>
        <td>
          <strong>{item.name}</strong>
          <small>
            {item.category.replace("-", " ")}
            {item.takes_milk === false && " · no milk"}
          </small>
        </td>
        <td>
          <form
            className="price-form"
            onSubmit={(e) => {
              e.preventDefault();
              const cents = toCents(price);
              if (!Number.isFinite(cents) || cents < 1)
                onError("Enter a price greater than zero.");
              else void action("menu.update", { price_cents: cents });
            }}
          >
            <span>S$</span>
            <input
              type="number"
              min="0.01"
              max="9999"
              step="0.01"
              required
              aria-label={`Price for ${item.name}`}
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
            <button
              className="secondary"
              disabled={busy || toCents(price) === item.price_cents}
            >
              Save
            </button>
          </form>
        </td>
        <td>
          <button
            className={`stock-toggle ${item.is_sold_out ? "out" : ""}`}
            disabled={busy}
            onClick={() =>
              action("menu.update", { is_sold_out: !item.is_sold_out })
            }
          >
            <span className="status-dot" />
            {item.is_sold_out ? "Sold out" : "Available"}
          </button>
        </td>
        <td>
          <button
            className="text-button danger-text"
            disabled={busy}
            onClick={() => setRemove(true)}
          >
            Remove
          </button>
        </td>
      </tr>
      {remove && (
        <Dialog title={`Remove ${item.name}?`} onClose={() => setRemove(false)}>
          <p>
            This removes the item from future orders. Existing orders and sales
            records are kept.
          </p>
          <div className="dialog-actions">
            <button className="secondary" onClick={() => setRemove(false)}>
              Keep item
            </button>
            <button
              className="danger"
              disabled={busy}
              onClick={() => action("menu.remove", {})}
            >
              Remove item
            </button>
          </div>
        </Dialog>
      )}
    </>
  );
}
function AddOnPrice({ name, label, cents, onError }) {
  const [price, setPrice] = useState((cents / 100).toFixed(2));
  return (
    <form
      className="price-form add-on-price"
      onSubmit={async (e) => {
        e.preventDefault();
        const value = toCents(price);
        if (!Number.isFinite(value) || value < 1)
          return onError("Enter an add-on price greater than zero.");
        try {
          await changeMenu("surcharge.update", { name, price_cents: value });
        } catch (err) {
          onError(err.message);
        }
      }}
    >
      <span>{label} +S$</span>
      <input
        type="number"
        min="0.01"
        max="1000"
        step="0.01"
        required
        aria-label={`${label} add-on price`}
        value={price}
        onChange={(e) => setPrice(e.target.value)}
      />
      <button className="secondary" disabled={toCents(price) === cents}>
        Save
      </button>
    </form>
  );
}
export function MenuScreen() {
  const items = useLiveQuery(() => db.menu.toArray(), [], []);
  const ingredients = useLiveQuery(
    () => db.ingredients.filter((i) => i.name !== "none").toArray(),
    [],
    [],
  );
  const config = useLiveQuery(() => db.settings.get("config"));
  const addOn = surcharges(config);
  const pending = useLiveQuery(
    () => db.outbox.filter((a) => a.kind === "menu.create").toArray(),
    [],
    [],
  );
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function add(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(e.currentTarget);
    const name = form.get("name").trim();
    try {
      if (!name) throw new Error("Enter an item name.");
      if (
        items.some((i) => i.name.toLowerCase() === name.toLowerCase()) ||
        pending.some((a) => a.payload.name.toLowerCase() === name.toLowerCase())
      )
        throw new Error("This item is already on the menu or waiting to sync.");
      await changeMenu("menu.create", {
        name,
        category: form.get("category"),
        price_cents: toCents(form.get("price")),
        takes_milk: form.get("takes_milk") === "yes",
      });
      setAdding(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  // Toast-style quick controls in one toolbar row above the item table.
  return (
    <>
      <div className="toolbar">
        <section className="toolbar-group" aria-labelledby="milk-heading">
          <h2 id="milk-heading">Milk</h2>
          {ingredients.map((i) => (
            <button
              key={i.name}
              className={`ingredient-button ${i.is_sold_out ? "is-out" : ""}`}
              onClick={async () => {
                try {
                  await changeMenu("ingredient.update", {
                    name: i.name,
                    is_sold_out: !i.is_sold_out,
                  });
                } catch (err) {
                  setError(err.message);
                }
              }}
            >
              <span>{`${i.name[0].toUpperCase()}${i.name.slice(1)} milk`}</span>
              <small>
                <span className="status-dot" />
                {i.is_sold_out ? "Sold out" : "Available"}
              </small>
            </button>
          ))}
        </section>
        <section className="toolbar-group" aria-labelledby="add-on-heading">
          <h2 id="add-on-heading">Add-ons</h2>
          <AddOnPrice
            key={`iced:${addOn.iced}`}
            name="iced"
            label="Iced"
            cents={addOn.iced}
            onError={setError}
          />
          <AddOnPrice
            key={`oat:${addOn.oat}`}
            name="oat"
            label="Oat milk"
            cents={addOn.oat}
            onError={setError}
          />
        </section>
        <button
          className="primary toolbar-end"
          onClick={() => {
            setError("");
            setAdding(true);
          }}
        >
          <Icon name="plus" />
          Add menu item
        </button>
      </div>
      {error && !adding && <Message error>{error}</Message>}
      <section className="panel table-panel">
        <div className="panel-heading">
          <h2>Items</h2>
          <span className="muted small">Prices in S$</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Unit price</th>
                <th>Availability</th>
                <th>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <MenuRow
                  key={`${i.id}:${i.price_cents}`}
                  item={i}
                  onError={setError}
                />
              ))}
            </tbody>
          </table>
        </div>
        {!items.length && (
          <Empty title="No menu items" />
        )}
      </section>
      {pending.length > 0 && (
        <Message>
          Waiting to add after sync:{" "}
          {pending.map((a) => a.payload.name).join(", ")}.
        </Message>
      )}
      {adding && (
        <Dialog
          title="Add menu item"
          onClose={() => setAdding(false)}
        >
          <form className="stack-form" onSubmit={add}>
            <label>
              Item name
              <input
                name="name"
                maxLength={80}
                required
                autoFocus
                placeholder="e.g. Hot chocolate"
              />
            </label>
            <label>
              Category
              <select name="category" aria-label="Category">
                <option value="coffee">Coffee</option>
                <option value="non-coffee">Non-coffee</option>
                <option value="pastry">Pastry</option>
              </select>
            </label>
            <label>
              Milk options
              <select name="takes_milk" aria-label="Milk options">
                <option value="yes">Dairy or oat</option>
                <option value="no">No milk (black)</option>
              </select>
            </label>
            <label>
              Price (S$)
              <input
                name="price"
                type="number"
                min="0.01"
                max="9999"
                step="0.01"
                required
                placeholder="6.00"
              />
            </label>
            {error && <Message error>{error}</Message>}
            <button className="primary full" disabled={busy}>
              {busy ? "Saving…" : "Add item"}
            </button>
          </form>
        </Dialog>
      )}
    </>
  );
}
// Views and exports for the owner. No guest names anywhere; staff logins are shown.
const REPORT_VIEWS = [
  ["sold", "Sold"],
  ["cancelled", "Cancelled"],
  ["discarded", "Discarded"],
  ["all", "All orders"],
];
const MONEY_FILTERS = [
  ["", "All"],
  ["Refunded on POS", "Refunded on POS"],
  ["Not paid yet", "Not paid yet"],
  ["Paid, no refund", "Paid, no refund"],
];
const CSV_KINDS = [
  ["sales", "Sales by item", "sales"],
  ["cancelled", "Cancelled orders", "cancelled"],
  ["discarded", "Discarded drinks", "discarded"],
  ["all", "All orders", "all_orders"],
];
const STATUS_TEXT = {
  received: "Received",
  making: "Making",
  ready: "Ready",
  collected: "Collected",
  cancelled: "Cancelled",
  discarded: "Discarded",
};

export function ReportScreen() {
  const [day, setDay] = useState(eventDay);
  const [scope, setScope] = useState("day");
  const [view, setView] = useState("sold");
  const [moneyFilter, setMoneyFilter] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const scopeQuery = scope === "all" ? "&scope=all" : "";
  const resource = useResource(`/reports/daily?event_day=${day}${scopeQuery}`);
  const orders = useResource(view !== "sold" ? `/reports/orders?kind=${view}&event_day=${day}${scopeQuery}` : null);
  const label = scope === "all" ? "all event days" : day;
  async function download(kind = "sales") {
    setMenuOpen(false);
    setExporting(true);
    setError("");
    try {
      await synchronize(true);
      if (await db.outbox.count())
        throw new Error(
          "Sync saved changes first so the export includes every order.",
        );
      const blob = await api(`/reports/csv?kind=${kind}&event_day=${day}${scopeQuery}`, {
        blob: true,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const file = CSV_KINDS.find(([k]) => k === kind)[2];
      link.download = `beansbeats_${file}_${scope === "all" ? "all-days" : day}.csv`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err.message);
    } finally {
      setExporting(false);
    }
  }
  const report = resource.data;
  const list = Array.isArray(orders.data?.orders)
    ? orders.data.orders.filter((o) => view !== "cancelled" || !moneyFilter || o.money === moneyFilter)
    : [];
  const segmented = (options, value, onPick, name) => (
    <div className="segmented report-segmented" role="group" aria-label={name}>
      {options.map(([key, text]) => (
        <button
          type="button"
          key={key}
          aria-pressed={value === key}
          className={value === key ? "active" : ""}
          onClick={() => onPick(key)}
        >
          {text}
        </button>
      ))}
    </div>
  );
  // ForkStack-style day close: one toolbar row, one summary strip, then the tables.
  return (
    <>
      <div className="toolbar report-toolbar">
        <label>
          Event day
          <input
            type="date"
            value={day}
            required
            disabled={scope === "all"}
            onChange={(e) => {
              if (e.target.value) setDay(e.target.value);
            }}
          />
        </label>
        {segmented(
          [
            ["day", "This day"],
            ["all", "All event days"],
          ],
          scope,
          setScope,
          "Report days",
        )}
        {segmented(REPORT_VIEWS, view, setView, "Show")}
        {!resource.connected && (
          <span className="muted">
            {report
              ? "Showing the last saved report."
              : "Connecting to load the report…"}
          </span>
        )}
        <div className="export-menu toolbar-end">
          <button
            className="primary"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            disabled={exporting || !resource.connected}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Icon name="report" />
            {exporting ? "Exporting…" : "Export CSV ▾"}
          </button>
          {menuOpen && (
            <div className="export-options" role="menu">
              {CSV_KINDS.map(([kind, text]) => (
                <button type="button" role="menuitem" key={kind} onClick={() => download(kind)}>
                  {text}
                  <small>{label}</small>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      {(error || resource.error) && (
        <Message error={Boolean(error)}>{error || resource.error}</Message>
      )}
      {report && (
        <>
          <div className="report-strip">
            <dl>
              <div className="strip-main">
                <dt>Total recorded sales</dt>
                <dd>{money(report.total_revenue_cents)}</dd>
              </div>
              <div>
                <dt>Cups sold</dt>
                <dd>{report.total_cups}</dd>
              </div>
              <div>
                <dt>Iced</dt>
                <dd>{report.iced_cups ?? 0}</dd>
              </div>
              <div>
                <dt>Oat milk</dt>
                <dd>{report.oat_cups ?? 0}</dd>
              </div>
              {/* Stories 15 and 16: cancelled and discarded orders. */}
              {/* These tiles open their view. */}
              <div>
                <dt>Cancelled</dt>
                <dd>
                  <button type="button" className="tile-link" onClick={() => setView("cancelled")}>
                    {report.cancelled_orders ?? 0}
                    {report.refunded_cents > 0 && <small> · {money(report.refunded_cents)} refunded</small>}
                  </button>
                </dd>
              </div>
              <div>
                <dt>Discarded</dt>
                <dd>
                  <button type="button" className="tile-link" onClick={() => setView("discarded")}>
                    {report.discarded_orders ?? 0}
                  </button>
                </dd>
              </div>
            </dl>
            <p className="strip-note">
              Includes add-ons and discarded drinks (paid). Leaves out cancelled
              orders that were refunded or not paid. Compare with the POS total
              for the day.
            </p>
          </div>
          {view !== "sold" && (
            <OrderList
              view={view}
              data={orders.data}
              list={list}
              moneyFilter={moneyFilter}
              setMoneyFilter={setMoneyFilter}
              segmented={segmented}
              error={orders.error}
            />
          )}
          {view === "sold" && (
          <div className="report-tables">
            {report.drinks?.length > 0 && (
              <section className="panel table-panel">
                <div className="panel-heading">
                  <h2>By drink</h2>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Drink</th>
                        <th>Cups</th>
                        <th>Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.drinks.map((d) => (
                        <tr key={d.item_name}>
                          <td>
                            <strong>{d.item_name}</strong>
                          </td>
                          <td>{d.cups_sold}</td>
                          <td>{money(d.total_revenue_cents)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
            <section className="panel table-panel">
              <div className="panel-heading">
                <h2>By item</h2>
                <span className="muted small">{label}</span>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Menu item</th>
                      <th>Options</th>
                      <th>Quantity</th>
                      <th>Unit price</th>
                      <th>Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.items.map((i, index) => (
                      <tr key={index}>
                        <td>
                          <strong>{i.item_name}</strong>
                        </td>
                        <td>{optionText(i)}</td>
                        <td>{i.cups_sold}</td>
                        <td>{money(i.unit_price_cents)}</td>
                        <td>
                          <strong>{money(i.total_revenue_cents)}</strong>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td>Total</td>
                      <td />
                      <td>{report.total_cups}</td>
                      <td />
                      <td>{money(report.total_revenue_cents)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              {!report.items.length && (
                <Empty title="No sales recorded for this day" />
              )}
            </section>
          </div>
          )}
        </>
      )}
      <p className="footnote">
        Payment stays on the organiser’s POS. Discounts and refunds entered
        there are not reflected here. The CSV contains no guest names.
      </p>
    </>
  );
}
// Cancelled (with the refund tally and final amount), Discarded and All orders lists.
function OrderList({ view, data, list, moneyFilter, setMoneyFilter, segmented, error }) {
  const columns = {
    cancelled: ["Date", "Time", "Order", "Drinks", "Amount", "Reason", "Money", "By"],
    discarded: ["Date", "Time", "Order", "Drinks", "Amount", "Waited", "By"],
    all: ["Date", "Time", "Order", "Status", "Drinks", "Amount", "In sales"],
  }[view];
  return (
    <section className="panel table-panel report-orders">
      {view === "cancelled" && (
        <div className="money-filter">
          <strong>Money</strong>
          {segmented(MONEY_FILTERS, moneyFilter, setMoneyFilter, "Money")}
        </div>
      )}
      {view === "cancelled" && data && (
        <dl className="cancel-tally">
          <div>
            <dt>Refunded on POS</dt>
            <dd>{money(data.refunded_cents)}</dd>
          </div>
          <div>
            <dt>Not paid yet</dt>
            <dd>{money(data.not_paid_cents)}</dd>
          </div>
          <div>
            <dt>Paid, no refund (in sales)</dt>
            <dd>{money(data.no_refund_cents)}</dd>
          </div>
          <div className="tally-final">
            <dt>Final sales (matches the POS)</dt>
            <dd>{money(data.final_sales_cents)}</dd>
          </div>
        </dl>
      )}
      {error && <Message>{error}</Message>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {list.map((o) => (
              <tr key={`${o.event_day}-${o.order_number}`}>
                <td>{o.event_day}</td>
                <td>{view === "all" ? o.time : o.closed_time || o.time}</td>
                <td>
                  <strong>{o.order_number}</strong>
                </td>
                {view === "all" && <td>{STATUS_TEXT[o.status] || o.status}</td>}
                <td>{o.drinks}</td>
                <td>{money(o.amount_cents)}</td>
                {view === "cancelled" && (
                  <>
                    <td>{o.reason}</td>
                    <td>{o.money}</td>
                    <td>{o.by}</td>
                  </>
                )}
                {view === "discarded" && (
                  <>
                    <td>{o.waited_min == null ? "—" : `${o.waited_min} min`}</td>
                    <td>{o.by}</td>
                  </>
                )}
                {view === "all" && <td>{o.counts_in_sales ? "Yes" : "No"}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && !list.length && <Empty title="Nothing to show for these choices" />}
    </section>
  );
}
function time(value) {
  return value
    ? new Date(
        value.endsWith("Z") || /[+-]\d\d:\d\d$/.test(value)
          ? value
          : `${value}Z`,
      ).toLocaleString("en-SG", {
        timeZone: "Asia/Singapore",
        dateStyle: "medium",
        timeStyle: "medium",
      })
    : "—";
}
export function AuditScreen() {
  const { data: logs, error, connected } = useResource("/admin/audit-logs");
  return (
    <>
      {error && (
        <Message>
          {connected
            ? error
            : `${error} Showing saved records where available.`}
        </Message>
      )}
      <section className="panel table-panel">
        <div className="panel-heading">
          <h2>Audit trail</h2>
          <span className="muted small">
            Staff actions only. Guest names are never recorded.
          </span>
          <span className="pill received">Read only</span>
        </div>
        <div className="table-scroll">
          <table className="audit-table">
            <thead>
              <tr>
                <th>When / staff</th>
                <th>Action</th>
                <th>Details</th>
                <th>Device / synced</th>
              </tr>
            </thead>
            <tbody>
              {logs?.map((log) => (
                <tr key={log.id}>
                  <td>
                    <strong>{time(log.occurred_at || log.timestamp)}</strong>
                    <small>{log.staff_username}</small>
                  </td>
                  <td>
                    <span className="audit-action">
                      {log.action.replaceAll("_", " ")}
                    </span>
                    {log.order_number && <small>{log.order_number}</small>}
                  </td>
                  <td>{log.details}</td>
                  <td>
                    <strong>{log.device_id || "Server"}</strong>
                    <small>{time(log.synced_at || log.timestamp)}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!logs?.length && (
          <Empty
            title={
              logs ? "No audit records" : "Loading audit trail…"
            }
          />
        )}
      </section>
    </>
  );
}
export function ResetScreen() {
  const previous = new Date(`${eventDay()}T12:00:00Z`);
  previous.setUTCDate(previous.getUTCDate() - 1);
  const [day, setDay] = useState(previous.toISOString().slice(0, 10));
  const [confirm, setConfirm] = useState(false);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const pending = useLiveQuery(() => db.outbox.count(), [], 0);
  async function reset() {
    setBusy(true);
    setError("");
    try {
      if (await db.outbox.count())
        throw new Error(
          "Sync all changes on this tablet before closing the day.",
        );
      const result = await saveSetting("/admin/reset-day", "POST", {
        closed_event_day: day,
        confirm: true,
      });
      const state = await api("/sync/state");
      await applyClosedDays(state.closed_days);
      await synchronize();
      setSuccess(
        `${day} is closed. ${result.names_sanitized} guest names cleared; ${result.queue_orders_cleared} open orders removed from the queue.`,
      );
      setConfirm(false);
      setChecked(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="panel reset-panel">
        <h2>Close an event day</h2>
        <p>
          Make sure every tablet has synced before continuing. Closing a day
          removes its orders from the queue and ready board, and clears their
          guest first names.
        </p>
        <p>
          Sales totals and the audit trail are kept. Orders use a new number
          sequence on the next event day.
        </p>
        <label>
          Day to close
          <input
            type="date"
            required
            max={eventDay()}
            value={day}
            onChange={(e) => setDay(e.target.value)}
          />
        </label>
        <Message>
          Closing today also stops new orders for today. Delayed offline orders
          from a closed day will be rejected and their names cleared on
          reconnection.
        </Message>
        {pending > 0 && (
          <Message error>
            {pending} changes on this tablet still need to sync.
          </Message>
        )}
        {error && <Message error>{error}</Message>}
        {success && <Message>{success}</Message>}
        <button
          className="danger"
          disabled={!day || pending > 0}
          onClick={() => {
            setChecked(false);
            setError("");
            setConfirm(true);
          }}
        >
          Review day reset <Icon name="arrow" />
        </button>
      </section>
      {confirm && (
        <Dialog
          title={`Close ${day}?`}
          onClose={() => {
            if (!busy) setConfirm(false);
          }}
        >
          <p>
            Ask the other staff to confirm their tablets show “Synced”. Guest
            names for this day will be cleared from the server and from
            connected browsers.
          </p>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
            />
            All tablets are synced. I understand this cannot be undone.
          </label>
          {error && <Message error>{error}</Message>}
          <div className="dialog-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setConfirm(false)}
            >
              Cancel
            </button>
            <button
              className="danger"
              disabled={!checked || busy}
              onClick={reset}
            >
              {busy ? "Closing day…" : "Close day and clear names"}
            </button>
          </div>
        </Dialog>
      )}
    </>
  );
}
