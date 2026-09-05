import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { timeAbsolute, shortHttpError, Button, Card, Row, Stack, SectionLabel, StateBlock, StatusPill, Skeleton, Input, Field, ActionBar, Tag, Segmented, catFor, useEditMode, type StatusKind, type SegmentedOption } from "@workshop/ui";
import {
  Repeat, Play, RefreshCw, X, Clock, Zap, RotateCw,
  ChevronDown, ChevronRight, HelpCircle, SlidersHorizontal,
} from "lucide-react";

import { jobStatusKind } from "../lib/jobs";
import { queryKeys } from "../lib/queryKeys";
import { useEventSources } from "../lib/eventSources";

// Autonomy = how a PROJECT decides when to work its own task queue through the
// Ralph loop. The PRIMARY control is a preset (Section
// 10.4 of orchestration-and-autonomy.md), not a raw trigger list:
//
//   off         no automatic runs; manual "Run now" only
//   on-demand   swept only by an explicit "Run now"
//   scheduled   swept on a cron cadence when work is ready
//   continuous  swept every tick while work is ready, bounded by limits
//
// Nothing here bounds what a run may WRITE: `touches` is a declared intent
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
// PROJECT SCOPE ONLY. This panel used to carry a second `scope="agent"` branch
// that wrote PUT /agents/{id}, along with a client-side mirror of the backend's
// triggers.preset_to_triggers. Nothing ever passed `scope`: the single callsite
// (ProjectDetail) takes the project default, and per-agent triggers are edited
// by AgentDetail's TriggerEditor, which is the live surface for that job. The
// two had already drifted apart in what they called the same trigger, which is
// what a second unused implementation of one job buys. Deleted 2026-08-17.

type Preset = "off" | "on-demand" | "scheduled" | "continuous";

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
  limits: Limits;
  triggers: Trigger[];
  queue?: Queue;
  awaiting?: Awaiting[];
  updated?: string | null;
  running?: boolean;
  last_run?: Job;
};

type PrimaryDraft = {
  preset: Preset;
  cron: string;
  maxIterations: string;
  budget: string;
};

const PRESET_OPTIONS: SegmentedOption<Preset>[] = [
  { value: "off", label: "Off" },
  { value: "on-demand", label: "On demand" },
  { value: "scheduled", label: "Scheduled" },
  { value: "continuous", label: "Continuous" },
];

const PRESET_DESC: Record<Preset, string> = {
  off: "No automatic runs. Manual Run now only.",
  "on-demand": "Runs only when you press Run now.",
  scheduled: "Runs on a cron schedule when work is ready.",
  continuous: "Sweeps every tick while work is ready, bounded by limits.",
};

function isPreset(v: unknown): v is Preset {
  return v === "off" || v === "on-demand" || v === "scheduled" || v === "continuous";
}

