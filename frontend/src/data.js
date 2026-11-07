import { listen } from "./liveEvents";
import Dexie from "dexie";

export const db = new Dexie("beansbeats-christmas-2026");
db.version(1).stores({
  settings: "&key",
  menu: "&id",
  ingredients: "&name",
  orders: "&id,event_day,created_at",
  outbox: "&id,created_at,username",
  snapshots: "&key",
});
// Collected, Discarded and Cancelled are all final: the first one wins.
export const rank = { received: 0, making: 1, ready: 2, collected: 3, discarded: 3, cancelled: 3 };
export const FINAL = ["collected", "discarded", "cancelled"];
export const isActive = (order) => !FINAL.includes(order.status);

// The same formula as app/estimate.py, from the day's figures the server sends.
const when = (value) =>
  new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).getTime();
export const WAITING = ["received", "making"];
export function ordersAhead(order, orders) {
  return orders.filter(
    (o) =>
      o.id !== order.id &&
      o.event_day === order.event_day &&
      WAITING.includes(o.status) &&
      when(o.created_at) < when(order.created_at),
  ).length;
}
export function estimateMinutes(ahead, figures) {
  const low = Math.max(1, Math.floor(((ahead + 1) * figures.low_s) / 60));
  const high = Math.max(low + 1, Math.ceil(((ahead + 1) * figures.high_s) / 60));
  return [low, high];
}
export const eventDay = () =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Singapore",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
export const money = (cents) =>
  new Intl.NumberFormat("en-SG", { style: "currency", currency: "SGD" }).format(
    cents / 100,
  );
