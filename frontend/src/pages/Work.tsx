import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { RefreshCw, Plus, Pencil, Trash2, ArrowUpRight, Play, Power } from "lucide-react";
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
  Tag,
  catFor,
  type SegmentedOption,
} from "../ui";
import JobsFeed from "../components/JobsFeed";
import AutonomyScopes from "../components/AutonomyScopes";
import { type Job, jobStatusKind } from "../lib/jobs";
import {
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

// The Work surface (plan D4 / sub-decision b): one nav item, two tabs.
//   Tickets — the cross-scope task list + detail drawer (the primary surface).
//   Runs    — the existing Jobs feed, rendered from the shared JobsFeed.
// A ticket IS a task; every identifier stays `task`. /jobs stays a route.

type Tab = "tickets" | "runs" | "autonomy";

const TABS: SegmentedOption<Tab>[] = [
  { value: "tickets", label: "Tickets" },
  { value: "runs", label: "Runs" },
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

// ---- Page ------------------------------------------------------------------

export default function Work() {
  const [tab, setTab] = useState<Tab>("tickets");

  return (
    <div>
      <PageHeader title="Work" sub="Tickets across every scope, the runs they produce, and how each scope processes work." />
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Segmented<Tab> ariaLabel="Work view" value={tab} onChange={setTab} options={TABS} />
      </div>
      {tab === "tickets" ? <TicketsTab /> : tab === "runs" ? <JobsFeed hideHeader /> : <AutonomyTab />}
    </div>
  );
}
