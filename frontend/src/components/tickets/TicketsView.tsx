// The Tickets surface. ONE filtered set of tickets, drawn three ways.
//
// IT IS A COMPONENT, NOT A PAGE, because there are two places a filtered ticket
// list is asked for and only one of them was getting this one. `/tickets` is a
// four-line wrapper over it (pages/Tickets.tsx); an app's Tickets tab mounts it
// with `scope` pinned to that app. The tab used to render its own list -- no
// search, no filter chips, no sort, no pagination, no view switcher -- over the
// same rows, and that bespoke list is deleted in the commit that added this
// prop. Reported by the operator: "the app Tickets tab is a worse copy of
// /tickets".
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
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { Button, Card, Field, Input, Modal, Notice, PageActionBar, Row, Select, Skeleton, Stack, StateBlock, Textarea, shortHttpError } from "@workshop/ui";
import { type Task, cleanTitle, scopeLabel, ticketHref } from "../../lib/tasks";
import type { components } from "../../api/schema.gen";
import { rowTotal } from "../../lib/pageTotal";
import { queryKeys } from "../../lib/queryKeys";
import {
  PAGE_SIZE, apiQuery, clampPage, defaultFilters, isNarrowed, pageCount,
  pageItems, parseFilters, parsePage, parseSort, parseView, writeParams,
  type TicketFilters, type TicketSort, type TicketView,
} from "../../lib/ticketFilters";
import { orderScopes } from "../../lib/devMode";
import { useIsWide } from "../../lib/useMediaQuery";
import { TicketBoard } from "./TicketBoard";
import { TicketTable } from "./TicketTable";
import { TicketList } from "./TicketList";
import {
  TicketFilterBar, TicketFilterCompact, TicketFilterSheet, ViewSwitcher,
  type LabelCount,
} from "./TicketFilters";

// THE SERVER'S OWN STATEMENT OF THIS SHAPE, not a second copy of it.
//
// This was hand-written as `{tasks, count, total}`. `GET /api/tasks` stopped
// answering in that shape on 2026-08-17 (7e4aacbf), when it moved to
// `Page[Task]` -- `tasks` became `items` and `total` moved inside `page`, a
// rename that route's own docstring spells out. Nothing failed: the hand-written
// type still described the old shape, so `data.tasks` typechecked and was
// `undefined` at runtime, and this page rendered "No tickets yet" over 188 open
// tickets for two weeks. The board and ProjectDetail read the same dead fields.
//
// `schema.gen.ts` is generated from the routes and gated by
// scripts/check_generated_client.py, so naming the generated type is what makes
// the next rename a red typecheck here instead of a silently empty page.
type TasksResp = components["schemas"]["Page_Task_"];

export type TicketsViewProps = {
  /** Pin the list to one scope. The scope stops being a filter: it is not
   *  offered in the filter menu, it takes no chip, the table drops its scope
   *  column, and a new ticket is filed there. Absent, this is the global list
   *  and the scope is one filter among the others.
   *
   *  A LOCK, NOT A DEFAULT. It is not written to the URL either, because on a
   *  surface that IS the scope (an app's own tab) the URL already says which
   *  one, and a `?scope=` there would be a second copy that a Clear could
   *  disagree with. */
  scope?: string | null;
  /** Draw the surface's own header band (eyebrow, title, count, view
   *  switcher). Off where a page header is already above it, which is every
   *  embedded use: two headers stacked is the "290px of duplicated chrome"
   *  failure frontend/CLAUDE.md records. The filter bar and the pager still
   *  state the count, so nothing is lost with it. */
  header?: boolean;
};

