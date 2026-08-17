import { useCallback, useMemo, useState } from "react";
import { usePoll } from "../lib/usePoll";
import {
  Repeat, Play, RefreshCw, X, Clock, Zap, RotateCw,
  ChevronDown, ChevronRight, HelpCircle, SlidersHorizontal, Compass,
} from "lucide-react";
import {
  Button, Card, Row, Stack, SectionLabel, StateBlock, StatusPill,
  Skeleton, Input, Field, ActionBar, Tag, Segmented, catFor,
  useEditMode, type StatusKind, type SegmentedOption,
} from "@workshop/ui";
import { LATITUDES, DEFAULT_LATITUDE, latitudeDesc, type Latitude } from "../lib/tasks";
import { isAutonomyMode, type AutonomyMode } from "../lib/autonomy";
import { jobStatusKind } from "../lib/jobs";
import { timeAbsolute } from "../lib/time";
import { shortHttpError } from "../lib/httpError";

// Autonomy = how a scope (a project OR an agent) decides when to work its own
// task queue through the Ralph loop. The PRIMARY control is a preset (Section
// 10.4 of orchestration-and-autonomy.md), not a raw trigger list:
//
//   off         no automatic runs; manual "Run now" only
//   on-demand   swept only by an explicit "Run now"
//   scheduled   swept on a cron cadence when work is ready
//   continuous  swept every tick while work is ready, bounded by limits
//
// latitude (strict/standard/open) sets what a sweep is TOLD to do WHERE THE
// TICKET DOES NOT SAY — decision rights, not effort. It is prompt text, and
// nothing here bounds what a run may write: `touches` is a declared intent
// checked against the committed diff AFTER the fact, so a stray write is
// flagged and the ticket blocked, not prevented. Limits bound a continuous
// loop. All of these are a CONFIG write: edit-mode + explicit Save (never
// commit-on-change per the design system). The raw expanded trigger list stays
// reachable under an "Advanced" disclosure for the operator who wants it. A
// manual "Run now" is a deliberate foreground sweep and stays a button.
//
// This panel used to offer `depth` (quick/standard/deep) and `fanout`
// (off/read-only) as well. Nothing anywhere read either one — no sweep, no
// runner, no prompt — so they were deleted rather than left as decoration
// (docs/system-honesty-plan-2026-08-10.md, item 19). Don't add them back
// without the code that honors them.
//
// The panel serves BOTH scopes off one prop set: a project (default) writes the
// preset directly to PUT /projects/{slug}/autonomy; an agent expands the preset
// to triggers and writes PUT /agents/{id}. The project callsite keeps working
// unchanged; agent scope is opt-in via the optional `scope`/`agentId` props.

type Preset = "off" | "on-demand" | "scheduled" | "continuous";
// AutonomyMode (the per-agent axis, distinct from the cadence preset above)
// comes from lib/autonomy, which owns that vocabulary.

type TriggerKind = "manual" | "cron" | "event" | "loop";

type Trigger = {
  kind: TriggerKind;
  cron_expression?: string;
  event_sources?: string[];
  max_iterations?: number;
  budget?: { max_wall_s?: number };
  completion_gate?: string;
};

type Limits = { max_iterations?: number; budget?: { max_wall_s?: number }; completion_gate?: string };

type Queue = {
  ready?: number; doing?: number; in_review?: number;
  blocked?: number; parked?: number; todo?: number;
};

type Awaiting = { id: string; status?: string; text?: string; question?: string; since?: string };

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
  autonomy: Preset;
  // Per-agent autonomy mode from the 2026-07-18 model. Populated for scope=
  // "agent"; ignored for scope="project" (project is a scope, not an agent).
  autonomy_mode?: AutonomyMode;
  // null = this scope sets none, so work here inherits the default. Kept null
  // (never defaulted to "standard") so the scope never claims a setting it lacks.
  latitude: Latitude | null;
  limits: Limits;
  triggers: Trigger[];
  queue?: Queue;
  awaiting?: Awaiting[];
  mode?: string;
  updated?: string | null;
  running?: boolean;
  last_run?: Job;
};

