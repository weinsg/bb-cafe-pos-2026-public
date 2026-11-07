import React, { useEffect, useState } from "react";
import { useLiveQuery } from "dexie-react-hooks";
import {
  api,
  db,
  getConnection,
  isActive,
  post,
  readAuth,
  registerTablet,
  retrySync,
  saveAuth,
  startSync,
  synchronize,
} from "./data";
import { Brand, Dialog, Icon, Message } from "./ui";
import { OrderScreen } from "./Orders";
import { QueueScreen } from "./Queue";
import { PickupScreen } from "./Pickup";
import { SettingsStrip } from "./SettingsStrip";
import { SettingsScreen } from "./Settings";
import { MenuScreen, ReportScreen } from "./Management";
import { GuestPage, ReadyBoard } from "./Public";

export function useConnection() {
  const [value, setValue] = useState(getConnection());
  useEffect(() => {
    const update = () => setValue(getConnection());
    window.addEventListener("connection-change", update);
    return () => window.removeEventListener("connection-change", update);
  }, []);
  return value;
}
function Login({ expired }) {
  const [username, setUsername] = useState(readAuth()?.username || "barista");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const pending = await db.outbox.toArray();
      if (pending.some((a) => a.username !== username))
        throw new Error(
          `Sign in as ${pending[0].username} first to sync the saved changes.`,
        );
      const auth = await api("/auth/login", post({ username, password }));
      // Registration must succeed before exposing the order screen.
      localStorage.setItem("bb-auth", JSON.stringify(auth));
      await registerTablet();
      saveAuth(auth);
    } catch (err) {
      setError(
        err.status
          ? err.message
          : "Could not sign in. Check the connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-page">
      <section className="login-form-wrap">
        <div className="login-form">
          <Brand />
          <h1>Staff sign in</h1>
          {expired && (
            <Message>
              Your session expired. Sign in again to sync your saved changes.
            </Message>
          )}
          <form onSubmit={submit}>
            <label>
              Staff username
              <input
                name="username"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {error && <Message error>{error}</Message>}
            <button className="primary full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
              <Icon name="arrow" />
            </button>
          </form>
          <a className="text-link" href="/board">
            Open ready board <span>↗</span>
          </a>
        </div>
      </section>
    </main>
  );
}
// Counter screens are for every role; management screens for Booth Lead and Admin.
const nav = [
  ["order", "New order", "plus", true],
  ["queue", "Queue", "queue", true],
  ["pickup", "Pickup", "qr", true],
  ["menu", "Menu", "menu", false],
  ["reports", "Daily sales", "report", false],
  ["settings", "Settings", "settings", false],
];

function RejectedChanges() {
  const rejected = useLiveQuery(
    () => db.outbox.filter((a) => Boolean(a.error)).toArray(),
    [],
    [],
  );
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  if (!rejected.length) return null;
  return (
    <section
      className="message message-error"
      aria-label="Saved changes needing attention"
    >
      <strong>Some saved changes need attention.</strong>
      {rejected.map((action) => (
        <div className="rejected-change" key={action.id}>
          <span>
            {action.kind.replace(".", " ")}
            {action.payload.name ? ` · ${action.payload.name}` : ""}:{" "}
            {action.error}
          </span>
          {!action.kind.startsWith("order.") && (
            <button className="text-button" onClick={() => setSelected(action)}>
              Discard rejected change
            </button>
          )}
        </div>
      ))}
      {selected && (
        <Dialog
          title="Discard this rejected change?"
          onClose={() => setSelected(null)}
        >
          <p>
            The server did not apply this {selected.kind.replace(".", " ")}{" "}
            change. Discarding it restores the server’s menu values after the
            next refresh.
          </p>
          {error && <Message error>{error}</Message>}
          <div className="dialog-actions">
            <button className="secondary" onClick={() => setSelected(null)}>
              Keep change
            </button>
            <button
              className="danger"
              onClick={async () => {
                try {
                  await db.outbox.delete(selected.id);
                  setSelected(null);
                  await synchronize();
                } catch (err) {
                  setError(err.message);
                }
              }}
            >
              Discard rejected change
            </button>
          </div>
        </Dialog>
      )}
    </section>
  );
}

