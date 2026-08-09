import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Plus, Pencil, Trash2, Check, X, CornerDownLeft, Compass } from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
  Field,
  Input,
  Select,
  SectionLabel,
  Skeleton,
  StateBlock,
  StatusPill,
  Stack,
  Row,
  Textarea,
  Toggle,
} from "../ui";
import {
  type Task,
  type TaskStatus,
  TASK_STATUSES,
  LATITUDES,
  DEFAULT_LATITUDE,
  taskStatusKind,
  taskStatusLabel,
  latitudeDesc,
  latitudeSourceLabel,
  title as taskTitle,
  description as taskDescription,
  cleanTitle,
} from "../lib/tasks";

// Reusable task panel over the canonical cross-scope Task API. It renders EVERY
// shipped status (todo/doing/in_review/blocked/parked/done/cancelled) through the
// shared status map, so one component serves build:, project:, and agent: scopes
// (the Work page owns the full-page Tickets surface; this is the compact,
// embeddable sibling used inside a chat drawer and on the Agent detail page).
//
// Reads  GET  /api/tasks?scope=<scope>
// Writes POST /api/tasks                          (create)
//        PATCH /api/tasks/<scope>/<id>            (edit + one-click Approve)
//        DELETE /api/tasks/<scope>/<id>           (delete)
//        POST /api/jobs/<jobId>/tasks/<id>/action (awaiting reply, reused from
//                                                   the JobDetail "Needs input")
//
// The operator is the review gate: an in_review task gets a one-click Approve
// (PATCH status=done); an awaiting task gets an inline answer box that resolves
// the run's bound task the same way the Job detail page does.

export type TaskPanelProps = {
  // The task-store scope key: "workshop" | "project:<slug>" | "agent:<slug>" |
  // "build:<slug>". Every read/write is scoped to it.
  scope: string;
  // Optional client-side narrowing to one assignee (e.g. "agent:<slug>"). The
  // scope already selects the store; this filters within it when a store is
  // shared. Omit to show every task in the scope.
  assignee?: string | null;
  // Bump to force a refetch (the chat bumps this when a turn ends so freshly
  // filed tasks appear without a manual refresh).
  refetchKey?: number;
  // Drawer gate: when explicitly false the panel renders nothing (the chat
  // toolbar owns the open/close toggle). Defaults to open.
  open?: boolean;
  // Report the count of not-done, not-cancelled tasks up for a toolbar badge.
  onCount?: (openCount: number) => void;
  // "drawer" = compact, scroll-capped body inside the chat panel (default).
  // "section" = a page section with its own heading, for the Agent detail page.
  variant?: "drawer" | "section";
  // Heading for the "section" variant.
  heading?: string;
};

const OVERFLOW_AT = 100;
const POLL_MS = 6000;

type EditDraft = {
  title: string;
  description: string;
  status: TaskStatus;
  priority: string;
  effort: string;
  // "" = inherit from the scope (the store's null). Deliberately NOT prefilled
  // with the effective value: prefilling would turn a glance at the ticket into
  // a per-ticket override on the next save, silently pinning the inherited value.
  latitude: string;
  assignee: string;
  // Advanced fields (persisted via the same PATCH). acceptanceKind "" = none;
  // acceptanceSpec only matters for the "command" kind. maxAttempts + dependsOn
  // are text inputs coerced on save.
  acceptanceKind: string;
  acceptanceSpec: string;
  maxAttempts: string;
  dependsOn: string;
  relatesTo: string;
};

function toDraft(t: Task): EditDraft {
  return {
    title: taskTitle(t),
    description: taskDescription(t),
    status: (t.status ?? "todo") as TaskStatus,
    priority: String(t.priority ?? 0),
    effort: t.effort ?? "",
    latitude: t.latitude ?? "",
    assignee: t.assignee ?? "",
    acceptanceKind: t.acceptance?.kind ?? "",
    acceptanceSpec: t.acceptance?.spec ?? "",
    maxAttempts: t.max_attempts != null ? String(t.max_attempts) : "",
    dependsOn: (t.depends_on ?? []).join(", "),
    relatesTo: (t.relates_to ?? []).join(", "),
  };
}

function isOpenStatus(status?: string): boolean {
  return status !== "done" && status !== "cancelled";
}

