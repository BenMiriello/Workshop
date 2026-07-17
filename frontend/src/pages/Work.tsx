import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  RefreshCw, Plus, Pencil, Trash2, ArrowUpRight, Play, Power,
  Check, X, KeyRound, BookOpen, EyeOff, ShieldCheck,
} from "lucide-react";
import {
  Button,
  Card,
  CardGrid,
  PageHeader,
  Row,
  Stack,
  SectionLabel,
  Segmented,
  Select,
  Input,
  Textarea,
  Field,
  Modal,
  ConfirmDialog,
  StateBlock,
  Skeleton,
  StatusPill,
  StatusDot,
  Toggle,
  Checkbox,
  Tag,
  catFor,
  type SegmentedOption,
} from "../ui";
import JobsFeed from "../components/JobsFeed";
import AutonomyScopes from "../components/AutonomyScopes";
import { type Job, jobStatusKind } from "../lib/jobs";
import {
  type BlockRequest,
  type Task,
  type TaskStatus,
  TASK_STATUSES,
  taskStatusKind,
  taskStatusLabel,
  title as taskTitle,
  description as taskDescription,
  joinText,
  scopeLabel,
  scopeHref,
} from "../lib/tasks";

// The Work surface (plan D4 / sub-decision b): one nav item, four tabs.
//   Tickets  — the cross-scope task list + detail drawer (the primary surface).
//   Runs     — the existing Jobs feed, rendered from the shared JobsFeed.
//   Review   — work that stopped on its own, and the answers that release it.
//   Autonomy — how each scope processes work.
// A ticket IS a task; every identifier stays `task`. /jobs stays a route.
// Review is a fourth TAB rather than a /review route: work state must not split
// across two pages, and this is already the multi-tab home of work state.

type Tab = "tickets" | "runs" | "review" | "autonomy";

const TABS: SegmentedOption<Tab>[] = [
  { value: "tickets", label: "Tickets" },
  { value: "runs", label: "Runs" },
  { value: "review", label: "Review" },
  { value: "autonomy", label: "Autonomy" },
];

const OVERFLOW_AT = 100;

type TasksResp = { tasks: Task[]; count: number; scopes?: number };

