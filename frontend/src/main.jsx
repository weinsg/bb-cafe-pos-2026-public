import React from "react";
import { createRoot } from "react-dom/client";
import { registerSW } from "virtual:pwa-register";
import App from "./App.jsx";
import "./styles.css";
import "./board.css";
import "./pos.css";
import "./kitchen.css";
import "./staff.css";

registerSW({
  onOfflineReady() {
    window.dispatchEvent(new Event("offline-ready"));
  },
});

class ErrorBoundary extends React.Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    return this.state.error ? (
      <main className="center-page">
        <h1>We couldn’t open the app</h1>
        <p>{this.state.error.message}</p>
        <p>
          Enable browser storage, then reload. Saved orders stay on this
          browser.
        </p>
        <button onClick={() => location.reload()}>Reload app</button>
      </main>
    ) : (
      this.props.children
    );
  }
}

createRoot(document.getElementById("root")).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>,
);
