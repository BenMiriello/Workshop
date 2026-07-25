import { useState } from "react";
import { MessageSquare, ChevronDown } from "lucide-react";
import Chat from "./Chat";
import { useVisualViewport } from "../lib/useVisualViewport";

// Floating chat surface. The collapsed state is an accent FAB; the expanded
// state is a card-elevated panel hosting the unified Chat component.
//
// Mode taxonomy per ADR-008:
//   "data"  — read + narrow per-app writes only.
//   "build" — full Claude Code toolset, scoped to a Project's cwd.
// Server-enforced; surfaced in the header so the operator always knows what
// the chat can and cannot do.
type ChatMode = "data" | "build";

type Props = {
  contextKey: string;
  title?: string;
  placeholder?: string;
  onAction?: () => void;
  mode?: ChatMode;
};

export default function ChatDock({ contextKey, title = "Assistant", placeholder, onAction, mode = "data" }: Props) {
  const [open, setOpen] = useState(false);
  const modeLabel = mode === "build" ? "Build" : "Data";
  // The dock is a chat surface with a composer, so it needs the same visual-
  // viewport measurement the focused chat has: without it the mobile keyboard
  // overlays the composer and leaves a blank band where the dock used to be.
  useVisualViewport(open);

  if (!open) {
    return (
      <button
        type="button"
        className="ws-dock-fab"
        onClick={() => setOpen(true)}
        aria-label={`Open ${title} chat`}
        title={`Open ${title} chat`}
      >
        <MessageSquare size={20} strokeWidth={1.8} aria-hidden />
      </button>
    );
  }

  return (
    <div className="ws-dock" style={{ height: "min(620px, calc(var(--app-vh, 100dvh) - 2.5rem))" }}>
      <div className="ws-dock__header" onClick={() => setOpen(false)} role="button" tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") setOpen(false);
        }}
      >
        <MessageSquare size={16} strokeWidth={1.8} aria-hidden />
        <span className="ws-dock__title">{title}</span>
        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)", marginLeft: "0.4rem" }}>· {modeLabel}</span>
        <span className="ws-spacer" />
        <ChevronDown size={16} strokeWidth={1.8} aria-hidden />
      </div>
      <Chat contextKey={contextKey} mode={mode} variant="dock" placeholder={placeholder} onAction={onAction} scoped />
    </div>
  );
}
