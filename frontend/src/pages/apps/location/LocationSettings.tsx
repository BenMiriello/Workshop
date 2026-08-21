import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MapPin, Trash2, Eye, EyeOff, Copy, Check } from "lucide-react";
import { Button, ConfirmDialog, StatusDot, SettingsSection, SettingsRow as Row, timeAgo, usePoll } from "@workshop/ui";

// Location app settings — Owntracks status + privacy. Moved verbatim out of the
// platform Settings page (Phase 5 settings split) so it lives inside the app it
// configures. Renders inside the Location page's <AppSettings> slot.

type LocationLatest = {
  ts?: string;
  lat?: number;
  lng?: number;
  accuracy_m?: number;
  source?: string;
} | null;

export default function LocationSettings() {
  const [latest, setLatest] = useState<LocationLatest>(null);
  const [, setTick] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/location/latest");
      const j = r.ok ? await r.json() : null;
      setLatest((j as LocationLatest) || null);
    } catch {
      setLatest(null);
    }
  }, []);

  usePoll(load, 30_000);

  // Relative "last ping" label re-render, not a server read: pausing it while
  // hidden is harmless and it is not what usePoll is for.
  useEffect(() => {
    const tick = setInterval(() => setTick((x) => x + 1), 10_000);
    return () => clearInterval(tick);
  }, []);

  const ageS = latest?.ts ? Math.max(0, Math.round((Date.now() - Date.parse(latest.ts)) / 1000)) : null;
  const stale = ageS != null && ageS > 30 * 60; // > 30min with no ping → likely a problem
  const noData = !latest?.ts;

  const onClearHistory = async () => {
    setClearing(true);
    try {
      await fetch("/api/location/history", { method: "DELETE" });
      await load();
    } finally {
      setClearing(false);
      setConfirmClear(false);
    }
  };

  return (
    <SettingsSection icon={MapPin} title="Location">
      <Row label="status">
        {noData ? (
          <StatusDot status="danger">no data, Owntracks not publishing yet</StatusDot>
        ) : stale ? (
          <StatusDot status="warn">stale ({timeAgo(latest!.ts!)}), check Owntracks app</StatusDot>
        ) : (
          <StatusDot status="ok">live · last ping {timeAgo(latest!.ts!)}</StatusDot>
        )}
      </Row>
      <Row label="source">{latest?.source || "–"}</Row>
      {latest?.lat != null && (
        <Row label="position">
          <span className="ws-mono" style={{ fontSize: "var(--fs-sm)" }}>
            {latest.lat.toFixed(5)}, {latest.lng?.toFixed(5)}
            {latest.accuracy_m != null && ` · ±${Math.round(latest.accuracy_m)}m`}
          </span>
        </Row>
      )}
      <Row label="iOS app">
        <a href="https://owntracks.org/booklet/features/ios/" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Owntracks
        </a>
      </Row>
      <OwntracksCredentials />
      <Row label="history">
        <Link to="/apps/location" style={{ color: "var(--accent)" }}>full history viewer ↗</Link>
        {" · "}
        <Button size="sm" variant="danger-ghost" onClick={() => setConfirmClear(true)}>
          <Trash2 aria-hidden /> Clear all
        </Button>
      </Row>
      <ConfirmDialog
        open={confirmClear}
        onCancel={() => setConfirmClear(false)}
        onConfirm={onClearHistory}
        title="Clear all location history?"
        body="Every recorded position is deleted. This can't be undone."
        confirmLabel="Clear history"
        busy={clearing}
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
