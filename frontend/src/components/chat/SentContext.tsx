// What the operator attached to a message from the page beside the chat, shown
// on the message as chips that open to the text that went with it. The message
// carries them as `=== ATTACHED (...) ===` blocks after the typed text
// (shell/beside/contextItems); this is the reading side of that.

import { Quote } from "lucide-react";
import type { AttachedPart } from "../shell/beside/contextItems";
import "../shell/beside/beside.css";

export default function SentContext({ parts }: { parts: AttachedPart[] }) {
  if (!parts.length) return null;
  return (
    <div className="chat-sent-ctx">
      {parts.map((p, i) => (
        <details key={i} className="chat-sent-ctx__item">
          <summary>
            <Quote size={13} strokeWidth={1.8} aria-hidden />
            <span>{p.heading}</span>
          </summary>
          {p.body && <pre className="chat-sent-ctx__body">{p.body}</pre>}
        </details>
      ))}
    </div>
  );
}
