// The health app's settings panel.
//
// This used to be two functions inside pages/Settings.tsx, which meant the core
// settings screen knew the App Store id of one iOS app, the shape of one app's
// metrics payload, and how to erase one app's history. Settings now renders
// whatever the installed apps declare (apps/registry.tsx `Settings`), and this
// panel appears only while the health app is installed.

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Eye, EyeOff, Heart } from "lucide-react";
import { ConfirmDialog, StatusDot, SettingsSection as Section, SettingsRow as Row, timeAgo, usePoll } from "@workshop/ui";

export default function HealthSettings() {
  const [metrics, setMetrics] = useState<Record<string, { ts?: string; date?: string; qty?: unknown; units?: string }> | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/health/latest");
      const j = r.ok ? await r.json() : {};
      setMetrics(j || {});
    } catch {
      setMetrics({});
    }
  }, []);

  usePoll(load, 60_000);

  // Relative "last reading" label re-render, not a server read: pausing it
  // while hidden is harmless and it is not what usePoll is for.
  useEffect(() => {
    const tick = setInterval(() => setTick((x) => x + 1), 15_000);
    return () => clearInterval(tick);
  }, []);

  const onClear = async () => {
    setClearing(true);
    try {
      await fetch("/api/health/history", { method: "DELETE" });
      await load();
    } finally {
      setClearing(false);
      setConfirmClear(false);
    }
  };

  const entries = metrics ? Object.entries(metrics) : [];
  // Latest ts across all metrics → top-line status
  const latestTs = entries.reduce<string | null>((acc, [, v]) => {
    if (!v?.ts) return acc;
    if (!acc) return v.ts;
    return v.ts > acc ? v.ts : acc;
  }, null);
  const ageS = latestTs ? Math.max(0, Math.round((Date.now() - Date.parse(latestTs)) / 1000)) : null;
  const stale = ageS != null && ageS > 24 * 3600; // > 24h with no push → likely a problem

  return (
    <Section icon={Heart} title="Health (Apple Health)">
      <Row label="status">
        {!latestTs ? (
          <StatusDot status="danger">no data, install Health Auto Export</StatusDot>
        ) : stale ? (
          <StatusDot status="warn">stale ({timeAgo(latestTs)}), check Health Auto Export</StatusDot>
        ) : (
          <StatusDot status="ok">{entries.length} metric{entries.length === 1 ? "" : "s"} · last push {timeAgo(latestTs)}</StatusDot>
        )}
      </Row>
      <Row label="iOS app">
        <a href="https://apps.apple.com/us/app/health-auto-export/id1115567069" target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
          Health Auto Export
        </a>{" · "}~$5 · config walkthrough at <code style={{ fontSize: "0.8rem" }}>docs/health-auto-export-ios.md</code>
      </Row>
      <HealthCredentials />
      {entries.slice(0, 8).map(([name, v]) => (
        <Row key={name} label={name}>
          <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>
            {String(v.qty ?? "–")}{v.units ? ` ${v.units}` : ""}
            {v.date && <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.78rem" }}>{v.date}</span>}
          </span>
        </Row>
      ))}
      <Row label="history">
        <button onClick={() => setConfirmClear(true)} style={{ padding: "0.2rem 0.6rem", fontSize: "0.8rem", color: "var(--err)" }}>
          clear all data
        </button>
      </Row>
      <ConfirmDialog
        open={confirmClear}
        onCancel={() => setConfirmClear(false)}
        onConfirm={onClear}
        title="Erase all stored Apple Health data?"
        body="Deletes every Apple Health metric Workshop has stored. This can't be undone, and the data only comes back if Health Auto Export pushes it again."
        confirmLabel="Erase health data"
        busy={clearing}
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
