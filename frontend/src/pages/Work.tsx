// The Tickets surface. ONE filtered set of tickets, drawn three ways.
//
// What this page was before, and why (operator direction 2026-08-12): four tabs
// and nine controls collapsed to one list and four. That collapse was right and
// is not being undone. What the 2026-08-14 redesign adds on top of it is SHAPE:
// the same one list, as a board when you are asking "what is where", as a table
// when you are comparing rows, and as a grouped list on a phone. The controls
// stay four in substance (search, filter, sort, new) and become chips, so what
// is narrowing the list is visible rather than hidden inside three selects.
//
// The board came back deliberately. It was deleted in August as "a surface you
// drag work across, which nothing here does" — and the cards still do not drag,
// because status is changed on the ticket where the consequences are. What a
// board is FOR here is the read: six lanes answering "what is stuck, what is
// waiting, what is in flight" in one glance, which a single-column list sorted
// by date cannot do at any length.
//
// A ticket IS a task; every identifier stays `task` (ADR 0001). The route stays
// /work so existing links keep resolving.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import {
  Button, Card, Field, Input, Modal, Row, Select, Skeleton, Stack, StateBlock,
  Textarea,
} from "@workshop/ui";
import { type Task, cleanTitle, scopeLabel, ticketHref } from "../lib/tasks";
import {
  apiQuery, defaultFilters, isNarrowed, parseFilters, parseSort, parseView,
  writeParams, type TicketFilters, type TicketSort, type TicketView,
} from "../lib/ticketFilters";
import { orderScopes } from "../lib/devMode";
import { useIsWide } from "../lib/useMediaQuery";
import { TicketBoard } from "../components/tickets/TicketBoard";
import { TicketTable } from "../components/tickets/TicketTable";
import { TicketList } from "../components/tickets/TicketList";
import {
  TicketFilterBar, TicketFilterCompact, TicketFilterSheet, ViewSwitcher,
  type LabelCount,
} from "../components/tickets/TicketFilters";

// One page of tickets. The endpoint sorts and pages, so this is a real page size.
const PAGE_SIZE = 50;

// A board is read as a whole, so paging it row by row makes no sense: a lane
// that is short because of a page boundary looks like a lane that is short.
// Ask for enough to draw the whole board and say so when it is still cut off.
const BOARD_SIZE = 300;

type TasksResp = { tasks: Task[]; count: number; total: number };