export default function TaskPanel({
  scope,
  assignee,
  refetchKey = 0,
  open = true,
  onCount,
  variant = "drawer",
  heading,
}: TaskPanelProps) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showDone, setShowDone] = useState(false);

  // Create
  const [newText, setNewText] = useState("");
  const [adding, setAdding] = useState(false);
  const [addErr, setAddErr] = useState<string | null>(null);

  // Per-row edit (edit-mode: fields commit on explicit Save, never on change)
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [rowErr, setRowErr] = useState<string | null>(null);

  // Approve / awaiting-reply / delete in flight
  const [acting, setActing] = useState<string | null>(null);
  const [replyId, setReplyId] = useState<string | null>(null);
  const [replyText, setReplyText] = useState("");
  const [confirmDel, setConfirmDel] = useState<Task | null>(null);

  const endpoint = `/api/tasks?scope=${encodeURIComponent(scope)}`;

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch(endpoint);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = (await r.json()) as { tasks?: Task[] };
      setTasks(j.tasks ?? []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    void load();
  }, [load, refetchKey]);

  // Poll while a sweep is actively working this scope (a task in `doing`), so the
  // panel reflects a running run without the operator refreshing. Idle scopes do
  // no polling. Refetch-on-demand (load) stays the primary path.
  const anyDoing = (tasks ?? []).some((t) => t.status === "doing");
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    if (!open || !anyDoing) return;
    const id = window.setInterval(() => void loadRef.current(), POLL_MS);
    return () => window.clearInterval(id);
  }, [open, anyDoing]);

  const scoped = assignee != null ? (tasks ?? []).filter((t) => (t.assignee ?? "") === assignee) : (tasks ?? []);
  const openCount = scoped.filter((t) => isOpenStatus(t.status)).length;

  useEffect(() => {
    if (onCount) onCount(openCount);
  }, [onCount, openCount]);

  const visibleAll = showDone ? scoped : scoped.filter((t) => isOpenStatus(t.status));
  const overflow = visibleAll.length > OVERFLOW_AT;
  const visible = overflow ? visibleAll.slice(0, OVERFLOW_AT) : visibleAll;

  // ---- writes --------------------------------------------------------------

  const add = useCallback(async () => {
    const text = newText.trim();
    if (!text || adding) return;
    setAdding(true);
    setAddErr(null);
    try {
      const r = await fetch("/api/tasks", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scope, text, priority: 0 }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setNewText("");
      await load();
    } catch (e) {
      setAddErr(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  }, [newText, adding, scope, load]);

  const beginEdit = useCallback((t: Task) => {
    setRowErr(null);
    setReplyId(null);
    setEditingId(t.id);
    setDraft(toDraft(t));
  }, []);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setDraft(null);
    setRowErr(null);
  }, []);

  const saveEdit = useCallback(
    async (t: Task) => {
      if (!draft) return;
      setSavingId(t.id);
      setRowErr(null);
      try {
        // Acceptance: an empty kind clears the gate (backend reads kind "" as
        // clear); "command" carries its spec, the others ignore it.
        const acceptance =
          draft.acceptanceKind === ""
            ? { kind: "" }
            : {
                kind: draft.acceptanceKind,
                spec: draft.acceptanceKind === "command" ? draft.acceptanceSpec.trim() || null : null,
              };
        // depends_on / relates_to: comma-separated task ids; [] clears them.
        // depends_on gates readiness; relates_to is a soft link with no
        // scheduling effect (entity-model 2026-07-18 §4.2).
        const depends_on = draft.dependsOn
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        const relates_to = draft.relatesTo
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        // max_attempts: a set number caps worker runs; blank leaves it unchanged
        // (the store has no clear path once set).
        const maxAttemptsNum = Number.parseInt(draft.maxAttempts, 10);
        const body: Record<string, unknown> = {
          // The canonical pair, not the joined blob (services/tasks.py).
          title: cleanTitle(draft.title),
          body: draft.description,
          status: draft.status,
          priority: Number(draft.priority) || 0,
          effort: draft.effort,
          latitude: draft.latitude,
          assignee: draft.assignee.trim() || null,
          acceptance,
          depends_on,
          relates_to,
        };
        if (Number.isFinite(maxAttemptsNum)) body.max_attempts = maxAttemptsNum;
        const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(t.id)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        setEditingId(null);
        setDraft(null);
        await load();
      } catch (e) {
        setRowErr(e instanceof Error ? e.message : String(e));
      } finally {
        setSavingId(null);
      }
    },
    [draft, scope, load],
  );

  const approve = useCallback(
    async (t: Task) => {
      setActing(`approve:${t.id}`);
      setRowErr(null);
      try {
        const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(t.id)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ status: "done" }),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        await load();
      } catch (e) {
        setRowErr(e instanceof Error ? e.message : String(e));
      } finally {
        setActing(null);
      }
    },
    [scope, load],
  );

  // Blocked-row review verbs (B4 / C16). Hits the same POST /review endpoint
  // that the Work tab's Review pane uses. `approve` releases the block back to
  // todo; `deny` cancels; `dismiss` keeps the block but drops it off the queue
  // so it stops being asked about. Grant-and-continue and mint-rule require
  // extra input and stay in Work.tsx's Review tab.
  const reviewVerb = useCallback(
    async (t: Task, action: "approve" | "deny" | "dismiss") => {
      setActing(`review:${t.id}`);
      setRowErr(null);
      try {
        const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(t.id)}/review`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action }),
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        await load();
      } catch (e) {
        setRowErr(e instanceof Error ? e.message : String(e));
      } finally {
        setActing(null);
      }
    },
    [scope, load],
  );

  // Resolve an awaiting task by answering its question. Reuses the same job/task
  // action endpoint the Job detail "Needs your input" section uses, carrying the
  // operator's answer as the note (recorded as evidence, clears the awaiting
  // payload). in_review sends the work back for rework with the answer; a blocked
  // task is unblocked back into the queue.
  const sendReply = useCallback(
    async (t: Task) => {
      const answer = replyText.trim();
      if (!answer) return;
      const jobId = t.job_ids && t.job_ids.length > 0 ? t.job_ids[t.job_ids.length - 1] : null;
      if (!jobId) {
        setRowErr("No run is linked to this task, so its question can't be answered here. Resolve it from the run under Jobs.");
        return;
      }
      const action = t.status === "in_review" ? "reject" : "unblock";
      setActing(`reply:${t.id}`);
      setRowErr(null);
      try {
        const r = await fetch(
          `/api/jobs/${encodeURIComponent(jobId)}/tasks/${encodeURIComponent(t.id)}/action`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ action, note: answer }),
          },
        );
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        setReplyId(null);
        setReplyText("");
        await load();
      } catch (e) {
        setRowErr(e instanceof Error ? e.message : String(e));
      } finally {
        setActing(null);
      }
    },
    [replyText, load],
  );

  const del = useCallback(
    async (t: Task) => {
      setActing(`delete:${t.id}`);
      setRowErr(null);
      try {
        const r = await fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(t.id)}`, {
          method: "DELETE",
        });
        if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
        setConfirmDel(null);
        await load();
      } catch (e) {
        setRowErr(e instanceof Error ? e.message : String(e));
        setConfirmDel(null);
      } finally {
        setActing(null);
      }
    },
    [scope, load],
  );

  // ---- rendering -----------------------------------------------------------

  if (open === false) return null;

  const setField = <K extends keyof EditDraft>(k: K, v: EditDraft[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  const editRow = (t: Task) => {
    if (!draft) return null;
    const original = toDraft(t);
    const dirty = JSON.stringify(draft) !== JSON.stringify(original);
    return (
      <Stack gap="sm">
        <Field label="Title" htmlFor={`tp-title-${t.id}`}>
          <Input id={`tp-title-${t.id}`} value={draft.title} onChange={(e) => setField("title", e.target.value)} data-autofocus />
        </Field>
        <Field label="Description" htmlFor={`tp-desc-${t.id}`} optional>
          <Textarea id={`tp-desc-${t.id}`} rows={3} value={draft.description} onChange={(e) => setField("description", e.target.value)} />
        </Field>
        <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
          <Field label="Status" htmlFor={`tp-status-${t.id}`}>
            <Select id={`tp-status-${t.id}`} value={draft.status} onChange={(e) => setField("status", e.target.value as TaskStatus)}>
              {TASK_STATUSES.map((s) => (
                <option key={s} value={s}>{taskStatusLabel(s)}</option>
              ))}
            </Select>
          </Field>
          <Field label="Priority" htmlFor={`tp-prio-${t.id}`}>
            <Input id={`tp-prio-${t.id}`} type="number" value={draft.priority} onChange={(e) => setField("priority", e.target.value)} style={{ maxWidth: "6rem" }} />
          </Field>
          <Field label="Effort" htmlFor={`tp-effort-${t.id}`}>
            <Select id={`tp-effort-${t.id}`} value={draft.effort} onChange={(e) => setField("effort", e.target.value)}>
              <option value="">none</option>
              <option value="s">s</option>
              <option value="m">m</option>
              <option value="l">l</option>
            </Select>
          </Field>
          <Field
            label="Latitude"
            htmlFor={`tp-latitude-${t.id}`}
            optional
            help={draft.latitude ? latitudeDesc(draft.latitude) : "Inherit the scope's setting."}
          >
            <Select id={`tp-latitude-${t.id}`} value={draft.latitude} onChange={(e) => setField("latitude", e.target.value)}>
              <option value="">inherit</option>
              {LATITUDES.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </Select>
          </Field>
          <Field label="Assignee" htmlFor={`tp-assignee-${t.id}`} help="operator, or an agent's name">
            <Input id={`tp-assignee-${t.id}`} value={draft.assignee} onChange={(e) => setField("assignee", e.target.value)} placeholder="operator" style={{ maxWidth: "12rem" }} />
          </Field>
        </Row>
        <SectionLabel>Advanced</SectionLabel>
        <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
          <Field label="Acceptance" htmlFor={`tp-accept-${t.id}`} help="gate before this task can finish">
            <Select id={`tp-accept-${t.id}`} value={draft.acceptanceKind} onChange={(e) => setField("acceptanceKind", e.target.value)}>
              <option value="">none</option>
              <option value="reviewer">reviewer pass</option>
              <option value="command">command exits 0</option>
              <option value="operator">operator approves</option>
            </Select>
          </Field>
          {draft.acceptanceKind === "command" && (
            <Field label="Command" htmlFor={`tp-accept-spec-${t.id}`} help="must exit 0 to pass">
              <Input id={`tp-accept-spec-${t.id}`} value={draft.acceptanceSpec} onChange={(e) => setField("acceptanceSpec", e.target.value)} placeholder="pytest -q" style={{ minWidth: "12rem" }} />
            </Field>
          )}
          <Field label="Max attempts" htmlFor={`tp-maxatt-${t.id}`} optional help="worker runs before parking">
            <Input id={`tp-maxatt-${t.id}`} type="number" min={1} value={draft.maxAttempts} onChange={(e) => setField("maxAttempts", e.target.value)} style={{ maxWidth: "6rem" }} />
          </Field>
        </Row>
        <Field label="Depends on" htmlFor={`tp-deps-${t.id}`} optional help="comma-separated task ids; this task waits for them">
          <Input id={`tp-deps-${t.id}`} value={draft.dependsOn} onChange={(e) => setField("dependsOn", e.target.value)} placeholder="task id, task id" />
        </Field>
        <Field label="Relates to" htmlFor={`tp-rel-${t.id}`} optional help="comma-separated task ids; a link only, no effect on order">
          <Input id={`tp-rel-${t.id}`} value={draft.relatesTo} onChange={(e) => setField("relatesTo", e.target.value)} placeholder="task id, task id" />
        </Field>
        {rowErr && <p className="ws-field__error">{rowErr}</p>}
        {dirty && <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>Unsaved changes.</p>}
        <Row wrap style={{ gap: "var(--space-2)" }}>
          <Button size="sm" variant="primary" loading={savingId === t.id} disabled={!dirty} onClick={() => void saveEdit(t)}>Save</Button>
          <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={savingId === t.id}>Cancel</Button>
        </Row>
      </Stack>
    );
  };

  const viewRow = (t: Task) => {
    const awaiting = t.awaiting?.question ? t.awaiting.question : null;
    const busyApprove = acting === `approve:${t.id}`;
    const busyReply = acting === `reply:${t.id}`;
    const anyBusy = acting?.endsWith(`:${t.id}`) ?? false;
    return (
      <Stack gap="sm">
        <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
          <StatusPill status={taskStatusKind(t.status)}>{taskStatusLabel(t.status)}</StatusPill>
          <span className="ws-truncate" title={taskTitle(t)}
                style={{ flex: "1 1 12rem", minWidth: 0, fontWeight: 600 }}>{taskTitle(t) || "(untitled)"}</span>
        </Row>
        <Row wrap style={{ gap: "var(--space-3)" }}>
          {typeof t.priority === "number" && t.priority !== 0 && (
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>priority {t.priority}</span>
          )}
          {t.effort && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>effort {t.effort}</span>}
          {/* The EFFECTIVE latitude a run would use, always with where it came
              from. The source is the point: null means inherit, so a value shown
              bare would make an inherited setting look like this ticket's own. */}
          <span
            className="ws-muted"
            style={{ fontSize: "var(--fs-xs)" }}
            title={latitudeDesc(t.effective_latitude?.value ?? DEFAULT_LATITUDE)}
          >
            <Compass size={12} strokeWidth={1.8} aria-hidden style={{ verticalAlign: "middle", marginRight: "0.3rem" }} />
            latitude {t.effective_latitude?.value ?? DEFAULT_LATITUDE}
            {" "}({latitudeSourceLabel(t.effective_latitude?.source ?? "default")})
          </span>
          {t.assignee && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{t.assignee}</span>}
          {t.depends_on && t.depends_on.length > 0 && (
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }} title={t.depends_on.join(", ")}>
              depends on {t.depends_on.length}
            </span>
          )}
          {(() => {
            // blocks is derived: tasks whose depends_on names this ticket
            // (entity-model 2026-07-18 §4.2). Cheap client-side pass.
            const blockedBy = (tasks ?? []).filter((o) => (o.depends_on ?? []).includes(t.id)).map((o) => o.id);
            return blockedBy.length > 0 ? (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }} title={blockedBy.join(", ")}>
                blocks {blockedBy.length}
              </span>
            ) : null;
          })()}
          {t.relates_to && t.relates_to.length > 0 && (
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }} title={t.relates_to.join(", ")}>
              relates to {t.relates_to.length}
            </span>
          )}
        </Row>
        {t.blocked_reason && (
          <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>Blocked: {t.blocked_reason}</p>
        )}
        {awaiting && (
          <Card compact style={{ padding: "var(--space-3)", background: "var(--bg-inset)" }}>
            <Stack gap="sm">
              <span style={{ fontSize: "var(--fs-sm)" }}>{awaiting}</span>
              {replyId === t.id ? (
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "flex-end" }}>
                  <Input
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    placeholder="Type your answer"
                    aria-label="Answer this question"
                    style={{ flex: "1 1 14rem" }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void sendReply(t);
                      }
                    }}
                  />
                  <Button size="sm" variant="primary" loading={busyReply} disabled={!replyText.trim()} onClick={() => void sendReply(t)}>
                    <CornerDownLeft aria-hidden /> Send
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => { setReplyId(null); setReplyText(""); }} disabled={busyReply}>Cancel</Button>
                </Row>
              ) : (
                <Row>
                  <Button size="sm" onClick={() => { setReplyId(t.id); setReplyText(""); setRowErr(null); }} disabled={anyBusy}>
                    Answer
                  </Button>
                </Row>
              )}
            </Stack>
          </Card>
        )}
        <Row wrap style={{ gap: "var(--space-2)" }}>
          {t.status === "in_review" && (
            <Button size="sm" variant="primary" loading={busyApprove} disabled={anyBusy} onClick={() => void approve(t)}>
              <Check aria-hidden /> Approve
            </Button>
          )}
          {/* Blocked tickets: expose the review verbs inline. Grant-and-continue
              and mint-rule need extra input, so they stay in Work.tsx Review
              tab; the three one-click verbs (approve = release, deny = cancel,
              dismiss = stop asking) surface here so the panel is actionable
              without a page hop. (B4 / C16.) */}
          {t.status === "blocked" && (
            <>
              <Button size="sm" variant="primary" disabled={anyBusy} onClick={() => void reviewVerb(t, "approve")}>
                <Check aria-hidden /> Release
              </Button>
              <Button size="sm" variant="ghost" disabled={anyBusy} onClick={() => void reviewVerb(t, "dismiss")}>
                Dismiss
              </Button>
              <Button size="sm" variant="danger-ghost" disabled={anyBusy} onClick={() => void reviewVerb(t, "deny")}>
                <X aria-hidden /> Deny
              </Button>
            </>
          )}
          <Button size="sm" variant="ghost" onClick={() => beginEdit(t)} disabled={anyBusy}>
            <Pencil aria-hidden /> Edit
          </Button>
          <Button size="sm" variant="danger-ghost" onClick={() => { setRowErr(null); setConfirmDel(t); }} disabled={anyBusy}>
            <Trash2 aria-hidden /> Delete
          </Button>
        </Row>
        {rowErr && editingId !== t.id && replyId !== t.id && (
          <p className="ws-field__error">{rowErr}</p>
        )}
      </Stack>
    );
  };

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load tasks"
        hint={<><code>{endpoint}</code> ({err})</>}
        action={<Button size="sm" onClick={() => void load()}>Retry</Button>}
      />
    );
  } else if (tasks === null) {
    body = (
      <Stack gap="sm" aria-busy="true">
        <Skeleton width="80%" />
        <Skeleton width="60%" />
        <Skeleton width="70%" />
      </Stack>
    );
  } else if (visibleAll.length === 0) {
    body = (
      <StateBlock
        title={openCount === 0 && !showDone && scoped.some((t) => !isOpenStatus(t.status)) ? "No open tasks" : "No tasks yet"}
        hint={
          openCount === 0 && !showDone && scoped.some((t) => !isOpenStatus(t.status))
            ? "Every task here is done or cancelled. Turn on Show done to see them."
            : "Add a task below, or the worker files tasks here as it plans and works."
        }
      />
    );
  } else {
    body = (
      <>
        {overflow && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: 0 }}>
            Showing the first {OVERFLOW_AT} of {visibleAll.length} tasks.
          </p>
        )}
        <ul className="chat-tasks__list">
          {visible.map((t) => (
            <li key={t.id} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
              {editingId === t.id ? editRow(t) : viewRow(t)}
            </li>
          ))}
        </ul>
      </>
    );
  }

  const toolbar = (
    <div className="chat-tasks__toolbar" style={{ justifyContent: "space-between" }}>
      <Toggle checked={showDone} onChange={setShowDone} label="Show done" />
      <Button size="sm" variant="ghost" onClick={() => void load()} loading={loading} aria-label="Refresh tasks" title="Refresh">
        <RefreshCw aria-hidden /> Refresh
      </Button>
    </div>
  );

  const addRow = (
    <div className="chat-tasks__add">
      <Input
        value={newText}
        placeholder="Add a task..."
        onChange={(e) => setNewText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void add();
          }
        }}
        aria-label="New task"
      />
      <Button variant="primary" icon onClick={() => void add()} disabled={adding || !newText.trim()} aria-label="Add task" title="Add task">
        <Plus aria-hidden />
      </Button>
    </div>
  );

  const confirmDialog = (
    <ConfirmDialog
      open={confirmDel !== null}
      onCancel={() => setConfirmDel(null)}
      onConfirm={() => confirmDel && void del(confirmDel)}
      busy={acting === `delete:${confirmDel?.id}`}
      title="Delete this task?"
      body={<>This removes <strong>{confirmDel ? (taskTitle(confirmDel) || "the task") : "the task"}</strong> from this scope. It can't be undone. Linked run history is kept.</>}
      confirmLabel="Delete task"
    />
  );

  const inner = (
    <Stack gap="sm">
      {toolbar}
      {addErr && <p className="ws-field__error">{addErr}</p>}
      {body}
      {addRow}
    </Stack>
  );

  if (variant === "section") {
    return (
      <section>
        <Row between wrap style={{ alignItems: "baseline" }}>
          <SectionLabel>{heading ?? "Tasks"}</SectionLabel>
        </Row>
        <Card style={{ padding: "var(--space-3)" }}>{inner}</Card>
        {confirmDialog}
      </section>
    );
  }

  return (
    <div className="chat-tasks">
      <div className="chat-tasks__body">{inner}</div>
      {confirmDialog}
    </div>
  );
}
