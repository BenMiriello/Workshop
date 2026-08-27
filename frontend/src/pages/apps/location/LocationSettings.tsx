import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MapPin, Trash2, Eye, EyeOff, Copy, Check, ArrowUpRight } from "lucide-react";
import {
  Button, ConfirmDialog, Notice, Skeleton, StatusDot,
  SettingsSection, SettingsRow as Row, timeAgo,
} from "@workshop/ui";
import { useClearLocationHistory, useLatestLocation } from "./queries";

// Location app settings: Owntracks status + privacy. Renders inside the
// Location page's <AppSettings> slot, and also stands alone on the platform
// Settings page.
//
// IT READS THE SAME CACHE ENTRY AS THE PAGE AROUND IT. This panel used to poll
// `/api/location/latest` on its own 30s timer into its own `useState`, while
// the page it renders inside polled the same URL on another one. Two timers on
// one fact meant the position quoted here and the position quoted six inches
// above it could be a full interval apart. Both now call `useLatestLocation()`
// (./queries.ts), so there is one key, one value, and no way for the two to
// disagree. The clear-history write is shared the same way, so the panel and
// the page cannot invalidate different things.

export default function LocationSettings() {
  const [, setTick] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);

  const latestQ = useLatestLocation();
  const clear = useClearLocationHistory();
  const latest = latestQ.data ?? null;

  // Relative "last ping" label re-render, not a server read: pausing it while
  // hidden is harmless and it is not what the poll is for.
  useEffect(() => {
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => clearInterval(tick);
  }, []);

  const ageS = latest?.ts ? Math.max(0, Math.round((Date.now() - Date.parse(latest.ts)) / 1000)) : null;
  const stale = ageS != null && ageS > 30 * 60; // > 30min with no ping -> likely a problem

  return (
    <SettingsSection icon={MapPin} title="Location">
      <Row label="status">
        {latestQ.isPending ? (
          <Skeleton width="14rem" />
        ) : latest?.ts ? (
          stale ? (
            <StatusDot status="warn">stale ({timeAgo(latest.ts)}), check Owntracks app</StatusDot>
          ) : (
            <StatusDot status="ok">live · last ping {timeAgo(latest.ts)}</StatusDot>
          )
        ) : latestQ.isError ? (
          // NOT "no data": a 200 with a null body means nothing has ever been
          // recorded, a failed request means the panel does not know. The line
          // below says which one this is.
          <span className="ws-muted">unknown</span>
        ) : (
          <StatusDot status="danger">no data, Owntracks not publishing yet</StatusDot>
        )}
      </Row>
      <Row label="source">{latest?.source || "–"}</Row>
      {latest?.lat != null && (
        <Row label="position">
          <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
            {latest.lat.toFixed(5)}, {latest.lng.toFixed(5)}
            {latest.accuracy_m != null && ` · ±${Math.round(latest.accuracy_m)}m`}
          </span>
        </Row>
      )}
      {/* A LINE, not a region: the rest of the panel is still on screen and one
          thing beside it went wrong. A sibling of the Row rather than a child,
          because SettingsRow wraps its children in a span and Notice is a div. */}
      {latestQ.isError && (
        <Notice
          tone="error"
          where="/api/location/latest"
          action={<Button size="sm" variant="ghost" onClick={() => void latestQ.refetch()}>Retry</Button>}
        >
          Could not load the latest position.
        </Notice>
      )}
      <Row label="iOS app">
        <a className="ws-link" href="https://owntracks.org/booklet/features/ios/" target="_blank" rel="noopener noreferrer">
          Owntracks
        </a>
      </Row>
      <OwntracksCredentials />
      <Row label="history">
        <Link className="ws-link" to="/apps/location">
          full history viewer <ArrowUpRight size={14} strokeWidth={1.8} aria-hidden />
        </Link>
        {" · "}
        <Button size="sm" variant="danger-ghost" onClick={() => { clear.reset(); setConfirmClear(true); }}>
          <Trash2 aria-hidden /> Clear all
        </Button>
      </Row>
      <ConfirmDialog
        open={confirmClear}
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => clear.mutate(undefined, { onSuccess: () => setConfirmClear(false) })}
        title="Clear all location history?"
        body={
          <>
            Every recorded position is deleted. This can't be undone.
            {/* The form is right here, so a rejected submit is a line inside it,
                never a region (frontend/CLAUDE.md, "A region or a line"). The
                write used to be fire and forget: a failed DELETE closed the
                dialog and said nothing. */}
            {clear.isError && (
              <Notice tone="error" detail={clear.error.message}>Nothing was deleted.</Notice>
            )}
          </>
        }
        confirmLabel="Clear history"
        busy={clear.isPending}
      />
    </SettingsSection>
  );
}

function OwntracksCredentials() {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const url = `${window.location.origin}/api/location/owntracks`;

  const copy = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      // ignore
    }
  };

  return (
    <>
      <Row label="endpoint URL">
        <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.78rem", wordBreak: "break-all" }}>{url}</span>
        <button onClick={() => copy("url", url)} style={{ marginLeft: "0.4rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }}>
          {copied === "url" ? <Check size={12} strokeWidth={2} /> : <Copy size={12} strokeWidth={1.8} />}
        </button>
      </Row>
      <Row label="auth">
        Basic · user <code style={{ fontSize: "0.8rem" }}>workshop</code> · pass{" "}
        <button onClick={() => setRevealed((v) => !v)} style={{ marginLeft: "0.3rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }} title={revealed ? "hide" : "reveal"}>
          {revealed ? <EyeOff size={12} strokeWidth={1.8} /> : <Eye size={12} strokeWidth={1.8} />}
        </button>
        {revealed && (
          <span style={{ marginLeft: "0.4rem", fontSize: "0.75rem", color: "var(--muted)" }}>
            from Workshop's saved credentials
          </span>
        )}
      </Row>
    </>
  );
}
