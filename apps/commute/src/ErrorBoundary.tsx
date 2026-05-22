import { Component, type ReactNode } from "react";

// Catches render-time errors anywhere below it so a bug doesn't white-screen
// the app and trigger the SW reload loop. iOS Safari + service workers + a
// silent crash = the "crashes and resets" cycle the operator hit before.
//
// With this boundary, a crash shows a tappable error card instead of a blank
// page. Operator can read what failed and choose to retry without nuking
// their entire session.

type State = {
  err: Error | null;
  info: { componentStack?: string } | null;
};

type Props = { children: ReactNode };

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null, info: null };

  static getDerivedStateFromError(err: Error): Partial<State> {
    return { err };
  }

  componentDidCatch(err: Error, info: { componentStack?: string }) {
    this.setState({ info });
    // Best-effort log to backend so we have a server-side record of the crash.
    try {
      void fetch("/api/events/commute-ui", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "react_error",
          message: err.message,
          stack: err.stack?.slice(0, 2000),
          component_stack: info.componentStack?.slice(0, 2000),
          ts: new Date().toISOString(),
          ua: typeof navigator !== "undefined" ? navigator.userAgent : "",
        }),
      });
    } catch {
      // ignore — boundary should never throw inside its own handler
    }
  }

  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="app">
        <div
          className="card"
          style={{
            margin: "1rem",
            padding: "1rem",
            border: "1px solid var(--danger, #b3261e)",
            background: "var(--card)",
          }}
        >
          <h2 style={{ margin: "0 0 0.4rem", fontSize: "1.05rem" }}>
            Commute app hit an error
          </h2>
          <p className="muted" style={{ margin: "0 0 0.6rem", fontSize: "0.9rem" }}>
            The page didn't reload itself this time so you can see what happened.
          </p>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              fontFamily: "ui-monospace, monospace",
              fontSize: "0.78rem",
              background: "var(--bg)",
              padding: "0.6rem",
              borderRadius: 6,
              maxHeight: 240,
              overflow: "auto",
              margin: 0,
            }}
          >
            {this.state.err.message}
            {this.state.info?.componentStack ? "\n\n" + this.state.info.componentStack : ""}
          </pre>
          <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.7rem" }}>
            <button onClick={() => this.setState({ err: null, info: null })}>
              Try again
            </button>
            <button onClick={() => window.location.reload()}>Reload</button>
          </div>
        </div>
      </div>
    );
  }
}
