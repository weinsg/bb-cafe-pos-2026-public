import React, { useEffect, useState } from "react";
import { saveSetting } from "./data";
import { useResource } from "./useResource";
import { Message } from "./ui";

export function BoardSettingsScreen() {
  const { data: config, connected, error, reload } = useResource("/config", "board-settings", false);
  const [position, setPosition] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [saveError, setSaveError] = useState("");
  const [savedPosition, setSavedPosition] = useState(null);
  useEffect(() => {
    if (config) setSavedPosition(config.ready_board_position);
  }, [config]);
  // With the charity counter shown, the board splits into thirds.
  const withCounter = config ? config.charity_show !== "off" && config.charity_place === "panel" : false;
  const selected = position || savedPosition || "left";
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setSaveError("");
    try {
      const result = await saveSetting("/admin/board-settings", "PATCH", { ready_board_position: selected });
      setSavedPosition(result.ready_board_position);
      setPosition(null);
      setNotice(`Saved. Ready to collect is on the ${result.ready_board_position}. Connected boards update automatically.`);
      void reload();
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <form className="panel board-settings-panel" onSubmit={save}>
        <fieldset disabled={!config || !connected || saving}>
          <legend>Ready to collect position</legend>
          <div className="board-layout-options">
            {["left", "right"].map((side) => (
              <label className={`board-layout-choice ${selected === side ? "selected" : ""}`} key={side}>
                <span className="board-layout-label"><input type="radio" name="ready-position" aria-label={`Ready on the ${side}`} value={side} checked={selected === side} onChange={() => { setPosition(side); setNotice(""); }} />Ready on the {side}</span>
                <span
                  className={`board-layout-preview preview-${side} ${withCounter ? "preview-thirds" : ""}`}
                  aria-hidden="true"
                >
                  {side === "right" && withCounter && <span className="preview-counter">Charity counter</span>}
                  {side === "left" && <span className="preview-ready">Ready</span>}
                  <span className="preview-making">In progress</span>
                  {side === "right" && <span className="preview-ready">Ready</span>}
                  {side === "left" && withCounter && <span className="preview-counter">Charity counter</span>}
                </span>
                <small>
                  {withCounter
                    ? side === "left"
                      ? "Ready ⅓ · In progress ⅓ · Charity counter ⅓"
                      : "Charity counter ⅓ · In progress ⅓ · Ready ⅓"
                    : "Ready ½ · In progress ½"}
                </small>
              </label>
            ))}
          </div>
          <button className="primary" disabled={selected === savedPosition}>{saving ? "Saving…" : "Save board layout"}</button>
        </fieldset>
        {notice && <Message>{notice}</Message>}
        {(saveError || error) && <Message error>{saveError || error}</Message>}
        {!connected && <Message>Connect to the server to change the board layout.</Message>}
        <a className="text-link" href="/board" target="_blank" rel="noreferrer">Open ready board ↗</a>
      </form>
      <CharityCounterForm config={config} connected={connected} reload={reload} />
    </>
  );
}

const SHOW = [
  ["off", "Off"],
  ["cups", "Cups"],
  ["cups_amount", "Cups and amount"],
];
const PERIOD = [
  ["today", "Today"],
  ["all", "All event days"],
];

// What the ready board's charity counter shows. Admin only, like the layout.
const PLACE = [
  ["top", "Top line"],
  ["panel", "Side panel"],
];

function CharityCounterForm({ config, connected, reload }) {
  const [choice, setChoice] = useState(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [saveError, setSaveError] = useState("");
  const saved = config ? { show: config.charity_show, period: config.charity_period, place: config.charity_place || "top" } : null;
  const current = { ...(saved || { show: "cups", period: "today", place: "top" }), ...(choice || {}) };
  const unchanged = saved && ["show", "period", "place"].every((key) => current[key] === saved[key]);
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setSaveError("");
    try {
      await saveSetting("/admin/charity-counter", "PATCH", current);
      setChoice(null);
      setNotice("Saved. Connected boards update automatically.");
      void reload();
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  }
  const pick = (key, value) => {
    setChoice({ ...current, [key]: value });
    setNotice("");
  };
  return (
    <form className="panel board-settings-panel charity-settings" onSubmit={save}>
      <fieldset disabled={!config || !connected || saving}>
        <legend>Charity counter</legend>
        <div className="segmented" role="group" aria-label="Charity counter shows">
          {SHOW.map(([value, label]) => (
            <button type="button" key={value} aria-pressed={current.show === value} className={current.show === value ? "active" : ""} onClick={() => pick("show", value)}>
              {label}
            </button>
          ))}
        </div>
        <div className="segmented" role="group" aria-label="Charity counter counts">
          {PERIOD.map(([value, label]) => (
            <button type="button" key={value} aria-pressed={current.period === value} disabled={current.show === "off"} className={current.period === value ? "active" : ""} onClick={() => pick("period", value)}>
              {label}
            </button>
          ))}
        </div>
        {/* Where the counter goes; both explanations always shown. */}
        <div className="segmented" role="group" aria-label="Charity counter where">
          {PLACE.map(([value, label]) => (
            <button type="button" key={value} aria-pressed={current.place === value} disabled={current.show === "off"} className={current.place === value ? "active" : ""} onClick={() => pick("place", value)}>
              {label}
            </button>
          ))}
        </div>
        <ul className="choice-notes place-notes">
          <li className={current.place === "top" ? "current" : ""}>
            <strong>Top line:</strong> Ready and In progress take half the board each; the counter sits in the top bar
          </li>
          <li className={current.place === "panel" ? "current" : ""}>
            <strong>Side panel:</strong> Ready, In progress and a large counter panel take one third each
          </li>
        </ul>
        <p className="muted small">Counted like Daily sales: refunded and unpaid cancels are left out. The amount is the sales recorded here, shown as "in sales".</p>
        <button className="primary" disabled={unchanged}>{saving ? "Saving…" : "Save charity counter"}</button>
      </fieldset>
      {notice && <Message>{notice}</Message>}
      {saveError && <Message error>{saveError}</Message>}
    </form>
  );
}
