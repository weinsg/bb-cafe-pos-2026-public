import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Brand, Icon, Message } from "./ui";
import { useResource } from "./useResource";

// Long names shrink until they fit beside the number; they are never cut off.
function FitName({ name }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const element = ref.current;
    const fit = () => {
      element.style.fontSize = "";
      let size = parseFloat(getComputedStyle(element).fontSize);
      while (element.scrollWidth > element.clientWidth + 1 && size > 16) {
        size -= 1;
        element.style.fontSize = `${size}px`;
      }
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(element.parentElement);
    void document.fonts?.ready.then(fit);
    return () => observer.disconnect();
  }, [name]);
  return (
    <span ref={ref} title={name}>
      {name}
    </span>
  );
}

function BoardLane({ title, kind, orders = [], columns = 1 }) {
  const rows = 6;
  const capacity = rows * columns;
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(orders.length / capacity));
  const current = page % pages;
  useEffect(() => {
    if (pages <= 1) return;
    const timer = setInterval(
      () => setPage((value) => (value + 1) % pages),
      8000,
    );
    return () => clearInterval(timer);
  }, [pages]);
  // Surface a newly ready ticket promptly without restarting the rotation timer.
  useEffect(() => setPage(0), [orders[0]?.order_number]);
  const start = current * capacity;
  const note =
    pages > 1
      ? `${start + 1}–${Math.min(start + capacity, orders.length)} of ${orders.length} · page ${current + 1}/${pages} · rotates every 8s`
      : kind === "ready"
        ? "Please collect at the counter"
        : "";
  return (
    <section className={`board-${kind}`} aria-label={title}>
      <div className="board-column-title">
        <div>
          <h2>{title}</h2>
          {note && <p className="board-page-note">{note}</p>}
        </div>
        <span aria-label={`${orders.length} ${kind} orders`}>
          {orders.length}
        </span>
      </div>
      <div
        className="board-tickets"
        style={{
          gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        }}
      >
        {orders.slice(start, start + capacity).map((order) => (
          <div className="board-ticket" key={order.order_number}>
            <strong>{order.order_number}</strong>
            <FitName name={order.first_name || "Guest"} />
          </div>
        ))}
        {!orders.length && (
          <div className="board-empty">No orders</div>
        )}
      </div>
    </section>
  );
}

