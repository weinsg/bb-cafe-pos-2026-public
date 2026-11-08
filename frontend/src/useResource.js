import { useCallback, useEffect, useRef, useState } from "react";
import { api, applyClosedDays, db } from "./data";
import { listen } from "./liveEvents";

const LIVE_POLL_MS = 20000; // guest pages while live push is connected
const POLL_MS = 5000; // every page while push is down
const jitter = (ms) => new Promise((done) => setTimeout(done, Math.random() * ms));

// SSE prompts a refetch; polling and visibility recovery also work if push drops.
// Guest: the page refetches only for its own order's change and, while push is
// connected, checks every 20 s instead of every 5 s.
export function useResource(path, cacheKey = path, cache = true, guest = false) {
  const [data, setData] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const generation = useRef(0);
  const reloadRef = useRef(() => {});
  const reload = useCallback(() => reloadRef.current(), []);
  useEffect(() => {
    let alive = true,
      loading = false,
      live = false,
      lastLoad = 0,
      greeted = false,
      tag = "";
    let revision = 0;
    const current = ++generation.current;
    setData(null);
    setUpdatedAt(null);
    setError("");
    setConnected(false);
    if (!path) return;
    if (cache)
      db.snapshots
        .get(cacheKey)
        .then((row) => {
          if (alive && row && generation.current === current && revision === 0)
            { setData((previous) => previous || row.value); setUpdatedAt(row.updatedAt || null); }
        })
        .catch(() => {});
    const load = async () => {
      if (!alive || loading) return;
      loading = true;
      const startedAt = revision;
      lastLoad = Date.now();
      try {
        // The guest page and board carry closed days themselves (one request).
        const ownDays = path === "/orders/ready-board" || guest;
        if (path.startsWith("/orders/") && !ownDays && path !== "/orders/wait-estimate") {
          const state = await api("/sync/state");
          await applyClosedDays(state.closed_days);
        }
        const value = await api(path);
        if (!alive || startedAt !== revision) return;
        if (ownDays && Array.isArray(value?.closed_days))
          await applyClosedDays(value.closed_days);
        if (!alive || startedAt !== revision) return;
        tag = value?.event_tag || "";
        setData(value);
        const fetchedAt = new Date().toISOString();
        setUpdatedAt(fetchedAt);
        setConnected(true);
        setError("");
        if (cache) await db.snapshots.put({ key: cacheKey, value, updatedAt: fetchedAt });
      } catch (err) {
        if (!alive || startedAt !== revision) return;
        setConnected(false);
        if (err.status === 404 || err.status === 403) {
          setData(null);
          if (cache) await db.snapshots.delete(cacheKey);
        }
        setError(err.status ? err.message : "Reconnecting…");
      } finally {
        loading = false;
        if (alive && startedAt !== revision) void load();
      }
    };
    reloadRef.current = load;
    void load();
    const timer = setInterval(() => {
      if (!guest || !live || Date.now() - lastLoad >= LIVE_POLL_MS) void load();
    }, POLL_MS);
    const visible = () => {
      if (!document.hidden) void load();
    };
    const onMessage = (event) => {
      const message = JSON.parse(event.data);
      live = true;
      if (message.type === "daily_reset") {
        revision += 1;
        setData(null);
        if (cache) void db.snapshots.clear();
      }
      if (!guest) return void load();
      // Guest page: its own order changed (or its tag is not known yet) -> at once.
      const other = message.data?.guest;
      if (other) {
        if (!tag || other === tag) void load();
        return;
      }
      // A reconnect, a daily reset or a guest-visible setting (estimate On/Off) reaches every
      // guest page: spread the refetches over 3 s.
      if (
        message.type === "daily_reset" ||
        message.type === "guest_settings_changed" ||
        (message.type === "connected" && greeted)
      )
        void jitter(3000).then(load);
      if (message.type === "connected") greeted = true;
    };
    // One shared connection per page (liveEvents.js).
    const stop = listen({
      onOpen: () => {
        live = true;
      },
      onError: () => {
        live = false;
      },
      onMessage,
    });
    window.addEventListener("online", load);
    document.addEventListener("visibilitychange", visible);
    return () => {
      alive = false;
      clearInterval(timer);
      stop();
      window.removeEventListener("online", load);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [path, cacheKey, cache]);
  return { data, error, connected, reload, updatedAt };
}
