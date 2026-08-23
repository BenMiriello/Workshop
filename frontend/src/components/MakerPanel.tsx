import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { Activity, Clock, ListChecks, Play, Power, Square, Ticket, Users } from "lucide-react";
import {
  ActionBar, Button, Card, Clamp, ConfirmDialog, Notice, Prop, Props, Row, SectionLabel,
  Segmented, Skeleton, Stack, StateBlock, StatusDot, StatusPill, Tag, catFor,
  shortHttpError, timeAbsolute, usePoll, useEditMode,
} from "@workshop/ui";

import { jobStatusKind, runHref } from "../lib/jobs";
import { ticketHref, ticketKey } from "../lib/tasks";
import { scopeLabel } from "../lib/scope";
import { AUTONOMY_MODES } from "../lib/autonomy";
import {
  armLevelGloss, armLevelName, ceilingLine, makerPrimary, makerStatus,
  START_HELP, STOP_HELP, type ArmLevel, type MakerState,
} from "../lib/makers";

// WHAT IS DOING MY TICKETS, on the agent that does them.
//
// The operator opened the roster and found no agent that looked like it worked
// tickets. One did the work (a chain of shell scripts) and its controls sat in
// a Settings drawer that never says the word agent. This panel is the other
// half of the fix: the maker's live state and its controls, on the maker's own
// page, beside the persona editor that already lives there. There is no second
// persona editor here on purpose; editing this agent's definition is what
// changes the worker, and `AgentDetail`'s Edit does that already.
//
// EVERY VALUE BELOW IS READ AT REQUEST TIME AND STORED NOWHERE. `GET
// /api/agents/{id}/maker` joins four existing facts (the arm level from
// scope_execution, residency from the work driver's flock, activity from the
// presence channel, runs from the Job store). Nothing here is a flag this page
// set, which is why polling it is the whole of "is it live".

// A live session list is a handful of rows in practice. The cap is the overflow
// state rather than a scroller: past a dozen the question stops being "who is
// here" and starts being "why are there so many", which the count answers.
const MAX_SESSIONS = 12;

// The backend returns at most ten runs. Reaching the cap is the overflow state,
// and the answer to it is the runs feed, which pages properly.
const RUNS_LIMIT = 10;

type Busy = null | "arm" | "start" | "stop";