export default function Work() {
  const nav = useNavigate();
  const [search, setSearch] = useSearchParams();
  const wide = useIsWide();

  // The URL IS the filter state (frontend/CLAUDE.md: view, scope and tab belong
  // in the URL), so a filtered list is a link and the back button walks real
  // states. Everything below reads from it rather than mirroring it.
  const urlView = parseView(search);
  // Below the board's width there is one body, so the stored view still decides
  // what a wide window would show while the phone shows the list either way.
  const view: TicketView = urlView;
  const sort = parseSort(search);
  const urlFilters = parseFilters(search, view);

  // The one piece of filter state that is NOT read straight from the URL.
  // Typing must not push a history entry per keystroke or refetch per letter.
  const [qDraft, setQDraft] = useState(urlFilters.q);
  useEffect(() => { setQDraft(urlFilters.q); }, [urlFilters.q]);
  // Deps are PRIMITIVES, and the params are re-read inside the updater. Both
  // matter: `urlFilters` is a new object on every render, so depending on it
  // would tear down and restart the timer whenever anything else re-rendered
  // (a fetch resolving, a poll landing), and a search typed while the list was
  // loading could keep being deferred. Reading `prev` inside also means a
  // filter committed during the 250ms is not overwritten by a stale copy.
  useEffect(() => {
    if (qDraft === urlFilters.q) return;
    const t = setTimeout(() => {
      setSearch((prev) => {
        const v = parseView(prev);
        return writeParams({ ...parseFilters(prev, v), q: qDraft }, parseSort(prev), v, prev);
      }, { replace: true });
    }, 250);
    return () => clearTimeout(t);
  }, [qDraft, urlFilters.q, setSearch]);

  // What the controls show: the URL, with the letters typed since.
  const filters = useMemo<TicketFilters>(
    () => ({ ...urlFilters, q: qDraft }), [urlFilters, qDraft]);

  const [data, setData] = useState<TasksResp | null>(null);
  const [scopes, setScopes] = useState<string[]>([]);
  const [labels, setLabels] = useState<LabelCount[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [creating, setCreating] = useState(false);
  const [sheet, setSheet] = useState(false);

  const pageSize = view === "board" ? BOARD_SIZE : PAGE_SIZE;

  const writeUrl = useCallback(
    (f: TicketFilters, s: TicketSort, v: TicketView) =>
      setSearch((prev) => writeParams(f, s, v, prev), { replace: true }),
    [setSearch]);

  const applyFilters = useCallback((next: TicketFilters) => {
    // A change to the text is the debounce's business; anything else commits
    // now, because a chip is a click and a click must land immediately.
    setQDraft(next.q);
    if (next.q === filters.q) writeUrl({ ...next, q: urlFilters.q }, sort, view);
  }, [filters.q, urlFilters.q, sort, view, writeUrl]);

  const applySort = useCallback((next: TicketSort) =>
    writeUrl(urlFilters, next, view), [urlFilters, view, writeUrl]);

  // Switching view re-reads the default status set, because each view's default
  // is the set it can actually draw. An EXPLICIT filter is carried across
  // untouched: it was a decision, and a view switch is not a reason to undo it.
  const applyView = useCallback((next: TicketView) => {
    const p = writeParams(urlFilters, sort, view, search);
    const keptStatus = p.get("status");
    const carried = keptStatus === null
      ? defaultFilters(next)
      : urlFilters;
    writeUrl({ ...carried, q: urlFilters.q }, sort, next);
  }, [urlFilters, sort, view, search, writeUrl]);

  // A new filter starts at the first page again.
  useEffect(() => { setLimit(pageSize); }, [search, pageSize]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const params = apiQuery(urlFilters, sort, limit);
      const r = await fetch(`/api/tasks?${params.toString()}`);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setData(await r.json());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // urlFilters is rebuilt each render; its VALUE is what matters here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, limit]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    fetch("/api/tasks/scopes")
      .then((r) => (r.ok ? r.json() : { scopes: [] }))
      .then((j) => setScopes(orderScopes(j.scopes ?? [])))
      .catch(() => setScopes([]));
  }, []);

  // The tag vocabulary, scoped with the list: a label is free text with no
  // registry, so the tickets themselves are the only source for "what tags
  // exist", and the menu must offer the ones this list can actually match.
  useEffect(() => {
    const p = urlFilters.scope ? `?scope=${encodeURIComponent(urlFilters.scope)}` : "";
    fetch(`/api/tasks/labels${p}`)
      .then((r) => (r.ok ? r.json() : { labels: [] }))
      .then((j) => setLabels(j.labels ?? []))
      .catch(() => setLabels([]));
  }, [urlFilters.scope]);

  const rows = data?.tasks ?? [];
  const total = data?.total ?? 0;
  const narrowed = isNarrowed(urlFilters, view);
  // The board draws its own scope-spanning rows; the table only shows the scope
  // column when the list actually spans more than one.
  const showScope = !urlFilters.scope;

  const clearAll = useCallback(
    () => writeUrl(defaultFilters(view), sort, view), [view, sort, writeUrl]);

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
        title={narrowed ? "No tickets match these filters" : "No tickets yet"}
        hint={narrowed
          ? "Widen the status or scope filter, or clear the search."
          : "File one with New ticket, or from a chat."}
        action={narrowed
          ? <Button size="sm" onClick={clearAll}>Clear filters</Button>
          : (
            <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
              <Plus aria-hidden /> New ticket
            </Button>
          )}
      />
    );
  } else if (!wide) {
    body = <TicketList tasks={rows} />;
  } else if (view === "board") {
    body = <TicketBoard tasks={rows} />;
  } else {
    body = <TicketTable tasks={rows} showScope={showScope} />;
  }

  const more = rows.length < total;

  return (
    <div className="ws-tickets">
      {/* The header band: what this list is, how many, and how to look at it.
          One band, not a PageHeader plus a separate control row: the count and
          the switcher are about the list below them, so they belong to it. */}
      <header className="ws-tickets__head">
        <div className="ws-tickets__headrow">
          <div className="ws-tickets__ident">
            <div className="ws-tickets__eyebrow">
              Work{urlFilters.scope ? ` · ${scopeLabel(urlFilters.scope)}` : ""}
            </div>
            <div className="ws-tickets__titlerow">
              <h1 className="ws-tickets__title">Tickets</h1>
              {data && <span className="ws-tickets__count">{total}</span>}
            </div>
          </div>
          {/* Below 900px there is one body whatever the switcher says, so
              showing it there would be a control with no effect. */}
          {wide && <ViewSwitcher view={view} onView={applyView} />}
        </div>

        {wide ? (
          <TicketFilterBar
            filters={filters}
            sort={sort}
            view={view}
            scopes={scopes}
            labels={labels}
            count={total}
            onFilters={applyFilters}
            onSort={applySort}
            onNew={() => setCreating(true)}
          />
        ) : (
          <TicketFilterCompact
            filters={filters}
            sort={sort}
            view={view}
            onOpen={() => setSheet(true)}
            onFilters={applyFilters}
          />
        )}
      </header>

      <div className="ws-tickets__body">{body}</div>

      {more && (
        <Row>
          <Button
            size="sm"
            loading={loading}
            onClick={() => setLimit((n) => n + pageSize)}
          >
            Load more ({rows.length} of {total})
          </Button>
        </Row>
      )}

      {/* A phone reaches New ticket here rather than in the bar: the bar is two
          controls wide there, and the primary action of the screen belongs at
          the thumb. */}
      {!wide && (
        <div className="ws-tickets__foot">
          <Button block variant="primary" onClick={() => setCreating(true)}>
            <Plus aria-hidden /> New ticket
          </Button>
        </div>
      )}

      <TicketFilterSheet
        open={sheet}
        onClose={() => setSheet(false)}
        filters={filters}
        sort={sort}
        scopes={scopes}
        labels={labels}
        count={total}
        onFilters={applyFilters}
        onSort={applySort}
        onReset={clearAll}
      />

      {creating && (
        <CreateTicket
          scopes={scopes}
          initialScope={urlFilters.scope ?? "workshop"}
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
