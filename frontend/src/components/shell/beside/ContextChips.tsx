// The items waiting to go with the next message in the chat beside a page, as
// removable chips above the composer, drawn like the composer's own attached
// files so the two read as one row of "what goes with this message".

import { FileText, Link2, PanelTop, Quote, Square, X } from "lucide-react";
import { beside, useBeside } from "./besideStore";
import { chipLabel, type ContextItem } from "./contextItems";

function KindIcon({ kind }: { kind: ContextItem["kind"] }) {
  const props = { size: 14, strokeWidth: 1.8, "aria-hidden": true } as const;
  if (kind === "text") return <Quote {...props} />;
  if (kind === "link") return <Link2 {...props} />;
  if (kind === "page") return <PanelTop {...props} />;
  if (kind === "item") return <Square {...props} />;
  return <FileText {...props} />;
}

export function ContextChips() {
  const { items } = useBeside();
  if (!items.length) return null;
  return (
    <div className="chat-attach-row ws-beside-chips">
      {items.map((item) => (
        <span key={item.id} className="chat-attach" title={item.text || item.url || item.title || ""}>
          <button
            type="button"
            className="chat-attach__remove"
            onClick={() => beside.removeItem(item.id)}
            aria-label={`Remove ${chipLabel(item)}`}
          >
            <X size={14} strokeWidth={2} aria-hidden />
          </button>
          <span className="chat-attach__mark" aria-hidden><KindIcon kind={item.kind} /></span>
          <span className="chat-attach__name">{chipLabel(item)}</span>
          <span className="chat-attach__meta">{item.source}</span>
        </span>
      ))}
    </div>
  );
}
