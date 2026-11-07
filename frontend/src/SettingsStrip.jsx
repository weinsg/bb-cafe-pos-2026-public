import React, { useEffect, useRef, useState } from "react";
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

// "admin_john · Making step switched off · 9:42 pm" on other staff devices.
// Stays until tapped; hides itself after 8 s on the Queue; never on Pickup.
// The public live-update channel only says "a setting changed"; who and what come from a staff-only API.
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
  useEffect(() => {
    if (page !== "queue" || !items.length) return undefined;
    const timer = setTimeout(() => setItems([]), 8000);
    return () => clearTimeout(timer);
  }, [page, items]);
  if (page === "pickup" || !items.length) return null;
  return (
    <div className="settings-strip" role="status" aria-label="Setting changes">
      {items.map((item) => (
        <button
          key={item.id}
          className="settings-strip-item"
          aria-label={`Dismiss: ${item.staff_username} · ${item.details}`}
          onClick={() =>
            setItems((list) => list.filter((row) => row.id !== item.id))
          }
        >
          <Icon name="settings" size={18} />
          <span>
            <strong>{item.staff_username}</strong> · {item.details} ·{" "}
            {time(item.timestamp)}
          </span>
          <Icon name="close" size={16} />
        </button>
      ))}
    </div>
  );
}
