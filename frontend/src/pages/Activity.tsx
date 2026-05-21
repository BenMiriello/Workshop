import { useState } from "react";
import Services from "./Services";
import Events from "./Events";
import Runs from "./Runs";

type Tab = "services" | "events" | "runs";

const TABS: { key: Tab; label: string }[] = [
  { key: "services", label: "Services" },
  { key: "events", label: "Events" },
  { key: "runs", label: "Runs" },
];

export default function Activity() {
  const initial: Tab = (() => {
    const h = window.location.hash.replace("#", "");
    return (TABS.find((t) => t.key === h)?.key) || "services";
  })();
  const [tab, setTab] = useState<Tab>(initial);

  const select = (t: Tab) => {
    setTab(t);
    window.history.replaceState(null, "", `#${t}`);
  };

  return (
    <div>
      <div style={{ display: "flex", gap: "0.25rem", marginBottom: "1rem", borderBottom: "1px solid var(--border)" }}>
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => select(t.key)}
              style={{
                background: active ? "var(--card)" : "transparent",
                color: active ? "var(--accent)" : "var(--muted)",
                fontWeight: active ? 600 : 400,
                border: "none",
                padding: "0.5rem 0.85rem",
                borderRadius: "6px 6px 0 0",
                cursor: "pointer",
                fontSize: "0.95rem",
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      {tab === "services" && <Services />}
      {tab === "events" && <Events />}
      {tab === "runs" && <Runs />}
    </div>
  );
}
