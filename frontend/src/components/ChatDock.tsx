import { useState } from "react";
import { MessageSquare, ChevronDown } from "lucide-react";
import ChatPanel from "./ChatPanel";

// Floating chat surface — the shared "Ask about…" dock, placed consistently
// across Commute / Finance / Location (design-system .ws-dock). Collapsed, it's
// an accent FAB (.ws-dock--collapsed); expanded, a card-elevated panel. The
// inline <ChatPanel> can also be embedded directly (e.g. as a tab).

type Props = {
  contextKey: string;
  title?: string;
  placeholder?: string;
  onAction?: () => void;
};

export default function ChatDock({ contextKey, title = "Assistant", placeholder, onAction }: Props) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <div className="ws-dock ws-dock--collapsed">
        <div className="ws-dock__header" onClick={() => setOpen(true)} role="button" tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") setOpen(true);
          }}
        >
          <MessageSquare size={16} strokeWidth={1.8} aria-hidden />
          <span className="ws-dock__title">{title}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="ws-dock" style={{ maxHeight: "min(620px, calc(100vh - 2.5rem))" }}>
      <div className="ws-dock__header" onClick={() => setOpen(false)} role="button" tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") setOpen(false);
        }}
      >
        <MessageSquare size={16} strokeWidth={1.8} aria-hidden />
        <span className="ws-dock__title">{title}</span>
        <span className="ws-spacer" />
        <ChevronDown size={16} strokeWidth={1.8} aria-hidden />
      </div>
      <ChatPanel contextKey={contextKey} placeholder={placeholder} onAction={onAction} />
    </div>
  );
}