type PrimaryDraft = {
  preset: Preset;
  // Agent-scope autonomy mode. For project scope this field is present but the
  // control is not rendered and the Save path drops it.
  autonomy_mode: AutonomyMode;
  // The draft carries the default rather than null: the control always shows a
  // position. Saving `standard` clears the stored value back to null, which is
  // the same behavior — the scope stops asserting a latitude.
  latitude: Latitude;
  cron: string;
  maxIterations: string;
  budget: string;
};

const KNOWN_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"];

const PRESET_OPTIONS: SegmentedOption<Preset>[] = [
  { value: "off", label: "Off" },
  { value: "on-demand", label: "On demand" },
  { value: "scheduled", label: "Scheduled" },
  { value: "continuous", label: "Continuous" },
];

const AUTONOMY_MODE_OPTIONS: SegmentedOption<AutonomyMode>[] = [
  { value: "reactive", label: "Reactive" },
  { value: "supervised", label: "Supervised" },
  { value: "autonomous", label: "Autonomous" },
];

const AUTONOMY_MODE_DESC: Record<AutonomyMode, string> = {
  reactive: "Never runs in the background. Manual invocation only.",
  supervised: "Fires on triggers; results land in Review before commit.",
  autonomous: "Fires on triggers; results commit directly.",
};

const LATITUDE_OPTIONS: SegmentedOption<Latitude>[] = LATITUDES.map((v) => ({
  value: v,
  label: v.charAt(0).toUpperCase() + v.slice(1),
}));

const PRESET_DESC: Record<Preset, string> = {
  off: "No automatic runs. Manual Run now only.",
  "on-demand": "Runs only when you press Run now.",
  scheduled: "Runs on a cron schedule when work is ready.",
  continuous: "Sweeps every tick while work is ready, bounded by limits.",
};

function isPreset(v: unknown): v is Preset {
  return v === "off" || v === "on-demand" || v === "scheduled" || v === "continuous";
}
function isLatitude(v: unknown): v is Latitude {
  return v === "strict" || v === "standard" || v === "open";
}


function cronFromTriggers(triggers?: Trigger[]): string {
  const cron = (triggers || []).find((t) => t.kind === "cron");
  return cron?.cron_expression ?? "";
}

// Client mirror of backend triggers.preset_to_triggers, used for the agent
// scope (whose PUT accepts a trigger list, not a preset). The project scope
// sends the preset directly and lets the backend expand it.
function presetToTriggers(preset: Preset, cron: string, limits: Limits): Trigger[] {
  if (preset === "continuous") {
    return [{
      kind: "loop",
      max_iterations: limits.max_iterations ?? 20,
      budget: limits.budget ?? { max_wall_s: 3000 },
      completion_gate: limits.completion_gate ?? "reviewer",
    }];
  }
  if (preset === "scheduled") return [{ kind: "cron", cron_expression: cron || "" }];
  if (preset === "on-demand") return [{ kind: "manual" }];
  return [];
}

function draftLimits(draft: PrimaryDraft): Limits {
  const limits: Limits = {};
  const mi = draft.maxIterations.trim();
  if (mi) limits.max_iterations = Math.max(1, Number(mi) || 1);
  const bg = draft.budget.trim();
  if (bg) limits.budget = { max_wall_s: Math.max(1, Number(bg) || 1) };
  return limits;
}

// ---- Advanced (raw trigger list) helpers -----------------------------------

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

// ---- Component -------------------------------------------------------------

