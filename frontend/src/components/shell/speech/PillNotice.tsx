// The bar's notice slot: what an action just did, with Cancel to undo it. The
// Cancel carries a ring that runs down over the time left.

import { useState, type CSSProperties } from "react";
import { ArrowUpRight, Check, Copy } from "lucide-react";
import { Button, copyText } from "@workshop/ui";
import { useNavigate } from "react-router-dom";
import type { Notice } from "./dictationState";

// Timed once, when it mounts: the whole span from when the notice was shown,
// started as far in as time has already gone. The bar re-renders many times a
// second (the microphone level), and a duration worked out again on each one
// made the ring jump ahead and stop early.
function Countdown({ since, until }: { since: number; until: number }) {
  const [style] = useState<CSSProperties>(() => ({
    animationDuration: `${until - since}ms`,
    animationDelay: `${since - Date.now()}ms`,
  }));
  return (
    <svg className="ws-dictpill-countdown" viewBox="0 0 20 20" aria-hidden>
      <circle cx="10" cy="10" r="8" style={style} />
    </svg>
  );
}

function CopyWords({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button size="sm" variant="ghost" onClick={() => void copyText(text).then(() => setDone(true))}>
      {done ? <Check aria-hidden /> : <Copy aria-hidden />} {done ? "Copied" : "Copy"}
    </Button>
  );
}

export function PillNotice({ notice }: { notice: Notice }) {
  const navigate = useNavigate();
  return (
    <span className="ws-dictpill-notice" role="status" aria-live="polite">
      <span className="ws-dictpill-notice__text" title={notice.text}>{notice.text}</span>
      {notice.href && (
        <Button size="sm" variant="ghost" onClick={() => navigate(notice.href!)}>
          <ArrowUpRight aria-hidden /> Open
        </Button>
      )}
      {notice.copy && <CopyWords text={notice.copy} />}
      {notice.action && <Button size="sm" variant="ghost" onClick={notice.action.run}>{notice.action.label}</Button>}
      {notice.cancel && (
        <Button size="sm" variant="ghost" onClick={notice.cancel}>
          {notice.until !== null && <Countdown key={notice.id} since={notice.since} until={notice.until} />} Cancel
        </Button>
      )}
    </span>
  );
}