function fmtTs(ts?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// ---- Tickets tab -----------------------------------------------------------

function TicketsTab() {
  const [params] = useSearchParams();
  const [data, setData] = useState<TasksResp | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<TaskStatus | "all">("all");
  const [scope, setScope] = useState<string>(params.get("scope") || "all");
  const [open, setOpen] = useState<{ mode: "create" } | { mode: "edit"; task: Task } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const params = new URLSearchParams();
      if (status !== "all") params.set("status", status);
      if (scope !== "all") params.set("scope", scope);
      const q = params.toString();
      const r = await fetch(`/api/tasks${q ? `?${q}` : ""}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [status, scope]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    fetch("/api/tasks/scopes")
      .then((r) => (r.ok ? r.json() : { scopes: [] }))
      .then((j) => setScopes(j.scopes ?? []))
      .catch(() => setScopes([]));
  }, []);

  const tasks = data?.tasks ?? [];
  const overflow = tasks.length > OVERFLOW_AT;
  const shown = overflow ? tasks.slice(0, OVERFLOW_AT) : tasks;

  const onChanged = useCallback(
    (msg?: string) => {
      setNotice(msg ?? null);
      refresh();
    },
    [refresh],
  );

  const filterBar = (
    <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
      <Field label="Status" inline>
        <Select value={status} onChange={(e) => setStatus(e.target.value as TaskStatus | "all")} aria-label="Filter by status">
          <option value="all">All statuses</option>
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>{taskStatusLabel(s)}</option>
          ))}
        </Select>
      </Field>
      <Field label="Scope" inline>
        <Select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Filter by scope">
          <option value="all">All scopes</option>
          {scopes.map((s) => (
            <option key={s} value={s}>{scopeLabel(s)}</option>
          ))}
        </Select>
      </Field>
      <Button size="sm" onClick={refresh} loading={loading}>
        <RefreshCw aria-hidden /> Refresh
      </Button>
      <Button size="sm" variant="primary" onClick={() => { setNotice(null); setOpen({ mode: "create" }); }}>
        <Plus aria-hidden /> New ticket
      </Button>
    </Row>
  );

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load tickets"
        hint={<><code>/api/tasks</code> ({err})</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (!data) {
    body = (
      <CardGrid>
        {[0, 1, 2, 3].map((i) => (
          <Card key={i}>
            <Stack gap="sm">
              <Skeleton width="60%" />
              <Skeleton width="35%" />
              <Skeleton width="25%" />
            </Stack>
          </Card>
        ))}
      </CardGrid>
    );
  } else if (tasks.length === 0) {
    body = (
      <StateBlock
        title={status === "all" && scope === "all" ? "No tickets yet" : "No tickets match these filters"}
        hint={
          status === "all" && scope === "all" ? (
            <>Tickets are the unit of work across every scope (the workshop root, projects, and agents). Create one with <strong>New ticket</strong>, or file one from a chat.</>
          ) : (
            <>Nothing matches. Reset <strong>Status</strong> and <strong>Scope</strong> to <strong>All</strong> to see every ticket.</>
          )
        }
        action={<Button size="sm" variant="primary" onClick={() => setOpen({ mode: "create" })}><Plus aria-hidden /> New ticket</Button>}
      />
    );
  } else {
    body = (
      <>
        {overflow && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>
            Showing the first {OVERFLOW_AT} of {tasks.length} tickets. Narrow by status or scope to see the rest.
          </p>
        )}
        <CardGrid>
          {shown.map((t) => (
            <li key={`${t.scope}:${t.id}`}>
              <Card
                interactive
                compact
                onClick={() => { setNotice(null); setOpen({ mode: "edit", task: t }); }}
                style={{ cursor: "pointer" }}
              >
                <Stack gap="sm">
                  <span style={{ fontWeight: 600 }}>{taskTitle(t) || "(untitled)"}</span>
                  <Row wrap style={{ gap: "var(--space-2)" }}>
                    <StatusPill status={taskStatusKind(t.status)}>{taskStatusLabel(t.status)}</StatusPill>
                    <Tag hue={catFor(t.scope || "workshop")}>{scopeLabel(t.scope)}</Tag>
                    {typeof t.priority === "number" && t.priority !== 0 && (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>priority {t.priority}</span>
                    )}
                    {t.effort && (
                      <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>effort {t.effort}</span>
                    )}
                  </Row>
                </Stack>
              </Card>
            </li>
          ))}
        </CardGrid>
      </>
    );
  }

  return (
    <div>
      {notice && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>{notice}</p>
      )}
      <div style={{ marginBottom: "var(--space-4)" }}>{filterBar}</div>
      {body}
      {open?.mode === "create" && (
        <CreateTicket
          scopes={scopes}
          onClose={() => setOpen(null)}
          onCreated={(msg) => { setOpen(null); onChanged(msg); }}
        />
      )}
      {open?.mode === "edit" && (
        <EditTicket
          task={open.task}
          onClose={() => setOpen(null)}
          onChanged={(msg) => { setOpen(null); onChanged(msg); }}
        />
      )}
    </div>
  );
}

// ---- Create drawer ---------------------------------------------------------

function CreateTicket({
  scopes,
  onClose,
  onCreated,
}: {
  scopes: string[];
  onClose: () => void;
  onCreated: (msg?: string) => void;
}) {
  const [scope, setScope] = useState<string>(scopes[0] ?? "workshop");
  const [titleLine, setTitleLine] = useState("");
  const [desc, setDesc] = useState("");
  const [priority, setPriority] = useState("0");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const options = scopes.length ? scopes : ["workshop"];

  const submit = useCallback(async () => {
    const text = joinText(titleLine, desc);
    if (!text.trim()) { setErr("A title is required."); return; }
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/tasks", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scope, text, priority: Number(priority) || 0 }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      const msg =
        j.decision && j.decision !== "distinct"
          ? `Filed into ${scopeLabel(scope)}: ${j.message ?? j.decision}.`
          : `Ticket created in ${scopeLabel(scope)}.`;
      onCreated(msg);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }, [scope, titleLine, desc, priority, onCreated]);

  return (
    <Modal
      open
      onClose={onClose}
      title="New ticket"
      size="lg"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={submit}>Create ticket</Button>
        </>
      }
    >
      <Stack gap="lg">
        <Field label="Scope" htmlFor="new-ticket-scope">
          <Select id="new-ticket-scope" value={scope} onChange={(e) => setScope(e.target.value)} data-autofocus>
            {options.map((s) => (
              <option key={s} value={s}>{scopeLabel(s)}</option>
            ))}
          </Select>
        </Field>
        <Field label="Title" htmlFor="new-ticket-title" help="The first line is the ticket title.">
          <Input
            id="new-ticket-title"
            value={titleLine}
            onChange={(e) => setTitleLine(e.target.value)}
            placeholder="What needs doing"
          />
        </Field>
        <Field label="Description" htmlFor="new-ticket-desc" optional>
          <Textarea
            id="new-ticket-desc"
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            rows={5}
            placeholder="Context, acceptance, links (optional)"
          />
        </Field>
        <Field label="Priority" htmlFor="new-ticket-priority" help="Higher runs first. Default 0.">
          <Input
            id="new-ticket-priority"
            type="number"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            style={{ maxWidth: "8rem" }}
          />
        </Field>
        {err && <p className="ws-field__error">{err}</p>}
      </Stack>
    </Modal>
  );
}

// ---- Edit drawer -----------------------------------------------------------

type EditDraft = {
  title: string;
  description: string;
  status: TaskStatus;
  priority: string;
  effort: string;
  assignee: string;
};

function EditTicket({
  task,
  onClose,
  onChanged,
}: {
  task: Task;
  onClose: () => void;
  onChanged: (msg?: string) => void;
}) {
  const nav = useNavigate();
  const scope = task.scope ?? "workshop";
  const [current, setCurrent] = useState<Task>(task);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<EditDraft>(() => toDraft(task));
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [runNote, setRunNote] = useState<string | null>(null);

  const idPath = `${encodeURIComponent(scope)}/${encodeURIComponent(task.id)}`;

  // Load the freshest task (evidence changes out of band) + its linked runs.
  useEffect(() => {
    let live = true;
    fetch(`/api/tasks/${idPath}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { if (live && j) { setCurrent(j); setDraft(toDraft(j)); } })
      .catch(() => {});
    fetch(`/api/tasks/${idPath}/jobs`)
      .then((r) => (r.ok ? r.json() : { jobs: [] }))
      .then((j) => { if (live) setJobs(j.jobs ?? []); })
      .catch(() => { if (live) setJobs([]); });
    return () => { live = false; };
  }, [idPath]);

  const setField = <K extends keyof EditDraft>(k: K, v: EditDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(toDraft(current));

  const save = useCallback(async () => {
    setSaving(true);
    setErr(null);
    try {
      const body = {
        text: joinText(draft.title, draft.description),
        status: draft.status,
        priority: Number(draft.priority) || 0,
        effort: draft.effort,
        assignee: draft.assignee.trim() || null,
      };
      const r = await fetch(`/api/tasks/${idPath}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      setCurrent(j);
      setDraft(toDraft(j));
      setEditing(false);
      setFlash(true);
      window.setTimeout(() => setFlash(false), 2000);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [draft, idPath, onChanged]);

  const del = useCallback(async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/tasks/${idPath}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      onChanged("Ticket deleted.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setSaving(false);
      setConfirmDel(false);
    }
  }, [idPath, onChanged]);

  // Run this ticket now: open a Job + launch a single-task worker (no cron wait).
  // A busy tree returns 409, surfaced inline as a status line (not a dialog).
  const runNow = useCallback(async () => {
    setRunBusy(true);
    setRunNote(null);
    setErr(null);
    try {
      const r = await fetch(`/api/tasks/${idPath}/run`, { method: "POST" });
      if (r.status === 409) {
        const e = await r.json().catch(() => ({}));
        setRunNote(e.detail || "A sweep is already running against this scope. Try again when it finishes.");
        return;
      }
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setRunNote("Run started. It appears under Runs below.");
      // Keep the drawer open (do NOT call onChanged, which closes it): just
      // refetch this ticket's linked runs so the new one shows immediately.
      const jr = await fetch(`/api/tasks/${idPath}/jobs`)
        .then((x) => (x.ok ? x.json() : { jobs: [] }))
        .catch(() => ({ jobs: [] }));
      setJobs(jr.jobs ?? []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunBusy(false);
    }
  }, [idPath]);

  const href = scopeHref(scope);
  const evidence = current.evidence ?? [];
  const canRun = current.status === "todo";

  return (
    <Modal
      open
      onClose={onClose}
      title={editing ? "Edit ticket" : (taskTitle(current) || "Ticket")}
      size="lg"
      actions={
        editing ? (
          <>
            <Button onClick={() => { setEditing(false); setDraft(toDraft(current)); }}>Cancel</Button>
            <Button variant="primary" loading={saving} disabled={!dirty} onClick={save}>Save</Button>
          </>
        ) : (
          <>
            <Button variant="danger-ghost" onClick={() => setConfirmDel(true)}>
              <Trash2 aria-hidden /> Delete
            </Button>
            {canRun && (
              <Button loading={runBusy} onClick={runNow}>
                <Play aria-hidden /> Run now
              </Button>
            )}
            <Button variant="primary" onClick={() => { setDraft(toDraft(current)); setEditing(true); }}>
              <Pencil aria-hidden /> Edit
            </Button>
          </>
        )
      }
    >
      <Stack gap="lg">
        {editing ? (
          <>
            <Field label="Title" htmlFor="edit-ticket-title">
              <Input id="edit-ticket-title" value={draft.title} onChange={(e) => setField("title", e.target.value)} data-autofocus />
            </Field>
            <Field label="Description" htmlFor="edit-ticket-desc" optional>
              <Textarea id="edit-ticket-desc" rows={5} value={draft.description} onChange={(e) => setField("description", e.target.value)} />
            </Field>
            <Row wrap style={{ gap: "var(--space-4)", alignItems: "flex-end" }}>
              <Field label="Status" htmlFor="edit-ticket-status">
                <Select id="edit-ticket-status" value={draft.status} onChange={(e) => setField("status", e.target.value as TaskStatus)}>
                  {TASK_STATUSES.map((s) => (
                    <option key={s} value={s}>{taskStatusLabel(s)}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Priority" htmlFor="edit-ticket-priority">
                <Input id="edit-ticket-priority" type="number" value={draft.priority} onChange={(e) => setField("priority", e.target.value)} style={{ maxWidth: "6rem" }} />
              </Field>
              <Field label="Effort" htmlFor="edit-ticket-effort">
                <Select id="edit-ticket-effort" value={draft.effort} onChange={(e) => setField("effort", e.target.value)}>
                  <option value="">none</option>
                  <option value="s">s</option>
                  <option value="m">m</option>
                  <option value="l">l</option>
                </Select>
              </Field>
              <Field label="Assignee" htmlFor="edit-ticket-assignee" help="operator or agent:<slug>">
                <Input id="edit-ticket-assignee" value={draft.assignee} onChange={(e) => setField("assignee", e.target.value)} placeholder="operator" style={{ maxWidth: "12rem" }} />
              </Field>
            </Row>
            {err && <p className="ws-field__error">{err}</p>}
            {dirty && <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>Unsaved changes.</p>}
          </>
        ) : (
          <>
            <Row wrap style={{ gap: "var(--space-2)" }}>
              <StatusPill status={taskStatusKind(current.status)}>{taskStatusLabel(current.status)}</StatusPill>
              {href ? (
                <Link to={href} className="ws-bc__link" onClick={onClose}>
                  <Tag hue={catFor(scope)}>{scopeLabel(scope)}</Tag>
                </Link>
              ) : (
                <Tag hue={catFor(scope)}>{scopeLabel(scope)}</Tag>
              )}
              {typeof current.priority === "number" && current.priority !== 0 && (
                <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>priority {current.priority}</span>
              )}
              {current.effort && <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>effort {current.effort}</span>}
              {current.assignee && <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{current.assignee}</span>}
            </Row>
            {taskDescription(current) ? (
              <p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{taskDescription(current)}</p>
            ) : (
              <p className="ws-muted" style={{ margin: 0 }}>No description.</p>
            )}
            {current.blocked_reason && (
              <p className="ws-muted" style={{ margin: 0 }}>Blocked: {current.blocked_reason}</p>
            )}
            {flash && <p className="ws-muted" style={{ margin: 0 }}>Saved.</p>}
            {runNote && <p className="ws-muted" style={{ margin: 0 }}>{runNote}</p>}
            {err && <p className="ws-field__error">{err}</p>}

            <div>
              <SectionLabel>Evidence</SectionLabel>
              {evidence.length === 0 ? (
                <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>No evidence recorded yet.</p>
              ) : (
                <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {evidence.map((e, i) => (
                    <li key={i} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
                      <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                        <Tag hue={catFor(String(e.kind || "note"))}>{String(e.kind || "note")}</Tag>
                        <span style={{ flex: "1 1 12rem", wordBreak: "break-word" }}>{String(e.ref ?? "")}</span>
                        {e.ts && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{fmtTs(e.ts)}</span>}
                      </Row>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <SectionLabel>Runs</SectionLabel>
              {jobs === null ? (
                <Skeleton width="50%" />
              ) : jobs.length === 0 ? (
                <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>No runs yet. A run is recorded whenever a worker picks up this ticket.</p>
              ) : (
                <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {jobs.map((job) => (
                    <li key={job.id} style={{ padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}>
                      <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                        <StatusDot status={jobStatusKind(job.status)}>{job.status ?? "unknown"}</StatusDot>
                        <span style={{ flex: "1 1 12rem", wordBreak: "break-word" }}>{job.title || job.id}</span>
                        <button
                          type="button"
                          className="ws-bc__link"
                          style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
                          onClick={() => { onClose(); nav(`/jobs/${encodeURIComponent(job.id)}`); }}
                        >
                          View run <ArrowUpRight size={14} aria-hidden />
                        </button>
                      </Row>
                      {(job.summary || job.error) && (
                        <p className="ws-muted" style={{ fontSize: "var(--fs-xs)", margin: "var(--space-1) 0 0" }}>
                          {receiptLine(job)}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </Stack>

      <ConfirmDialog
        open={confirmDel}
        onCancel={() => setConfirmDel(false)}
        onConfirm={del}
        busy={saving}
        title="Delete this ticket?"
        body={<>This removes the ticket from <strong>{scopeLabel(scope)}</strong>. It can't be undone. Linked run history is kept.</>}
        confirmLabel="Delete ticket"
      />
    </Modal>
  );
}

function toDraft(t: Task): EditDraft {
  return {
    title: taskTitle(t),
    description: taskDescription(t),
    status: (t.status ?? "todo") as TaskStatus,
    priority: String(t.priority ?? 0),
    effort: t.effort ?? "",
    assignee: t.assignee ?? "",
  };
}

// A one-line receipt summary for a run: the worker's summary, else the error,
// plus a changed-file count when the Phase 1 receipt evidence is present.
function receiptLine(job: Job): string {
  const ev = job.evidence;
  let files = 0;
  if (ev && typeof ev === "object" && !Array.isArray(ev)) {
    const f = (ev as Record<string, unknown>).files;
    if (Array.isArray(f)) files = f.length;
  }
  const base = job.summary || job.error || "";
  const filePart = files ? `${files} file${files === 1 ? "" : "s"} changed` : "";
  return [filePart, base].filter(Boolean).join(" · ");
}

// ---- Autonomy tab ----------------------------------------------------------
// The central autonomy surface: a prominent global master switch (the same
// .runtime/AUTONOMY_DISABLED flag the Agents page toggles, via the shared
// /api/agents/autonomy/{status,enable,disable} endpoints) over the per-scope
// management list. OFF halts every background sweep; a manual Run now still
// works, scope by scope.

type SupStatus = { cron_autonomy?: { disabled?: boolean } } | null;

function AutonomyTab() {
  const [sup, setSup] = useState<SupStatus>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const r = await fetch("/api/agents/autonomy/status");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setSup(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const setAutonomy = useCallback(async (enabled: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/agents/autonomy/${enabled ? "enable" : "disable"}`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [load]);

  const on = sup ? !sup.cron_autonomy?.disabled : null;

  return (
    <Stack>
      <Card style={{ padding: "var(--space-4)" }}>
        {err && !sup ? (
          <StateBlock
            variant="error"
            title="Couldn't load the autonomy switch"
            hint={<><code>/api/agents/autonomy/status</code> ({err})</>}
            action={<Button size="sm" onClick={load}>Retry</Button>}
          />
        ) : sup === null ? (
          <Stack gap="sm"><Skeleton width="40%" /><Skeleton width="60%" /></Stack>
        ) : (
          <Row between wrap style={{ gap: "var(--space-4)", alignItems: "center" }}>
            <Stack gap="sm" style={{ minWidth: 0 }}>
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <Power size={16} strokeWidth={1.8} aria-hidden />
                <span style={{ fontWeight: 600, fontSize: "var(--fs-md)" }}>Autonomous job processing</span>
                <StatusPill status={on ? "running" : "stopped"}>{on ? "On" : "Off"}</StatusPill>
              </Row>
              <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
                When off, no background sweeps fire across any scope. Manual Run now still works per scope.
              </span>
              {err && <span style={{ color: "var(--danger)", fontSize: "var(--fs-sm)" }}>{err}</span>}
            </Stack>
            <Toggle
              checked={!!on}
              disabled={busy}
              onChange={(next) => setAutonomy(next)}
              label={<span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{on ? "on" : "off"}</span>}
            />
          </Row>
        )}
      </Card>

      <AutonomyScopes />
    </Stack>
  );
}

// ---- Review tab ------------------------------------------------------------
// Work that stopped on its own, and the answers that let it continue. This is a
// dashboard BEHIND the stop, not a gate in front of the work: nothing routes
// through it and nothing waits on it for permission to start. Remove the tab and
// not one blocked ticket proceeds — which is the test that it is not a queue.
//
// Blocks group by `dedupe_key`, so ten tickets stopped by one rule are one card
// and one answer. Answering a group fans out to its tickets; the operator makes
// the decision once because there was only ever one decision.

type Candidate = {
  hash: string;
  scope: string;
  text: string;
  sources?: string[];
  proposed_at?: string;
};

type BlockGroup = {
  key: string;
  tasks: Task[];
  request: BlockRequest;
};

function groupBlocks(tasks: Task[]): BlockGroup[] {
  const groups = new Map<string, BlockGroup>();
  for (const t of tasks) {
    const req = t.blocked_request ?? t.awaiting ?? null;
    if (!req || req.dismissed) continue;
    // No dedupe key means the block speaks only for its own ticket.
    const key = req.dedupe_key || `task:${t.scope}:${t.id}`;
    const g = groups.get(key);
    if (g) g.tasks.push(t);
    else groups.set(key, { key, tasks: [t], request: req });
  }
  return [...groups.values()];
}

type ReviewAction = "approve" | "deny" | "grant-and-continue" | "mint-rule" | "dismiss";

async function postReview(task: Task, body: Record<string, unknown>): Promise<void> {
  const path = `${encodeURIComponent(task.scope ?? "workshop")}/${encodeURIComponent(task.id)}`;
  const r = await fetch(`/api/tasks/${path}/review`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
}

function ReviewTab() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await fetch("/api/tasks?status=blocked&include_done=false");
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j: TasksResp = await r.json();
      setTasks(j.tasks ?? []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    try {
      const r = await fetch("/api/tasks/promotion-candidates");
      setCandidates(r.ok ? (await r.json()).candidates ?? [] : []);
    } catch {
      setCandidates([]);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const groups = groupBlocks(tasks ?? []);
  const overflow = groups.length > OVERFLOW_AT;
  const shown = overflow ? groups.slice(0, OVERFLOW_AT) : groups;

  const done = useCallback((msg: string) => { setNotice(msg); refresh(); }, [refresh]);

  let body: JSX.Element;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load blocked work"
        hint={<><code>/api/tasks?status=blocked</code> ({err})</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (tasks === null) {
    body = (
      <Stack gap="sm">
        {[0, 1].map((i) => (
          <Card key={i}><Stack gap="sm"><Skeleton width="55%" /><Skeleton width="80%" /><Skeleton width="30%" /></Stack></Card>
        ))}
      </Stack>
    );
  } else if (groups.length === 0) {
    body = (
      <StateBlock
        title="Nothing is waiting on you"
        hint={<>A ticket lands here when a run stops and asks: for a permission it doesn't hold, a resource it can't reach, or a call the ticket didn't make. Nothing is stuck behind this tab, so an empty list means work is running.</>}
      />
    );
  } else {
    body = (
      <Stack>
        {overflow && (
          <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
            Showing the first {OVERFLOW_AT} of {groups.length} blocks. Answering a block removes it from this list.
          </p>
        )}
        {shown.map((g) => (
          <BlockCard key={g.key} group={g} onDone={done} />
        ))}
      </Stack>
    );
  }

  return (
    <Stack gap="lg">
      <div>
        <Row between wrap style={{ gap: "var(--space-3)", alignItems: "center", marginBottom: "var(--space-3)" }}>
          <SectionLabel>Blocked work</SectionLabel>
          <Button size="sm" onClick={refresh} loading={loading}>
            <RefreshCw aria-hidden /> Refresh
          </Button>
        </Row>
        {notice && <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: "0 0 var(--space-3)" }}>{notice}</p>}
        {body}
      </div>
      <PromotionCandidates rows={candidates} onDone={done} />
    </Stack>
  );
}

function BlockCard({ group, onDone }: { group: BlockGroup; onDone: (msg: string) => void }) {
  const { request, tasks } = group;
  const [busy, setBusy] = useState<ReviewAction | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDeny, setConfirmDeny] = useState(false);
  const [open, setOpen] = useState<"grant" | "rule" | null>(null);

  const n = tasks.length;
  const label = n === 1 ? "1 ticket" : `${n} tickets`;

  // One gesture, N tickets: the decision was made once, so it is applied once.
  const run = useCallback(
    async (action: ReviewAction, extra: Record<string, unknown> = {}, msg?: string) => {
      setBusy(action);
      setErr(null);
      try {
        await Promise.all(tasks.map((t) => postReview(t, { action, ...extra })));
        onDone(msg ?? `${label} updated.`);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
        setBusy(null);
      }
    },
    [tasks, label, onDone],
  );

  return (
    <Card>
      <Stack>
        <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
          <Tag hue={catFor(request.kind || "other")}>{request.kind || "other"}</Tag>
          {[...new Set(tasks.map((t) => t.scope ?? "workshop"))].map((s) => (
            <Tag key={s} hue={catFor(s)}>{scopeLabel(s)}</Tag>
          ))}
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{label}</span>
          {request.since && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>since {fmtTs(request.since)}</span>}
          {request.raised_by && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>raised by {request.raised_by}</span>}
        </Row>

        <span style={{ fontWeight: 600 }}>{request.ask || request.question}</span>
        {request.why && <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>{request.why}</p>}
        {request.unblocks_if && (
          <Row style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
            <KeyRound size={14} strokeWidth={1.8} aria-hidden />
            <span style={{ fontSize: "var(--fs-sm)" }}>{request.unblocks_if}</span>
          </Row>
        )}
        {request.options && request.options.length > 0 && (
          <Row wrap style={{ gap: "var(--space-2)" }}>
            {request.options.map((o) => <Tag key={o} hue={catFor(o)}>{o}</Tag>)}
          </Row>
        )}
        {request.default && (
          <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
            Would have done, at a wider latitude: {request.default}
          </p>
        )}
        {request.proposal && (
          <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
            Asks for <strong>{request.proposal.verb}</strong> on <code>{request.proposal.uri}</code>
          </p>
        )}

        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {tasks.map((t) => (
            <li key={`${t.scope}:${t.id}`} style={{ padding: "var(--space-1) 0" }}>
              <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
                <StatusDot status={taskStatusKind(t.status)}>{taskStatusLabel(t.status)}</StatusDot>
                <span style={{ flex: "1 1 12rem", wordBreak: "break-word", fontSize: "var(--fs-sm)" }}>{taskTitle(t) || t.id}</span>
              </Row>
            </li>
          ))}
        </ul>

        {err && <p className="ws-field__error">{err}</p>}

        <Row wrap style={{ gap: "var(--space-2)" }}>
          <Button size="sm" variant="primary" loading={busy === "approve"}
                  onClick={() => run("approve", {}, `${label} released. The next sweep picks the work up fresh.`)}>
            <Check aria-hidden /> Approve
          </Button>
          {request.proposal && (
            <Button size="sm" loading={busy === "grant-and-continue"} onClick={() => setOpen("grant")}>
              <KeyRound aria-hidden /> Grant + continue
            </Button>
          )}
          <Button size="sm" loading={busy === "mint-rule"} onClick={() => setOpen("rule")}>
            <BookOpen aria-hidden /> Mint rule
          </Button>
          <Button size="sm" loading={busy === "dismiss"}
                  onClick={() => run("dismiss", {}, `${label} left blocked, off the review list.`)}>
            <EyeOff aria-hidden /> Dismiss
          </Button>
          <Button size="sm" variant="danger-ghost" onClick={() => setConfirmDeny(true)}>
            <X aria-hidden /> Deny
          </Button>
        </Row>
      </Stack>

      {open === "grant" && request.proposal && (
        <GrantDialog
          proposal={request.proposal}
          count={n}
          onClose={() => setOpen(null)}
          onSubmit={async (fields) => {
            setOpen(null);
            await run("grant-and-continue", fields, `Granted, and ${label} released.`);
          }}
        />
      )}
      {open === "rule" && (
        <RuleDialog
          ask={request.ask || request.question || ""}
          scope={tasks[0].scope ?? "workshop"}
          onClose={() => setOpen(null)}
          onSubmit={async (fields) => {
            setOpen(null);
            await run("mint-rule", fields, `Rule recorded, and ${label} released.`);
          }}
        />
      )}

      <ConfirmDialog
        open={confirmDeny}
        onCancel={() => setConfirmDeny(false)}
        onConfirm={() => { setConfirmDeny(false); run("deny", {}, `${label} cancelled.`); }}
        title={n === 1 ? "Deny this ticket?" : `Deny ${label}?`}
        body={<>This cancels {n === 1 ? "the ticket" : `all ${n} tickets`} in this block. It can't be undone; the work will not happen. Run history and evidence are kept.</>}
        confirmLabel="Deny and cancel"
      />
    </Card>
  );
}

// Grant + continue: the durable answer to a block that named a resource and a
// verb. Prefilled from the block's own proposal, because the thing that stopped
// the job is the thing that becomes the rule that stops stopping it.
function GrantDialog({
  proposal,
  count,
  onClose,
  onSubmit,
}: {
  proposal: { uri: string; verb: string };
  count: number;
  onClose: () => void;
  onSubmit: (fields: Record<string, unknown>) => void;
}) {
  const [uri, setUri] = useState(proposal.uri);
  const [verb, setVerb] = useState(proposal.verb);
  const [subtree, setSubtree] = useState(false);
  const [ttl, setTtl] = useState("");

  return (
    <Modal
      open
      onClose={onClose}
      title="Grant standing access"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => onSubmit({
            store_uri: uri.trim(),
            verbs: [verb],
            subtree,
            ttl_hours: ttl.trim() ? Number(ttl) : null,
          })}>Grant and release</Button>
        </>
      }
    >
      <Stack gap="lg">
        <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>
          Mints an enforced grant and releases {count === 1 ? "the ticket" : `all ${count} tickets`}. Checked mechanically on every store call from then on, with no model in the loop.
        </p>
        <Field label="Store URI" htmlFor="grant-uri">
          <Input id="grant-uri" value={uri} onChange={(e) => setUri(e.target.value)} data-autofocus />
        </Field>
        <Row wrap style={{ gap: "var(--space-4)", alignItems: "flex-end" }}>
          <Field label="Verb" htmlFor="grant-verb">
            <Select id="grant-verb" value={verb} onChange={(e) => setVerb(e.target.value)}>
              <option value="read">read</option>
              <option value="write">write</option>
              <option value="append">append</option>
            </Select>
          </Field>
          <Field label="Expires in" htmlFor="grant-ttl" help="Hours. Empty = standing.">
            <Input id="grant-ttl" type="number" value={ttl} onChange={(e) => setTtl(e.target.value)} style={{ maxWidth: "7rem" }} />
          </Field>
        </Row>
        <Checkbox checked={subtree} onChange={setSubtree}
                  label="Cover everything under this URI, not just the URI itself" />
      </Stack>
    </Modal>
  );
}

// Mint rule: the durable answer to a block whose ask was a judgment. Scoped to
// the ticket's own scope by default — `global` is injected into every agent's
// context forever, so it stays a deliberate act, never a default.
function RuleDialog({
  ask,
  scope,
  onClose,
  onSubmit,
}: {
  ask: string;
  scope: string;
  onClose: () => void;
  onSubmit: (fields: Record<string, unknown>) => void;
}) {
  const [rule, setRule] = useState("");
  const [ruleScope, setRuleScope] = useState("");

  return (
    <Modal
      open
      onClose={onClose}
      title="Mint a rule from this block"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!rule.trim()} onClick={() => onSubmit({
            rule: rule.trim(),
            rule_scope: ruleScope || null,
          })}>Record and release</Button>
        </>
      }
    >
      <Stack gap="lg">
        <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>Asked: {ask}</p>
        <Field label="The standing answer" htmlFor="rule-text" help="Prose a future run reads before it works this scope. It is guidance, not a wall: a rule that proves itself across three blocks is offered for promotion to an enforced grant.">
          <Textarea id="rule-text" rows={4} value={rule} onChange={(e) => setRule(e.target.value)}
                    placeholder="Prefer the existing UI primitive over a new one." data-autofocus />
        </Field>
        <Field label="Scope" htmlFor="rule-scope" help="Global rules are injected into every agent's context, forever.">
          <Select id="rule-scope" value={ruleScope} onChange={(e) => setRuleScope(e.target.value)}>
            <option value="">{scopeLabel(scope)} (this ticket's scope)</option>
            <option value="global">global — every scope, every run</option>
          </Select>
        </Field>
      </Stack>
    </Modal>
  );
}

// Promotion candidates: prose that keeps getting re-derived from independent
// blocks is not really a judgment — it is a grant that was typed as prose because
// prose was the easy box. Nothing here has happened yet; the system proposes and
// the operator decides, because the operator owns the rules.
function PromotionCandidates({ rows, onDone }: { rows: Candidate[] | null; onDone: (msg: string) => void }) {
  const [open, setOpen] = useState<Candidate | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const promote = useCallback(async (c: Candidate, fields: Record<string, unknown>) => {
    setOpen(null);
    setErr(null);
    try {
      const r = await fetch("/api/tasks/promote", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scope: c.scope, hash: c.hash, ...fields }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      onDone("Rule promoted: it is now enforced, and the prose stays as the reason.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [onDone]);

  return (
    <div>
      <SectionLabel>Promotion candidates</SectionLabel>
      {err && <p className="ws-field__error">{err}</p>}
      {rows === null ? (
        <Skeleton width="45%" />
      ) : rows.length === 0 ? (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
          None yet. A rule minted from a block shows up here once three independent blocks have produced it, as an offer to make it enforced instead of advisory.
        </p>
      ) : (
        <Stack gap="sm">
          {rows.map((c) => (
            <Card key={`${c.scope}:${c.hash}`} compact>
              <Stack gap="sm">
                <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                  <Tag hue={catFor(c.scope)}>{scopeLabel(c.scope)}</Tag>
                  <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                    held {(c.sources ?? []).length} times across independent blocks
                  </span>
                </Row>
                <span>{c.text}</span>
                <Row wrap style={{ gap: "var(--space-2)" }}>
                  <Button size="sm" onClick={() => setOpen(c)}>
                    <ShieldCheck aria-hidden /> Make it enforced
                  </Button>
                </Row>
              </Stack>
            </Card>
          ))}
        </Stack>
      )}
      {open && (
        <GrantDialog
          proposal={{ uri: "", verb: "write" }}
          count={1}
          onClose={() => setOpen(null)}
          onSubmit={(fields) => promote(open, fields)}
        />
      )}
    </div>
  );
}

// ---- Page ------------------------------------------------------------------

export default function Work() {
  const [tab, setTab] = useState<Tab>("tickets");

  return (
    <div>
      <PageHeader title="Work" sub="Tickets across every scope, the runs they produce, what stopped and needs an answer, and how each scope processes work." />
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Segmented<Tab> ariaLabel="Work view" value={tab} onChange={setTab} options={TABS} />
      </div>
      {tab === "tickets" ? <TicketsTab />
        : tab === "runs" ? <JobsFeed hideHeader />
        : tab === "review" ? <ReviewTab />
        : <AutonomyTab />}
    </div>
  );
}
