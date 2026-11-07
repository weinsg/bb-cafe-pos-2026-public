// One live-update (SSE) connection per page, shared by every listener on it.
// Over HTTP/1.1 a browser allows only 6 connections per site; one SSE per hook used them all on the
// Queue (with an order popup open) and blocked the tablet's sync requests (7 Nov).
const listeners = new Set();
let source = null;

const each = (name) => (event) => [...listeners].forEach((entry) => entry[name]?.(event));

export function listen({ onOpen, onError, onMessage }) {
  const entry = { onOpen, onError, onMessage };
  listeners.add(entry);
  if (!source) {
    source = new EventSource("/api/events");
    source.onopen = each("onOpen");
    source.onerror = each("onError");
    source.onmessage = each("onMessage");
  } else if (source.readyState === EventSource.OPEN) {
    // Already live: tell the new listener at once, as its own connection would have.
    queueMicrotask(() => listeners.has(entry) && entry.onOpen?.());
  }
  return () => {
    listeners.delete(entry);
    if (!listeners.size && source) {
      source.close();
      source = null;
    }
  };
}