function cronFromTriggers(triggers?: Trigger[]): string {
  const cron = (triggers || []).find((t) => t.kind === "cron");
  return cron?.cron_expression ?? "";
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

// A sweep in flight is the only time any of this moves fast enough to be worth
// two and a half seconds; an idle project is read on the same fifteen-second
// cadence as every other settled surface.
const RUNNING_MS = 2500;
const IDLE_MS = 15000;

export default function AutonomyPanel({ slug }: { slug: string }) {
  const knownSources = useEventSources();
  const getUrl = `/api/projects/${encodeURIComponent(slug)}/autonomy`;
  const putUrl = getUrl;
  const runUrl = `/api/projects/${encodeURIComponent(slug)}/autonomy/run`;

  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const cfgQuery = useQuery({
    queryKey: queryKeys.projects.autonomy(slug),
    queryFn: async (): Promise<Autonomy> => {
      const r = await fetch(getUrl);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const raw = await r.json();
      // Normalising INSIDE the read is what lets the cadence below read
      // `running` off the cached payload: the interval callback is handed the
      // query's own state, not a render, so there is no way for the two to
      // describe different sweeps.
      return {
        autonomy: isPreset(raw.autonomy) ? raw.autonomy : "off",
        limits: (raw.limits && typeof raw.limits === "object") ? raw.limits : {},
        triggers: Array.isArray(raw.triggers) ? raw.triggers : [],
        queue: raw.queue,
        awaiting: Array.isArray(raw.awaiting) ? raw.awaiting : [],
        updated: raw.updated ?? null,
        running: !!raw.running,
        last_run: raw.last_run ?? null,
      };
    },
    // The cadence follows the data, which is the same rule the poll it replaces
    // applied, read off the payload instead of off a render.
    refetchInterval: (query) => (query.state.data?.running ? RUNNING_MS : IDLE_MS),
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read `usePoll` did on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });

  // A DROPPED READ LEAVES THE PANEL ON SCREEN: only a panel that has never read
  // anything falls to the error region, and the next success clears the line.
  // Both halves unchanged.
  const cfg = cfgQuery.data ?? null;
  const loading = cfgQuery.isPending;
  const err = cfgQuery.isError
    ? (cfgQuery.error instanceof Error ? cfgQuery.error.message : String(cfgQuery.error))
    : null;
  const { refetch } = cfgQuery;
  // Returns the promise because both Save paths awaited the re-read before
  // dropping out of their saving state.
  const load = useCallback(async () => { await refetch(); }, [refetch]);

  const running = cfg?.running ?? false;

  // Primary preset config: the main control. Edit-mode + explicit Save.
  const primaryModel = useMemo<PrimaryDraft>(() => ({
    preset: cfg?.autonomy ?? "off",
    cron: cronFromTriggers(cfg?.triggers),
    maxIterations: cfg?.limits?.max_iterations != null ? String(cfg.limits.max_iterations) : "",
    budget: cfg?.limits?.budget?.max_wall_s != null ? String(cfg.limits.budget.max_wall_s) : "",
  }), [cfg]);

  const pedit = useEditMode<PrimaryDraft>(primaryModel, async (draft) => {
    setSaveErr(null);
    const limits = draftLimits(draft);
    // The preset goes over the wire as a preset. The backend expands it
    // (triggers.preset_to_triggers) so there is exactly one expansion, on the
    // side that owns the trigger vocabulary.
    const body = {
      preset: draft.preset,
      cron: draft.preset === "scheduled" ? draft.cron : undefined,
      limits,
    };
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
          {cfg && (
            <StatusPill status={running ? "running" : jobStatusKind(last?.status)}>
              {running ? "running" : (last?.status ?? "never run")}
            </StatusPill>
          )}
          <Button size="sm" variant="ghost" onClick={load}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
          {!anyEditing && (
            <Button size="sm" variant="ghost" onClick={pedit.begin} disabled={!cfg}>
              <SlidersHorizontal aria-hidden /> Edit
            </Button>
          )}
          <Button
            size="sm"
            variant="primary"
            loading={busy && !running}
            disabled={busy || running || anyEditing}
            onClick={runNow}
          >
            <Play aria-hidden /> Run now
          </Button>
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
                <Field
                  label="Autonomy"
                  help={PRESET_DESC[pedit.draft.preset]}
                >
                  <Segmented
                    ariaLabel="Autonomy preset"
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
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <Tag hue={catFor(preset)}>{preset}</Tag>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{PRESET_DESC[preset]}</span>
                </Row>
                <Row wrap style={{ gap: "var(--space-4)" }}>
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
                {running && last?.trigger && (
                  <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
                    Sweep running (trigger <span className="ws-mono">{last.trigger}</span>, started {timeAbsolute(last.started_at)}).
                  </p>
                )}
                {!running && last && (
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
                              <Field label="Sources (comma separated)" htmlFor={`ev-${i}`} help={knownSources.join(", ")}>
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
