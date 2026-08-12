import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  RefreshCw, Plus, Search,
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
  Tag,
  catFor,
  type SegmentedOption,
} from "@workshop/ui";
import JobsFeed from "../components/JobsFeed";
import QueueRunner from "../components/QueueRunner";
import AutonomyPanel from "../components/AutonomyPanel";
import { GrantDialog, PromoteGrantDialog, RuleDialog } from "../components/ReviewDialogs";
import {
  type BlockRequest,
  type Task,
  type TaskSort,
  type TaskStatus,
  TASK_SORTS,
  TASK_STATUSES,
  taskStatusKind,
  taskStatusLabel,
  title as taskTitle,
  description as taskDescription,
  cleanTitle,
  scopeLabel,
  ticketHref,
} from "../lib/tasks";
import { boardLanes, groupTickets, type GroupBy } from "../lib/ticketGroups";
import { orderScopes } from "../lib/devMode";
import { pickupFor, pickupNote, type PickupMap } from "../lib/scopePickup";
import { ALL_SCOPES, nextWorkParams, parseWorkScope, parseWorkTab } from "../lib/workParams";

// The Tickets surface: one nav item, three tabs.
//   Open   — the cross-scope ticket list (the primary surface), as a list or a board.
//   Runs   — the existing Jobs feed, rendered from the shared JobsFeed.
//   Review — work that stopped on its own, and the answers that release it.
//
// A ticket IS a task; every identifier stays `task` (ADR 0001, and
// entity-model-2026-07-18 §9.5 closes it). What changed on 2026-08-11 is the
// SURFACE name only: the page was called "Work", which is a mass noun and so
// could never name the individual item, while the item had been ratified as
// "Ticket" since July. The route stays /work so every existing link, bookmark
// and `?tab=` deep link keeps resolving.
//
// Review is a third TAB rather than a /review route: work state must not split
// across two pages, and this is already the multi-tab home of work state.
// Autonomy moved off here (C6): the canonical toggle lives on /settings; a
// read-only pill on /agents shows current state.

type Tab = "tickets" | "runs" | "review";

const TABS: SegmentedOption<Tab>[] = [
  { value: "tickets", label: "Open" },
  { value: "runs", label: "Runs" },
  { value: "review", label: "Review" },
];

type View = "list" | "board";

const VIEWS: SegmentedOption<View>[] = [
  { value: "list", label: "List" },
  { value: "board", label: "Board" },
];

const GROUPS: { value: GroupBy; label: string }[] = [
  { value: "none", label: "No grouping" },
  { value: "status", label: "By status" },
  { value: "scope", label: "By scope" },
];

// One page of tickets. The list used to slice an arbitrary first 100 out of
// whatever order the store file happened to be in, with no way to reach the
// rest; the endpoint now sorts and pages, so this is a real page size.
const PAGE_SIZE = 60;

type TasksResp = { tasks: Task[]; count: number; total: number };

