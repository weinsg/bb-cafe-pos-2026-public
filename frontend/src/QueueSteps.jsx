import React, { useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { db, saveSetting, synchronize } from "./data";
import { Message } from "./ui";

const choices = [
  [true, "Use Making step", "Received → Making → Ready. Two taps: Start making, then Mark ready."],
  [false, "Skip Making", "Received → Ready. One tap: Mark ready. The ready board shows the order when it is Ready."],
];

// Booth Lead or Admin switches the optional Making step for every tablet.
export function QueueStepsScreen() {
  const config = useLiveQuery(() => db.settings.get("config"));
  const saved = config?.use_making_step !== false;
  const [choice, setChoice] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const selected = choice ?? saved;
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setError("");
    try {
      const result = await saveSetting("/admin/queue-settings", "PATCH", {
        use_making_step: selected,
      });
      await synchronize(true);
      setChoice(null);
      setNotice(
        `Saved. ${result.use_making_step ? "Tablets use the Making step." : "Tablets skip Making."} Connected tablets update automatically.`,
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
    <form className="panel board-settings-panel" onSubmit={save}>
      <fieldset disabled={!config || saving}>
        <legend>Queue steps for every tablet</legend>
        <div className="board-layout-options">
          {choices.map(([value, label, help]) => (
            <label
              key={label}
              className={`board-layout-choice ${selected === value ? "selected" : ""}`}
            >
              <span className="board-layout-label">
                <input
                  type="radio"
                  name="making-step"
                  checked={selected === value}
                  onChange={() => {
                    setChoice(value);
                    setNotice("");
                  }}
                />
                {label}
              </span>
              <small>{help}</small>
            </label>
          ))}
        </div>
        <button className="primary" disabled={selected === saved}>
          {saving ? "Saving…" : "Save queue steps"}
        </button>
      </fieldset>
      {notice && <Message>{notice}</Message>}
      {error && <Message error>{error}</Message>}
      <p className="footnote">
        Orders already in Making keep their Mark ready button. Changing this
        needs a connection; an offline tablet keeps the last setting it
        received.
      </p>
    </form>
    <CancelPlaceForm />
    <EstimateSettingForm />
    </>
  );
}

// One On/Off row with both explanations shown, the selected one highlighted.
function Switch({ title, value, onChange, onText, offText }) {
  return (
    <div className="setting-row">
      <strong>{title}</strong>
      <div className="segmented" role="group" aria-label={title}>
        {[
          [true, "On"],
          [false, "Off"],
        ].map(([choice, text]) => (
          <button
            type="button"
            key={text}
            aria-pressed={value === choice}
            className={value === choice ? "active" : ""}
            onClick={() => onChange(choice)}
          >
            {text}
          </button>
        ))}
        <ul className="choice-notes">
          <li className={value ? "current" : ""}>
            <strong>On:</strong> {onText}
          </li>
          <li className={!value ? "current" : ""}>
            <strong>Off:</strong> {offText}
          </li>
        </ul>
      </div>
    </div>
  );
}

// The estimate on the ready board and on the guest's phone; Queue has none; QR popups keep the range.
// How the estimate reads on the guest's phone and the board (QR popups keep the range).
const DISPLAYS = [
  ["range", "Range", "about 7–11 min"],
  ["upper", "Upper bound only", "up to 11 min"],
  ["average", "Average", "about 9 min"],
  ["average_upper", "Average to upper bound", "about 9–11 min"],
];

function EstimateSettingForm() {
  const config = useLiveQuery(() => db.settings.get("config"));
  const saved = {
    on_board: config?.estimate_on_board !== false,
    on_guest: config?.estimate_on_guest !== false,
    display: config?.estimate_display || "range",
  };
  const [draft, setDraft] = useState({});
  const value = { ...saved, ...draft };
  const changed = ["on_board", "on_guest", "display"].some((key) => value[key] !== saved[key]);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const set = (patch) => {
    setDraft((d) => ({ ...d, ...patch }));
    setNotice("");
  };
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setError("");
    try {
      await saveSetting("/admin/estimate-settings", "PATCH", value);
      await synchronize(true);
      setDraft({});
      setNotice("Saved. Boards and guest pages update automatically.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <form className="panel pickup-settings estimate-settings" onSubmit={save}>
      <fieldset disabled={!config || saving}>
        <legend>Waiting-time estimate</legend>
        <Switch
          title="On the ready board"
          value={value.on_board}
          onChange={(on) => set({ on_board: on })}
          onText="The board's header shows “Order now: ready in about X–Y min”"
          offText="No estimate on the ready board"
        />
        <Switch
          title="On the guest's phone"
          value={value.on_guest}
          onChange={(on) => set({ on_guest: on })}
          onText="The guest's page shows “Usually ready in about X–Y min” and how many orders are ahead"
          offText="No estimate on the guest's page"
        />
        <div className="setting-row">
          <strong>Shows as</strong>
          <div className="segmented" role="group" aria-label="Estimate shows as">
            {DISPLAYS.map(([key, text]) => (
              <button
                type="button"
                key={key}
                aria-pressed={value.display === key}
                className={value.display === key ? "active" : ""}
                onClick={() => set({ display: key })}
              >
                {text}
              </button>
            ))}
            <ul className="choice-notes display-notes">
              {DISPLAYS.map(([key, text, example]) => (
                <li key={key} className={value.display === key ? "current" : ""}>
                  <strong>{text}:</strong> “Usually ready in {example}”
                </li>
              ))}
            </ul>
          </div>
        </div>
        <p className="footnote">
          Applies to the guest's phone and the ready board. Order QR popups keep the range. No waiting-time estimate is shown on the staff Queue screen.
        </p>
        <button className="primary" disabled={!changed}>
          {saving ? "Saving…" : "Save waiting-time estimate"}
        </button>
      </fieldset>
      {notice && <Message>{notice}</Message>}
      {error && <Message error>{error}</Message>}
    </form>
  );
}

const CANCEL_CHOICES = [
  ["popup", "In the QR popup", "Tap a ticket’s QR button, then Cancel order…; also in the Order saved popup after Place order. Tickets stay compact."],
  ["below", "Below the main button", "A Cancel order button under Start making / Mark ready / Collected on every active ticket. Tickets get taller."],
  ["both", "Both", "Cancel order in the QR popups and below the main button."],
];

// Where every tablet shows Cancel order.
function CancelPlaceForm() {
  const config = useLiveQuery(() => db.settings.get("config"));
  const saved = config?.cancel_place || "popup";
  const [choice, setChoice] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const selected = choice ?? saved;
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setError("");
    try {
      await saveSetting("/admin/cancel-place", "PATCH", { cancel_place: selected });
      await synchronize(true);
      setChoice(null);
      setNotice("Saved. Connected tablets update automatically.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <form className="panel board-settings-panel cancel-place-settings" onSubmit={save}>
      <fieldset disabled={!config || saving}>
        <legend>Cancel button</legend>
        <div className="board-layout-options three">
          {CANCEL_CHOICES.map(([value, text, help]) => (
            <label key={value} className={`board-layout-choice ${selected === value ? "selected" : ""}`}>
              <span className="board-layout-label">
                <input
                  type="radio"
                  name="cancel-place"
                  checked={selected === value}
                  onChange={() => {
                    setChoice(value);
                    setNotice("");
                  }}
                />
                {text}
              </span>
              <small>{help}</small>
            </label>
          ))}
        </div>
        <button className="primary" disabled={selected === saved}>
          {saving ? "Saving…" : "Save cancel button"}
        </button>
      </fieldset>
      {notice && <Message>{notice}</Message>}
      {error && <Message error>{error}</Message>}
    </form>
  );
}
