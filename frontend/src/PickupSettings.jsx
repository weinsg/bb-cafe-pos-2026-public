import React, { useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import { db, saveSetting, synchronize } from "./data";
import { Message } from "./ui";

// Booth Lead or Admin: Collect on scan and pickup reminders, shared by every tablet.
export function PickupSettingsScreen() {
  const config = useLiveQuery(() => db.settings.get("config"));
  const saved = {
    collect_on_scan: config?.collect_on_scan !== false,
    reminders: config?.reminders !== false,
    warn_minutes: config?.warn_minutes ?? 30,
    discard_minutes: config?.discard_minutes ?? 60,
  };
  const [draft, setDraft] = useState({});
  const value = { ...saved, ...draft };
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const set = (patch) => {
    setDraft((d) => ({ ...d, ...patch }));
    setNotice("");
    setError("");
  };
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setNotice("");
    setError("");
    try {
      await saveSetting("/admin/pickup-settings", "PATCH", {
        collect_on_scan: value.collect_on_scan,
        reminders: value.reminders,
        warn_minutes: Number(value.warn_minutes),
        discard_minutes: Number(value.discard_minutes),
      });
      await synchronize(true);
      setDraft({});
      setNotice("Saved. Connected tablets update automatically.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }
  // Both explanations always show, the selected one highlighted.
  const toggle = (name, title, onLabel, offLabel) => (
    <div className="segmented" role="group" aria-label={title}>
      {[
        [true, "On"],
        [false, "Off"],
      ].map(([choice, label]) => (
        <button
          type="button"
          key={label}
          aria-pressed={value[name] === choice}
          className={value[name] === choice ? "active" : ""}
          onClick={() => set({ [name]: choice })}
        >
          {label}
        </button>
      ))}
      {offLabel == null ? (
        // One description, the same with On or Off selected.
        <p className="choice-notes single">{onLabel}</p>
      ) : (
        <ul className="choice-notes">
          {[
            [true, "On", onLabel],
            [false, "Off", offLabel],
          ].map(([choice, label, text]) => (
            <li key={label} className={value[name] === choice ? "current" : ""}>
              <strong>{label}:</strong> {text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
  return (
    <form className="panel pickup-settings" onSubmit={save}>
      <fieldset disabled={!config || saving}>
        <div className="setting-row">
          <strong>Collect on scan</strong>
          {toggle(
            "collect_on_scan",
            "Collect on scan",
            "Skips a step: scanning a Ready guest’s QR marks it Collected straight away, no Hand over tap (Undo for 10 s)",
            "Staff tap Hand over after scanning the guest’s QR to mark it Collected",
          )}
        </div>
        <div className="setting-row">
          <strong>Pickup reminders</strong>
          {toggle(
            "reminders",
            "Pickup reminders",
            `Orange ticket and guest warned after waiting ${value.warn_minutes} min, red ticket after ${value.discard_minutes} min; Discard button appears from ${value.warn_minutes} min`,
            null,
          )}
        </div>
        <div className="setting-row">
          <label>
            Warn guest after (minutes)
            <input
              type="number"
              min="5"
              max="240"
              value={value.warn_minutes}
              disabled={!value.reminders}
              onChange={(e) => set({ warn_minutes: e.target.value })}
            />
          </label>
          <label>
            Red ticket after (minutes)
            <input
              type="number"
              min="10"
              max="480"
              value={value.discard_minutes}
              disabled={!value.reminders}
              onChange={(e) => set({ discard_minutes: e.target.value })}
            />
          </label>
        </div>
        <button className="primary" disabled={!Object.keys(draft).length}>
          {saving ? "Saving…" : "Save pickup settings"}
        </button>
      </fieldset>
      {notice && <Message>{notice}</Message>}
      {error && <Message error>{error}</Message>}
    </form>
  );
}