export function ReadyBoard() {
  const [smallScreen, setSmallScreen] = useState(
    () => window.matchMedia("(max-width: 700px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(max-width: 700px)");
    const update = () => setSmallScreen(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const {
    data: board,
    connected,
    error,
  } = useResource("/orders/ready-board", "ready-board");
  const position = board?.ready_board_position === "right" ? "right" : "left";
  const lanes = [
    <BoardLane key="ready" title="Ready to collect" kind="ready" orders={board?.ready} columns={smallScreen ? 1 : 2} />,
    <BoardLane key="making" title="In progress" kind="making" orders={board?.making} columns={smallScreen ? 1 : 2} />,
  ];
  const tally = board?.charity_counter;
  // The charity counter takes 1/3 of the board, on the side away from Ready.
  const counter = tally && (
    <aside className="board-counter" aria-label="Charity counter" key="counter">
      <p className="counter-kicker">For charity</p>
      <strong className="counter-cups">{tally.cups.toLocaleString("en-SG")}</strong>
      <span className="counter-unit">
        {tally.cups === 1 ? "cup" : "cups"} {tally.period === "all" ? "so far" : "today"}
      </span>
      {tally.amount_cents != null && (
        <p className="counter-amount">
          <strong>
            S$
            {(tally.amount_cents / 100).toLocaleString("en-SG", {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </strong>{" "}
          in sales
        </p>
      )}
      <p className="counter-cause">All profits go to Singapore Red Cross Young Hearts</p>
    </aside>
  );
  return (
    <main className="ready-board" data-ready-position={position}>
      <header className="board-header">
        <Brand light />
        <h1 className="board-title">Order status</h1>
        {board?.estimate && (
          // The Queue's new-order estimate; Settings → Queue steps can switch it off.
          <p className="board-estimate">
            Order now: ready in about{" "}
            <strong>
              {board.estimate.low_min}–{board.estimate.high_min} min
            </strong>
          </p>
        )}
        <span className={`public-connection ${!connected ? "pending" : ""}`}>
          <span className="status-dot" />
          {connected ? "Live" : "Reconnecting…"}
        </span>
      </header>
      <div className={`board-columns ${counter ? "with-counter" : ""}`}>
        {position === "right" && counter}
        {position === "right" ? [...lanes].reverse() : lanes}
        {position === "left" && counter}
      </div>
      {error && !board && (
        <p className="board-error" role="status">
          Connecting to the booth. The last saved board will appear when
          available.
        </p>
      )}
    </main>
  );
}
export function GuestPage({ id }) {
  const {
    data: order,
    connected,
    error,
  } = useResource(
    `/orders/${encodeURIComponent(id)}/status`,
    `guest:${id}`,
    true,
    true,
  );
  const [sound, setSound] = useState(false);
  const [soundError, setSoundError] = useState("");
  const [flash, setFlash] = useState(false);
  const audio = useRef(null);
  const lastStatus = useRef(null);
  async function enableSound() {
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new Error();
      audio.current ||= new Context();
      await audio.current.resume();
      // Unlock the audio path during a user gesture on iOS.
      const source = audio.current.createBufferSource();
      source.buffer = audio.current.createBuffer(
        1,
        1,
        audio.current.sampleRate,
      );
      source.connect(audio.current.destination);
      source.start();
      setSound(true);
      setSoundError("");
    } catch {
      setSoundError(
        "Sound could not be enabled. Keep this page open for the visual alert.",
      );
    }
  }
  useEffect(() => {
    let timer;
    if (order?.status === "ready") {
      document.title = `Ready to collect! ${order.order_number} · Beans&Beats`;
      if (lastStatus.current !== "ready") {
        setFlash(true);
        timer = setTimeout(() => setFlash(false), 2400);
        if (sound && audio.current) {
          const context = audio.current;
          void context
            .resume()
            .then(() => {
              [659.25, 783.99, 1046.5].forEach((frequency, n) => {
                const oscillator = context.createOscillator(),
                  gain = context.createGain();
                const start = context.currentTime + n * 0.22;
                oscillator.frequency.value = frequency;
                oscillator.connect(gain);
                gain.connect(context.destination);
                gain.gain.setValueAtTime(0, start);
                gain.gain.linearRampToValueAtTime(0.16, start + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.001, start + 0.5);
                oscillator.start(start);
                oscillator.stop(start + 0.5);
              });
            })
            .catch(() =>
              setSoundError(
                "Sound is paused by your browser. Your order is ready to collect.",
              ),
            );
        }
      }
    } else
      document.title = order
        ? `${order.order_number} · Beans&Beats`
        : "Your coffee · Beans&Beats";
    lastStatus.current = order?.status;
    return () => clearTimeout(timer);
  }, [order?.status, order?.order_number, sound]);
  useEffect(
    () => () => {
      void audio.current?.close();
    },
    [],
  );
  const ready = order?.status === "ready";
  // Cancelled and Discarded end the order like Collected, with their own message.
  const ended = order?.status === "cancelled" || order?.status === "discarded";
  const collected = order?.status === "collected" || ended;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 20000);
    return () => clearInterval(timer);
  }, []);
  const waitedMinutes =
    ready && order?.ready_at
      ? (now -
          new Date(
            /[zZ]|[+-]\d\d:\d\d$/.test(order.ready_at)
              ? order.ready_at
              : `${order.ready_at}Z`,
          )) /
        60000
      : 0;
  // Warn the guest once the drink has waited past the first reminder time.
  const spoilWarning =
    ready && order.warn_minutes != null && waitedMinutes >= order.warn_minutes;
  const steps = ["received", "making", "ready"];
  const position = collected ? 3 : steps.indexOf(order?.status);
  return (
    <main
      className={`guest-page ${ready ? "guest-ready" : ""} ${flash ? "ready-flash" : ""}`}
    >
      <header>
        <Brand />
        <span
          className={`guest-connection ${connected ? "" : "pending"}`}
          role="status"
        >
          <span className="status-dot" />
          {connected ? "Live order updates" : "Reconnecting…"}
        </span>
      </header>
      <section className="guest-card" aria-live="polite">
        {!order ? (
          <>
            <span className="guest-cup">
              <Icon name="cup" size={48} />
            </span>
            <h1>
              {error === "Order not found"
                ? "Your cup is on its way here."
                : "Finding your little cup of good…"}
            </h1>
            <p>
              {error === "Order not found"
                ? "The counter may still be syncing your order. Keep this page open, or check your number on the ready board."
                : "We’ll show your order as soon as we can connect."}
            </p>
          </>
        ) : (
          <>
            <p className="eyebrow">
              {order.status === "cancelled"
                ? "ORDER CANCELLED"
                : order.status === "discarded"
                  ? "DRINK DISCARDED"
                  : collected
                ? "THANKS FOR DOING GOOD WITH US"
                : ready
                  ? "A LITTLE JOY, READY FOR YOU"
                  : "YOUR LITTLE CUP OF GOOD"}
            </p>
            <div className="guest-number">{order.order_number}</div>
            <h1>
              {order.status === "cancelled"
                ? "This order was cancelled."
                : order.status === "discarded"
                  ? "Sorry, your drink was discarded for hygiene."
                  : collected
                ? "Enjoy every sip."
                : ready
                  ? `${order.first_name || "Friend"}, your order is ready!`
                  : `We’re on it${order.first_name ? `, ${order.first_name}` : ""}.`}
            </h1>
            <p>
              {ended
                ? order.status === "cancelled"
                  ? "Please see the counter if this is a mistake."
                  : "It waited too long after it was ready. Please see the counter."
                : collected
                ? "Thanks for supporting Young Hearts. You’ve made someone’s day a little brighter."
                : ready
                  ? "Pop back to the counter and collect your order."
                  : "Take a little wander. We’ll let you know when it’s ready."}
            </p>
            {!collected && !ready && order.estimate_low_min != null && (
              // An estimate (median to 80th percentile of the booth's pace), never a promise.
              <div className="guest-estimate" role="status">
                <strong>
                  Usually ready in{" "}
                  <span className="nowrap">
                    about {order.estimate_low_min}–{order.estimate_high_min} min
                  </span>
                </strong>
                <span>
                  {order.orders_ahead === 0
                    ? "You’re next"
                    : `${order.orders_ahead} ${order.orders_ahead === 1 ? "order" : "orders"} ahead of you`}
                  {order.average_wait_min != null &&
                    ` · Average wait today: ${order.average_wait_min} min`}
                </span>
              </div>
            )}
            {spoilWarning && (
              <p className="guest-spoil-warning" role="alert">
                Your drink may spoil. Please collect it within{" "}
                {order.discard_minutes - order.warn_minutes} minutes or it will
                be discarded.
              </p>
            )}
            {!collected && (
              // Staff scan this at the counter to hand over the order.
              <figure className={`pickup-qr ${ready ? "pickup-qr-ready" : ""}`}>
                <QRCodeSVG
                  value={`${location.origin}/guest/${encodeURIComponent(order.id)}`}
                  size={ready ? 220 : 160}
                  level="M"
                  marginSize={2}
                  title={`Pickup code for order ${order.order_number}`}
                />
                <figcaption>
                  <strong>{order.order_number}</strong>
                  Show this at pickup
                </figcaption>
              </figure>
            )}
            {!ended && (
            <ol className="progress-steps">
              {steps.map((step, index) => (
                <li key={step} className={index <= position ? "complete" : ""}>
                  <span>
                    {index < position || collected ? (
                      <Icon name="check" size={18} />
                    ) : (
                      index + 1
                    )}
                  </span>
                  <strong>
                    {["Received", "Making", "Ready to collect"][index]}
                  </strong>
                </li>
              ))}
            </ol>
            )}
            <div className="guest-order-items">
              <p className="eyebrow">MADE JUST FOR YOU</p>
              {order.items?.length
                ? // drink name on its own line, options in words below it.
                  order.items.map((item, n) => (
                    <p className="guest-drink" key={n}>
                      <strong>{item.name}</strong>
                      {item.details && <span>{item.details}</span>}
                    </p>
                  ))
                : order.items_summary.map((item, n) => (
                    <p className="guest-drink" key={n}>
                      {item}
                    </p>
                  ))}
            </div>
            {!collected && (
              <button
                className={sound ? "sound-enabled" : "primary full"}
                onClick={enableSound}
                disabled={sound}
              >
                <Icon name="sound" />
                {sound ? "Sound is on" : "Tap to turn on sound"}
              </button>
            )}
            {!collected && (
              <small className="guest-hint">
                Keep this page open for your alert.
              </small>
            )}
            {soundError && <Message>{soundError}</Message>}
          </>
        )}
      </section>
      <footer>
        <span>✦</span>
        <p>
          Good coffee. Greater good.
          <small>
            100% of event profits support
            <br />
            Singapore Red Cross Young Hearts.
          </small>
        </p>
      </footer>
    </main>
  );
}
