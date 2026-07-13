import { useCallback, useEffect, useRef, useState } from "react";
import { Repeat, Play, RefreshCw, X, Clock, Zap, RotateCw } from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusPill,
  Skeleton, Input, Field, ActionBar, Tag, catFor, useEditMode, type StatusKind,
} from "../ui";

// Per-project autonomy = a project's OWN Ralph loop over its OWN TODO.md + git
// repo (scripts/project_ralph.sh). The config surface is now a unified trigger
// list (services.triggers), not a single mode switch: the operator composes the
// triggers that fire this project's work. `loop` carries an iteration cap so an
// unattended loop is bounded. Writes go through PUT /{slug}/autonomy as a config
// write: edit-mode + explicit Save (never commit-on-change). A manual "Run now"
// is a deliberate foreground sweep and is a separate button.

type TriggerKind = "manual" | "cron" | "event" | "loop";

type Trigger = {
  kind: TriggerKind;
  cron_expression?: string;
  event_sources?: string[];
  max_iterations?: number;
  budget?: { max_wall_s?: number };
  completion_gate?: string;
};

type Job = {
  id: string;
  status?: string;
  trigger?: string;
  started_at?: string;
  ended_at?: string;
  tasks_done?: number;
  tasks_failed?: number;
} | null;

type Autonomy = {
  triggers: Trigger[];
  mode: string;
  updated: string | null;
  running: boolean;
  last_run: Job;
};

const KNOWN_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"];

