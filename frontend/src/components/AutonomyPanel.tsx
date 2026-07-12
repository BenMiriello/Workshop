import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Repeat, Play, RefreshCw, ScrollText } from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusPill,
  Segmented, Modal, Skeleton, type StatusKind,
} from "../ui";

// Per-project autonomy = a project's OWN Ralph loop over its OWN TODO.md + git
// repo (scripts/project_ralph.sh). Backend: routes/projects.py /autonomy*. Mode
// is the per-project switch; the global cron off-switch (Settings) is a separate
// master kill. A manual "Run now" is a deliberate foreground sweep and bypasses
// the master switch; `off` still refuses.
//
// Mode commits on change — it is a low-risk, reversible behavior switch (like an
// enable/disable toggle), and the expensive action (a sweep) is its own explicit
// button. The sweep drives each open task through Ralph until the reviewer gate
// passes, marking tasks done in the project's structured store.

type Mode = "off" | "manual" | "continuous";

type LastRun = {
  status?: string;
  updated?: string;
  mode?: string;
  reason?: string;
  task_id?: string;
  done?: number;
  last?: { done: number; incomplete: number; blocked: number; failed: number; duration_s: number };
} | null;

type Autonomy = {
  mode: Mode;
  updated: string | null;
  running: boolean;
  last_run: LastRun;
};

const MODE_OPTIONS: { value: Mode; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "manual", label: "Manual" },
  { value: "continuous", label: "Continuous" },
];

const MODE_HINT: Record<Mode, string> = {
  off: "Never runs. Enable Manual or Continuous to let this project work its own queue.",
  manual: "Runs only when you press Run now. Nothing happens on its own.",
  continuous: "Runs on Run now, and is eligible for background sweeps by the autonomy cron.",
};

function fmtTs(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

export default function AutonomyPanel({ slug }: { slug: string }) {
  const [cfg, setCfg] = useState<Autonomy | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [logText, setLogText] = useState("");
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/projects/${slug}/autonomy`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setCfg(await r.json());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  // Poll while a sweep is running so the live state is reflected without a
  // manual refresh; back off to idle otherwise.
  const running = cfg?.running ?? false;
  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(load, running ? 2500 : 15000) as unknown as number;
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [running, load]);

  const setMode = useCallback(async (mode: Mode) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/projects/${slug}/autonomy`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slug, load]);

  const runNow = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/projects/${slug}/autonomy/run`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      // A sweep kicks off background work; nudge a couple of refreshes so the
      // running state appears promptly.
      setTimeout(load, 1200);
      setTimeout(load, 3500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slug, load]);

  const openLog = useCallback(async () => {
    setLogOpen(true);
    setLogText("Loading…");
    try {
      const r = await fetch(`/api/projects/${slug}/autonomy/log?tail=300`);
      const d = await r.json();
      setLogText(d.log || "(no output yet — run a sweep to populate)");
    } catch (e) {
      setLogText(String(e));
    }
  }, [slug]);

  const mode: Mode = cfg?.mode ?? "off";
  const last = cfg?.last_run ?? null;

  const runKind: StatusKind = useMemo(() => {
    if (running) return "running";
    const s = last?.status;
    if (s === "crashed") return "danger";
    if (s === "idle") return "ok";
    return "stopped";
  }, [running, last]);
  const runLabel = running ? "running" : (last?.status === "idle" ? "idle" : (last?.status ?? "never run"));

  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel icon={<Repeat aria-hidden />}>Autonomy</SectionLabel>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {cfg && <StatusPill status={runKind}>{runLabel}</StatusPill>}
          <Button size="sm" variant="ghost" onClick={load}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
          <Button
            size="sm"
            variant="primary"
            loading={busy && !running}
            disabled={busy || running || mode === "off"}
            onClick={runNow}
          >
            <Play aria-hidden /> Run now
          </Button>
        </Row>
      </Row>

      <Card>
        {loading && !cfg ? (
          <Stack gap="sm"><Skeleton width="55%" /><Skeleton width="40%" /></Stack>
        ) : err && !cfg ? (
          <StateBlock
            variant="error"
            title="Couldn't load autonomy config"
            hint={<code>GET /api/projects/{slug}/autonomy — {err}</code>}
            action={<Button size="sm" onClick={load}>Retry</Button>}
          />
        ) : (
          <Stack>
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
              Let this project drive its own <span className="ws-mono">TODO.md</span> autonomously: each
              open task runs through the Ralph loop against the project's own git repo until the reviewer
              gate passes. The project brief (<span className="ws-mono">PROJECT.md</span>) and{" "}
              <span className="ws-mono">AGENTS.md</span> are what the worker sees.
            </p>

            <Segmented<Mode>
              ariaLabel="Autonomy mode"
              value={mode}
              onChange={(m) => { if (m !== mode) setMode(m); }}
              options={MODE_OPTIONS}
              hint={MODE_HINT[mode]}
            />

            {err && cfg && (
              <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{err}</p>
            )}

            <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
              <Button size="sm" variant="ghost" onClick={openLog}>
                <ScrollText aria-hidden /> Sweep log
              </Button>
              {cfg?.updated && (
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                  Mode set {fmtTs(cfg.updated)}
                </span>
              )}
            </Row>

            {running && last?.task_id && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Working task <span className="ws-mono">{last.task_id.slice(0, 8)}</span>
                {typeof last.done === "number" ? ` · ${last.done} done so far` : ""}.
              </p>
            )}

            {!running && last?.last && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Last sweep {fmtTs(last.updated)}: {last.last.done} done
                {last.last.incomplete ? `, ${last.last.incomplete} incomplete` : ""}
                {last.last.blocked ? `, ${last.last.blocked} blocked` : ""}
                {last.last.failed ? `, ${last.last.failed} failed` : ""} in {last.last.duration_s}s.
              </p>
            )}

            {!running && last?.status === "crashed" && (
              <p style={{ color: "var(--danger)", fontSize: "var(--fs-xs)", margin: 0 }}>
                The last sweep exited without finishing cleanly. Check the sweep log.
              </p>
            )}
          </Stack>
        )}
      </Card>

      <Modal open={logOpen} onClose={() => setLogOpen(false)} title="Sweep log">
        <pre className="ws-mono" style={{
          maxHeight: "60vh", overflow: "auto", fontSize: "var(--fs-xs)",
          background: "var(--surface-2)", padding: "var(--space-3)", borderRadius: "var(--radius-2)",
          whiteSpace: "pre-wrap", margin: 0,
        }}>
          {logText}
        </pre>
        <Row end style={{ marginTop: "var(--space-3)" }}>
          <Button size="sm" variant="ghost" onClick={openLog}><RefreshCw aria-hidden /> Reload</Button>
        </Row>
      </Modal>
    </section>
  );
}