export default function MakerPanel({
  agentId, canEdit = true,
}: {
  agentId: string;
  /** False while the page's own definition editor is open, so two sticky
   *  ActionBars can never stack on top of each other. */
  canEdit?: boolean;
}) {
  const [state, setState] = useState<MakerState | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actErr, setActErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [confirmStop, setConfirmStop] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}/maker`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setState((await r.json()) as MakerState);
      setLoadErr(null);
    } catch (e) {
      setLoadErr(String(e));
    }
  }, [agentId]);

  // Activity is the reason this page exists, so it is polled. The interval
  // matches the roster's.
  usePoll(load, 15_000);

  const post = useCallback(async (path: string, body?: unknown, kind: Busy = "arm") => {
    setBusy(kind);
    setActErr(null);
    try {
      const r = await fetch(`/api/agents/${encodeURIComponent(agentId)}/maker${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      setState((await r.json()) as MakerState);
    } catch (e) {
      setActErr(String(e));
      throw e;
    } finally {
      setBusy(null);
    }
  }, [agentId]);

  // The arm level writes config, so it commits on an explicit Save with a
  // sticky ActionBar (frontend/CLAUDE.md rule 7). Start and Stop are actions,
  // not settings, and fire on click like every other button.
  const edit = useEditMode<{ mode: ArmLevel }>(
    { mode: (state?.arm_level ?? "reactive") as ArmLevel },
    async (draft) => { await post("/execution", { mode: draft.mode }, "arm"); },
  );

  if (loadErr && !state) {
    return (
      <section>
        <SectionLabel icon={<Activity aria-hidden />}>Ticket work</SectionLabel>
        <StateBlock
          variant="error"
          title="Couldn't read what this maker is doing"
          hint={<code>GET /api/agents/{agentId}/maker ({loadErr})</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      </section>
    );
  }
  if (!state) {
    return (
      <section>
        <SectionLabel icon={<Activity aria-hidden />}>Ticket work</SectionLabel>
        <Card style={{ padding: "var(--space-3)" }}>
          <Stack gap="sm"><Skeleton width="45%" /><Skeleton width="70%" /><Skeleton width="30%" /></Stack>
        </Card>
      </section>
    );
  }

  const status = makerStatus(state);
  const primary = makerPrimary(state);
  const runner = state.runner;
  const sessions = state.sessions || [];
  const runs = state.recent_runs || [];
  const shownSessions = sessions.slice(0, MAX_SESSIONS);
  const armLevel = (edit.editing ? edit.draft.mode : state.arm_level) as ArmLevel;

  return (
    <section>
      <Row between wrap style={{ alignItems: "baseline" }}>
        <SectionLabel icon={<Activity aria-hidden />}>Ticket work</SectionLabel>
        <Link to="/runs" className="ws-bc__link">All runs</Link>
      </Row>

      <Stack>
        {/* --- What it is doing, right now. The first thing on the page,
             because it is the question that had no answer anywhere. --- */}
        <Card style={{ padding: "var(--space-3)" }}>
          <Stack gap="sm">
            <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
              <StatusPill status={status.kind}>{status.label}</StatusPill>
              <span style={{ fontSize: "var(--fs-sm)" }}>
                {primary.text}
                {primary.ticketId && (
                  <>
                    {" "}
                    <Link to={ticketHref(state.scope, primary.ticketId)}>
                      <Ticket size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                      {primary.ticketLabel}
                    </Link>
                    {primary.ticketStatus && (
                      <span className="ws-muted"> ({primary.ticketStatus})</span>
                    )}
                  </>
                )}
              </span>
            </Row>
            {/* The backend's own sentence about what it can and cannot see.
                `present` covers idling on the poll AND sweeping without a
                claim, and this is where that is said instead of the UI picking
                one and being wrong half the time. */}
            <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
              {state.activity_detail}
            </p>
            {!state.runnable && (
              <Notice tone="warn">
                This maker's tree is no longer a runnable target. The project was removed, or
                developer mode is off for the platform repo. The record is kept so its definition
                is not lost.
              </Notice>
            )}
          </Stack>
        </Card>

        {/* --- The facts, read once. --- */}
        <Card style={{ padding: "var(--space-3)" }}>
          <Props>
            <Prop label="Queue">
              <Link to={`/tickets?scope=${encodeURIComponent(state.scope)}`}>
                {state.label || scopeLabel(state.scope)}
              </Link>
            </Prop>
            <Prop label="Arm level">
              {armLevelName(state.arm_level as ArmLevel)}
              <span className="ws-muted"> ({state.arm_level})</span>
            </Prop>
            <Prop label="Ceiling">{ceilingLine(state)}</Prop>
            <Prop label="Worker">
              <StatusDot status={state.runner_resident ? "running" : "stopped"}>
                {state.runner_resident ? "resident on the tree" : "not on the tree"}
                {runner?.pid ? ` (pid ${runner.pid})` : ""}
              </StatusDot>
            </Prop>
            <Prop label="Tree"><code className="ws-mono">{state.dir || "unknown"}</code></Prop>
          </Props>
          {/* NOT a `Prop`. The runner's last line is a full sweep command,
              several hundred unbroken characters of it, and in one auto-fill
              column it wrapped to twenty lines and stretched every other
              property to match. It gets the full width and a clamp instead.

              AND ONLY WHILE THIS RUNNER IS RUNNING, because the log is not
              per-tree. The lock is (`queue-runner-<hash>.lock`), but every tree
              writes `.runtime/queue-runner.log`, so `last_line` is whatever the
              busiest tree wrote last. On a stopped maker that is plainly
              another maker's work: the notes-app maker's page showed a line
              from the development tree. The path is printed beside it so the
              reader can see which file it is reading. */}
          {runner?.running && runner.last_line && (
            <div style={{ marginTop: "var(--space-3)" }}>
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Last line in <code className="ws-mono">{runner.log_path}</code>, which every tree writes
              </span>
              <Clamp max={72}>
                <p
                  className="ws-mono"
                  style={{
                    margin: "var(--space-1) 0 0", fontSize: "var(--fs-xs)",
                    whiteSpace: "pre-wrap", wordBreak: "break-word",
                  }}
                >
                  {runner.last_line}
                </p>
              </Clamp>
            </div>
          )}
        </Card>

        {/* --- The controls. On the agent, which is where "manage it" means. --- */}
        <Card style={{ padding: "var(--space-3)" }}>
          <Stack gap="sm">
            <Row between wrap style={{ alignItems: "center" }}>
              <SectionLabel icon={<Power aria-hidden />}>Controls</SectionLabel>
              {!edit.editing && canEdit && (
                <Button size="sm" variant="ghost" onClick={edit.begin}>Edit arm level</Button>
              )}
            </Row>

            {state.global_override_engaged && (
              <Notice
                tone="warn"
                action={<Link to="/settings" className="ws-bc__link">Settings</Link>}
              >
                The global autonomy brake is engaged. It sits above every scope and supersedes the
                arm level below, so this maker picks up nothing while it is on, however it is set here.
              </Notice>
            )}

            {edit.editing ? (
              <Stack gap="sm">
                <Segmented
                  ariaLabel="Arm level"
                  value={edit.draft.mode}
                  onChange={(v) => edit.set("mode", v)}
                  options={AUTONOMY_MODES.map((m) => ({ value: m, label: armLevelName(m) }))}
                />
                <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
                  {armLevelGloss(armLevel)}
                </p>
                <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
                  This is the scope's execution mode, not a copy of it. The same setting is reached
                  from a chat's ticket settings, and there is nothing that can disagree with it.
                </p>
              </Stack>
            ) : (
              <Stack gap="sm">
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <Tag hue={catFor(state.arm_level)}>{armLevelName(state.arm_level as ArmLevel)}</Tag>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                    {armLevelGloss(state.arm_level as ArmLevel)}
                  </span>
                </Row>
                <Row wrap style={{ gap: "var(--space-2)" }}>
                  {state.runner_resident ? (
                    <Button
                      size="sm"
                      variant="danger-ghost"
                      disabled={busy != null}
                      onClick={() => setConfirmStop(true)}
                    >
                      <Square aria-hidden /> Stop and disarm
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="primary"
                      loading={busy === "start"}
                      disabled={busy != null || !state.runnable}
                      onClick={() => { void post("/runner/start", undefined, "start").catch(() => {}); }}
                    >
                      <Play aria-hidden /> Arm and start a worker
                    </Button>
                  )}
                </Row>
                <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
                  {state.runner_resident ? STOP_HELP : START_HELP}
                </p>
              </Stack>
            )}

            {actErr && <Notice tone="error" detail={actErr}>That control did not go through.</Notice>}
            {loadErr && !actErr && (
              <Notice tone="warn" detail={loadErr} action={<Button size="sm" onClick={load}>Retry</Button>}>
                This panel stopped refreshing, so what it shows may be out of date.
              </Notice>
            )}
          </Stack>
        </Card>

        {/* --- Who is here. All four states. --- */}
        <div>
          <SectionLabel icon={<Users aria-hidden />}>Live sessions</SectionLabel>
          {sessions.length === 0 ? (
            <StateBlock
              title="No live sessions"
              hint="A session appears here while a worker or a chat is working this scope. Arm the maker and start a worker to put one on the tree."
            />
          ) : (
            <Stack gap="sm">
              {shownSessions.map((s) => (
                <Card key={s.session_id} compact style={{ padding: "var(--space-3)" }}>
                  <Row between wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <Tag hue={catFor(s.session_kind)}>{s.session_kind}</Tag>
                      <span style={{ fontSize: "var(--fs-sm)" }}>
                        {s.activity || "no activity reported"}
                      </span>
                    </Row>
                    <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
                      {s.last_beat_at ? timeAbsolute(s.last_beat_at) : "no beat yet"}
                    </span>
                  </Row>
                  <p className="ws-card__meta ws-muted" style={{ marginTop: "var(--space-1)" }}>
                    {s.subject_kind === "ticket" && s.subject_id ? (
                      <>
                        on{" "}
                        <Link className="ws-muted" to={ticketHref(s.scope || state.scope, s.subject_id)}>
                          {s.subject_label || s.subject_id}
                        </Link>
                        {s.subject_status ? ` (${s.subject_status})` : ""}
                      </>
                    ) : (
                      <>holds no ticket</>
                    )}
                    {s.pid ? ` · pid ${s.pid}` : ""}
                    {s.principal ? ` · ${s.principal}` : ""}
                  </p>
                </Card>
              ))}
              {sessions.length > MAX_SESSIONS && (
                <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
                  Showing {MAX_SESSIONS} of {sessions.length} live sessions.
                </p>
              )}
            </Stack>
          )}
        </div>

        {/* --- What it has run. All four states. --- */}
        <div>
          <SectionLabel icon={<ListChecks aria-hidden />}>Recent runs</SectionLabel>
          {runs.length === 0 ? (
            <StateBlock
              title="No runs yet"
              hint="Every sweep this maker's scope has run appears here, newest first. File a ticket in this queue and arm the maker to produce one."
            />
          ) : (
            <Stack gap="sm">
              {runs.map((j) => (
                <Card key={j.id} compact style={{ padding: "var(--space-3)" }}>
                  <Row between wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                    <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                      <StatusDot status={jobStatusKind(j.status ?? undefined)}>
                        {j.status || "unknown"}
                      </StatusDot>
                      {/* These are RAW Job records off the store, not the
                          enriched ones `/api/jobs` serves, so there is no
                          `title` to print. What a sweep run has is the ticket
                          it worked, or nothing, in which case it is the
                          coordinator pass. Printing a truncated job id was the
                          card naming the row after its primary key. */}
                      <Link to={runHref(j.id)}>
                        {j.title || (j.task_id ? `ticket ${ticketKey(j.task_id)}` : "queue sweep")}
                      </Link>
                    </Row>
                    <span className="ws-muted ws-mono" style={{ fontSize: "var(--fs-xs)" }}>
                      <Clock size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
                      {j.started_at ? timeAbsolute(j.started_at) : "not started"}
                    </span>
                  </Row>
                  <p className="ws-card__meta ws-muted" style={{ marginTop: "var(--space-1)" }}>
                    {j.trigger || "manual"}
                    {j.task_id && (
                      <>
                        {" · "}
                        <Link className="ws-muted" to={ticketHref(state.scope, j.task_id)}>
                          the ticket it worked
                        </Link>
                      </>
                    )}
                  </p>
                </Card>
              ))}
              {runs.length >= RUNS_LIMIT && (
                <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
                  Showing the {RUNS_LIMIT} most recent. <Link className="ws-muted" to="/runs">See all runs</Link>.
                </p>
              )}
            </Stack>
          )}
        </div>
      </Stack>

      {edit.editing && (
        <ActionBar flash={edit.flash ? "Saved ✓" : undefined}>
          <Button variant="primary" onClick={() => { void edit.save().catch(() => {}); }} loading={edit.saving || busy === "arm"}>
            Save arm level
          </Button>
          <Button variant="ghost" onClick={edit.cancel} disabled={edit.saving}>Cancel</Button>
        </ActionBar>
      )}

      <ConfirmDialog
        open={confirmStop}
        onCancel={() => setConfirmStop(false)}
        onConfirm={() => {
          setConfirmStop(false);
          void post("/runner/stop", undefined, "stop").catch(() => {});
        }}
        title="Stop this maker and disarm its scope?"
        body={"Ends the resident worker and any sweep in flight, and switches unattended work off for this scope. A ticket half-worked goes back on the queue, and filed tickets sit there until you arm it again."}
        confirmLabel="Stop and disarm"
      />
    </section>
  );
}