function fmtTs(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

function jobKind(status?: string): StatusKind {
  if (status === "running") return "running";
  if (status === "done") return "ok";
  if (status === "failed") return "danger";
  if (status === "awaiting_input") return "warn";
  return "stopped";
}

function newTrigger(kind: TriggerKind): Trigger {
  if (kind === "cron") return { kind, cron_expression: "" };
  if (kind === "event") return { kind, event_sources: [] };
  if (kind === "loop") return { kind, max_iterations: 20, budget: { max_wall_s: 3000 }, completion_gate: "reviewer" };
  return { kind };
}

function triggerIcon(kind: TriggerKind) {
  if (kind === "cron") return <Clock aria-hidden />;
  if (kind === "event") return <Zap aria-hidden />;
  if (kind === "loop") return <RotateCw aria-hidden />;
  return <Play aria-hidden />;
}

function triggerSummary(t: Trigger): string {
  if (t.kind === "cron") return t.cron_expression ? `schedule ${t.cron_expression}` : "schedule (unset)";
  if (t.kind === "event") return (t.event_sources && t.event_sources.length) ? `on ${t.event_sources.join(", ")}` : "on (no sources)";
  if (t.kind === "loop") return `loop, cap ${t.max_iterations ?? 20} tasks`;
  return "manual run only";
}

export default function AutonomyPanel({ slug }: { slug: string }) {
  const [cfg, setCfg] = useState<Autonomy | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
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

  const running = cfg?.running ?? false;
  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = window.setInterval(load, running ? 2500 : 15000) as unknown as number;
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [running, load]);

  const edit = useEditMode<{ triggers: Trigger[] }>(
    { triggers: cfg?.triggers ?? [] },
    async (draft) => {
      setSaveErr(null);
      const r = await fetch(`/api/projects/${slug}/autonomy`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ triggers: draft.triggers }),
      });
      if (!r.ok) {
        const msg = `${r.status} ${await r.text()}`;
        setSaveErr(msg);
        throw new Error(msg);
      }
      await load();
    },
  );

  const runNow = useCallback(async () => {
    setBusy(true);
    setSaveErr(null);
    try {
      const r = await fetch(`/api/projects/${slug}/autonomy/run`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setTimeout(load, 1200);
      setTimeout(load, 3500);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [slug, load]);

  const triggers = edit.editing ? edit.draft.triggers : (cfg?.triggers ?? []);

  const addTrigger = (kind: TriggerKind) => {
    edit.set("triggers", [...edit.draft.triggers, newTrigger(kind)]);
  };
  const removeTrigger = (idx: number) => {
    edit.set("triggers", edit.draft.triggers.filter((_, i) => i !== idx));
  };
  const patchTrigger = (idx: number, patch: Partial<Trigger>) => {
    edit.set("triggers", edit.draft.triggers.map((t, i) => (i === idx ? { ...t, ...patch } : t)));
  };

  const last = cfg?.last_run ?? null;

  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel icon={<Repeat aria-hidden />}>Autonomy</SectionLabel>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {cfg && (
            <StatusPill status={running ? "running" : jobKind(last?.status)}>
              {running ? "running" : (last?.status ?? "never run")}
            </StatusPill>
          )}
          <Button size="sm" variant="ghost" onClick={load}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
          {!edit.editing && (
            <Button size="sm" variant="ghost" onClick={edit.begin} disabled={!cfg}>
              Edit triggers
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            loading={busy && !running}
            disabled={busy || running || edit.editing}
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
            hint={<code>GET /api/projects/{slug}/autonomy: {err}</code>}
            action={<Button size="sm" onClick={load}>Retry</Button>}
          />
        ) : (
          <Stack>
            <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
              Triggers decide when this project works its own <span className="ws-mono">TODO.md</span> through the
              Ralph loop against its git repo. With no triggers it only runs on <strong>Run now</strong>. A{" "}
              <span className="ws-mono">loop</span> trigger makes it eligible for unattended background sweeps,
              bounded by its iteration cap.
            </p>

            {triggers.length === 0 && !edit.editing && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                No triggers. Manual runs only.
              </p>
            )}

            <Stack gap="sm">
              {triggers.map((t, i) => (
                <Row key={i} between wrap style={{ gap: "var(--space-2)", alignItems: "flex-start" }}>
                  <Row style={{ gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}>
                    <Tag hue={catFor(t.kind)}>{t.kind}</Tag>
                    {!edit.editing && (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{triggerSummary(t)}</span>
                    )}
                    {edit.editing && t.kind === "cron" && (
                      <Field label="Cron expression" htmlFor={`cron-${i}`}>
                        <Input
                          id={`cron-${i}`}
                          value={t.cron_expression ?? ""}
                          placeholder="0 */6 * * *"
                          onChange={(e) => patchTrigger(i, { cron_expression: e.target.value })}
                        />
                      </Field>
                    )}
                    {edit.editing && t.kind === "event" && (
                      <Field label="Sources (comma separated)" htmlFor={`ev-${i}`} help={KNOWN_SOURCES.join(", ")}>
                        <Input
                          id={`ev-${i}`}
                          value={(t.event_sources ?? []).join(", ")}
                          placeholder="plaid, github"
                          onChange={(e) => patchTrigger(i, {
                            event_sources: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                          })}
                        />
                      </Field>
                    )}
                    {edit.editing && t.kind === "loop" && (
                      <Field label="Iteration cap (tasks per sweep)" htmlFor={`loop-${i}`}>
                        <Input
                          id={`loop-${i}`}
                          type="number"
                          min={1}
                          value={t.max_iterations ?? 20}
                          onChange={(e) => patchTrigger(i, { max_iterations: Math.max(1, Number(e.target.value) || 1) })}
                        />
                      </Field>
                    )}
                  </Row>
                  {edit.editing && (
                    <Button size="sm" variant="ghost" onClick={() => removeTrigger(i)} aria-label={`Remove ${t.kind} trigger`}>
                      <X aria-hidden /> Remove
                    </Button>
                  )}
                </Row>
              ))}
            </Stack>

            {edit.editing && (
              <Row wrap style={{ gap: "var(--space-2)" }}>
                <Button size="sm" variant="ghost" onClick={() => addTrigger("cron")}>
                  {triggerIcon("cron")} Add cron
                </Button>
                <Button size="sm" variant="ghost" onClick={() => addTrigger("event")}>
                  {triggerIcon("event")} Add event
                </Button>
                <Button size="sm" variant="ghost" onClick={() => addTrigger("loop")}>
                  {triggerIcon("loop")} Add loop
                </Button>
              </Row>
            )}

            {saveErr && (
              <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{saveErr}</p>
            )}

            {!edit.editing && cfg?.updated && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Triggers set {fmtTs(cfg.updated)}
              </span>
            )}

            {!edit.editing && running && last?.trigger && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Sweep running (trigger <span className="ws-mono">{last.trigger}</span>, started {fmtTs(last.started_at)}).
              </p>
            )}

            {!edit.editing && !running && last && (
              <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                Last run {fmtTs(last.started_at)}: {last.status}
                {typeof last.tasks_done === "number" ? `, ${last.tasks_done} done` : ""}
                {typeof last.tasks_failed === "number" && last.tasks_failed > 0 ? `, ${last.tasks_failed} failed` : ""}.
              </p>
            )}

            {edit.editing && (
              <ActionBar flash={edit.flash ? "Saved" : undefined}>
                <Button size="sm" variant="primary" onClick={edit.save} loading={edit.saving} disabled={!edit.dirty}>
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={() => { setSaveErr(null); edit.cancel(); }}>
                  Cancel
                </Button>
              </ActionBar>
            )}
          </Stack>
        )}
      </Card>
    </section>
  );
}
