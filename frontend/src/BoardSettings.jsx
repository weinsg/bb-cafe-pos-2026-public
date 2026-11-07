import React, { useState } from "react";
import { saveSetting } from "./data";
import { useResource } from "./useResource";
import { Message } from "./ui";

// One Ready board section — Ready side, charity counter (what, which days, where),
// one live Board preview of the current (unsaved) choices, and one Save. Admin only.
const DEFAULTS = { ready: "left", show: "cups", period: "today", place: "top" };
const SIDES = [
  ["left", "Ready on the left"],
  ["right", "Ready on the right"],
];
const SHOW = [
  ["off", "Off"],
  ["cups", "Cups"],
  ["cups_amount", "Cups and amount"],
];
const PERIOD = [
  ["today", "Today"],
  ["all", "All event days"],
];
const PLACE = [
  ["top", "Top line"],
  ["left", "Left side"],
  ["right", "Right side"],
];
const LABEL = { ready: "Ready to collect", making: "In progress", counter: "Charity counter" };

function columns(ready, place, show) {
  const lanes = ready === "right" ? ["making", "ready"] : ["ready", "making"];
  const panel = show !== "off" && (place === "left" || place === "right");
  if (!panel) return lanes;
  return place === "left" ? ["counter", ...lanes] : [...lanes, "counter"];
}

// A drawing of the board. Small: the picture on a choice button. Large: the Board preview.
function MiniBoard({ ready, place, show, counterText, large = false }) {
  const cols = columns(ready, place, show);
  return (
    <span className={`mini-board ${large ? "large" : ""}`} aria-hidden={!large || undefined}>
      {large && (
        <span className="mini-top">
          <b>Order status</b>
          {show !== "off" && place === "top" && <em>{counterText}</em>}
          <i>● Live</i>
        </span>
      )}
      <span className={`mini-cols cols-${cols.length}`}>
        {cols.map((col) => (
          <span key={col} className={`mini-${col}`}>
            {large ? LABEL[col] : ""}
          </span>
        ))}
      </span>
    </span>
  );
}

function counterText({ show, period }) {
  const cups = `247 cups ${period === "all" ? "so far" : "today"}`;
  return show === "cups_amount" ? `${cups} · S$1,482.00 in sales` : cups;
}

function previewCaption(current) {
  const cols = columns(current.ready, current.place, current.show);
  const share = cols.length === 3 ? "⅓" : "½";
  const parts = cols.map((col) => `${LABEL[col]} ${share}`);
  if (current.show !== "off" && current.place === "top") parts.push("counter in the top bar");
  if (current.show === "off") parts.push("no charity counter");
  return parts.join(" · ");
}

export function BoardSettingsScreen() {
  const { data: config, connected, error, reload } = useResource("/config", "board-settings", false);
  const saved = config
    ? {
        ready: config.ready_board_position === "right" ? "right" : "left",
        show: config.charity_show || DEFAULTS.show,
        period: config.charity_period || DEFAULTS.period,
        place: config.charity_place || DEFAULTS.place,
      }
    : null;
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [saveError, setSaveError] = useState("");
  const current = { ...(saved || DEFAULTS), ...draft };
  const changed = saved && Object.keys(DEFAULTS).some((key) => current[key] !== saved[key]);
  const pick = (key, value) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setNotice("");
    setSaveError("");
  };
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setSaveError("");
    try {
      if (current.ready !== saved.ready)
        await saveSetting("/admin/board-settings", "PATCH", { ready_board_position: current.ready });
      if (["show", "period", "place"].some((key) => current[key] !== saved[key]))
        await saveSetting("/admin/charity-counter", "PATCH", {
          show: current.show,
          period: current.period,
          place: current.place,
        });
      setDraft({});
      setNotice("Saved. Connected boards update automatically.");
      void reload();
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  }
  const segmented = (key, options, label, disabled = false) => (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([value, text]) => (
        <button
          type="button"
          key={value}
          aria-pressed={current[key] === value}
          className={current[key] === value ? "active" : ""}
          disabled={disabled}
          onClick={() => pick(key, value)}
        >
          {text}
        </button>
      ))}
    </div>
  );
  const counterOff = current.show === "off";
  return (
    <form className="panel board-settings-panel ready-board-settings" onSubmit={save}>
      <fieldset disabled={!config || !connected || saving}>
        <legend>Ready board</legend>

        <div className="setting-line">
          <strong>Ready to collect</strong>
          <div className="picture-choices" role="group" aria-label="Ready to collect position">
            {SIDES.map(([side, text]) => (
              <button
                type="button"
                key={side}
                aria-pressed={current.ready === side}
                className={`picture-choice ${current.ready === side ? "active" : ""}`}
                onClick={() => pick("ready", side)}
              >
                <MiniBoard ready={side} place="top" show="off" />
                <span>{text}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="setting-line">
          <strong>Charity counter</strong>
          {segmented("show", SHOW, "Charity counter shows")}
        </div>
        <div className="setting-line">
          <strong>Count</strong>
          {segmented("period", PERIOD, "Charity counter counts", counterOff)}
        </div>
        <div className="setting-line">
          <strong>Counter position</strong>
          <div className="picture-choices" role="group" aria-label="Charity counter position">
            {PLACE.map(([place, text]) => (
              <button
                type="button"
                key={place}
                aria-pressed={current.place === place}
                className={`picture-choice ${current.place === place ? "active" : ""}`}
                disabled={counterOff}
                onClick={() => pick("place", place)}
              >
                <span className={`mini-place place-${place}`} aria-hidden="true">
                  <span className="mini-place-bar" />
                  <span className="mini-place-cols">
                    {place === "left" && <span className="mini-counter" />}
                    <span className="mini-lane" />
                    <span className="mini-lane" />
                    {place === "right" && <span className="mini-counter" />}
                  </span>
                </span>
                <span>{text}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="board-preview-block">
          <strong>Board preview</strong>
          <span className="muted small"> — what the board shows with these choices{changed ? " (not saved yet)" : ""}</span>
          <MiniBoard
            ready={current.ready}
            place={current.place}
            show={current.show}
            counterText={counterText(current)}
            large
          />
          <p className="preview-caption">{previewCaption(current)}</p>
        </div>

        <p className="muted small">
          Counted like Daily sales: refunded and unpaid cancels are left out. The amount is the sales recorded here, shown as
          "in sales".
        </p>
        <button className="primary" disabled={!changed}>
          {saving ? "Saving…" : "Save ready board"}
        </button>
      </fieldset>
      {notice && <Message>{notice}</Message>}
      {(saveError || error) && <Message error>{saveError || error}</Message>}
      {!connected && <Message>Connect to the server to change the ready board.</Message>}
      <a className="text-link" href="/board" target="_blank" rel="noreferrer">
        Open ready board ↗
      </a>
    </form>
  );
}
