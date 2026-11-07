import React, { useState } from "react";
import { changeStatus } from "./data";
import { Dialog, Message } from "./ui";

// Required reason and money status for a cancel.
export const CANCEL_REASONS = [
  ["keyed_wrong", "Keyed wrong (drink or options)"],
  ["duplicate", "Duplicate order"],
  ["changed_mind", "Guest changed their mind"],
  ["guest_left", "Guest left"],
  ["ran_out", "Drink or milk ran out"],
  ["other", "Other"],
];
export const CANCEL_MONEY = [
  ["refunded", "Refunded on the POS"],
  ["not_paid", "Not paid yet"],
  ["no_refund", "Paid, no refund"],
];
export const label = (list, value) => list.find(([key]) => key === value)?.[1] || value || "—";

// Where Cancel order shows ("popup", "below" or "both"; default the QR popup).
export function cancelPlaces(config) {
  const place = config?.cancel_place || "popup";
  return { popup: place !== "below", below: place !== "popup" };
}

// Shared by the Queue (ticket button and order popup) and the Order saved popup on New order.
export function CancelDialog({ order, onClose, onDone }) {
  const [reason, setReason] = useState("");
  const [money, setMoney] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function confirm() {
    setBusy(true);
    setError("");
    try {
      await changeStatus(order, "cancelled", { reason, money });
      (onDone || onClose)();
    } catch (err) {
      setError(`Could not save: ${err.message}`);
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={`Cancel order ${order.order_number}`}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <fieldset className="choice-list">
        <legend>Why?</legend>
        {CANCEL_REASONS.map(([value, text]) => (
          <label key={value}>
            <input
              type="radio"
              name="cancel-reason"
              value={value}
              checked={reason === value}
              onChange={() => setReason(value)}
            />
            {text}
          </label>
        ))}
      </fieldset>
      <fieldset className="choice-list">
        <legend>Money</legend>
        {CANCEL_MONEY.map(([value, text]) => (
          <label key={value}>
            <input
              type="radio"
              name="cancel-money"
              value={value}
              checked={money === value}
              onChange={() => setMoney(value)}
            />
            {text}
          </label>
        ))}
      </fieldset>
      {error && <Message error>{error}</Message>}
      <div className="dialog-actions">
        <button className="secondary" disabled={busy} onClick={onClose}>
          Keep order
        </button>
        <button className="danger" disabled={busy || !reason || !money} onClick={confirm}>
          Cancel order
        </button>
      </div>
    </Dialog>
  );
}
