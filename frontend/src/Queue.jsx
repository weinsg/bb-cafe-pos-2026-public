import React, { useEffect, useRef, useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { changeStatus, db, eventDay, isActive, reopenCollected } from "./data";
import { useResource } from "./useResource";
import { Dialog, Empty, Icon, Message } from "./ui";
import { optionText, Ticket } from "./Orders";
import { CANCEL_MONEY, CANCEL_REASONS, CancelDialog, cancelPlaces, label } from "./Cancel";

export const parseTime = (value) =>
  new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);

function timestamp(value) {
  if (!value) return "—";
  return parseTime(value).toLocaleTimeString("en-SG", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "Asia/Singapore",
  });
}

export function QueueScreen({ canViewCompleted, canReopen, canCancel }) {
  const orders = useLiveQuery(
    () => db.orders.orderBy("created_at").toArray(),
    [],
    [],
  );
  const closedDays = useLiveQuery(() => db.settings.get("closed-days"));
  // Booth setting: with Making off, Received goes straight to Ready.
  const config = useLiveQuery(() => db.settings.get("config"));
  const useMaking = config?.use_making_step !== false;
  // How long a Ready drink has waited.
  const reminders = config?.reminders !== false;
  const warnMinutes = config?.warn_minutes ?? 30;
  const discardMinutes = config?.discard_minutes ?? 60;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 20000);
    return () => clearInterval(timer);
  }, []);
  const waited = (order) =>
    order.status === "ready" && order.ready_at
      ? Math.floor((now - parseTime(order.ready_at)) / 60000)
      : 0;
  const lateness = (order) => {
    if (!reminders) return "";
    const minutes = waited(order);
    return minutes >= discardMinutes ? "late" : minutes >= warnMinutes ? "warn" : "";
  };
  const [cancel, setCancel] = useState(null);
  // Cancel order in the QR popup, below the main button, or both (Settings → Queue steps).
  const places = cancelPlaces(config);
  const [discard, setDiscard] = useState(null);
  const [day, setDay] = useState(eventDay);
  // Today's figures for "New order: about X–Y min" in the filter bar.
  const { data: figures } = useResource("/orders/wait-estimate", "wait-estimate");
  const history = useResource(
    canViewCompleted ? `/orders/completed?event_day=${day}` : null,
  );
  // The Cancelled tab, with each cancel's reason, money status, who and when.
  const cancelledHistory = useResource(
    canViewCompleted ? `/orders/cancelled?event_day=${day}` : null,
  );
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(new Set());
  const [ticket, setTicket] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [reopen, setReopen] = useState(null);
  const [reason, setReason] = useState("accidental_tap");
  useEffect(() => {
    if (!canViewCompleted && ["collected", "cancelled"].includes(filter)) setFilter("all");
  }, [canViewCompleted, filter]);
  const active = orders.filter(isActive);
  const merged = new Map(
    (Array.isArray(history.data) ? history.data : []).map((o) => [o.id, o]),
  );
  for (const order of orders) {
    const remote = merged.get(order.id);
    if (
      !remote ||
      (order.status_revision || 0) >= (remote.status_revision || 0)
    )
      merged.set(order.id, order);
  }
  const completed = [...merged.values()]
    .filter((o) => o.status === "collected" && o.event_day === day)
    .sort((a, b) =>
      (b.collected_at || b.created_at).localeCompare(
        a.collected_at || a.created_at,
      ),
    );
  // Cancelled tab: the server's records (reason, money, who), plus cancels still waiting to sync.
  const cancelledRemote = new Map(
    (Array.isArray(cancelledHistory.data) ? cancelledHistory.data : []).map((o) => [o.id, o]),
  );
  const cancelled = [
    ...cancelledRemote.values(),
    ...orders.filter(
      (o) => o.status === "cancelled" && o.event_day === day && !cancelledRemote.has(o.id),
    ),
  ].sort((a, b) =>
    (b.cancelled_at || b.created_at).localeCompare(a.cancelled_at || a.created_at),
  );
  // "Late" = Ready for at least the first reminder time (orange and red tickets).
  const late = (o) => o.status === "ready" && reminders && waited(o) >= warnMinutes;
  const groups = {
    all: [...active.filter((o) => !late(o)), ...active.filter(late)],
    received: active.filter((o) => o.status === "received"),
    making: active.filter((o) => o.status === "making"),
    ready: active.filter((o) => o.status === "ready" && !late(o)),
    late: active.filter(late),
    collected: canViewCompleted ? completed : [],
    cancelled: canViewCompleted ? cancelled : [],
  };
  const shown = groups[filter] || groups.all;
  const history_tab = filter === "collected" || filter === "cancelled";
  const next = {
    received: useMaking ? ["making", "Start making"] : ["ready", "Mark ready"],
    making: ["ready", "Mark ready"],
    ready: ["collected", "Collected"],
  };
  async function advance(order) {
    if (busy.has(order.id)) return;
    setBusy((b) => new Set(b).add(order.id));
    setError("");
    try {
      await changeStatus(order, next[order.status][0]);
      setConfirm(null);
    } catch (err) {
      setError(`Could not save status: ${err.message}`);
    } finally {
      setBusy((b) => {
        const copy = new Set(b);
        copy.delete(order.id);
        return copy;
      });
    }
  }
  // Type 012 (or A012) and Enter to mark that order Ready to collect.
  const [readyNo, setReadyNo] = useState("");
  const [notice, setNotice] = useState("");
  const numberBox = useRef(null);
  async function readyByNumber(text) {
    const typed = text.trim().toUpperCase();
    setReadyNo("");
    setError("");
    setNotice("");
    if (!typed) return;
    const digitsOnly = /^\d{1,3}$/.test(typed);
    const same = (o) =>
      digitsOnly ? o.order_number.slice(-3) === typed.padStart(3, "0") : o.order_number.toUpperCase() === typed;
    const matches = orders.filter((o) => o.event_day === eventDay() && same(o));
    const open = matches.filter(isActive);
    if (open.length > 1)
      return setError(
        `${open.map((o) => o.order_number).join(" and ")} both end in ${typed.padStart(3, "0")}: type the letter too, e.g. ${open[0].order_number}.`,
      );
    const [order] = open;
    if (!order)
      return setError(
        matches.length
          ? `${matches[0].order_number} is ${matches[0].status === "collected" ? "already collected" : matches[0].status}; nothing changed.`
          : `No order ${digitsOnly ? typed.padStart(3, "0") : typed} today.`,
      );
    if (order.status === "ready") return setNotice(`${order.order_number} is already Ready to collect.`);
    try {
      await changeStatus(order, "ready");
      setNotice(`${order.order_number} ${order.first_name || ""} → Ready to collect`.replace("  ", " "));
    } catch (err) {
      setError(`Could not save status: ${err.message}`);
    }
  }
  const readyNoRef = useRef("");
  readyNoRef.current = readyNo;
  useEffect(() => {
    // Typing on a keyboard while the Queue is open goes into the Ready # box.
    function onKey(event) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (document.querySelector("dialog[open]")) return;
      const field = event.target.closest?.("input, textarea, select, [contenteditable]");
      if (field) return;
      if (/^[0-9]$/.test(event.key) || (/^[abAB]$/.test(event.key) && !readyNoRef.current)) {
        event.preventDefault();
        setReadyNo((v) => (v + event.key).slice(0, 4));
        numberBox.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  async function close(order, status, extra) {
    setBusy((b) => new Set(b).add(order.id));
    setError("");
    try {
      await changeStatus(order, status, extra);
      setCancel(null);
      setDiscard(null);
    } catch (err) {
      setError(`Could not save: ${err.message}`);
    } finally {
      setBusy(new Set());
    }
  }
  async function correctCollection() {
    setBusy((b) => new Set(b).add(reopen.id));
    setError("");
    try {
      await reopenCollected(reopen, reason);
      setReopen(null);
      setFilter("ready");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(new Set());
    }
  }
  // Late (Ready 30+ min) tab; Cancelled after Completed; no To make tab. With the Making step
  // Off, the Making tab hides unless orders are still in Making.
  const showMaking = useMaking || groups.making.length > 0;
  useEffect(() => {
    if (!showMaking && filter === "making") setFilter("all");
  }, [showMaking, filter]);
  const tabs = [
    ["all", "Active"],
    ["received", "Received"],
    ...(showMaking ? [["making", "Making"]] : []),
    ["ready", "Ready"],
    ...(reminders ? [["late", `Late ${warnMinutes}+ min`]] : []),
    ...(canViewCompleted ? [["collected", "Completed"], ["cancelled", "Cancelled"]] : []),
  ];
  return (
    <>
      <div
        className="category-tabs queue-tabs"
        role="group"
        aria-label="Filter queue"
      >
        {tabs.map(([id, text]) => (
          <button
            key={id}
            aria-pressed={filter === id}
            className={filter === id ? "active" : ""}
            onClick={() => {
              setFilter(id);
              setError("");
              setNotice("");
            }}
          >
            {text}
            <span>{groups[id].length}</span>
          </button>
        ))}
        <form
          className="ready-by-number"
          onSubmit={(event) => {
            event.preventDefault();
            void readyByNumber(readyNo);
          }}
        >
          <input
            ref={numberBox}
            value={readyNo}
            onChange={(e) => setReadyNo(e.target.value.slice(0, 4))}
            placeholder="Ready #"
            aria-label="Mark ready by order number"
            aria-describedby="ready-hint"
            inputMode="numeric"
            autoComplete="off"
          />
          {/* Beside the box on wide screens; under it while focused on narrow ones. */}
          <span className="ready-hint" id="ready-hint">
            <span>← Type 3 digits + Enter</span>
            <span>A112 → 112 = Ready</span>
          </span>
        </form>
        {figures && (
          <span className="queue-estimate" role="status">
            <span>
              New order: about {figures.new_order_low_min}–{figures.new_order_high_min} min
            </span>
            {figures.average_wait_min != null && <span>Avg wait today: {figures.average_wait_min} min</span>}
          </span>
        )}
      </div>
      {history_tab && canViewCompleted && (
        <div className="filter-row">
          <label>
            {filter === "cancelled" ? "Cancelled on" : "Completed on"}
            <input
              type="date"
              value={day}
              max={eventDay()}
              required
              onChange={(e) => {
                if (e.target.value) setDay(e.target.value);
              }}
            />
          </label>
          {!history.connected && (
            <span className="muted small">
              Showing saved orders while reconnecting.
            </span>
          )}
        </div>
      )}
      {error && !confirm && !reopen && !cancel && !discard && (
        <Message error>{error}</Message>
      )}
      {notice && !error && <Message>{notice}</Message>}
      {filter === "collected" && history.error && (
        <Message>{history.error}</Message>
      )}
      <div className="queue-grid">
        {shown.map((order) => (
          <article
            className={`queue-card status-${order.status} ${lateness(order) ? `wait-${lateness(order)}` : ""}`}
            key={order.id}
          >
            <header className={isActive(order) ? "" : "no-qr"}>
              <div className="queue-card-title">
                <span className="order-number">{order.order_number}</span>
                <h2>{order.first_name || "Name cleared"}</h2>
              </div>
              {isActive(order) && (
                <button
                  className="qr-button"
                  aria-label={`Guest QR code for ${order.order_number}`}
                  onClick={() => setTicket(order)}
                >
                  <Icon name="qr" size={22} />
                </button>
              )}

              <div className="queue-card-meta">
                <span className={`pill ${order.status}`}>
                  {/* "Done" fits beside the QR button; the tab keeps "Completed". */}
                  {order.status === "collected" ? "Done" : order.status}
                </span>
                <time>
                  {lateness(order)
                    ? `Since ${timestamp(order.ready_at)}`
                    : order.status === "cancelled"
                    ? `Cancelled ${timestamp(order.cancelled_at || order.created_at)}`
                    : order.status !== "collected"
                    ? timestamp(order.created_at)
                    : order.collected_at
                      ? `Collected ${timestamp(order.collected_at)}`
                      : "Closed during day reset"}
                </time>
              </div>
            </header>
            <ul>
              {order.items.map((item, n) => (
                <li key={n}>
                  <strong>
                    {item.quantity} × {item.item_name}
                  </strong>
                  {optionText(item) && <span>{optionText(item)}</span>}
                </li>
              ))}
            </ul>
            {isActive(order) ? (
              <div className="ticket-actions">
                <button
                  className="primary full"
                  disabled={busy.has(order.id)}
                  onClick={() =>
                    order.status === "ready"
                      ? (setError(""), setConfirm(order))
                      : advance(order)
                  }
                >
                  {next[order.status][1]}
                  <Icon name="check" />
                </button>
                {lateness(order) && (
                  <button
                    className="danger discard-button"
                    disabled={busy.has(order.id)}
                    onClick={() => {
                      setError("");
                      setDiscard(order);
                    }}
                  >
                    Discard
                  </button>
                )}
                {canCancel && places.below && (
                  <button
                    className="secondary danger-text full cancel-below"
                    disabled={busy.has(order.id)}
                    onClick={() => {
                      setError("");
                      setCancel(order);
                    }}
                  >
                    Cancel order
                  </button>
                )}
              </div>
            ) : order.status === "cancelled" ? (
              <p className="cancel-record">
                {order.reason
                  ? `${label(CANCEL_REASONS, order.reason)} · ${label(CANCEL_MONEY, order.money)} · by ${order.cancelled_by}`
                  : "Cancelled on this tablet; details appear after sync"}
              </p>
            ) : order.status !== "collected" ? (
              <p className="footnote">Discarded</p>
            ) : canReopen && !closedDays?.value.includes(order.event_day) ? (
              <button
                className="secondary full"
                onClick={() => {
                  setError("");
                  setReason("accidental_tap");
                  setReopen(order);
                }}
              >
                Reopen to Ready
              </button>
            ) : (
              <p className="footnote">
                {closedDays?.value.includes(order.event_day)
                  ? "This event day is closed."
                  : "Ask a Booth Lead or Admin to correct an accidental collection."}
              </p>
            )}
          </article>
        ))}
      </div>
      {!shown.length && (
        <Empty
          title={
            filter === "collected"
              ? "No completed orders for this day"
              : filter === "cancelled"
                ? "No cancelled orders for this day"
                : "No orders"
          }
        />
      )}
      {!canViewCompleted && (
        <p className="footnote">
          Completed-order access is disabled for your account. A Booth Lead or
          Admin can enable it in Staff access.
        </p>
      )}
      {ticket && (
        <Ticket
          order={ticket}
          onClose={() => setTicket(null)}
          // Cancel in the popup when the setting includes the QR popup.
          onCancel={
            canCancel && places.popup && isActive(ticket)
              ? () => {
                  setError("");
                  setCancel(ticket);
                  setTicket(null);
                }
              : undefined
          }
        />
      )}
      {confirm && (
        <Dialog
          title={`Confirm collection ${confirm.order_number}`}
          onClose={() => {
            if (!busy.size) setConfirm(null);
          }}
        >
          <p>
            Confirm the guest has received this order. It will move to
            Completed.
          </p>
          {error && <Message error>{error}</Message>}
          <div className="dialog-actions">
            <button
              className="secondary"
              disabled={busy.size > 0}
              onClick={() => setConfirm(null)}
            >
              Keep ready
            </button>
            <button
              className="primary"
              disabled={busy.size > 0}
              onClick={() => advance(confirm)}
            >
              Confirm collected
            </button>
          </div>
        </Dialog>
      )}
      {cancel && <CancelDialog order={cancel} onClose={() => setCancel(null)} />}
      {discard && (
        <Dialog
          title={`Discard ${discard.order_number}`}
          onClose={() => {
            if (!busy.size) setDiscard(null);
          }}
        >
          <p>
            This drink has waited {waited(discard)} minutes. Discard it for
            hygiene? The guest's page will say it was discarded. It stays in
            today's sales.
          </p>
          {error && <Message error>{error}</Message>}
          <div className="dialog-actions">
            <button
              className="secondary"
              disabled={busy.size > 0}
              onClick={() => setDiscard(null)}
            >
              Keep ready
            </button>
            <button
              className="danger"
              disabled={busy.size > 0}
              onClick={() => close(discard, "discarded", {})}
            >
              Discard drink
            </button>
          </div>
        </Dialog>
      )}
      {reopen && (
        <Dialog
          title={`Correct collection ${reopen.order_number}`}
          onClose={() => {
            if (!busy.size) setReopen(null);
          }}
        >
          <p>
            Return this order to Ready and the public ready board. The
            correction and your staff login are recorded. Older offline updates
            cannot override the correction.
          </p>
          <label>
            Reason
            <select
              aria-label="Correction reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            >
              <option value="accidental_tap">
                Collected tapped by mistake
              </option>
              <option value="guest_not_collected">
                Guest has not collected
              </option>
            </select>
          </label>
          {error && <Message error>{error}</Message>}
          <div className="dialog-actions">
            <button
              className="secondary"
              disabled={busy.size > 0}
              onClick={() => setReopen(null)}
            >
              Cancel
            </button>
            <button
              className="primary"
              disabled={busy.size > 0}
              onClick={correctCollection}
            >
              {busy.size ? "Correcting…" : "Confirm reopen"}
            </button>
          </div>
        </Dialog>
      )}
    </>
  );
}