export default function TicketsView({ scope: lockedScope = null, header = true }: TicketsViewProps = {}) {
  const nav = useNavigate();
  const [search, setSearch] = useSearchParams();
  const wide = useIsWide();
  const qc = useQueryClient();

  // The URL IS the filter state (frontend/CLAUDE.md: view, scope and tab belong
  // in the URL), so a filtered list is a link and the back button walks real
  // states. Everything below reads from it rather than mirroring it.
  const urlView = parseView(search);
  // Below the board's width there is one body, so the stored view still decides
  // what a wide window would show while the phone shows the list either way.
  const view: TicketView = urlView;
  const sort = parseSort(search);
  // The lock wins over the URL, and the URL never carries it back out (see
  // `writeUrl`). Everything downstream -- the API query, the chips, the create
  // dialog -- reads this one value, so there is no second place the scope
  // could be decided.
  const parsed = parseFilters(search, view);
  const urlFilters = useMemo<TicketFilters>(
    () => (lockedScope ? { ...parsed, scope: lockedScope } : parsed),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `parsed` is a new
    // object every render; its VALUES are the real dependency.
    [lockedScope, search.toString(), view],
  );
  const page = parsePage(search);

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

  const [creating, setCreating] = useState(false);
  const [sheet, setSheet] = useState(false);

  // Any change to WHAT is being asked for returns to page 1: staying on page 7
  // of a set that just shrank to two pages is how a filter reads as "no
  // results". `writeUrl` therefore takes the page explicitly and defaults to 1.
  const writeUrl = useCallback(
    (f: TicketFilters, s: TicketSort, v: TicketView, nextPage = 1) =>
      setSearch(
        (prev) => writeParams(
          lockedScope ? { ...f, scope: null } : f, s, v, prev, nextPage),
        { replace: true }),
    [setSearch, lockedScope]);

  const goToPage = useCallback((nextPage: number) =>
    writeUrl(urlFilters, sort, view, nextPage),
    [urlFilters, sort, view, writeUrl]);

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

  const dataQuery = useQuery({
    queryKey: queryKeys.tasks.filteredList(
      view === "board" ? "" : apiQuery(urlFilters, sort, PAGE_SIZE, (page - 1) * PAGE_SIZE).toString()
    ),
    queryFn: async (): Promise<TasksResp> => {
      const params = apiQuery(urlFilters, sort, PAGE_SIZE, (page - 1) * PAGE_SIZE);
      const r = await fetch(`/api/tasks?${params.toString()}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return r.json();
    },
    enabled: view !== "board",
    staleTime: 0,
  });

  const scopesQuery = useQuery({
    queryKey: queryKeys.tasks.scopes(),
    queryFn: async (): Promise<string[]> => {
      const r = await fetch("/api/tasks/scopes");
      const j: components["schemas"]["ScopesOut"] | null = r.ok ? await r.json() : null;
      return orderScopes(j?.scopes ?? []);
    },
    staleTime: 60_000,
  });

  const labelScope = urlFilters.scope ?? "";
  const labelsQuery = useQuery({
    queryKey: queryKeys.tasks.labels(labelScope),
    queryFn: async (): Promise<LabelCount[]> => {
      const p = labelScope ? `?scope=${encodeURIComponent(labelScope)}` : "";
      const r = await fetch(`/api/tasks/labels${p}`);
      const j: components["schemas"]["LabelsOut"] | null = r.ok ? await r.json() : null;
      return (j?.labels ?? []) as LabelCount[];
    },
    staleTime: 30_000,
  });

  const assigneesQuery = useQuery({
    queryKey: queryKeys.tasks.assignees(labelScope),
    queryFn: async (): Promise<LabelCount[]> => {
      const p = labelScope ? `?scope=${encodeURIComponent(labelScope)}` : "";
      const r = await fetch(`/api/tasks/assignees${p}`);
      const j: components["schemas"]["AssigneesOut"] | null = r.ok ? await r.json() : null;
      return (j?.assignees ?? []) as LabelCount[];
    },
    staleTime: 30_000,
  });

  const data = dataQuery.data ?? null;
  const scopes = scopesQuery.data ?? [];
  const labels = labelsQuery.data ?? [];
  const assignees = assigneesQuery.data ?? [];
  const err = !dataQuery.isFetching && dataQuery.isError
    ? (dataQuery.error instanceof Error ? dataQuery.error.message : String(dataQuery.error))
    : null;
  const loading = dataQuery.isPending;

  // A page the data cannot support (filtered down while sitting on page 9) is
  // corrected rather than shown empty.
  useEffect(() => {
    if (!data) return;
    const safe = clampPage(page, rowTotal(data.page?.total, data.items?.length ?? 0));
    if (safe !== page) goToPage(safe);
  }, [data, page, goToPage]);

  // The cast narrows `status` only. The generated schema types it `string`
  // because the route's Pydantic model does; `lib/tasks.Task` states the closed
  // set the UI actually switches on. Same strings either way -- the field NAMES
  // above are what the generated type is being relied on for.
  const rows = (data?.items ?? []) as Task[];
  // `?? 0` here would print 0 over a full list the moment this route stopped
  // counting, which is what GET /api/jobs already does by design. See
  // lib/pageTotal.ts.
  const total = rowTotal(data?.page?.total, rows.length);
  const pages = pageCount(total);
  // What the search box may claim. The board pages PER LANE and this query is
  // disabled there, so the page-level total is not merely 0, it is unknown --
  // and it read "Search 0 tickets" above a board holding 176.
  const searchCount = view === "board" ? null : total;
  // A PINNED SCOPE IS NOT A FILTER THE READER APPLIED, so it must not make the
  // empty state read "No tickets match these filters" with a Clear button that
  // cannot clear anything. An app with an empty queue says it is empty.
  const narrowed = isNarrowed(
    lockedScope ? { ...urlFilters, scope: null } : urlFilters, view);
  // The board draws its own scope-spanning rows; the table only shows the scope
  // column when the list actually spans more than one.
  const showScope = !urlFilters.scope;
  // The scope label for the header's eyebrow and for the create dialog's one
  // option. `scopeLabel` is the same formatter the global filter chip uses.
  const scopeName = urlFilters.scope ? scopeLabel(urlFilters.scope) : "Workshop";

  const clearAll = useCallback(
    () => writeUrl(defaultFilters(view), sort, view), [view, sort, writeUrl]);

  let body: React.ReactNode;
  if (wide && view === "board") {
    // The board owns its own fetching: one paged request PER LANE. A single
    // query cannot fill a board, because the server pages the whole set and a
    // large queue comes back as one status' worth of rows.
    body = <TicketBoard filters={urlFilters} sort={sort} />;
  } else if (err) {
    body = (
      <StateBlock
        variant="error"
        title="Couldn't load tickets"
        hint={<><code>/api/tasks</code> ({err})</>}
        action={<Button size="sm" onClick={() => void dataQuery.refetch()}>Retry</Button>}
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
  } else {
    body = <TicketTable tasks={rows} showScope={showScope} />;
  }

  return (
    // `--footed` cancels the scroller's bottom padding so the pinned New ticket
    // bar can rest flush against the bottom edge. See .ws-tickets__foot.
    <div className={`ws-tickets${!wide ? " ws-tickets--footed" : ""}`}>
      {/* The header band: what this list is, how many, and how to look at it.
          One band, not a PageHeader plus a separate control row: the count and
          the switcher are about the list below them, so they belong to it. */}
      <header className="ws-tickets__head">
        {header && (
          <div className="ws-tickets__headrow">
            <div className="ws-tickets__ident">
              <div className="ws-tickets__eyebrow">{scopeName}</div>
              <div className="ws-tickets__titlerow">
                <h1 className="ws-tickets__title">Tickets</h1>
                {data && <span className="ws-tickets__count">{total}</span>}
              </div>
            </div>
            {/* Below 900px there is one body whatever the switcher says, so
                showing it there would be a control with no effect. */}
            {wide && <ViewSwitcher view={view} onView={applyView} />}
          </div>
        )}
        {/* Embedded, the switcher has nowhere else to go: it belongs to the
            list, and the page header above this is the app's, not the list's. */}
        {!header && wide && (
          <div className="ws-tickets__headrow">
            <ViewSwitcher view={view} onView={applyView} />
          </div>
        )}

        {wide ? (
          <TicketFilterBar
            filters={filters}
            sort={sort}
            view={view}
            scopes={scopes}
            labels={labels}
            assignees={assignees}
            count={searchCount}
            lockedScope={!!lockedScope}
            onFilters={applyFilters}
            onSort={applySort}
            onNew={() => setCreating(true)}
          />
        ) : (
          <TicketFilterCompact
            filters={filters}
            sort={sort}
            view={view}
            lockedScope={!!lockedScope}
            onOpen={() => setSheet(true)}
            onFilters={applyFilters}
          />
        )}
      </header>

      <div className="ws-tickets__body">{body}</div>

      {/* Numbered pages. The count reads first because "how much is there" is
          the question; the numbers are how you move. Which numbers appear is
          `pageItems`, a tested rule, so a 2-page pager and a 400-page pager are
          the same control rather than two cases. */}
      {!(wide && view === "board") && rows.length > 0 && (
        <div className="ws-tickets__end">
          <span>{total === 1 ? "1 ticket" : `${total} tickets`}</span>
          {pages > 1 && (
            <nav className="ws-pager" aria-label="Pages">
              <button
                type="button"
                className="ws-pager__step"
                disabled={page <= 1}
                aria-label="Previous page"
                onClick={() => goToPage(page - 1)}
              >
                <ChevronLeft aria-hidden />
              </button>
              {pageItems(page, pages).map((it, i) =>
                it === "gap" ? (
                  <span key={`gap${i}`} className="ws-pager__gap" aria-hidden>...</span>
                ) : (
                  <button
                    key={it}
                    type="button"
                    className="ws-pager__page"
                    aria-current={it === page ? "page" : undefined}
                    aria-label={`Page ${it}`}
                    onClick={() => goToPage(it)}
                  >
                    {it}
                  </button>
                ))}
              <button
                type="button"
                className="ws-pager__step"
                disabled={page >= pages}
                aria-label="Next page"
                onClick={() => goToPage(page + 1)}
              >
                <ChevronRight aria-hidden />
              </button>
            </nav>
          )}
        </div>
      )}

      {/* A phone reaches New ticket here rather than in the bar: the bar is two
          controls wide there, and the primary action of the screen belongs at
          the thumb. `PageActionBar` renders into the shell's overlay host, so
          it sits at the bottom of the content region by construction rather
          than by compensating for the scroller's padding. */}
      {!wide && (
        <PageActionBar>
          <Button block variant="primary" onClick={() => setCreating(true)}>
            <Plus aria-hidden /> New ticket
          </Button>
        </PageActionBar>
      )}

      <TicketFilterSheet
        open={sheet}
        onClose={() => setSheet(false)}
        filters={filters}
        sort={sort}
        scopes={scopes}
        labels={labels}
        assignees={assignees}
        count={searchCount}
        lockedScope={!!lockedScope}
        onFilters={applyFilters}
        onSort={applySort}
        onReset={clearAll}
      />

      {creating && (
        <CreateTicket
          scopes={lockedScope ? [lockedScope] : scopes}
          initialScope={urlFilters.scope ?? "workshop"}
          onClose={() => setCreating(false)}
          onCreated={(t) => {
            setCreating(false);
            void qc.invalidateQueries({ queryKey: queryKeys.tasks.all() });
            nav(ticketHref(t.scope ?? "workshop", t.id));
          }}
        />
      )}
    </div>
  );
}

// ---- New ticket -------------------------------------------------------------
// Scope, title, description, and the check that says it is done. Nothing else:
// every other field is either empty on every live ticket or has a sensible
// default, and a create form that asks for nine things is the form people stop
// filling in.
//
// ACCEPTANCE IS THE FOURTH FIELD BECAUSE THE SERVER REFUSES A TICKET WITHOUT IT.
// Measured 2026-09-01: 208 open tickets, none of them carrying one, so every
// ticket's finish line was a sentence in a 337-word body and a worker handed one
// picked its own. It is a command a reviewer can run, not a restatement of the
// objective; `services/ticket_format` owns the rule and the refusal wording, and
// the refusal is shown here as a Notice rather than invented a second time.

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
  const [check, setCheck] = useState("");
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
        body: JSON.stringify({
          scope, title: clean, body: body.trim(),
          acceptance: { kind: "command", spec: check.trim() },
        }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
        {/* A rejected submit is a LINE, not a region: the form is right there.
            It used to ride the Title field's `error` slot, which put a refusal
            about the acceptance check under the title. */}
        {err && <Notice tone="error">{err}</Notice>}
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
        <Field label="Title" htmlFor="new-ticket-title">
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
        {/* Not optional, and the label says what it takes rather than
            explaining the rule. The server states the rule when it refuses. */}
        <Field label="Check that it is done" htmlFor="new-ticket-check">
          <Input
            id="new-ticket-check"
            value={check}
            onChange={(e) => setCheck(e.target.value)}
            placeholder="npm run typecheck"
          />
        </Field>
      </Stack>
    </Modal>
  );
}
