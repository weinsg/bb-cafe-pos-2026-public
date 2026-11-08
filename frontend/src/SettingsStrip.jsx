import React, { useCallback, useEffect, useRef, useState } from "react";
import { listen } from "./liveEvents";
import { api, ownChanges } from "./data";
import { Icon } from "./ui";

const time = (value) =>
  new Date(
    /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`,
  ).toLocaleTimeString("en-SG", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "Asia/Singapore",
  });

// BRD A13: "admin_john · Making step switched off · 9:42 pm" on other staff devices.
// Each notice expires after 3 s without moving focus; never on Pickup.
// The public live-update channel only says "a setting changed"; who and what come from a staff-only API.
function SettingNotice({ item, dismiss }) {
  useEffect(() => {
    const timer = setTimeout(() => dismiss(item.id), 3000);
    return () => clearTimeout(timer);
  }, [item.id, dismiss]);
  return (
    <button className="settings-strip-item"
      aria-label={`Dismiss: ${item.staff_username} · ${item.details}`}
      onClick={() => dismiss(item.id)}>
      <Icon name="settings" size={18} />
      <span><strong>{item.staff_username}</strong> · {item.details} · {time(item.timestamp)}</span>
      <Icon name="close" size={16} />
    </button>
  );
}
export function SettingsStrip({ page }) {
  const [items, setItems] = useState([]);
  const seen = useRef(null);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const rows = await api("/admin/setting-changes");
        if (!alive) return;
        if (!seen.current) {
          // Changes made before this screen opened are history, not news.
          seen.current = new Set(rows.map((row) => row.id));
          return;
        }
        const fresh = rows.filter(
          (row) => !seen.current.has(row.id) && !ownChanges.has(row.id),
        );
        rows.forEach((row) => seen.current.add(row.id));
        if (fresh.length) setItems((list) => [...fresh, ...list].slice(0, 3));
      } catch {
        /* Offline: the next event or reconnect catches up. */
      }
    };
    void load();
    const stop = listen({
      onMessage: (event) => {
        try {
          const type = JSON.parse(event.data).type;
          if (type === "setting_changed" || type === "daily_reset") void load();
        } catch {
          /* Ignore pings. */
        }
      },
    });
    window.addEventListener("online", load);
    return () => {
      alive = false;
      stop();
      window.removeEventListener("online", load);
    };
  }, []);
  const dismiss = useCallback((id) => setItems((list) => list.filter((row) => row.id !== id)), []);
  if (page === "pickup" || !items.length) return null;
  return (
    <div className="settings-strip" role="status" aria-label="Setting changes">
      {items.map((item) => (
        <SettingNotice key={item.id} item={item} dismiss={dismiss} />
      ))}
    </div>
  );
}