function StaffApp() {
  const [auth, setAuth] = useState(readAuth);
  const [expired, setExpired] = useState(false);
  const [page, setPage] = useState(location.hash.slice(1) || "order");
  const [error, setError] = useState("");
  const connection = useConnection();
  const pending = useLiveQuery(() => db.outbox.count(), [], 0);
  const active = useLiveQuery(
    () => db.orders.filter(isActive).count(),
    [],
    0,
  );
  const tablet = useLiveQuery(() => db.settings.get("tablet"));
  useEffect(() => {
    const update = () => {
      setAuth(readAuth());
      setExpired(false);
    };
    const expire = () => setExpired(true);
    const storage = (event) => {
      if (event.key === "bb-auth") update();
    };
    const route = () => setPage(location.hash.slice(1) || "order");
    window.addEventListener("auth-change", update);
    window.addEventListener("login-expired", expire);
    window.addEventListener("hashchange", route);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener("auth-change", update);
      window.removeEventListener("login-expired", expire);
      window.removeEventListener("hashchange", route);
      window.removeEventListener("storage", storage);
    };
  }, []);
  useEffect(
    () => (auth && !expired ? startSync() : undefined),
    [auth, expired],
  );
  async function logout() {
    setError("");
    try {
      if (await db.outbox.count())
        throw new Error("Sync saved changes before signing out.");
      if (!connection.online)
        throw new Error(
          "Reconnect before signing out so your shift is recorded.",
        );
      await api("/auth/logout", post({}));
      saveAuth(null);
    } catch (err) {
      setError(err.message);
    }
  }
  if (!auth || expired) return <Login expired={expired} />;
  const allowed = ["admin", "booth_lead"].includes(auth.role)
    ? nav
    : nav.filter(([, , , everyone]) => everyone);
  const current = allowed.some(([id]) => id === page) ? page : "order";
  // New order stays mounted so an unfinished cart survives a look at the queue.
  const screens = {
    queue: (
      <QueueScreen
        canViewCompleted={auth.can_view_completed !== false}
        canReopen={["admin", "booth_lead"].includes(auth.role)}
        canCancel={auth.can_cancel !== false}
      />
    ),
    pickup: <PickupScreen />,
    menu: <MenuScreen />,
    reports: <ReportScreen />,
    settings: <SettingsScreen role={auth.role} />,
  };
  return (
    <div className={`app-shell ${current === "order" ? "order-workspace" : ""}`}>
      {/* POS rail: navigation, sync, who is signed in and Sign out. No top bar. */}
      <aside className="sidebar">
        <a className="rail-logo" href="/" aria-label="Beans and Beats home">
          <Icon name="cup" size={28} />
        </a>
        {/* Sync state first, under the logo, so staff see it before acting. */}
        <span
          className={`sync-status ${!connection.online || pending ? "pending" : ""}`}
          role="status"
        >
          <span className="status-dot" />
          {pending
            ? `${pending} not synced`
            : !connection.online
              ? "Offline"
              : "Synced"}
        </span>
        <nav aria-label="Staff navigation">
          {allowed.map(([id, label, icon]) => (
            <a
              href={`#${id}`}
              aria-label={label}
              key={id}
              className={`nav-item ${current === id ? "selected" : ""}`}
              aria-current={current === id ? "page" : undefined}
            >
              <Icon name={icon} />
              <span>{label}</span>
              {id === "queue" && active > 0 && (
                <span className="nav-count">{active}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <a
            className="board-link"
            aria-label="Ready board"
            href="/board"
            target="_blank"
            rel="noreferrer"
          >
            <Icon name="board" />
            <span>Board</span>
          </a>
          <span className="staff-chip">
            <span className="avatar">{auth.username[0].toUpperCase()}</span>
            <span>
              {auth.role === "admin"
                ? "Admin"
                : auth.role === "booth_lead"
                  ? "Booth lead"
                  : "Barista"}
              <small>Tablet {tablet?.prefix || "…"}</small>
            </span>
          </span>
          <button className="rail-signout" onClick={logout}>
            <Icon name="logout" />
            <span>Sign out</span>
          </button>
        </div>
      </aside>
      <div className="workspace">
        <main className="main-content">
          <h1 className="sr-only">
            {allowed.find(([id]) => id === current)[1]}
          </h1>
          {(connection.error || error) && (
            <Message error>
              {error || connection.error}{" "}
              <button
                className="text-button"
                onClick={() => {
                  setError("");
                  void retrySync();
                }}
              >
                Retry sync
              </button>
            </Message>
          )}
          {!connection.online && (
            <Message>
              Working offline. Orders and changes are saved on this tablet and
              will sync when the connection returns.
            </Message>
          )}
          <SettingsStrip page={current} />
          <RejectedChanges />
          <OrderScreen hidden={current !== "order"} canCancel={auth.can_cancel !== false} />
          {screens[current]}
        </main>
      </div>
    </div>
  );
}
export default function App() {
  if (location.pathname === "/board") return <ReadyBoard />;
  const guest = location.pathname.match(/^\/guest\/([^/]+)\/?$/);
  if (guest) return <GuestPage id={decodeURIComponent(guest[1])} />;
  return <StaffApp />;
}
