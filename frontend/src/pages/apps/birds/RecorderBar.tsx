import { useNavigate } from "react-router-dom";
import { Radio, Square } from "lucide-react";
import { useRecorder } from "./recorder";

// Persistent bar shown on every page while this device is listening, so leaving
// the bird app does not feel like it stopped. Tap it to jump back to the live
// view; Stop ends the session.

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export default function RecorderBar() {
  const { recording, location, nowHearing, elapsed, stop } = useRecorder();
  const nav = useNavigate();
  if (!recording) return null;
  const top = nowHearing.filter((d) => d.is_bird).sort((a, b) => b.confidence - a.confidence)[0];

  return (
    <div
      onClick={() => nav("/apps/birds")}
      style={{
        position: "fixed", left: 0, right: 0, bottom: 0, zIndex: 50, cursor: "pointer",
        background: "var(--card)", borderTop: "1px solid color-mix(in srgb, var(--accent) 33%, transparent)",
        display: "flex", alignItems: "center", gap: ".6rem", padding: ".5rem .9rem",
        boxShadow: "0 -4px 16px rgba(0,0,0,.35)",
      }}
    >
      <Radio size={16} className="spin-pulse" style={{ color: "var(--ok)", flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
        <div style={{ fontWeight: 600, fontSize: ".88rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          Listening{location ? ` · ${location}` : ""}
        </div>
        <div className="muted" style={{ fontSize: ".76rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {top ? `${top.common_name} (${Math.round(top.confidence * 100)}%)` : "no bird right now"}
        </div>
      </div>
      <span className="mono muted" style={{ fontSize: ".8rem", flexShrink: 0 }}>{clock(elapsed)}</span>
      <button
        className="pill"
        onClick={(e) => { e.stopPropagation(); stop(); }}
        style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".3rem", flexShrink: 0 }}
      >
        <Square size={13} /> Stop
      </button>
    </div>
  );
}
