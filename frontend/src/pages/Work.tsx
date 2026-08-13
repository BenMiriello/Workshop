// The Tickets list. ONE surface: a filtered list of tickets, and nothing else.
//
// What this page used to be, and why it is not any more (operator direction,
// 2026-08-12): a PageHeader, a queue-runner start/stop banner, a four-tab strip
// (Open / Runs / Review / Inbox), a nine-control filter row, a drafts-and-parked
// strip, and four-band cards. Fifteen interactive things above the first ticket.
//
// The four tabs were not four kinds of thing:
//   Runs   — what a ticket DID. It belongs on the ticket, the way a CI run hangs
//            off a PR. A feed of runs detached from their tickets answers no
//            question. /runs/:id survives as the detail page.
//   Review — tickets whose run stopped. Measured against the live store: the
//            `awaiting` field it reads is empty on 179 of 179 tickets, so the
//            five-button answer flow had never once rendered a real block. The
//            answer UI moved onto the ticket page, where the question is.
//   Inbox  — the same set for a single operator, plus demo seeds from July.
//
// Every tracker surveyed (Linear, GitHub, Shortcut, Jira, Asana) has exactly two
// surfaces for issues: a filtered list and an issue page. What we called tabs,
// they call filters on the one list.
//
// The controls are four, which is what Linear's issue list has: search, status,
// scope, sort. Grouping, the board, the ready checkbox and the backlog strip are
// gone. Grouping by status duplicates the status filter; the board was four
// lanes nothing drags between (status changes come from runs, not from the
// operator); the strip was a filter wearing a banner.
//
// A ticket IS a task; every identifier stays `task` (ADR 0001). The route stays
// /work so existing links keep resolving.
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import {
  Button,
  Card,
  PageHeader,
  Row,
  Stack,
  Select,
  Input,
  Field,
  Textarea,
  Modal,
  StateBlock,
  Skeleton,
  StatusDot,
} from "@workshop/ui";
import {
  type Task,
  type TaskSort,
  TASK_SORTS,
  taskStatusKind,
  taskStatusLabel,
  title as taskTitle,
  cleanTitle,
  scopeLabel,
  ticketHref,
  readinessLine,
} from "../lib/tasks";
import {
  DEFAULT_STATUS_FILTER,
  STATUS_FILTERS,
  isStatusFilter,
  statusFilterQuery,
  type StatusFilter,
} from "../lib/ticketGroups";
import { orderScopes } from "../lib/devMode";
import { ALL_SCOPES, nextWorkParams, parseWorkScope, parseWorkStatus } from "../lib/workParams";

// One page of tickets. The endpoint sorts and pages, so this is a real page size.
const PAGE_SIZE = 50;

type TasksResp = { tasks: Task[]; count: number; total: number };