function fmtTs(ts?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// ---- Tickets tab -----------------------------------------------------------

// One ticket in the grid: strong line, muted line, pills, meta — the same four
// bands its sibling tab's run card uses, so the two tabs read as one surface.
//
// The description is collapsed to a single run of text on purpose. A ticket body
// is `Objective: ...\nOutput: ...\nStart from: ...`, and preserving those breaks
// in a two-line preview shows one and a half labels and no content.
function TicketCard({ task, onOpen }: { task: Task; onOpen: () => void }) {
  const title = taskTitle(task);
  const desc = taskDescription(task).replace(/\s+/g, " ").trim();
  const meta = [
    typeof task.priority === "number" && task.priority !== 0 ? `priority ${task.priority}` : null,
    task.effort ? `effort ${task.effort}` : null,
    (task.comments?.length ?? 0) > 0
      ? `${task.comments?.length} comment${task.comments?.length === 1 ? "" : "s"}` : null,
  ].filter(Boolean).join(" · ");

  return (
    <Card interactive compact className="ws-ticket-card" onClick={onOpen} style={{ cursor: "pointer" }}>
      <span className="ws-ticket-card__title ws-truncate" title={title}>
        {title || "(untitled)"}
      </span>
      {desc && (
        <span className="ws-ticket-card__desc ws-truncate" title={desc}>{desc}</span>
      )}
      <div className="ws-ticket-card__pills">
        <StatusPill status={taskStatusKind(task.status)}>{taskStatusLabel(task.status)}</StatusPill>
        <Tag hue={catFor(task.scope || "workshop")}>{scopeLabel(task.scope)}</Tag>
      </div>
      {meta && <span className="ws-ticket-card__meta">{meta}</span>}
    </Card>
  );
}

function TicketsTab() {
  const nav = useNavigate();
  // `?scope=` DRIVES the filter; it used to seed it once, on mount, so a
  // "/work?scope=X" link pressed from a card while already on /work changed the
  // address bar and nothing else.
  const [search, setSearch] = useSearchParams();
  const scope = parseWorkScope(search.get("scope"));
  const setScope = (v: string) => setSearch(nextWorkParams(search, { scope: v }), { replace: true });
  const [data, setData] = useState<TasksResp | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);
  const [pickup, setPickup] = useState<PickupMap | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<TaskStatus | "all">("all");
  // View preferences, so they commit on change (frontend/CLAUDE.md).
  const [sort, setSort] = useState<TaskSort>("updated");
  const [group, setGroup] = useState<GroupBy>("none");
  const [view, setView] = useState<View>("list");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [open, setOpen] = useState<{ mode: "create" } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Typing must not fire a cross-scope scan per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  // A new filter starts at the first page again. Without this, narrowing the
  // list while paged in leaves an offset the new result set may not have.
  useEffect(() => { setLimit(PAGE_SIZE); }, [status, scope, debouncedQuery, sort]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const params = new URLSearchParams();
      if (status !== "all") params.set("status", status);
      if (scope !== ALL_SCOPES) params.set("scope", scope);
      if (debouncedQuery) params.set("q", debouncedQuery);
      params.set("sort", sort);
      // The board needs every lane populated to be a board, so it is not paged.
      if (view === "list") params.set("limit", String(limit));
      const r = await fetch(`/api/tasks?${params.toString()}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [status, scope, debouncedQuery, sort, limit, view]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    fetch("/api/tasks/scopes")
      .then((r) => (r.ok ? r.json() : { scopes: [] }))
      // `orderScopes` puts Development above Workshop and drops it entirely
      // from a non-dev bundle, so this list reads the same whichever backend
      // answered (ADR 0014).
      .then((j) => { setScopes(orderScopes(j.scopes ?? [])); setPickup(j.pickup ?? null); })
      .catch(() => { setScopes([]); setPickup(null); });
  }, []);

  const tasks = data?.tasks ?? [];
  const total = data?.total ?? tasks.length;
  const more = tasks.length < total;
  const scopeNote = scope !== ALL_SCOPES ? pickupNote(pickupFor(pickup, scope)) : null;

  const onChanged = useCallback(
    (msg?: string) => {
      setNotice(msg ?? null);
      refresh();
    },
    [refresh],
  );

  const openTicket = useCallback((t: Task) => {
    setNotice(null);
    nav(ticketHref(t.scope || "workshop", t.id));
  }, [nav]);

  // Work the system proposed and nobody has agreed to yet. A draft is never
  // picked up by a sweep and cannot be run, so if it is not surfaced it is
  // simply lost — which is the failure the approval gate would otherwise cause
  // rather than prevent. Hidden once the operator is already looking at drafts.
  const draftCount = tasks.filter((t) => t.status === "draft").length;
  const draftStrip = draftCount > 0 && status !== "draft" ? (
    <Card>
      <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
        <StatusDot status="warn">
          {draftCount} proposed {draftCount === 1 ? "ticket is" : "tickets are"} waiting for you
        </StatusDot>
        <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
          Nothing runs them until you approve.
        </span>
        <Button size="sm" variant="ghost" onClick={() => setStatus("draft")}>
          Review {draftCount === 1 ? "it" : "them"}
        </Button>
      </Row>
    </Card>
  ) : null;

  // Scope-level autonomy, parked where the scope's tickets already are.
  //
  // DELIBERATELY MINIMAL, AND NOT A DESIGN. `development` reached its sweep
  // config through the Projects page, and the platform repo is no longer
  // offered as a project (ADR 0014 §4), so the preset, cron, latitude, limits
  // and the whole-queue Run now became unreachable: still served, still
  // working, linked from nowhere. This puts them back next to the queue they
  // govern so the capability is not silently missing.
  //
  // Only `development`, because it is the only scope whose panel has no other
  // home. A project's lives on its detail page, an agent's on its own. Showing
  // it for those too would be a second copy of a control that already exists,
  // which is the duplication this app keeps having to undo.
  //
  // The seam that a proper design has to settle later: this reads and writes
  // through `/api/projects/development/autonomy`, so a scope we deliberately
  // stopped calling a project still configures itself through the projects
  // API. That is incoherent, and it is why this is wiring rather than design.
  //
  // AutonomyPanel renders its own SectionLabel, so this does not add a second
  // one on top of it. It used to, and the panel arrived under two stacked
  // headings that said nearly the same word.
  const scopeAutonomy = scope === "development" ? (
    <div style={{ marginBottom: "var(--space-4)" }}>
      <AutonomyPanel slug="development" />
    </div>
  ) : null;

  const filterBar = (
    <Stack gap="sm">
      <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
        <Field label="Find" inline>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Title or body"
            aria-label="Search tickets"
            style={{ maxWidth: "16rem" }}
          />
        </Field>
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
      <Row wrap between style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
        <Row wrap style={{ gap: "var(--space-3)", alignItems: "flex-end" }}>
          {/* Sorting is done by the SERVER. The list used to come back in raw
              store order (insertion order) with priority displayed and never
              sorted on, so "higher runs first" was invisible in the one place
              it should have been obvious. */}
          <Field label="Sort" inline>
            <Select value={sort} onChange={(e) => setSort(e.target.value as TaskSort)} aria-label="Sort tickets">
              {TASK_SORTS.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </Select>
          </Field>
          {view === "list" && (
            <Field label="Group" inline>
              <Select value={group} onChange={(e) => setGroup(e.target.value as GroupBy)} aria-label="Group tickets">
                {GROUPS.map((g) => (
                  <option key={g.value} value={g.value}>{g.label}</option>
                ))}
              </Select>
            </Field>
          )}
        </Row>
        <Segmented<View> ariaLabel="Ticket view" value={view} onChange={setView} options={VIEWS} />
      </Row>
    </Stack>
  );

  const grid = (items: Task[]) => (
    <CardGrid className="ws-ticket-grid">
      {items.map((t) => (
        <li key={`${t.scope}:${t.id}`}>
          <TicketCard task={t} onOpen={() => openTicket(t)} />
        </li>
      ))}
    </CardGrid>
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
    const filtered = status !== "all" || scope !== ALL_SCOPES || !!debouncedQuery;
    body = (
      <StateBlock
        title={filtered ? "No tickets match these filters" : "No tickets yet"}
        hint={
          filtered ? (
            <>Nothing matches. Clear <strong>Find</strong>, or reset <strong>Status</strong> and <strong>Scope</strong> to <strong>All</strong>.</>
          ) : (
            <>Create one with <strong>New ticket</strong>, or file one from a chat.</>
          )
        }
        action={
          filtered ? (
            <Button size="sm" onClick={() => { setQuery(""); setStatus("all"); setScope(ALL_SCOPES); }}>
              <Search aria-hidden /> Clear filters
            </Button>
          ) : (
            <Button size="sm" variant="primary" onClick={() => setOpen({ mode: "create" })}><Plus aria-hidden /> New ticket</Button>
          )
        }
      />
    );
  } else if (view === "board") {
    body = (
      <div className="ws-board">
        {boardLanes(tasks).map((lane) => (
          <div key={lane.id} className="ws-board__lane">
            <div className="ws-board__head">
              <SectionLabel>{lane.label}</SectionLabel>
              <span className="ws-board__count">{lane.tasks.length}</span>
            </div>
            {lane.tasks.length === 0 ? (
              <span className="ws-board__empty">{lane.hint}</span>
            ) : (
              <ul className="ws-board__items">
                {lane.tasks.map((t) => (
                  <li key={`${t.scope}:${t.id}`}>
                    <TicketCard task={t} onOpen={() => openTicket(t)} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    );
  } else {
    const groups = groupTickets(tasks, group);
    body = (
      <Stack gap="lg">
        {groups.map((g) => (
          <div key={g.key}>
            {g.label && (
              <Row style={{ gap: "var(--space-2)", alignItems: "baseline", marginBottom: "var(--space-2)" }}>
                <SectionLabel>{g.label}</SectionLabel>
                <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{g.tasks.length}</span>
              </Row>
            )}
            {grid(g.tasks)}
          </div>
        ))}
        {/* Real paging, not a slice with a note. The endpoint returns the
            filtered total, so this says how much is actually left. */}
        {more && (
          <Row wrap style={{ gap: "var(--space-3)", alignItems: "center" }}>
            <Button size="sm" onClick={() => setLimit((n) => n + PAGE_SIZE)} loading={loading}>
              Show more
            </Button>
            <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
              Showing {tasks.length} of {total}.
            </span>
          </Row>
        )}
      </Stack>
    );
  }

  return (
    <div>
      {notice && (
        <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", marginBottom: "var(--space-3)" }}>{notice}</p>
      )}
      {draftStrip && <div style={{ marginBottom: "var(--space-3)" }}>{draftStrip}</div>}
      <div style={{ marginBottom: "var(--space-4)" }}>{filterBar}</div>
      {/* Whether anything will pick these up on its own. A ticket filed to a
          project scope is picked up by nothing today, and the list of them
          reads exactly like a queue, so the list says so. */}
      {scopeNote && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <StatusDot status="warn">
            <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>{scopeNote}</span>
          </StatusDot>
        </div>
      )}
      {scopeAutonomy}
      {body}
      {open?.mode === "create" && (
        <CreateTicket
          scopes={scopes}
          onClose={() => setOpen(null)}
          onCreated={(msg) => { setOpen(null); onChanged(msg); }}
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
  // Filing defaults to the root inbox, not to whatever happens to sort first.
  // `workshop` is where work is filed, triaged and routed to the scope that
  // owns it (ADR 0014 §4.1); Development leads the picker because that is where
  // a developer usually is, which is a different question from where a new,
  // unsorted ticket belongs.
  const [scope, setScope] = useState<string>(
    scopes.includes("workshop") ? "workshop" : (scopes[0] ?? "workshop"));
  const [titleLine, setTitleLine] = useState("");
  const [desc, setDesc] = useState("");
  const [priority, setPriority] = useState("0");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const options = scopes.length ? scopes : ["workshop"];

  const submit = useCallback(async () => {
    const title = cleanTitle(titleLine);
    if (!title) { setErr("A title is required."); return; }
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/tasks", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scope, title, body: desc, priority: Number(priority) || 0 }),
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
      const r = await fetch("/api/tasks?status=blocked");
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
        hint="A ticket lands here when a run stops to ask you something."
      />
    );
  } else {
    body = (
      <Stack>
        {groups.map((g) => (
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
  const groupScopes = [...new Set(tasks.map((t) => t.scope ?? "workshop"))];

  // One gesture, N tickets: the decision was made once, so it is applied once.
  //
  // `mintOn` is the fix for a real defect. This used to `Promise.all` the SAME
  // action across every ticket in the group, so answering a group of three with
  // "mint rule" recorded the identical rule three times under three different
  // provenance sources — which `promote_when_stable` then counted as three
  // independent confirmations and could auto-promote off a single decision.
  // Grant did the same, minting three grants. Those two actions now apply to ONE
  // ticket and the rest are simply released, which is what "one decision" means.
  const run = useCallback(
    async (action: ReviewAction, extra: Record<string, unknown> = {}, msg?: string,
           mintOn: "all" | "first" = "all") => {
      setBusy(action);
      setErr(null);
      try {
        if (mintOn === "first") {
          await postReview(tasks[0], { action, ...extra });
          await Promise.all(tasks.slice(1).map((t) => postReview(t, { action: "approve" })));
        } else {
          await Promise.all(tasks.map((t) => postReview(t, { action, ...extra })));
        }
        onDone(msg ?? `${label} updated.`);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        // Cleared on BOTH paths. It used to be cleared only in `catch`, so a
        // successful action left the button spinning until the refresh
        // happened to unmount the card.
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
          {groupScopes.map((s) => (
            <Tag key={s} hue={catFor(s)}>{scopeLabel(s)}</Tag>
          ))}
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>{label}</span>
          {request.since && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>since {fmtTs(request.since)}</span>}
          {request.raised_by && <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>raised by {request.raised_by}</span>}
        </Row>

        <span className="ws-wrap" style={{ fontWeight: 600 }}>{request.ask || request.question}</span>
        {request.why && <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-sm)" }}>{request.why}</p>}
        {request.unblocks_if && (
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "baseline" }}>
            <KeyRound size={14} strokeWidth={1.8} aria-hidden />
            <span className="ws-wrap" style={{ fontSize: "var(--fs-sm)" }}>{request.unblocks_if}</span>
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
                <span className="ws-truncate" style={{ flex: "1 1 12rem", minWidth: 0, fontSize: "var(--fs-sm)" }}
                      title={taskTitle(t)}>{taskTitle(t) || t.id}</span>
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
            await run("grant-and-continue", fields, `Granted, and ${label} released.`, "first");
          }}
        />
      )}
      {open === "rule" && (
        <RuleDialog
          // The rule is minted against the FIRST ticket, so it lands in that
          // ticket's scope. When the group spans scopes that is a real choice,
          // and the dialog names the scope it will land in.
          ask={request.ask || request.question || ""}
          scope={tasks[0].scope ?? "workshop"}
          onClose={() => setOpen(null)}
          onSubmit={async (fields) => {
            setOpen(null);
            await run("mint-rule", fields, `Rule recorded, and ${label} released.`, "first");
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
          None yet. A rule that has come up three separate times is offered here as a hard limit.
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
      {/* Its own dialog, not the block one. Reusing GrantDialog here meant a
          surface with no ticket at all was told it would "release the ticket",
          and it was handed a stub proposal of `{uri: "", verb: "write"}`. */}
      {open && (
        <PromoteGrantDialog
          onClose={() => setOpen(null)}
          onSubmit={(fields) => promote(open, fields)}
        />
      )}
    </div>
  );
}

// ---- Page ------------------------------------------------------------------

export default function Work() {
  // The tab lives in the URL, not in local state. Five navigations already
  // wrote `?tab=` and nothing read it, so a button labelled "Back to runs"
  // landed on the ticket list. `replace` so flipping tabs does not stack
  // history entries between a page and itself.
  const [search, setSearch] = useSearchParams();
  const tab = parseWorkTab(search.get("tab"));
  const setTab = (t: Tab) => setSearch(nextWorkParams(search, { tab: t }), { replace: true });

  return (
    <div>
      <PageHeader title="Tickets" sub="Work items, the runs they produce, and anything waiting on an answer." />
      {/* The worker loop's real state + its start/stop control, above the tabs:
          whether tickets are being drained is the first thing to know on this
          page, and it is true of every tab, not just one. */}
      <div style={{ marginBottom: "var(--space-4)" }}>
        <QueueRunner />
      </div>
      <div style={{ marginBottom: "var(--space-4)" }}>
        <Segmented<Tab> ariaLabel="Ticket view" value={tab} onChange={setTab} options={TABS} />
      </div>
      {tab === "tickets" ? <TicketsTab />
        : tab === "runs" ? <JobsFeed />
        : <ReviewTab />}
    </div>
  );
}