export default function AutonomyPanel({
  slug,
  scope = "project",
  agentId,
}: {
  slug: string;
  // Which scope this panel governs. Defaults to "project" so the existing
  // project callsite keeps its exact behavior. "agent" points the same UI at
  // the agent autonomy endpoints instead.
  scope?: "project" | "agent";
  // Agent identifier for scope === "agent". Falls back to `slug` when omitted,
  // so a caller can pass the agent id as `slug` alone if it prefers.
  agentId?: string;
}) {
  const scopeId = scope === "agent" ? (agentId ?? slug) : slug;
  const getUrl = scope === "agent"
    ? `/api/agents/${encodeURIComponent(scopeId)}`
    : `/api/projects/${encodeURIComponent(slug)}/autonomy`;
  const putUrl = getUrl;
  const runUrl = `/api/projects/${encodeURIComponent(slug)}/autonomy/run`;

  const [cfg, setCfg] = useState<Autonomy | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(getUrl);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const raw = await r.json();
      const cfgNext: Autonomy = {
        autonomy: isPreset(raw.autonomy) ? raw.autonomy : "off",
        autonomy_mode: isAutonomyMode(raw.autonomy_mode) ? raw.autonomy_mode : "reactive",
        latitude: isLatitude(raw.latitude) ? raw.latitude : null,
        limits: (raw.limits && typeof raw.limits === "object") ? raw.limits : {},
        triggers: Array.isArray(raw.triggers) ? raw.triggers : [],
        queue: raw.queue,
        awaiting: Array.isArray(raw.awaiting) ? raw.awaiting : [],
        mode: raw.mode,
        updated: raw.updated ?? null,
        running: !!raw.running,
        last_run: raw.last_run ?? null,
      };
      setCfg(cfgNext);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [getUrl]);

  const running = cfg?.running ?? false;
  usePoll(load, running ? 2500 : 15000);

  // Primary preset config: the main control. Edit-mode + explicit Save.
  const primaryModel = useMemo<PrimaryDraft>(() => ({
    preset: cfg?.autonomy ?? "off",
    autonomy_mode: cfg?.autonomy_mode ?? "reactive",
    latitude: cfg?.latitude ?? DEFAULT_LATITUDE,
    cron: cronFromTriggers(cfg?.triggers),
    maxIterations: cfg?.limits?.max_iterations != null ? String(cfg.limits.max_iterations) : "",
    budget: cfg?.limits?.budget?.max_wall_s != null ? String(cfg.limits.budget.max_wall_s) : "",
  }), [cfg]);

  const pedit = useEditMode<PrimaryDraft>(primaryModel, async (draft) => {
    setSaveErr(null);
    const limits = draftLimits(draft);
    let body: unknown;
    if (scope === "agent") {
      body = {
        triggers: presetToTriggers(draft.preset, draft.cron, limits),
        autonomy: draft.autonomy_mode,
        latitude: draft.latitude,
        limits,
      };
    } else {
      body = {
        preset: draft.preset,
        cron: draft.preset === "scheduled" ? draft.cron : undefined,
        latitude: draft.latitude,
        limits,
      };
    }
    const r = await fetch(putUrl, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const msg = shortHttpError(r.status, r.statusText, await r.text().catch(() => ""));
      setSaveErr(msg);
      throw new Error(msg);
    }
    await load();
  });

  // Advanced raw trigger list: the escape hatch. Writes the { triggers } path.
  const aedit = useEditMode<{ triggers: Trigger[] }>(
    { triggers: cfg?.triggers ?? [] },
    async (draft) => {
      setSaveErr(null);
      const r = await fetch(putUrl, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ triggers: draft.triggers }),
      });
      if (!r.ok) {
        const msg = shortHttpError(r.status, r.statusText, await r.text().catch(() => ""));
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
      const r = await fetch(runUrl, { method: "POST" });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setTimeout(load, 1200);
      setTimeout(load, 3500);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [runUrl, load]);

  const anyEditing = pedit.editing || aedit.editing;

  // Advanced trigger editor operations.
  const advTriggers = aedit.editing ? aedit.draft.triggers : (cfg?.triggers ?? []);
  const addTrigger = (kind: TriggerKind) => {
    aedit.set("triggers", [...aedit.draft.triggers, newTrigger(kind)]);
  };
  const removeTrigger = (idx: number) => {
    aedit.set("triggers", aedit.draft.triggers.filter((_, i) => i !== idx));
  };
  const patchTrigger = (idx: number, patch: Partial<Trigger>) => {
    aedit.set("triggers", aedit.draft.triggers.map((t, i) => (i === idx ? { ...t, ...patch } : t)));
  };

  const last = cfg?.last_run ?? null;
  const preset = cfg?.autonomy ?? "off";

  // Queue counts as small StatusPill badges (color is a signal: ready = ok,
  // in progress = running, in review = warn, blocked = danger).
  const q = cfg?.queue;
  const queueItems: { key: string; label: string; count: number; status: StatusKind }[] = q ? [
    { key: "ready", label: "ready", count: q.ready ?? 0, status: "ok" },
    { key: "doing", label: "in progress", count: q.doing ?? 0, status: "running" },
    { key: "in_review", label: "in review", count: q.in_review ?? 0, status: "warn" },
    { key: "blocked", label: "blocked", count: q.blocked ?? 0, status: "danger" },
  ] : [];
  const anyQueue = queueItems.some((i) => i.count > 0);
  const awaiting = cfg?.awaiting ?? [];

  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <Row between style={{ marginBottom: "var(--space-3)" }}>
        <SectionLabel icon={<Repeat aria-hidden />}>Autonomy</SectionLabel>
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {cfg && (scope === "project" ? (
            <StatusPill status={running ? "running" : jobStatusKind(last?.status)}>
              {running ? "running" : (last?.status ?? "never run")}
            </StatusPill>
          ) : (
            <Tag hue={catFor(preset)}>{preset}</Tag>
          ))}
          <Button size="sm" variant="ghost" onClick={load}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
          {!anyEditing && (
            <Button size="sm" variant="ghost" onClick={pedit.begin} disabled={!cfg}>
              <SlidersHorizontal aria-hidden /> Edit
            </Button>
          )}
          {scope === "project" && (
            <Button
              size="sm"
              variant="primary"
              loading={busy && !running}
              disabled={busy || running || anyEditing}
              onClick={runNow}
            >
              <Play aria-hidden /> Run now
            </Button>
          )}
        </Row>
      </Row>

      <Card>
        {loading && !cfg ? (
          <Stack gap="sm"><Skeleton width="55%" /><Skeleton width="40%" /><Skeleton width="30%" /></Stack>
        ) : err && !cfg ? (
          <StateBlock
            variant="error"
            title="Couldn't load autonomy config"
            hint={<code>GET {getUrl}: {err}</code>}
            action={<Button size="sm" onClick={load}>Retry</Button>}
          />
        ) : (
          <Stack>
            {/* ---- Primary preset control ---- */}
            {pedit.editing ? (
              <Stack>
                {scope === "agent" && (
                  <Field
                    label="Autonomy mode"
                    help={AUTONOMY_MODE_DESC[pedit.draft.autonomy_mode]}
                  >
                    <Segmented
                      ariaLabel="Per-agent autonomy mode"
                      options={AUTONOMY_MODE_OPTIONS}
                      value={pedit.draft.autonomy_mode}
                      onChange={(v) => pedit.set("autonomy_mode", v)}
                    />
                  </Field>
                )}
                <Field
                  label={scope === "agent" ? "Trigger cadence" : "Autonomy"}
                  help={PRESET_DESC[pedit.draft.preset]}
                >
                  <Segmented
                    ariaLabel={scope === "agent" ? "Trigger cadence" : "Autonomy preset"}
                    options={PRESET_OPTIONS}
                    value={pedit.draft.preset}
                    onChange={(v) => pedit.set("preset", v)}
                  />
                </Field>

                {pedit.draft.preset === "scheduled" && (
                  <Field label="Cron expression" htmlFor="autonomy-cron" help="Standard 5-field cron. Sweeps fire on this cadence when work is ready.">
                    <Input
                      id="autonomy-cron"
                      value={pedit.draft.cron}
                      placeholder="0 */6 * * *"
                      onChange={(e) => pedit.set("cron", e.target.value)}
                    />
                  </Field>
                )}

                <Field
                  label={<><Compass size={14} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.35rem" }} />Latitude</>}
                  help={latitudeDesc(pedit.draft.latitude)}
                >
                  <Segmented
                    ariaLabel="Sweep latitude"
                    options={LATITUDE_OPTIONS}
                    value={pedit.draft.latitude}
                    onChange={(v) => pedit.set("latitude", v)}
                  />
                </Field>

                <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
                  <Field label="Max iterations" htmlFor="autonomy-maxiter" help="Task cap per continuous sweep.">
                    <Input
                      id="autonomy-maxiter"
                      type="number"
                      min={1}
                      value={pedit.draft.maxIterations}
                      placeholder="20"
                      onChange={(e) => pedit.set("maxIterations", e.target.value)}
                    />
                  </Field>
                  <Field label="Budget (seconds)" htmlFor="autonomy-budget" help="Wall-clock bound per sweep.">
                    <Input
                      id="autonomy-budget"
                      type="number"
                      min={1}
                      value={pedit.draft.budget}
                      placeholder="3000"
                      onChange={(e) => pedit.set("budget", e.target.value)}
                    />
                  </Field>
                </Row>

                {saveErr && (
                  <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{saveErr}</p>
                )}

                <ActionBar flash={pedit.flash ? "Saved" : undefined}>
                  <Button size="sm" variant="primary" onClick={pedit.save} loading={pedit.saving} disabled={!pedit.dirty}>
                    Save
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => { setSaveErr(null); pedit.cancel(); }}>
                    Cancel
                  </Button>
                </ActionBar>
              </Stack>
            ) : (
              <Stack gap="sm">
                {scope === "agent" && cfg?.autonomy_mode && (
                  <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                    <Tag hue={catFor(cfg.autonomy_mode)}>{cfg.autonomy_mode}</Tag>
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                      {AUTONOMY_MODE_DESC[cfg.autonomy_mode]}
                    </span>
                  </Row>
                )}
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <Tag hue={catFor(preset)}>{scope === "agent" ? `cadence: ${preset}` : preset}</Tag>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{PRESET_DESC[preset]}</span>
                </Row>
                <Row wrap style={{ gap: "var(--space-4)" }}>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    <Compass size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                    {/* Says "(default)" when this scope sets none, so an inherited
                        value is never mistaken for one the operator chose here. */}
                    latitude: <strong>{cfg?.latitude ?? DEFAULT_LATITUDE}</strong>
                    {cfg?.latitude ? "" : " (default)"}
                  </span>
                  {preset === "scheduled" && cronFromTriggers(cfg?.triggers) && (
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                      <Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                      {cronFromTriggers(cfg?.triggers)}
                    </span>
                  )}
                  {(cfg?.limits?.max_iterations != null || cfg?.limits?.budget?.max_wall_s != null) && (
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                      limits: {cfg?.limits?.max_iterations != null ? `${cfg.limits.max_iterations} iters` : ""}
                      {cfg?.limits?.max_iterations != null && cfg?.limits?.budget?.max_wall_s != null ? ", " : ""}
                      {cfg?.limits?.budget?.max_wall_s != null ? `${cfg.limits.budget.max_wall_s}s` : ""}
                    </span>
                  )}
                </Row>
                {cfg?.updated && (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Updated {timeAbsolute(cfg.updated)}</span>
                )}
                {scope === "project" && running && last?.trigger && (
                  <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                    Sweep running (trigger <span className="ws-mono">{last.trigger}</span>, started {timeAbsolute(last.started_at)}).
                  </p>
                )}
                {scope === "project" && !running && last && (
                  <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                    Last run {timeAbsolute(last.started_at)}: {last.status}
                    {typeof last.tasks_done === "number" ? `, ${last.tasks_done} done` : ""}
                    {typeof last.tasks_failed === "number" && last.tasks_failed > 0 ? `, ${last.tasks_failed} failed` : ""}.
                  </p>
                )}
              </Stack>
            )}

            {/* ---- Queue counts ---- */}
            <Stack gap="sm">
              <SectionLabel>Queue</SectionLabel>
              {q ? (
                anyQueue ? (
                  <Row wrap style={{ gap: "var(--space-2)" }}>
                    {queueItems.filter((i) => i.count > 0).map((i) => (
                      <StatusPill key={i.key} status={i.status}>{i.count} {i.label}</StatusPill>
                    ))}
                  </Row>
                ) : (
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Queue clear.</span>
                )
              ) : (
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>No queue data for this scope.</span>
              )}
            </Stack>

            {/* ---- Awaiting operator input (read-only here) ---- */}
            {awaiting.length > 0 && (
              <Stack gap="sm">
                <SectionLabel icon={<HelpCircle aria-hidden />}>Awaiting your input</SectionLabel>
                {awaiting.map((a) => (
                  <div
                    key={a.id}
                    style={{
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-md)",
                      padding: "var(--space-3)",
                    }}
                  >
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "center", marginBottom: "var(--space-1)" }}>
                      <StatusPill status="warn">awaiting</StatusPill>
                      {a.text && <span style={{ fontSize: "var(--fs-sm)" }}>{a.text}</span>}
                    </Row>
                    {a.question && <p style={{ fontSize: "var(--fs-sm)", margin: "0 0 var(--space-1)" }}>{a.question}</p>}
                    <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                      {a.since ? `since ${timeAbsolute(a.since)}. ` : ""}Reply from the task panel.
                    </span>
                  </div>
                ))}
              </Stack>
            )}

            {/* ---- Advanced disclosure: raw expanded trigger list ---- */}
            <div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setShowAdvanced((s) => !s)}
                aria-expanded={showAdvanced}
              >
                {showAdvanced ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />} Advanced triggers
              </Button>

              {showAdvanced && (
                <Stack gap="sm" style={{ marginTop: "var(--space-3)" }}>
                  <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                    The triggers behind the preset. Editing them here sets the preset above to match.
                  </p>

                  {advTriggers.length === 0 && !aedit.editing ? (
                    <StateBlock
                      title="No triggers"
                      hint={<span>This preset runs nothing automatically. Use <strong>Edit triggers</strong> to add one.</span>}
                    />
                  ) : (
                    <Stack gap="sm">
                      {advTriggers.map((t, i) => (
                        <Row key={i} between wrap style={{ gap: "var(--space-2)", alignItems: "flex-start" }}>
                          <Row style={{ gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap" }}>
                            <Tag hue={catFor(t.kind)}>{t.kind}</Tag>
                            {!aedit.editing && (
                              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{triggerSummary(t)}</span>
                            )}
                            {aedit.editing && t.kind === "cron" && (
                              <Field label="Cron expression" htmlFor={`cron-${i}`}>
                                <Input
                                  id={`cron-${i}`}
                                  value={t.cron_expression ?? ""}
                                  placeholder="0 */6 * * *"
                                  onChange={(e) => patchTrigger(i, { cron_expression: e.target.value })}
                                />
                              </Field>
                            )}
                            {aedit.editing && t.kind === "event" && (
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
                            {aedit.editing && t.kind === "loop" && (
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
                          {aedit.editing && (
                            <Button size="sm" variant="ghost" onClick={() => removeTrigger(i)} aria-label={`Remove ${t.kind} trigger`}>
                              <X aria-hidden /> Remove
                            </Button>
                          )}
                        </Row>
                      ))}
                    </Stack>
                  )}

                  {aedit.editing ? (
                    <>
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
                      {saveErr && (
                        <p style={{ color: "var(--danger)", fontSize: "var(--fs-sm)", margin: 0 }}>{saveErr}</p>
                      )}
                      <ActionBar flash={aedit.flash ? "Saved" : undefined}>
                        <Button size="sm" variant="primary" onClick={aedit.save} loading={aedit.saving} disabled={!aedit.dirty}>
                          Save triggers
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => { setSaveErr(null); aedit.cancel(); }}>
                          Cancel
                        </Button>
                      </ActionBar>
                    </>
                  ) : (
                    <Row>
                      <Button size="sm" variant="ghost" onClick={aedit.begin} disabled={!cfg || pedit.editing}>
                        Edit triggers
                      </Button>
                    </Row>
                  )}
                </Stack>
              )}
            </div>
          </Stack>
        )}
      </Card>
    </section>
  );
}