// Relative time, the unit every tracker's list column uses. An absolute
// timestamp in a list is precision nobody reads; the ticket page has the full one.
function ago(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.floor(months / 12)}y`;
}

export default function Work() {
  const nav = useNavigate();
  // `?scope=` DRIVES the filter rather than seeding it once, so a /work?scope=X
  // link pressed while already on /work changes the list and not just the URL.
  const [search, setSearch] = useSearchParams();
  const scope = parseWorkScope(search.get("scope"));
  const setScope = (v: string) =>
    setSearch(nextWorkParams(search, { scope: v }), { replace: true });

  const [data, setData] = useState<TasksResp | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // ACTIVE, not "all". The list is asked "what is in flight", and answering with
  // 179 rows including every cancelled ticket from July is not an answer.
  // The status filter lives in the URL so it can be linked to. An unrecognised
  // value falls back to the default rather than showing an empty list.
  const rawStatus = parseWorkStatus(search.get("status"));
  const status: StatusFilter = isStatusFilter(rawStatus) ? rawStatus : DEFAULT_STATUS_FILTER;
  const setStatus = (v: StatusFilter) =>
    setSearch(nextWorkParams(search, { status: v === DEFAULT_STATUS_FILTER ? null : v }), { replace: true });
  const [sort, setSort] = useState<TaskSort>("updated");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [creating, setCreating] = useState(false);

  // Typing must not fire a cross-scope scan per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => clearTimeout(t);
  }, [query]);

  // A new filter starts at the first page again.
  useEffect(() => {
    setLimit(PAGE_SIZE);
  }, [status, scope, debouncedQuery, sort]);

  const { status: serverStatus } = statusFilterQuery(status);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const params = new URLSearchParams();
      if (serverStatus) params.set("status", serverStatus);
      if (scope !== ALL_SCOPES) params.set("scope", scope);
      if (debouncedQuery) params.set("q", debouncedQuery);
      params.set("sort", sort);
      params.set("limit", String(limit));
      const r = await fetch(`/api/tasks?${params.toString()}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [serverStatus, scope, debouncedQuery, sort, limit]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    fetch("/api/tasks/scopes")
      .then((r) => (r.ok ? r.json() : { scopes: [] }))
      .then((j) => setScopes(orderScopes(j.scopes ?? [])))
      .catch(() => setScopes([]));
  }, []);

  const rows = data?.tasks ?? [];
  const total = data?.total ?? 0;
  const filtered = status !== DEFAULT_STATUS_FILTER || scope !== ALL_SCOPES || !!debouncedQuery;

  // Bare controls, no per-control label. Each `Field label inline` is its own
  // `minmax(120px, 0.4fr) 1fr` grid, so four of them could not share a line at
  // any phone width and the mobile rule stacked every one into a label row plus
  // a control row: eight rows of chrome above the first ticket. A select shows
  // its value, which is the label. New ticket moved into the page header.
  const controls = (
    <div className="ws-tickets-bar">
      <Input
        className="ws-tickets-bar__search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search tickets"
        aria-label="Search tickets"
      />
      <div className="ws-tickets-bar__filters">
        <Select
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
          aria-label="Filter by status"
        >
          {STATUS_FILTERS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </Select>
        <Select
          value={scope}
          onChange={(e) => setScope(e.target.value)}
          aria-label="Filter by scope"
        >
          <option value={ALL_SCOPES}>All scopes</option>
          {scopes.map((s) => (
            <option key={s} value={s}>{scopeLabel(s)}</option>
          ))}
        </Select>
        <Select
          value={sort}
          onChange={(e) => setSort(e.target.value as TaskSort)}
          aria-label="Sort tickets"
        >
          {TASK_SORTS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </Select>
      </div>
    </div>
  );

  let body: React.ReactNode;
  if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load tickets"
        hint={<><code>/api/tasks</code> ({err})</>}
        action={<Button size="sm" onClick={refresh}>Retry</Button>}
      />
    );
  } else if (!data && loading) {
    body = (
      <Card>
        <Stack gap="sm">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} width="100%" />)}
        </Stack>
      </Card>
    );
  } else if (rows.length === 0) {
    body = (
      <StateBlock
        title={filtered ? "No tickets match these filters" : "No tickets yet"}
        hint={filtered
          ? "Widen the status or scope filter, or clear the search."
          : "File one with New ticket, or from a chat."}
        action={filtered
          ? (
            <Button size="sm" onClick={() => {
              setStatus(DEFAULT_STATUS_FILTER);
              setScope(ALL_SCOPES);
              setQuery("");
            }}>Clear filters</Button>
          )
          : (
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
              <Plus aria-hidden /> New ticket
            </Button>
          )}
      />
    );
  } else {
    body = (
      <>
        <Card pad0>
          <ul className="ws-list">
            {rows.map((t) => {
              // What this ticket does not say, as ONE line. In a row it is the
              // dot's `title` and nothing else: a sentence per row would wreck
              // the rhythm the list is built on. The full line is on the ticket
              // page and on the card that files one.
              const notReady = readinessLine(t.readiness?.missing);
              return (
                <li key={`${t.scope}:${t.id}`}>
                  {/* A link, not a row with an onClick: it opens in a new tab on
                      a middle click, it is reachable by keyboard, and a phone
                      gives it the native tap treatment. */}
                  <Link className="ws-listrow" to={ticketHref(t.scope ?? "workshop", t.id)}>
                    <span
                      className="ws-listrow__mark"
                      title={taskStatusLabel(t.status)}
                      aria-label={taskStatusLabel(t.status)}
                    >
                      <StatusDot status={taskStatusKind(t.status)}>{null}</StatusDot>
                    </span>
                    <span className="ws-listrow__title" title={taskTitle(t)}>
                      {taskTitle(t) || "(untitled)"}
                    </span>
                    <span className="ws-listrow__meta">
                      {/* The state word, as plain text. The dot alone told you
                          which of five colours it was and nothing more, and
                          "which of blocked / to do / done is this" is exactly
                          what the list is read for. A word, not a second chip:
                          the dot beside the title already carries the colour. */}
                      <span>{taskStatusLabel(t.status)}</span>
                      {notReady && (
                        <span className="ws-listrow__mark" title={notReady} aria-label={notReady}>
                          <StatusDot status="warn">{null}</StatusDot>
                        </span>
                      )}
                      {/* Only when the list spans scopes. Filtered to one, the
                          column repeats the filter on every row. */}
                      {scope === ALL_SCOPES && (
                        <span className="ws-listrow__scope">{scopeLabel(t.scope)}</span>
                      )}
                      <span className="ws-listrow__time" title={t.updated}>{ago(t.updated)}</span>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </Card>
        {rows.length < total && (
          <Row>
            <Button
              size="sm"
              loading={loading}
              onClick={() => setLimit((n) => n + PAGE_SIZE)}
            >
              Load more ({rows.length} of {total})
            </Button>
          </Row>
        )}
      </>
    );
  }

  return (
    <div>
      {/* No `sub`. On a phone the header already wraps the action below the
          title, and a subtitle restating what a ticket list is added a third
          row of chrome above the first ticket. */}
      <PageHeader
        title="Tickets"
        actions={
          <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
            <Plus aria-hidden /> New ticket
          </Button>
        }
      />
      <Stack>
        {controls}
        {body}
      </Stack>
      {creating && (
        <CreateTicket
          scopes={scopes}
          initialScope={scope === ALL_SCOPES ? "workshop" : scope}
          onClose={() => setCreating(false)}
          onCreated={(t) => {
            setCreating(false);
            nav(ticketHref(t.scope ?? "workshop", t.id));
          }}
        />
      )}
    </div>
  );
}

// ---- New ticket -------------------------------------------------------------
// Scope, title, description. Nothing else: every other field is either empty on
// every live ticket or has a sensible default, and a create form that asks for
// nine things is the form people stop filling in.

function CreateTicket({
  scopes,
  initialScope,
  onClose,
  onCreated,
}: {
  scopes: string[];
  initialScope: string;
  onClose: () => void;
  onCreated: (t: Task) => void;
}) {
  const [scope, setScope] = useState(initialScope);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const clean = cleanTitle(title);

  const submit = async () => {
    if (!clean) {
      setErr("A ticket needs a title.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch(`/api/tasks`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ scope, title: clean, body: body.trim() }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      onCreated(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="New ticket"
      actions={
        <Row end>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={submit}>Create ticket</Button>
        </Row>
      }
    >
      <Stack>
        <Field label="Scope" htmlFor="new-ticket-scope">
          <Select
            id="new-ticket-scope"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
            data-autofocus
          >
            {(scopes.length ? scopes : ["workshop"]).map((s) => (
              <option key={s} value={s}>{scopeLabel(s)}</option>
            ))}
          </Select>
        </Field>
        <Field label="Title" htmlFor="new-ticket-title" error={err ?? undefined}>
          <Input
            id="new-ticket-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What needs doing"
          />
        </Field>
        <Field label="Description" htmlFor="new-ticket-desc" optional>
          <Textarea
            id="new-ticket-desc"
            rows={6}
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="What done looks like, where to start, what to leave alone."
          />
        </Field>
      </Stack>
    </Modal>
  );
}
