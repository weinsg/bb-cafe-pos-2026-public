import React, { useEffect, useRef, useState } from "react";
import QrScanner from "qr-scanner";
import { useLiveQuery } from "dexie-react-hooks";
import { api, changeStatus, db, getConnection, rank, undoCollect } from "./data";
import { optionText } from "./Orders";
import { Icon, Message } from "./ui";

// Pickup scan: the scan identifies the order; one tap on Hand over collects it.
const results = {
  ready: ["ok", "Ready · hand over"],
  // Never show a bare "Received": staff read it as "the guest has received it".
  making: ["warn", "Not ready yet — do not hand over", "Being made"],
  received: ["warn", "Not ready yet — do not hand over", "Order taken, not started"],
  collected: ["bad", "Already collected"],
  cancelled: ["bad", "Cancelled — do not hand over"],
  discarded: ["bad", "Discarded — do not hand over"],
  handed: ["done", "Handed over"],
  closed: ["bad", "Day closed"],
  foreign: ["bad", "Not a Beans&Beats order"],
  missing: ["bad", "Not on this tablet yet"],
  unreadable: ["bad", "No QR code found"],
};

function time(value) {
  if (!value) return "";
  return new Date(
    /[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`,
  ).toLocaleTimeString("en-SG", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "Asia/Singapore",
  });
}

// Short tones so staff can tell accepted from rejected without looking.
let audio;
function tone(ok) {
  try {
    audio ||= new AudioContext();
    const notes = ok ? [660, 990] : [220, 180];
    notes.forEach((frequency, index) => {
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      const start = audio.currentTime + index * 0.14;
      oscillator.type = ok ? "sine" : "square";
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.15, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.13);
      oscillator.connect(gain).connect(audio.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.14);
    });
  } catch {
    /* Sound is a bonus; colour and label carry the result. */
  }
}

// The pickup QR holds the guest page link; any host is accepted, the path must match.
function orderIdFrom(text) {
  try {
    const match = new URL(text).pathname.match(/^\/guest\/([^/]+)\/?$/);
    return match ? decodeURIComponent(match[1]) : null;
  } catch {
    return null;
  }
}

async function lookup(id) {
  const local = await db.orders.get(id);
  const closedDays = (await db.settings.get("closed-days"))?.value || [];
  let order = local;
  let dayClosed = Boolean(local && closedDays.includes(local.event_day));
  // Only a server answer proves a QR is foreign; a dropped request may not be noticed yet.
  let answered = false;
  if (getConnection().online) {
    try {
      const found = await api(`/orders/${encodeURIComponent(id)}/pickup`);
      answered = true;
      dayClosed = found.day_closed;
      // Keep whichever status is further along: this tablet may hold unsynced taps.
      order =
        local && rank[local.status] > rank[found.order.status]
          ? local
          : found.order;
      if (!local && !dayClosed) await db.orders.put(found.order);
    } catch (err) {
      answered = err.status === 404;
      // An order made offline on this tablet is not on the server yet.
      if (err.status === 404 && !local) return { kind: "foreign" };
      if (err.status && err.status !== 404) throw err;
    }
  }
  if (!order) return { kind: answered ? "foreign" : "missing" };
  if (dayClosed) return { kind: "closed", order };
  return { kind: order.status, order };
}

export function PickupScreen() {
  const video = useRef(null);
  const scanner = useRef(null);
  const last = useRef({ text: "", at: 0 });
  const [camera, setCamera] = useState(false);
  const [cameraError, setCameraError] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Collect on scan (default On) collects a Ready order at once, with Undo for 10 s.
  const config = useLiveQuery(() => db.settings.get("config"));
  const collectOnScan = useRef(true);
  collectOnScan.current = config?.collect_on_scan !== false;
  const [undoOpen, setUndoOpen] = useState(false);
  useEffect(() => {
    if (!undoOpen) return undefined;
    const timer = setTimeout(() => setUndoOpen(false), 10000);
    return () => clearTimeout(timer);
  }, [undoOpen]);

  async function collect(order, fromScan) {
    // The same action as Collected in the queue: same audit record, offline-safe.
    const at = (await changeStatus(order, "collected")) || new Date().toISOString();
    setResult({ kind: "handed", order, at });
    setUndoOpen(fromScan);
    tone(true);
  }
  async function undo() {
    setBusy(true);
    setError("");
    try {
      await undoCollect(result.order);
      setUndoOpen(false);
      setResult({ kind: "ready", order: { ...result.order, status: "ready" } });
      // The camera may still see the same QR: do not collect it again straight away.
      last.current = { text: last.current.text, at: Date.now() + 7000 };
    } catch (err) {
      setError(`Could not undo: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  async function handle(text) {
    const now = Date.now();
    // A live camera reads the same code many times a second.
    if (text === last.current.text && now - last.current.at < 3000) return;
    last.current = { text, at: now };
    setError("");
    const id = orderIdFrom(text);
    try {
      const found = id ? await lookup(id) : { kind: "foreign" };
      setUndoOpen(false);
      if (found.kind === "ready" && collectOnScan.current) {
        await collect(found.order, true);
        return;
      }
      setResult(found);
      tone(found.kind === "ready");
    } catch (err) {
      setError(`Could not look up the order: ${err.message}`);
    }
  }

  useEffect(() => () => scanner.current?.destroy(), []);

  async function startCamera() {
    setCameraError("");
    tone(true);
    if (!window.isSecureContext) {
      setCameraError(
        "The live camera needs HTTPS. Use Scan from photo until the secure link is set up.",
      );
      return;
    }
    try {
      scanner.current ||= new QrScanner(
        video.current,
        (scan) => void handle(scan.data),
        { preferredCamera: "environment", highlightScanRegion: true },
      );
      await scanner.current.start();
      setCamera(true);
    } catch (err) {
      setCameraError(
        `Camera not available (${err?.message || err}). Use Scan from photo.`,
      );
    }
  }
  function stopCamera() {
    scanner.current?.stop();
    setCamera(false);
  }
  async function scanPhoto(event) {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    try {
      const scan = await QrScanner.scanImage(file, {
        returnDetailedScanResult: true,
      });
      last.current = { text: "", at: 0 };
      await handle(scan.data);
    } catch {
      setResult({ kind: "unreadable" });
      tone(false);
    }
  }
  async function handOver() {
    setBusy(true);
    setError("");
    try {
      await collect(result.order, false);
    } catch (err) {
      setError(`Could not hand over: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  const [variant, label, stage] = result ? results[result.kind] || results.foreign : [];
  const order = result?.order;
  return (
    <div className="pickup">
      <section className="panel pickup-camera" aria-label="Scanner">
        <div className={`pickup-video ${camera ? "on" : ""}`}>
          <video ref={video} muted playsInline />
          {!camera && (
            <p>
              <Icon name="qr" size={40} />
              Scan the QR on the guest’s phone
            </p>
          )}
        </div>
        <div className="toolbar pickup-actions">
          {camera ? (
            <button className="secondary" onClick={stopCamera}>
              Stop camera
            </button>
          ) : (
            <button className="primary" onClick={startCamera}>
              <Icon name="qr" />
              Start camera
            </button>
          )}
          <label className="secondary pickup-photo">
            Scan from photo
            <input
              className="sr-only"
              type="file"
              accept="image/*"
              capture="environment"
              onChange={scanPhoto}
            />
          </label>
        </div>
        {cameraError && <Message>{cameraError}</Message>}
      </section>
      <section
        className={`panel pickup-result ${variant || "idle"}`}
        aria-live="polite"
        aria-label="Scan result"
      >
        {!result ? (
          <p className="pickup-idle">
            The order appears here after a scan. No phone? Use Collected in the
            Queue.
          </p>
        ) : (
          <>
            <p className="pickup-label" role="status">
              {label}
              {result.kind === "collected" && order?.collected_at
                ? ` at ${time(order.collected_at)}`
                : result.kind === "handed" && result.at
                  ? ` at ${time(result.at)}`
                  : ""}
            </p>
            {stage && <p className="pickup-stage">{stage}</p>}
            {order && (
              <>
                <h2>
                  <span className="order-number">{order.order_number}</span>{" "}
                  {order.first_name}
                </h2>
                <ul>
                  {order.items.map((item, n) => (
                    <li key={n}>
                      <strong>
                        {item.quantity} × {item.item_name}
                      </strong>
                      <span>{optionText(item)}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {result.kind === "missing" && (
              <p>Reconnect to look it up, or use the Queue.</p>
            )}
            {result.kind === "handed" && undoOpen && (
              <button
                className="secondary full pickup-undo"
                disabled={busy}
                onClick={undo}
              >
                Undo — not handed over
              </button>
            )}
            {result.kind === "ready" && (
              <button
                className="primary full pickup-handover"
                disabled={busy}
                onClick={handOver}
              >
                Hand over <Icon name="check" />
              </button>
            )}
          </>
        )}
        {error && <Message error>{error}</Message>}
      </section>
    </div>
  );
}
