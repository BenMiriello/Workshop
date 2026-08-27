// The health app's settings panel.
//
// This used to be two functions inside pages/Settings.tsx, which meant the core
// settings screen knew the App Store id of one iOS app, the shape of one app's
// metrics payload, and how to erase one app's history. Settings now renders
// whatever the installed apps declare (apps/registry.tsx `Settings`), and this
// panel appears only while the health app is installed.
//
// THE READ IS THE QUERY LAYER'S (`./queries.ts`). It was a `usePoll` timer over
// a hand-rolled `useState`, which is the pattern the client-state audit found
// everywhere: the hook returns void, so every call site re-invented data,
// loading and error itself. Those three are gone here; what is left is what is
// genuinely local (which dialog is open, and a re-render tick for a relative
// label).

import { useEffect, useState } from "react";
import { Check, Copy, Eye, EyeOff, Heart, Trash2 } from "lucide-react";
import {
  Button, ConfirmDialog, Notice, Skeleton, StatusDot,
  SettingsSection as Section, SettingsRow as Row, timeAgo,
} from "@workshop/ui";
import { useClearHealthHistory, useHealthLatest } from "./queries";

/** Metrics listed before the panel cuts off; this is a summary, not the app. */
const SHOWN_METRICS = 8;

export default function HealthSettings() {
  const [confirmClear, setConfirmClear] = useState(false);
  const [, setTick] = useState(0);

  const latest = useHealthLatest();
  const clear = useClearHealthHistory();

  // Relative "last reading" label re-render, not a server read: pausing it
  // while hidden is harmless and it is not what the poll is for.
  useEffect(() => {
    const tick = setInterval(() => setTick((x) => x + 1), 15_000);
    return () => clearInterval(tick);
  }, []);

  // A FAILED read keeps whatever was last read, exactly as the `useState` this
  // replaces did. `{}` is the shape of "nothing pushed yet", which is a real
  // answer from this route and not the same thing as a failure.
  const entries = Object.entries(latest.data ?? {});
  // Latest ts across all metrics -> top-line status
  const latestTs = entries.reduce<string | null>((acc, [, v]) => {
    if (!v?.ts) return acc;
    if (!acc) return v.ts;
    return v.ts > acc ? v.ts : acc;
  }, null);
  const ageS = latestTs ? Math.max(0, Math.round((Date.now() - Date.parse(latestTs)) / 1000)) : null;
  const stale = ageS != null && ageS > 24 * 3600; // > 24h with no push -> likely a problem

  return (
    <Section icon={Heart} title="Health (Apple Health)">
      <Row label="status">
        {latest.isPending ? (
          <Skeleton width="14rem" />
        ) : latestTs ? (
          stale ? (
            <StatusDot status="warn">stale ({timeAgo(latestTs)}), check Health Auto Export</StatusDot>
          ) : (
            <StatusDot status="ok">{entries.length} metric{entries.length === 1 ? "" : "s"} · last push {timeAgo(latestTs)}</StatusDot>
          )
        ) : latest.isError ? (
          // NOT "no data": the read failed, so the panel does not know whether
          // anything has ever been pushed. The line below says which it was.
          <span className="ws-muted">unknown</span>
        ) : (
          <StatusDot status="danger">no data, install Health Auto Export</StatusDot>
        )}
      </Row>
      {/* A LINE, not a region: the rest of the panel (the endpoint, the auth,
          the app link) is still on screen and one thing beside it went wrong.
          A sibling of the Row rather than a child, because SettingsRow wraps
          its children in a span and Notice is a div. */}
      {latest.isError && (
        <Notice
          tone="error"
          where="/api/health/latest"
          action={<Button size="sm" variant="ghost" onClick={() => void latest.refetch()}>Retry</Button>}
        >
          Could not load the latest metrics.
        </Notice>
      )}
      <Row label="iOS app">
        <a className="ws-link" href="https://apps.apple.com/us/app/health-auto-export/id1115567069" target="_blank" rel="noopener noreferrer">
          Health Auto Export
        </a>{" · "}~$5 · config walkthrough at <code style={{ fontSize: "0.8rem" }}>docs/health-auto-export-ios.md</code>
      </Row>
      <HealthCredentials />
      {entries.slice(0, SHOWN_METRICS).map(([name, v]) => (
        <Row key={name} label={name}>
          <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
            {String(v.qty ?? "–")}{v.units ? ` ${v.units}` : ""}
            {v.date && <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.78rem" }}>{v.date}</span>}
          </span>
        </Row>
      ))}
      <Row label="history">
        <Button size="sm" variant="danger-ghost" onClick={() => { clear.reset(); setConfirmClear(true); }}>
          <Trash2 aria-hidden /> Clear all data
        </Button>
      </Row>
      <ConfirmDialog
        open={confirmClear}
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => clear.mutate(undefined, { onSuccess: () => setConfirmClear(false) })}
        title="Erase all stored Apple Health data?"
        body={
          <>
            Deletes every Apple Health metric Workshop has stored. This can't be undone, and the
            data only comes back if Health Auto Export pushes it again.
            {/* The form is right here, so a rejected submit is a line inside it,
                never a region (frontend/CLAUDE.md, "A region or a line"). The
                dialog stays open so the operator can try again. */}
            {clear.isError && (
              <Notice tone="error" detail={clear.error.message}>Nothing was deleted.</Notice>
            )}
          </>
        }
        confirmLabel="Erase health data"
        busy={clear.isPending}
      />
    </Section>
  );
}

function HealthCredentials() {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const url = `${window.location.origin}/api/health/ingest`;

  const copy = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      setTimeout(() => setCopied(null), 1200);
    } catch { /* ignore */ }
  };

  return (
    <>
      <Row label="endpoint URL">
        <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.78rem", wordBreak: "break-all" }}>{url}</span>
        <button onClick={() => copy("health-url", url)} style={{ marginLeft: "0.4rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }}>
          {copied === "health-url" ? <Check size={12} strokeWidth={2} /> : <Copy size={12} strokeWidth={1.8} />}
        </button>
      </Row>
      <Row label="auth">
        Basic · user <code style={{ fontSize: "0.8rem" }}>workshop</code> · pass{" "}
        <button onClick={() => setRevealed((v) => !v)} style={{ marginLeft: "0.3rem", padding: "0.2rem 0.4rem", fontSize: "0.75rem" }}>
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
