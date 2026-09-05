// The Tickets surface. ONE filtered set of tickets, and one row shape for it.
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
// WHAT WAS WRONG WITH IT, reported 2026-09-01: "when I go to the tickets page,
// I'm still seeing some old tickets fucking page." It was not stale code and it
// was not a stale bundle. It was that the default shape above 900px was a table
// -- eight columns of which four (tags, priority, effort, assignee) are unset on
// most of the live store -- which is precisely the grid this system threw out on
// 2026-08-12, when `.ws-listrow` became "the one row shape for every list in the
// app: tickets and runs today". The runs feed kept that row; tickets got a table
// back two days later. So the page looked like the tracker it had already
// stopped being, and the operator was right that he was looking at an old one.
//
// THREE THINGS CHANGED, and nothing else:
//
//   1. The table is deleted. There is one list, the `.ws-listrow` one, at every
//      width, and the row states its own status in words (frontend/CLAUDE.md:
//      "the word goes in the meta line as plain text").
//   2. Status became a bar of five presets with the server's own counts, above
//      the list, at every width -- rather than eight checkboxes inside a menu.
//      A queue of ~200 is navigated by status first, and a control you have to
//      open cannot say how much is behind it.
//   3. The phone list's status GROUPING is gone. Its headings counted the
//      current page of 50, so the same heading returned page after page with a
//      different number, none of which was the count of anything.
//
// The 2026-08-12 collapse (four tabs and nine controls down to one list and
// four controls, on operator direction) is intact and is not being undone.
//
// The board stays. It was deleted in August as "a surface you drag work across,
// which nothing here does" -- and the cards still do not drag, because status is
// changed on the ticket where the consequences are. What a board is FOR here is
// the read: six lanes answering "what is stuck, what is waiting, what is in
// flight" in one glance, which a single sorted column cannot do at any length.
//
// A ticket IS a task; every identifier stays `task` (ADR 0001).
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { Button, Card, Field, Input, Modal, Notice, PageActionBar, Row, Select, Skeleton, Stack, StateBlock, Textarea, shortHttpError } from "@workshop/ui";
import { type Task, cleanTitle, scopeLabel, spansScopes, ticketHref } from "../../lib/tasks";
import type { components } from "../../api/schema.gen";
import { rowTotal } from "../../lib/pageTotal";
import { queryKeys } from "../../lib/queryKeys";
import {
  DEFAULT_SORT, PAGE_SIZE, SOURCE_PRESETS, STATUS_PRESETS, apiQuery, clampPage,
  defaultFilters, isNarrowed,
  pageCount, pageItems, parseFilters, parsePage, parseSort, parseView,
  sourcePresetOf,
  writeParams,
  type TicketFilters, type TicketSort, type TicketView,
} from "../../lib/ticketFilters";
import { orderScopes } from "../../lib/devMode";
import { useIsWide } from "../../lib/useMediaQuery";
import { TicketBoard } from "./TicketBoard";
import { TicketList } from "./TicketList";
import {
  TicketFilterBar, TicketFilterCompact, TicketFilterSheet, TicketSourceBar,
  TicketStatusBar,
  ViewSwitcher,
  type LabelCount, type StatusCounts,
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
      return r.json() as Promise<TasksResp>;
    },
    enabled: view !== "board",
    staleTime: 0,
  });

  const scopesQuery = useQuery({
    queryKey: queryKeys.tasks.scopes(),
    queryFn: async (): Promise<string[]> => {
      const r = await fetch("/api/tasks/scopes");
      const j = r.ok ? ((await r.json()) as components["schemas"]["ScopesOut"]) : null;
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
      const j = r.ok ? ((await r.json()) as components["schemas"]["LabelsOut"]) : null;
      return (j?.labels ?? []) as LabelCount[];
    },
    staleTime: 30_000,
  });

  // THE COUNT BEHIND EACH STATUS PRESET, one request each, `limit=1`.
  //
  // The whole point of the status bar is a number you can read without running
  // the filter, and this route has no counts endpoint: the only thing that
  // knows how many tickets are blocked is the list route answering a query
  // whose status is `blocked`. So each preset asks for one row and reads
  // `page.total` off the envelope, which is the same trick the board already
  // uses per lane, at a fifth of the payload.
  //
  // EVERY OTHER FILTER IS CARRIED. A count has to be the count of what the
  // reader would get by pressing the pill, so on an app's Tickets tab, or under
  // a search, "Blocked 2" means two blocked tickets in THAT set. A count over
  // the whole store beside a narrowed list is a number that contradicts the
  // rows under it.
  //
  // A failed count renders as no count rather than as 0: see `StatusCounts`.
  const countQueries = useQueries({
    queries: STATUS_PRESETS.map((preset) => {
      const params = apiQuery(
        { ...urlFilters, status: preset.statuses }, DEFAULT_SORT, 1);
      return {
        queryKey: queryKeys.tasks.filteredList(`count:${params.toString()}`),
        queryFn: async (): Promise<number> => {
          const r = await fetch(`/api/tasks?${params.toString()}`);
          if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, ""));
          const j: TasksResp = await r.json();
          return rowTotal(j.page?.total, j.items?.length ?? 0);
        },
        staleTime: 30_000,
      };
    }),
  });

  const statusCounts = useMemo<StatusCounts>(() => {
    const out: StatusCounts = {};
    STATUS_PRESETS.forEach((preset, i) => {
      const q = countQueries[i];
      out[String(preset.value)] = q?.isSuccess ? q.data : undefined;
    });
    return out;
    // The query results are new objects every render; their DATA is the real
    // dependency, and it is a short array of numbers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [countQueries.map((q) => (q.isSuccess ? q.data : "")).join(",")]);

  // THE COUNT BEHIND EACH SOURCE PRESET. Same trick as the status counts above,
  // one request each, `limit=1`, reading `page.total` off the envelope.
  //
  // EVERY OTHER FILTER IS CARRIED, INCLUDING STATUS, for the same reason: the
  // number on a pill has to be the number of rows pressing it would produce. So
  // under the default status set "From agents 128" means 128 ACTIVE tickets an
  // agent filed, which is the honest count beside a list showing active work.
  //
  // NOTHING HERE IS CACHED OR DERIVED FROM A CONSTANT. The statuses in this
  // store are being reconciled continuously (a ticket built weeks ago can still
  // read `todo`), so every number on this page is a live read of the same route
  // the rows come from and moves when the store does.
  const sourceCountQueries = useQueries({
    queries: SOURCE_PRESETS.map((preset) => {
      const params = apiQuery(
        { ...urlFilters, source: preset.sources }, DEFAULT_SORT, 1);
      return {
        queryKey: queryKeys.tasks.filteredList(`count:${params.toString()}`),
        queryFn: async (): Promise<number> => {
          const r = await fetch(`/api/tasks?${params.toString()}`);
          if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, ""));
          const j: TasksResp = await r.json();
          return rowTotal(j.page?.total, j.items?.length ?? 0);
        },
        staleTime: 30_000,
      };
    }),
  });

  const sourceCounts = useMemo<StatusCounts>(() => {
    const out: StatusCounts = {};
    SOURCE_PRESETS.forEach((preset, i) => {
      const q = sourceCountQueries[i];
      out[preset.value] = q?.isSuccess ? q.data : undefined;
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see statusCounts.
  }, [sourceCountQueries.map((q) => (q.isSuccess ? q.data : "")).join(",")]);

  const assigneesQuery = useQuery({
    queryKey: queryKeys.tasks.assignees(labelScope),
    queryFn: async (): Promise<LabelCount[]> => {
      const p = labelScope ? `?scope=${encodeURIComponent(labelScope)}` : "";
      const r = await fetch(`/api/tasks/assignees${p}`);
      const j = r.ok ? ((await r.json()) as components["schemas"]["AssigneesOut"]) : null;
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
  // The count with the SOURCE filter lifted and everything else left alone. It
  // is what separates "there is nothing here" from "there is nothing here from
  // you", which are two different sentences with two different actions and used
  // to be one.
  const anySourceCount = sourceCounts["anywhere"];
  // The list is empty because the page opened on the operator's own queue and
  // his queue is empty here, while other rows exist behind the pill next to it.
  // That case must NOT read "No tickets match these filters" with a Clear
  // button: the reader applied no filter, and clearing to the default would
  // return him to the state he is already in. It gets its own sentence and a
  // one-tap widen. See `isNarrowed`.
  const hiddenBySource = !!data && rows.length === 0
    && urlFilters.source.length > 0 && (anySourceCount ?? 0) > 0;
  // The preset the source bar is showing as pressed, for that sentence. Null
  // when the URL carries a hand-picked set, which the chip handles instead.
  const sourcePreset = SOURCE_PRESETS.find(
    (p) => p.value === sourcePresetOf(urlFilters.source)) ?? null;
  // Nothing here, and nothing behind any other source either: the store is
  // empty rather than the filters being tight. Read by the empty state and by
  // both pill bars.
  const emptyStore = !!data && rows.length === 0 && !narrowed
    && anySourceCount === 0;
  // The scope goes on a row only when the rows in hand actually differ in it.
  // See `spansScopes`: on this installation the unfiltered list is one scope for
  // page after page, so the old rule (print it unless a scope filter is set)
  // put one identical word on fifty rows.
  const showScope = spansScopes(rows);
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
  } else if (hiddenBySource) {
    // The source cut emptied the list and there is work behind the other pill.
    // Say which cut, say how much is behind it, and widen in one tap.
    body = (
      <StateBlock
        title={sourcePreset
          ? `No tickets ${sourcePreset.label.toLowerCase()}`
          : "No tickets from that source"}
        hint={anySourceCount === 1
          ? "1 ticket matches everything else."
          : `${anySourceCount} tickets match everything else.`}
        action={(
          <Button size="sm" onClick={() => applyFilters({ ...filters, source: [] })}>
            Show every source
          </Button>
        )}
      />
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
  } else {
    // ONE LIST, at every width. There used to be a table above 900px and this
    // list below it, which is the same rows drawn twice with two sets of rules
    // to keep in agreement. `.ws-listrow` is already a phone row that becomes a
    // wide row at 720px with no second markup path, so the breakpoint belongs
    // to the row and not to this branch.
    body = <TicketList tasks={rows} showScope={showScope} />;
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

        {/* WHERE THE TICKETS CAME FROM, first, above the status bar. It is the
            coarsest cut this list has (75 of 215 rows came from the operator,
            140 from an agent) and it is the one the page now opens on, so it
            reads before the control that divides whatever it selected. Gated on
            the same `emptyStore` as the status bar so the two cannot disagree
            about whether there is anything here. See TicketSourceBar. */}
        {!emptyStore && (
          <TicketSourceBar
            filters={filters}
            counts={sourceCounts}
            onFilters={applyFilters}
          />
        )}

        {/* The status control, above the search and the chips, at every width.
            It is the first question this list is asked and the only one whose
            answer is a number, so it reads before the controls that narrow it
            further. See TicketStatusBar.

            NOT DRAWN OVER AN EMPTY STORE. On a scope with no tickets at all
            (a newly linked app's own tab) all five presets are 0 and all five
            lead to the same empty list, which is five controls that can do
            nothing. Gated on the same condition as the "No tickets yet" state
            below, so the two cannot disagree, and not on the counts, so it does
            not appear and then vanish while they land. */}
        {!emptyStore && (
          <TicketStatusBar
            filters={filters}
            counts={statusCounts}
            onFilters={applyFilters}
          />
        )}

        {wide ? (
          <TicketFilterBar
            filters={filters}
            sort={sort}
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
          onCreated={(created) => {
            setCreating(false);
            void qc.invalidateQueries({ queryKey: queryKeys.tasks.all() });
            // `task` is null only when dedup declined to create one (an exact
            // match with nothing to return). There is nothing to open then, so
            // the list refresh above is the whole answer.
            const t = created.task;
            if (t) nav(ticketHref(t.scope ?? "workshop", t.id));
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
  onCreated: (created: components["schemas"]["TaskCreateOut"]) => void;
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
      // POST /api/tasks answers TaskCreateOut -- the task INSIDE a dedup
      // envelope ({task, decision, matched_id, cosine, message}), never a bare
      // Task. This handed the envelope straight to onCreated, which read
      // `t.id` off it and got undefined, so filing a ticket from this page
      // navigated to /tickets/workshop/undefined.
      onCreated((await r.json()) as components["schemas"]["TaskCreateOut"]);
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
