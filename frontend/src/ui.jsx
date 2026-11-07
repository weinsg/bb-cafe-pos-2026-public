import React from "react";
import { createPortal } from "react-dom";

const paths = {
  cup: "M5 8h12v7a6 6 0 0 1-12 0V8Zm12 1h2a3 3 0 0 1 0 6h-2M4 22h16M8 2v3m4-3v3m4-3v3",
  pastry:
    "M4 15c-4-1-2-7 2-7 0-3 5-5 7-2 4-1 7 3 5 5 5 1 5 7 1 8l-3-4-4 5-5-4-3-1Zm2-7 3 7m4-9 1 9m4-4-2 4",
  plus: "M12 5v14M5 12h14",
  queue: "M9 6h12M9 12h12M9 18h12M3 6h.01M3 12h.01M3 18h.01",
  menu: "M4 5h16v14H4zM4 10h16M10 10v9",
  report: "M4 20h16M7 16v-5m5 5V5m5 11V8",
  audit: "M9 5H5v16h14V5h-4M9 3h6v4H9zM9 12h6m-6 4h4",
  reset: "M3 10a9 9 0 1 1 2 8M3 4v6h6",
  arrow: "m9 5 7 7-7 7",
  check: "m5 12 4 4L19 6",
  logout: "M9 4H4v16h5m6-12 4 4-4 4m-6-4h10",
  board: "M3 4h18v13H3zM8 21h8m-4-4v4",
  sound: "m11 5-6 4H2v6h3l6 4V5Zm4 3a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14",
  close: "m6 6 12 12M6 18 18 6",
  settings: "M4 6h10m4 0h2M4 12h4m4 0h8M4 18h12m4 0h0M16 4v4M10 10v4M18 16v4",
  qr: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2M14 18h2M18 18h2v2h-2z",
};
export function Icon({ name, size = 20 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name] || paths.cup} />
    </svg>
  );
}
export function Brand({ light = false }) {
  return (
    <a
      className={`brand ${light ? "brand-light" : ""}`}
      href="/"
      aria-label="Beans and Beats home"
    >
      <span className="brand-mark">
        <Icon name="cup" size={28} />
      </span>
      <span>
        <strong>
          beans<span className="amp">&</span>beats
        </strong>
        <small>CHRISTMAS TOGETHER · 2026</small>
      </span>
    </a>
  );
}
export function Empty({ title, children }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}
export function Message({ error, children }) {
  return (
    <div
      className={`message ${error ? "message-error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {children}
    </div>
  );
}
export function Dialog({ title, children, onClose }) {
  const ref = React.useRef(null);
  const titleId = React.useId();
  React.useEffect(() => {
    const previous = document.activeElement;
    ref.current.showModal();
    return () => {
      previous?.focus();
    };
  }, []);
  return createPortal(
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="dialog-heading">
        <h2 id={titleId}>{title}</h2>
        <button
          className="icon-button"
          aria-label="Close dialog"
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </div>
      {children}
    </dialog>,
    document.body,
  );
}