export const readAuth = () => {
  try {
    return JSON.parse(localStorage.getItem("bb-auth"));
  } catch {
    return null;
  }
};
export function saveAuth(value) {
  if (value) localStorage.setItem("bb-auth", JSON.stringify(value));
  else localStorage.removeItem("bb-auth");
  window.dispatchEvent(new Event("auth-change"));
}
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}
export async function api(path, options = {}) {
  const token = readAuth()?.access_token;
  const response = await fetch(`/api${path}`, {
    ...options,
    signal: options.signal || AbortSignal.timeout(12000),
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const message =
      typeof data.detail === "string"
        ? data.detail
        : "Check the form and try again.";
    throw new ApiError(message, response.status);
  }
  if (options.blob) return response.blob();
  return response.json();
}
export const post = (body) => ({ method: "POST", body: JSON.stringify(body) });
// This device's own setting changes, so it never shows its own strip.
export const ownChanges = new Set();
export function saveSetting(path, method, body) {
  const id = crypto.randomUUID();
  ownChanges.add(id);
  return api(path, {
    method,
    body: JSON.stringify(body),
    headers: { "X-Change-Id": id },
  });
}
let connection = { online: navigator.onLine, error: "", busy: false };
export const getConnection = () => connection;
function notify(patch) {
  connection = { ...connection, ...patch };
  window.dispatchEvent(new Event("connection-change"));
}
export async function registerTablet() {
  let identity = await db.settings.get("identity");
  if (!identity) {
    const id = crypto.randomUUID();
    await db.settings.add({ key: "identity", id }).catch(() => {});
    identity = await db.settings.get("identity");
  }
  const tablet = await api("/sync/tablets", post({ id: identity.id }));
  await db.transaction("rw", db.settings, async () => {
    await db.settings.put({ key: "tablet", ...tablet });
    const key = `sequence:${tablet.event_day}`;
    const current = await db.settings.get(key);
    await db.settings.put({
      key,
      value: Math.max(current?.value || 0, tablet.sequence),
    });
  });
  return tablet;
}
export async function enqueue(kind, payload, extra = {}) {
  const auth = readAuth();
  const tablet = await db.settings.get("tablet");
  if (!auth || !tablet)
    throw new Error("Sign in online once to set up this tablet.");
  let action;
  await db.transaction("rw", db.settings, db.outbox, async () => {
    // Keep the outbox ordered even when two tabs save in the same millisecond.
    const previous = (await db.settings.get("action-clock"))?.value || 0;
    const clock = Math.max(Date.now(), previous + 1);
    await db.settings.put({ key: "action-clock", value: clock });
    action = {
      id: crypto.randomUUID(),
      device_id: tablet.id,
      occurred_at: new Date().toISOString(),
      kind,
      payload,
      username: auth.username,
      created_at: clock,
      ...extra,
    };
    await db.outbox.add(action);
  });
  return action;
}
export async function createLocalOrder(firstName, items) {
  let order;
  await db.transaction("rw", db.settings, db.orders, db.outbox, async () => {
    const tablet = await db.settings.get("tablet");
    if (!tablet) throw new Error("Connect once to register this tablet.");
    const day = eventDay();
    const closed = await db.settings.get("closed-days");
    if (closed?.value.includes(day))
      throw new Error(
        "Today has been closed by the booth lead. Ordering resumes on the next event day.",
      );
    const key = `sequence:${day}`;
    const sequence = ((await db.settings.get(key))?.value || 0) + 1;
    await db.settings.put({ key, value: sequence });
    const id = crypto.randomUUID();
    order = {
      id,
      idempotency_key: id,
      device_id: tablet.id,
      first_name: firstName.trim(),
      event_day: day,
      sequence,
      order_number: `${tablet.prefix}${String(sequence).padStart(3, "0")}`,
      status: "received",
      created_at: new Date().toISOString(),
      total_price_cents: items.reduce(
        (n, i) => n + i.unit_price_cents * i.quantity,
        0,
      ),
      items,
    };
    await db.orders.add(order);
    await enqueue("order.create", order);
  });
  void synchronize();
  return order;
}
// Extra: { reason, money } for Cancelled; Discarded needs a Ready order.
export async function changeStatus(order, status, extra = {}) {
  let changedAt = null;
  await db.transaction("rw", db.orders, db.outbox, db.settings, async () => {
    const current = await db.orders.get(order.id);
    if (!current || rank[status] <= rank[current.status]) return;
    // Only a Ready drink can be handed over; the server refuses it too.
    if (status === "collected" && current.status !== "ready")
      throw new Error(`${current.order_number} is not ready yet and cannot be collected.`);
    if (status === "discarded" && current.status !== "ready")
      throw new Error(`Only a Ready drink can be discarded.`);
    const action = await enqueue("order.status", {
      id: order.id,
      status,
      status_revision: current.status_revision || 0,
      ...extra,
    });
    changedAt = action.occurred_at;
    // Local step times let reminders and handover times work before the server answers.
    await db.orders.update(order.id, {
      status,
      ...(["making", "ready", "collected"].includes(status)
        ? { [`${status}_at`]: changedAt }
        : {}),
    });
  });
  void synchronize();
  return changedAt;
}
// Undo a collect-on-scan. Before it syncs, drop the saved action; after, ask the server.
export async function undoCollect(order) {
  const work = async () => {
    let dropped = false;
    await db.transaction("rw", db.orders, db.outbox, async () => {
      const pending = (await db.outbox.toArray()).find(
        (a) =>
          a.kind === "order.status" &&
          a.payload.id === order.id &&
          a.payload.status === "collected",
      );
      if (pending) {
        await db.outbox.delete(pending.id);
        await db.orders.update(order.id, { status: "ready", collected_at: null });
        dropped = true;
      }
    });
    if (dropped) return;
    const current = await db.orders.get(order.id);
    const updated = await api(
      `/orders/${order.id}/undo-collect`,
      post({ status_revision: current?.status_revision || 0 }),
    );
    await db.orders.put(updated);
  };
  if (navigator.locks) await navigator.locks.request("bb-sync", work);
  else await work();
  void synchronize();
}
export async function changeMenu(kind, payload) {
  await db.transaction(
    "rw",
    db.menu,
    db.ingredients,
    db.outbox,
    db.settings,
    async () => {
      await enqueue(kind, payload);
      if (kind === "menu.update") await db.menu.update(payload.id, payload);
      if (kind === "menu.remove") await db.menu.delete(payload.id);
      if (kind === "ingredient.update") await db.ingredients.put(payload);
      if (kind === "surcharge.update") await applySurcharge(payload);
    },
  );
  void synchronize();
}
async function applySurcharge({ name, price_cents }) {
  const config = await db.settings.get("config");
  await db.settings.put({
    ...config,
    key: "config",
    [`${name}_surcharge_cents`]: price_cents,
  });
}
// Add-on prices for New order; S$1.00 each until the server sends its own.
export const surcharges = (config) => ({
  iced: config?.iced_surcharge_cents ?? 100,
  oat: config?.oat_surcharge_cents ?? 100,
});
export async function reopenCollected(order, reason) {
  await synchronize(true);
  const correct = async () => {
    if (await db.outbox.count())
      throw new Error("Sync pending changes before correcting a collection.");
    const updated = await api(
      `/orders/${order.id}/reopen`,
      post({
        confirm: true,
        reason,
        status_revision: order.status_revision || 0,
      }),
    );
    await db.orders.put(updated);
  };
  if (navigator.locks) await navigator.locks.request("bb-sync", correct);
  else await correct();
  void synchronize();
}
export async function applyClosedDays(days) {
  await db.transaction(
    "rw",
    db.orders,
    db.outbox,
    db.snapshots,
    db.settings,
    async () => {
      const closedOrders = await db.orders
        .where("event_day")
        .anyOf(days)
        .toArray();
      const ids = new Set(closedOrders.map((o) => o.id));
      const pending = await db.outbox.toArray();
      const remove = pending.filter(
        (a) =>
          (a.kind === "order.create" && days.includes(a.payload.event_day)) ||
          (a.kind === "order.status" && ids.has(a.payload.id)),
      );
      await db.outbox.bulkDelete(remove.map((a) => a.id));
      await db.orders.bulkDelete(closedOrders.map((o) => o.id));
      const previous = (await db.settings.get("closed-days"))?.value || [];
      if (days.some((day) => !previous.includes(day)))
        await db.snapshots.clear();
      await db.settings.put({ key: "closed-days", value: days });
      if (remove.length)
        notify({
          error: `${remove.length} local changes belonged to a day the booth lead has closed. Guest names were cleared.`,
        });
    },
  );
}
async function refreshStaff() {
  const me = await api("/auth/me");
  const auth = readAuth();
  if (
    auth &&
    (auth.role !== me.role ||
      auth.can_view_completed !== me.can_view_completed ||
      auth.can_cancel !== me.can_cancel)
  )
    saveAuth({ ...auth, ...me });
  if (me.role !== "admin") await db.snapshots.delete("/admin/audit-logs");
  if (!me.can_view_completed) {
    await db.orders.filter((order) => order.status === "collected").delete();
    await db.snapshots
      .filter((row) => row.key.startsWith("/orders/completed"))
      .delete();
  }
  const state = await api("/sync/state");
  await applyClosedDays(state.closed_days);
  const [menu, ingredients, orders, completed, config] = await Promise.all([
    api("/menu"),
    api("/sync/ingredients"),
    api("/orders/active"),
    me.can_view_completed
      ? api(`/orders/completed?event_day=${eventDay()}`)
      : Promise.resolve([]),
    api("/config"),
  ]);
  await db.transaction(
    "rw",
    db.menu,
    db.ingredients,
    db.orders,
    db.outbox,
    db.settings,
    async () => {
      const pending = await db.outbox.orderBy("created_at").toArray();
      const pendingCreates = pending.filter((a) => a.kind === "order.create");
      await db.orders.clear();
      await db.orders.bulkPut([...orders, ...completed]);
      for (const action of pendingCreates) await db.orders.put(action.payload);
      await db.menu.clear();
      await db.menu.bulkPut(menu);
      await db.ingredients.clear();
      await db.ingredients.bulkPut(ingredients);
      // Overlay actions saved while the fetch was in flight.
      for (const action of pending) {
        const p = action.payload;
        if (action.kind === "order.status") {
          const order = await db.orders.get(p.id);
          if (
            order &&
            (p.status_revision || 0) === (order.status_revision || 0) &&
            rank[p.status] > rank[order.status]
          )
            await db.orders.update(p.id, { status: p.status });
        }
        if (action.kind === "menu.update") await db.menu.update(p.id, p);
        if (action.kind === "menu.remove") await db.menu.delete(p.id);
        if (action.kind === "ingredient.update") await db.ingredients.put(p);
      }
      await db.settings.put({ key: "config", ...config });
      for (const action of pending)
        if (action.kind === "surcharge.update")
          await applySurcharge(action.payload);
    },
  );
}
let syncing = false;
async function syncLoop() {
  if (syncing || !readAuth()) return;
  syncing = true;
  notify({ busy: true });
  try {
    if (!(await db.settings.get("tablet"))) await registerTablet();
    const state = await api("/sync/state");
    await applyClosedDays(state.closed_days);
    for (const action of await db.outbox.orderBy("created_at").toArray()) {
      if (action.username !== readAuth()?.username)
        throw new Error(
          `Sign in as ${action.username} to sync the saved changes.`,
        );
      if (action.error) {
        notify({ error: action.error });
        continue;
      }
      try {
        const result = await api("/sync", post(action));
        await db.transaction("rw", db.outbox, db.orders, async () => {
          if (result.order) {
            const local = await db.orders.get(result.order.id);
            if (
              local &&
              (local.status_revision || 0) ===
                (result.order.status_revision || 0) &&
              rank[local.status] > rank[result.order.status]
            )
              result.order.status = local.status;
            if (
              !local ||
              (local.status_revision || 0) <=
                (result.order.status_revision || 0)
            )
              await db.orders.put(result.order);
          }
          await db.outbox.delete(action.id);
        });
      } catch (error) {
        if (error.status >= 400 && error.status < 500 && error.status !== 401) {
          await db.outbox.update(action.id, { error: error.message });
          notify({ error: error.message });
          continue;
        }
        throw error;
      }
    }
    await refreshStaff();
    notify({
      online: true,
      error: (await db.outbox.toArray()).find((a) => a.error)?.error || "",
    });
  } catch (error) {
    const offline =
      !error.status && /fetch|network|timeout|aborted/i.test(error.message);
    notify({
      online: !offline && navigator.onLine,
      error:
        error.status === 401
          ? "Your login expired. Sign in again; your saved orders are safe."
          : offline
            ? ""
            : error.message,
    });
    if (error.status === 401) window.dispatchEvent(new Event("login-expired"));
  } finally {
    syncing = false;
    notify({ busy: false });
  }
}
export function synchronize(wait = false) {
  if (navigator.locks)
    return navigator.locks.request(
      "bb-sync",
      wait ? {} : { ifAvailable: true },
      (lock) => (lock ? syncLoop() : undefined),
    );
  return syncLoop();
}
export async function retrySync() {
  await db.outbox.toCollection().modify((a) => {
    delete a.error;
  });
  await synchronize(true);
}
export function startSync() {
  void synchronize();
  const interval = setInterval(() => void synchronize(), 5000);
  const online = () => void synchronize();
  const visible = () => {
    if (!document.hidden) void synchronize();
  };
  window.addEventListener("online", online);
  document.addEventListener("visibilitychange", visible);
  // One shared live-update connection per page; the five-second timer covers a dropped one.
  const stop = listen({ onMessage: online });
  return () => {
    clearInterval(interval);
    stop();
    window.removeEventListener("online", online);
    document.removeEventListener("visibilitychange", visible);
  };
}
