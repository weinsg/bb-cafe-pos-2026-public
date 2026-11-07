import React, { useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { QRCodeSVG } from "qrcode.react";
import {
  createLocalOrder,
  db,
  estimateMinutes,
  money,
  ordersAhead,
  surcharges,
  WAITING,
} from "./data";
import { useResource } from "./useResource";
import { Dialog, Empty, Icon, Message } from "./ui";
import { CancelDialog, cancelPlaces } from "./Cancel";

const categories = [
  ["all", "All items"],
  ["coffee", "Coffee"],
  ["non-coffee", "Non-coffee"],
  ["pastry", "Pastries"],
];
const milkLabel = { dairy: "Dairy", oat: "Oat" };
// One line of options, e.g. "Iced · Oat milk". Black drinks show the temperature only.
export function optionText(item) {
  return [
    item.temperature &&
      `${item.temperature[0].toUpperCase()}${item.temperature.slice(1)}`,
    item.milk && item.milk !== "none" && `${milkLabel[item.milk]} milk`,
  ]
    .filter(Boolean)
    .join(" · ");
}
// OnCancel: the order popup also offers Cancel order, like a POS "void".
// saved: the popup right after Place order ("Order saved", Next order).
export function Ticket({ order, onClose, onCancel, saved = false }) {
  const config = useLiveQuery(() => db.settings.get("config"));
  const closedDays = useLiveQuery(() => db.settings.get("closed-days"));
  const pending = useLiveQuery(
    () =>
      db.outbox
        .filter((a) => a.kind === "order.create" && a.payload.id === order.id)
        .count(),
    [order.id],
    0,
  );
  // This order's estimate, from the day's figures and the orders on this tablet.
  const { data: figures } = useResource("/orders/wait-estimate", "wait-estimate");
  const orders = useLiveQuery(() => db.orders.toArray(), [], []);
  const range =
    figures && WAITING.includes(order.status)
      ? estimateMinutes(ordersAhead(order, orders), figures)
      : null;
  const link = `${(config?.guest_base_url || location.origin).replace(/\/$/, "")}/guest/${order.id}`;
  if (closedDays?.value.includes(order.event_day))
    return (
      <Dialog title="This event day is closed" onClose={onClose}>
        <p>
          Guest names have been cleared. Sales records and audit history are
          kept.
        </p>
        <button className="primary full" onClick={onClose}>
          Close
        </button>
      </Dialog>
    );
  return (
    <Dialog title={saved ? "Order saved" : `Order ${order.order_number}`} onClose={onClose}>
      <div className="ticket-confirm">
        <h3>{order.order_number}</h3>
        <p className="ticket-confirm-name">{order.first_name}</p>
        <p className="ticket-confirm-total">
          Bill at POS <strong>{money(order.total_price_cents)}</strong>
        </p>
        <div className="qr-wrap">
          <QRCodeSVG
            value={link}
            size={240}
            level="M"
            marginSize={2}
            title={`Track order ${order.order_number}`}
          />
        </div>
        <p>Scan to track your order</p>
        {range && (
          <p className="ticket-estimate">
            Usually ready in about {range[0]}–{range[1]} min
          </p>
        )}
        {pending > 0 && (
          <Message>
            Saved on this tablet. The link works once it syncs.
          </Message>
        )}
        <div className={`ticket-confirm-actions ${saved && onCancel ? "saved-with-cancel" : ""}`}>
          <a href={link} target="_blank" rel="noreferrer" className="text-link">
            Open guest status ↗
          </a>
          {onCancel && (
            <button className="secondary danger-text" onClick={onCancel}>
              Cancel order…
            </button>
          )}
          {saved ? (
            <button className="primary" onClick={onClose}>
              Next order <Icon name="plus" />
            </button>
          ) : (
            <button className="primary" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
    </Dialog>
  );
}
function OptionButtons({ label, value, options, onChange }) {
  return (
    <div className="option-row" role="group" aria-label={label}>
      <span>{label}</span>
      <div className="option-buttons">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            className={value === o.value ? "selected" : ""}
            aria-pressed={value === o.value}
            disabled={o.disabled}
            onClick={() => onChange(o.value)}
          >
            {o.label}
            {o.note && <small>{o.note}</small>}
          </button>
        ))}
      </div>
    </div>
  );
}
export function OrderScreen({ hidden = false, canCancel = true }) {
  const menu = useLiveQuery(() => db.menu.toArray(), [], []);
  const ingredients = useLiveQuery(() => db.ingredients.toArray(), [], []);
  const config = useLiveQuery(() => db.settings.get("config"));
  const pendingItems = useLiveQuery(
    () => db.outbox.filter((a) => a.kind === "menu.create").count(),
    [],
    0,
  );
  const [category, setCategory] = useState("all");
  const [cart, setCart] = useState([]);
  // The drink whose options are open: a new one (not in the cart yet) or a copy of a cart line.
  const [draft, setDraft] = useState(null);
  const [recent, setRecent] = useState(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ticket, setTicket] = useState(null);
  const [cancelling, setCancelling] = useState(null);
  const addOn = surcharges(config);
  const soldMilk = ingredients.filter((i) => i.is_sold_out).map((i) => i.name);
  const shownCategories = categories.filter(
    ([id]) => id === "all" || menu.some((i) => i.category === id),
  );
  const filtered = menu.filter(
    (i) => category === "all" || i.category === category,
  );
  // Unit price = menu price + Iced/Oat add-ons. Captured when the order is placed.
  const unitPrice = (line) =>
    line.base_cents +
    (line.temperature === "iced" ? addOn.iced : 0) +
    (line.milk === "oat" ? addOn.oat : 0);
  const total = cart.reduce((sum, i) => sum + unitPrice(i) * i.quantity, 0);
  const line = draft?.line;
  const index = line ? cart.findIndex((i) => i.key === line.key) : -1;
  // Toast-style item screen: tapping a drink opens its options; Add to order commits.
  function open(item) {
    setError("");
    const takesMilk = item.takes_milk !== false && item.category !== "pastry";
    const milk = takesMilk
      ? ["dairy", "oat"].find((m) => !soldMilk.includes(m))
      : null;
    if (takesMilk && !milk) {
      setError(
        "Dairy and oat milk are both sold out. Ask the booth lead to update availability.",
      );
      return;
    }
    setDraft({
      isNew: true,
      line: {
        key: crypto.randomUUID(),
        menu_id: item.id,
        item_name: item.name,
        base_cents: item.price_cents,
        takes_milk: takesMilk,
        milk,
        temperature: item.category === "pastry" ? null : "hot",
        quantity: 1,
      },
    });
  }
  function update(field, value) {
    setDraft({ ...draft, line: { ...draft.line, [field]: value } });
  }
  function commit() {
    setCart(
      draft.isNew
        ? [...cart, line]
        : cart.map((i) => (i.key === line.key ? line : i)),
    );
    setRecent(line.key);
    setTimeout(() => setRecent(null), 1500);
    setDraft(null);
  }
  function remove() {
    setCart(cart.filter((i) => i.key !== line.key));
    setDraft(null);
  }
  async function place(event) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      if (draft) throw new Error(`Add or cancel the ${line.item_name} first.`);
      if (!cart.length) throw new Error("Choose at least one item.");
      if (!name.trim()) throw new Error("Enter the guest’s first name.");
      if (
        cart.some(
          (i) =>
            !menu.find((m) => m.id === i.menu_id) ||
            menu.find((m) => m.id === i.menu_id)?.is_sold_out ||
            (i.milk && soldMilk.includes(i.milk)),
        )
      )
        throw new Error(
          "An item or milk option just sold out. Update the ticket before submitting.",
        );
      const order = await createLocalOrder(
        name,
        cart.map((i) => ({
          item_name: i.item_name,
          unit_price_cents: unitPrice(i),
          milk: i.milk,
          temperature: i.temperature,
          quantity: i.quantity,
        })),
      );
      setTicket(order);
      setCart([]);
      setDraft(null);
      setName("");
    } catch (err) {
      setError(`Could not save order: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="pos-order" aria-label="New order" hidden={hidden}>
      <div className="order-layout">
        <section className="menu-section">
          {shownCategories.length > 2 && (
            <div
              className="category-tabs"
              role="group"
              aria-label="Menu categories"
            >
              {shownCategories.map(([id, label]) => (
                <button
                  key={id}
                  className={category === id ? "active" : ""}
                  aria-pressed={category === id}
                  onClick={() => setCategory(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          {filtered.length ? (
            <div className="menu-grid">
              {filtered.map((item) => (
                <button
                  key={item.id}
                  className={`menu-card ${item.is_sold_out ? "sold-out" : ""}`}
                  disabled={item.is_sold_out}
                  onClick={() => open(item)}
                  aria-label={`Add ${item.name}`}
                >
                  <span className="menu-card-name">{item.name}</span>
                  <span className="menu-card-bottom">
                    <strong>{money(item.price_cents)}</strong>
                    {item.is_sold_out && (
                      <span className="sold-out-label">Sold out</span>
                    )}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <Empty title="Menu not loaded">Connect to load the menu.</Empty>
          )}
          {pendingItems > 0 && (
            <Message>New menu items will appear after they sync.</Message>
          )}
          {line && (
            <section className="option-panel" aria-label="Drink options">
              <div className="option-panel-heading">
                <h2>
                  {line.item_name}
                  <small>
                    {draft.isNew
                      ? "New drink"
                      : `Line ${index + 1} of ${cart.length}`}
                  </small>
                </h2>
                {!draft.isNew && (
                  <button
                    type="button"
                    className="remove-line"
                    aria-label={`Remove ${line.item_name}`}
                    onClick={remove}
                  >
                    Remove
                  </button>
                )}
              </div>
              {line.temperature && (
                <OptionButtons
                  label="Temperature"
                  value={line.temperature}
                  onChange={(v) => update("temperature", v)}
                  options={[
                    { value: "hot", label: "Hot" },
                    { value: "iced", label: "Iced", note: `+${money(addOn.iced)}` },
                  ]}
                />
              )}
              {line.takes_milk && (
                <OptionButtons
                  label="Milk"
                  value={line.milk}
                  onChange={(v) => update("milk", v)}
                  options={["dairy", "oat"].map((m) => ({
                    value: m,
                    label: milkLabel[m],
                    disabled: soldMilk.includes(m),
                    note: soldMilk.includes(m)
                      ? "Sold out"
                      : m === "oat"
                        ? `+${money(addOn.oat)}`
                        : "",
                  }))}
                />
              )}
              <div className="option-row" role="group" aria-label="Quantity">
                <span>Quantity</span>
                <div className="stepper">
                  <button
                    type="button"
                    aria-label={`Decrease ${line.item_name} quantity`}
                    disabled={line.quantity === 1}
                    onClick={() => update("quantity", line.quantity - 1)}
                  >
                    −
                  </button>
                  <output>{line.quantity}</output>
                  <button
                    type="button"
                    aria-label={`Increase ${line.item_name} quantity`}
                    disabled={line.quantity >= 20}
                    onClick={() => update("quantity", line.quantity + 1)}
                  >
                    +
                  </button>
                </div>
              </div>
              <div className="option-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setDraft(null)}
                >
                  Cancel
                </button>
                <button type="button" className="primary" onClick={commit}>
                  {draft.isNew ? "Add to order" : "Update"} ·{" "}
                  {money(unitPrice(line) * line.quantity)}
                </button>
              </div>
            </section>
          )}
        </section>
        <aside className="ticket-panel">
          <div className="ticket-header">
            <div>
              <h2>Current order</h2>
            </div>
            <span className="item-count">
              {cart.reduce((sum, i) => sum + i.quantity, 0)} items
            </span>
          </div>
          <form onSubmit={place}>
            <div className="ticket-body">
              <label>
                Guest’s first name
                <input
                  name="first_name"
                  autoComplete="off"
                  maxLength={50}
                  placeholder="e.g. Mei"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <div className="cart-items">
                {cart.length === 0 ? (
                  <Empty title="No items" />
                ) : (
                  cart.map((item) => (
                    <button
                      type="button"
                      key={item.key}
                      className={`cart-line ${item.key === line?.key ? "editing" : ""} ${item.key === recent ? "just-added" : ""}`}
                      aria-pressed={item.key === line?.key}
                      aria-label={`Edit ${item.quantity} ${item.item_name}`}
                      onClick={() => setDraft({ isNew: false, line: { ...item } })}
                    >
                      <span className="cart-line-qty">{item.quantity}×</span>
                      <span>
                        <strong>{item.item_name}</strong>
                        <small>{optionText(item)}</small>
                      </span>
                      <span className="cart-line-price">
                        {money(unitPrice(item) * item.quantity)}
                      </span>
                    </button>
                  ))
                )}
              </div>
              {error && <Message error>{error}</Message>}
            </div>
            <div className="ticket-total">
              <div>
                <span>Order total</span>
                <strong>{money(total)}</strong>
              </div>
              <p className={draft ? "add-first" : ""}>
                {draft
                  ? `Add or cancel the ${line.item_name} first.`
                  : "Key this total into the organiser’s POS."}
              </p>
              <button
                className="primary full"
                disabled={!cart.length || busy || Boolean(draft)}
              >
                {busy ? "Saving order…" : "Place order"}
                <Icon name="arrow" />
              </button>
            </div>
          </form>
        </aside>
      </div>
      {ticket && (
        <Ticket
          order={ticket}
          saved
          onClose={() => setTicket(null)}
          // The Order saved popup offers Cancel too when the setting includes the QR popup.
          onCancel={
            canCancel && cancelPlaces(config).popup
              ? () => {
                  setCancelling(ticket);
                  setTicket(null);
                }
              : undefined
          }
        />
      )}
      {cancelling && <CancelDialog order={cancelling} onClose={() => setCancelling(null)} />}
    </section>
  );
}
