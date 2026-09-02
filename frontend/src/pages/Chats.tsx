import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ChevronLeft, Plus } from "lucide-react";
import {
  Button,
  ConfirmDialog,
  Field,
  Input,
  Notice,
  PageHeader,
  SectionLabel,
  Segmented,
  Skeleton,
  StateBlock,
} from "@workshop/ui";
import { chatApi, threadAgent, type ThreadSummary } from "../lib/chat";
import ChatRow from "../components/chat/ChatRow";
import { queryKeys } from "../lib/queryKeys";
import { newChatHref } from "../lib/chatSelection";
import { scopeLabel } from "../components/chat/chatScope";
import { groupByDate, groupByField, spansScopes, visibleThreads, type ArmedFilter } from "./chatList";

// Cross-context chat discovery. Chat and Project are loosely coupled: a chat is
// its own object with an optional context binding (general / project / app /
// agent). This page lists every thread regardless of where it is bound, so a
// conversation is never lost just because you navigated away from its context.

// How a scope key reads, and what kind of place it is, come from chat/chatScope
// — the same functions the chat's own scope picker uses. They were duplicated
// here, which meant the list and the picker could name the same scope
// differently and neither would be wrong.

// ONE FLAT LIST, NEWEST FIRST, AND NO ORDERING CONTROL.
// `threads-branches-and-pages-2026-08-29.md` §6: "Chats is a flat list of
// threads, newest first. It is the quick-jump surface. No branch headings, no
// grouping. (Reverted once already; it stays reverted.)"
//
// What was here: a three-value sort (Recent / Oldest / Most messages) and the
// date headings Recent produced (Today, Yesterday, Earlier this week...). The
// headings are the grouping §6 names, and the control existed to switch them on
// and off, so both are deleted together. The order and the filter now live in
// `./chatList`, where they can be executed by a test.
//
// The one control left is the armed filter, which is not an ordering: §1 makes
// `armed` the field that decides whether a thread advances without a person,
// and §6 retires the Agents roster in favour of "an armed filter on Chats".

// One page of rows. The list renders a page at a time and loads the next when
// the foot scrolls into view (see the observer below), so a long history never
// mounts unbounded and the reader never meets a wall of rows.
const PAGE = 20;

// THE LIVE LIST'S CACHE KEY IS NOT SPELLED HERE. It used to be, as
// `["chat", "threads"]`, while the dashboard's thread cards and the chat
// toolbar's recent-chats menu read the same URL under `["chat", "threads",
// null]`. Two spellings are two cache entries: this page polled the endpoint on
// its own, and archiving a chat here wrote through to this copy while the
// dashboard went on listing the chat that had just been archived.
//
// Keys come from `lib/queryKeys` now, so the three readers cannot disagree
// about how the fact is named. See the comment at the top of that file.

// When a chat was archived, at the resolution the archive is read at: you are
// looking for the one you put here, so the day is the useful unit and today is
// the common case.
function archivedLabel(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const sameDay = new Date().toDateString() === d.toDateString();
  return sameDay
    ? `Archived ${d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
    : `Archived ${d.toLocaleDateString([], { month: "short", day: "numeric" })}`;
}

/** What the archive is being asked to do to a chat, while the dialog is open. */
type Pending = { id: string; title: string; action: "restore" | "delete" };

/** Search over a loaded list. Shared by both pages so they narrow identically. */
function matches(t: ThreadSummary, needle: string): boolean {
  if (!needle) return true;
  const agent = threadAgent(t);
  return (
    (t.title || "").toLowerCase().includes(needle) ||
    (t.last_message || "").toLowerCase().includes(needle) ||
    scopeLabel(t.context_key).toLowerCase().includes(needle) ||
    (agent !== null && agent.toLowerCase().includes(needle))
  );
}

/** An app, when this list is one app's chats rather than every chat. */
export type ChatsScope = { id: string; slug: string; name: string };

/**
 * THE CHAT LIST. One component, two mount points, exactly like TicketsView.
 *
 * `/chats` is a thin wrapper over it, and an app's Chats tab mounts it with
 * `app` set. The tab used to draw its OWN list: no armed filter, no archive, no
 * delete, no infinite scroll, a duplicate "CHATS" heading under a page already
 * titled Chats, and a scope tag reading the app's own name on every single row.
 * A worse copy of this list, over the same rows.
 *
 * Reported by the operator on 2026-08-29, 2026-08-31 and twice on 2026-09-01,
 * the last time as "I have completely lost count of how many times I've told
 * you to do this". Three previous rounds changed how the copy RENDERED. The
 * copy was the bug.
 */
export function ChatsView({ app = null, header = true, eyebrow, sub }: {
  app?: ChatsScope | null;
  /** False where a page header is already on screen above this (an app tab). */
  header?: boolean;
  /** Drawn INSIDE the header this view owns, by a caller that would otherwise
   *  have drawn a header of its own above it.
   *
   *  The app tab did exactly that, and the cost was visible: the filter and
   *  New chat could not reach the title's line from below it, so they took a
   *  row to themselves and the page had a band of empty space between its
   *  subtitle and its list, where /chats has none. Compared side by side at
   *  1440px on 2026-09-01, that band and the missing tab strip were the only
   *  things left that made the two pages look like different pages. */
  eyebrow?: string;
  sub?: ReactNode;
} = {}) {
  const nav = useNavigate();
  const qc = useQueryClient();
  // All threads, or only the ones that advance on their own. A view preference,
  // so it commits on change and is not persisted.
  const [armed, setArmed] = useState<ArmedFilter>("all");
  // A delete waiting on its confirm dialog, and a one-off action failure that
  // leaves the list on screen (a Notice, not a whole-page error block).
  const [pending, setPending] = useState<{ id: string; title: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<{ text: string; endpoint: string } | null>(null);
  // How many rows are mounted. Grows a page at a time as the foot scrolls into
  // view; reset when the filter changes so a re-narrowed list starts at the top.
  const [shown, setShown] = useState(PAGE);

  // The list live-updates by polling the shared TanStack client the rest of the
  // app uses. Short interval because liveness (the running dot, a new turn, a new
  // last message) is the point; the payload is small and cached, so a tick that
  // changes nothing re-renders nothing.
  // WHICH ROWS. An app's chats are the DATABASE's answer (chat_thread.app_id),
  // and it is broader than any scope filter: an app's chats can sit under
  // several context keys. So the app mount reads the app's own collection
  // rather than filtering this one, and both render the same row.
  const endpoint = app ? `/api/apps/${app.id}/chats` : "/api/chat/threads";
  const q = useQuery({
    queryKey: app ? queryKeys.apps.chats(app.id) : queryKeys.chat.threads(null),
    queryFn: app
      ? async (): Promise<ThreadSummary[]> => {
          const r = await fetch(endpoint);
          if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => "")}`);
          const d = await r.json();
          return (d.chats ?? []) as ThreadSummary[];
        }
      : () => chatApi.listThreads(),
    refetchInterval: 4000,
    staleTime: 0,
    retry: false,
  });
  const threads = q.data ?? null;
  // A failed poll keeps the list on screen; only a failure with nothing to show
  // yet becomes the whole-page error.
  const err = q.isError && !q.data ? String(q.error) : null;

  // Drop a row from the cached list at once (archive and delete both end with it
  // gone); the next poll reconciles with the server.
  // Drop the row from EVERY cached list at once, not just this page's. Archive
  // and delete both end with the chat gone from the live list wherever it is
  // shown, and the dashboard and the recent-chats menu hold their own entries
  // under the same prefix. Predicate-matching the prefix reaches all of them
  // without this page having to know who else is reading.
  const dropThread = (id: string) => {
    qc.setQueriesData<ThreadSummary[]>(
      { queryKey: queryKeys.chat.threadsAll() },
      (old) => (old ? old.filter((x) => x.id !== id) : old),
    );
    // The archive GAINED a row when this was an archive rather than a delete,
    // and this page cannot tell which from here, so the archive re-reads rather
    // than being patched. It is not mounted, so this costs nothing until it is.
    void qc.invalidateQueries({ queryKey: queryKeys.chat.threadsArchived() });
    // The app's own collection is a different key and is not under the prefix
    // above, so an archive from the app tab would leave the row on screen until
    // the next poll without this.
    if (app) void qc.invalidateQueries({ queryKey: queryKeys.apps.chats(app.id) });
  };

  const newChat = () => nav(newChatHref(app ? app.slug : "general"));

  // THE SCOPE TAG, ONLY WHERE IT SAYS SOMETHING. Never on an app's own tab (it
  // would be the page title once per row), and on the global list only when the
  // rows in hand actually differ. See `spansScopes`.
  const showScope = useMemo(() => !app && spansScopes(threads ?? []), [app, threads]);

  // The list: narrowed by the filter, then newest first. Both rules live in
  // ./chatList so this page holds no ordering logic of its own.
  const rows = useMemo(() => visibleThreads(threads, armed), [threads, armed]);

  // Reset only when the filter changes, NOT on every poll: a refetch returns a
  // new array each tick, and resetting on that would snap the list back to the
  // first page.
  useEffect(() => { setShown(PAGE); }, [armed]);

  const visible = rows.slice(0, shown);
  const more = shown < rows.length;
  // The mounted page, cut into its date headings. Grouping happens AFTER the
  // slice so a heading always matches the rows actually on screen: bucketing
  // the whole list first would print "Earlier this month 84" over the four of
  // those eighty-four that have loaded.
  const groups = useMemo(() => groupByDate(visible), [visible]);

  // Load the next page when the foot of the list nears the viewport. rootMargin
  // starts the next page early so the scroll never actually stops at the seam.
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !more) return;
    const io = new IntersectionObserver(
      (entries) => { if (entries[0]?.isIntersecting) setShown((n) => n + PAGE); },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [more, rows.length]);

  // Each running dot's position among the running dots, top to bottom. It feeds
  // the 1/6 pulse offset, and is read only at the instant a dot goes live (see
  // RunningDot), so a shift here never disturbs a dot already pulsing.
  const runningOrdinals = useMemo(() => {
    const m = new Map<string, number>();
    let n = 0;
    for (const t of visible) if (t.running) m.set(t.id, n++);
    return m;
  }, [visible]);

  // THE APP'S SLUG STANDS IN FOR A MISSING SCOPE KEY, and only on the app
  // mount, where there is a right answer. `/api/apps/{id}/chats` selects rows
  // by `chat_thread.app_id`, which is broader than any scope key, so a row can
  // come back with `context_key` null; without this the row opened
  // `/chat/null`. The bespoke list this mount replaced carried the same
  // fallback, so dropping it would have been a regression hidden inside a fix.
  const open = (t: ThreadSummary) => {
    const key = (t.context_key || (app ? app.slug : "")).trim();
    if (!key) return;
    nav(`/chat/${encodeURIComponent(key)}?thread=${encodeURIComponent(t.id)}`);
  };

  // Archive is the one action on the row itself: frequent and reversible. It
  // drops the row locally rather than refetching the whole list to learn one
  // row left.
  const archiveOne = async (t: ThreadSummary) => {
    try {
      await chatApi.archiveThread(t.id);
      dropThread(t.id);
    } catch (e) {
      setActionErr({
        text: `Couldn't archive "${t.title || "this chat"}": ${e}. It is still here.`,
        endpoint: `/api/chat/threads/${t.id}/archive`,
      });
    }
  };

  const runDelete = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      await chatApi.deleteThread(pending.id);
      dropThread(pending.id);
      setPending(null);
    } catch (e) {
      setActionErr({
        text: `Couldn't delete "${pending.title}": ${e}. It is still here.`,
        endpoint: `/api/chat/threads/${pending.id}`,
      });
      setPending(null);
    } finally {
      setBusy(false);
    }
  };

  const controls = (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
            {/* The armed filter, and the only control on the list. §6 retires
                the Agents roster into it: a thread that is armed is what used
                to be called an agent, so "Armed" is that page's list without a
                second entity behind it. A view preference, so it commits on
                change. */}
            <Segmented
              ariaLabel="Filter chats"
              value={armed}
              onChange={setArmed}
              options={[
                { value: "all", label: "All" },
                { value: "armed", label: "Armed" },
              ]}
            />
            <Button variant="primary" onClick={newChat}>
              <Plus aria-hidden /> New chat
            </Button>
          </div>
  );

  return (
    <div>
      {header ? (
        <PageHeader
          className="ws-chats-head"
          eyebrow={eyebrow}
          title="Chats"
          sub={sub}
          actions={controls}
        />
      ) : (
        // The app tab draws its own header above this (AppTabShell), so only
        // the controls belong here. A second "Chats" heading under a page
        // headed Chats is what this mount replaced.
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "var(--space-3)" }}>
          {controls}
        </div>
      )}

      {actionErr && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <Notice
            tone="error"
            where={actionErr.endpoint}
            action={<Button size="sm" variant="ghost" onClick={() => setActionErr(null)}>Dismiss</Button>}
          >
            {actionErr.text}
          </Notice>
        </div>
      )}

      {err ? (
        <StateBlock
          variant="error"
          title="Couldn't load chats"
          hint={<code>GET {endpoint}: {err}</code>}
          action={<Button size="sm" onClick={() => void q.refetch()}>Retry</Button>}
        />
      ) : threads === null ? (
        <div style={{ display: "grid", gap: "var(--space-2)" }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: 52 }} />
          ))}
        </div>
      ) : threads.length === 0 ? (
        <StateBlock
          title="No chats yet"
          hint={app
            ? `Open a chat scoped to ${app.name} to see it here.`
            : "Start a conversation with the general assistant, or open chat inside any app or project."}
          action={
            <Button variant="primary" size="sm" onClick={newChat}>
              <Plus aria-hidden /> New chat
            </Button>
          }
        />
      ) : rows.length === 0 ? (
        <StateBlock
          title="No armed chats"
          hint="An armed chat advances without you: on a new message, on a schedule, or when a ticket is ready. None of these chats is armed."
          action={<Button size="sm" onClick={() => setArmed("all")}>Show all chats</Button>}
        />
      ) : (
        <>
          {/* NEWEST FIRST, UNDER DATE HEADINGS. The order is the one order and
              the headings only NAME runs of it, so nothing here reorders a row.
              The rule is `groupByDate` in pages/chatList, tested there. */}
          {groups.map((g) => (
            <div key={g.key} className="ws-chatgroup" style={{ display: "grid", gap: "var(--space-2)" }}>
              <div className="ws-chatgroup__head">
                <SectionLabel>{g.label}</SectionLabel>
                <span className="ws-card__meta">{g.items.length}</span>
              </div>
              {g.items.map((t) => (
                <ChatRow
                  key={t.id}
                  thread={t}
                  ordinal={runningOrdinals.get(t.id) ?? 0}
                  showScope={showScope}
                  onOpen={open}
                  onArchive={(x) => { void archiveOne(x); }}
                  onDelete={(x) => setPending({ id: x.id, title: x.title || "this chat" })}
                />
              ))}
            </div>
          ))}
          {more && <div ref={sentinel} aria-hidden style={{ height: 1 }} />}
        </>
      )}

      {/* The way into the archive is the "Archived" tab in this page's own
          sub-nav (places.ts). A second button at the foot pointed at the same
          route, which read as two doors to one room; the tab is the nav. */}

      {/* Delete is the one destructive verb and there is no undo on the API, so
          it always confirms. Archive does not: it is reversible from the archive. */}
      <ConfirmDialog
        open={pending !== null}
        busy={busy}
        onCancel={() => setPending(null)}
        onConfirm={() => { void runDelete(); }}
        title="Delete chat?"
        body={<span>This permanently deletes "{pending?.title}" and its messages. This can't be undone.</span>}
        confirmLabel="Delete chat"
      />
    </div>
  );
}

/** `/chats`: the whole list, with its own page header. */
export default function Chats() {
  return <ChatsView />;
}

/**
 * The archive: a different LIST, not a filter over the one above.
 *
 * Its own route and its own component on purpose. As a view flag inside Chats it
 * rendered the still-loaded live chats as archive rows for one frame before the
 * refetch cleared them, so an empty archive announced itself by flashing several
 * chats and then deleting them. Separate mounts cannot show each other's data.
 */
export function ChatsArchived() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  // A failed restore or delete. Kept apart from `err` (which replaces the whole
  // list) because the list is still good: one row's action failed, and the row
  // is still there to try again.
  const [actionErr, setActionErr] = useState<{ text: string; endpoint: string } | null>(null);
  // How many rows are mounted. Grows a page at a time as the foot scrolls into
  // view, the same infinite-scroll the live list uses; reset when the search
  // narrows so a new query starts at the top.
  const [shown, setShown] = useState(PAGE);

  // THE SAME QUERY LAYER THE LIVE LIST USES. This was a `useState` + a
  // `useEffect` + a hand-rolled `load()`, which is how one page came to hold two
  // different answers to "where does a chat list come from": the live list
  // polled through the cache and this one re-fetched into local state. The two
  // mechanisms is the whole defect -- new code picks whichever it sees first.
  //
  // No `refetchInterval`: the archive does not move on its own. Something has to
  // archive a chat for it to change, and whatever did that invalidates this key.
  const q0 = useQuery({
    queryKey: queryKeys.chat.threadsArchived(),
    queryFn: () => chatApi.listThreads(undefined, true),
    retry: false,
  });
  const threads = q0.data ?? null;
  const err = q0.isError ? String(q0.error) : null;
  // `q0.refetch` and not `q0`: the query RESULT is a new object every render,
  // so depending on it memoizes nothing and would become a refetch loop the
  // moment this went into another dependency array. The bound method is stable.
  // Clearing `actionErr` is what the hand-rolled `load()` did, and a Retry that
  // left a failed restore's notice on screen would be reporting the wrong
  // request.
  const refetchArchive = q0.refetch;
  const load = useCallback(() => {
    setActionErr(null);
    void refetchArchive();
  }, [refetchArchive]);

  // Restore and delete both END with the row gone from the list on screen, so
  // they drop it locally rather than re-fetching: the archive can be long, and
  // re-reading it to learn one row left is a request that says nothing new.
  const runPending = useCallback(async () => {
    if (!pending) return;
    const { id, action } = pending;
    setBusy(true);
    try {
      if (action === "restore") await chatApi.restoreThread(id);
      else await chatApi.deleteThread(id);
      qc.setQueryData<ThreadSummary[]>(
        queryKeys.chat.threadsArchived(),
        (list) => (list ? list.filter((t) => t.id !== id) : list),
      );
      // A restore puts the chat BACK on the live list, so the live readers have
      // to hear about it. A delete does not, but invalidating either way is one
      // branch fewer and the live list is polling anyway.
      if (action === "restore") void qc.invalidateQueries({ queryKey: queryKeys.chat.threadsAll() });
      setPending(null);
      setActionErr(null);
    } catch (e) {
      setPending(null);
      setActionErr({
        text: action === "restore"
          ? `Couldn't restore this conversation: ${e}. It is still archived.`
          : `Couldn't delete this conversation: ${e}. It is still here.`,
        endpoint: action === "restore"
          ? `/api/chat/threads/${id}/restore`
          : `/api/chat/threads/${id}`,
      });
    } finally {
      setBusy(false);
    }
  }, [pending, qc]);

  // The server's order, which is most-recently-archived first: it answers "where
  // did the one I just archived go", where updated_at would sort by a
  // conversation's own last activity and bury it.
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (threads ?? []).filter((t) => matches(t, needle));
  }, [threads, q]);

  // Reset the page only when the search changes, not when the list reloads:
  // a re-narrow starts at the top, a restore/delete drop does not.
  useEffect(() => { setShown(PAGE); }, [q]);

  const visible = filtered.slice(0, shown);
  const more = shown < filtered.length;
  const archivedGroups = useMemo(
    () => groupByField(visible, (t) => t.archived_at),
    [visible],
  );

  // Load the next page when the foot nears the viewport (same observer the live
  // list uses). rootMargin starts it early so the scroll never stops at the seam.
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !more) return;
    const io = new IntersectionObserver(
      (entries) => { if (entries[0]?.isIntersecting) setShown((n) => n + PAGE); },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [more, filtered.length]);

  return (
    <div>
      <PageHeader
        back={<Link to="/chats"><ChevronLeft aria-hidden /> Chats</Link>}
        title="Archived chats"
      />

      <div style={{ flex: "1 1 260px", maxWidth: 420, marginBottom: "var(--space-4)" }}>
        <Field label="Search">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by title, context, or last message"
            spellCheck={false}
          />
        </Field>
      </div>

      {actionErr && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <Notice
            tone="error"
            where={actionErr.endpoint}
            action={<Button size="sm" variant="ghost" onClick={() => setActionErr(null)}>Dismiss</Button>}
          >
            {actionErr.text}
          </Notice>
        </div>
      )}

      {/* ERROR BEFORE LOADING, the same order the live list uses above. The
          hand-rolled `load()` this replaced ended its catch with
          `setThreads([])`, which is what let a failed read fall past the
          `threads === null` check and reach the error block. A query leaves
          `data` undefined on failure, so testing null first would show four
          skeletons forever and make this StateBlock and its Retry unreachable
          -- a page that never stops loading rather than one that says what
          went wrong. */}
      {err ? (
        <StateBlock
          variant="error"
          title="Couldn't load the archive"
          hint={<code>GET /api/chat/threads?archived=true: {err}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      ) : threads === null ? (
        <div style={{ display: "grid", gap: "var(--space-2)" }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: 52 }} />
          ))}
        </div>
      ) : threads.length === 0 ? (
        <StateBlock
          title="Nothing archived"
          hint="Archiving a chat from its menu puts it here. It keeps its messages, and can be restored or deleted from this list."
        />
      ) : filtered.length === 0 ? (
        <StateBlock
          title="No matches"
          hint="Clear the search to see everything in the archive."
        />
      ) : (
        <>
          {/* AN ARCHIVED CHAT IS NOT A LINK. There is no way in from here on
              purpose: an archived conversation is closed, so opening one would
              have to either un-archive it silently or drop you in a chat you
              cannot send to. The two things you can do to it are the two
              buttons, and both of them ask first. ChatRow renders a plain
              title rather than a button when it is handed no `onOpen`.

              THE SAME ROW AND THE SAME HEADINGS AS THE OTHER TWO CHATS LISTS.
              This page was the last copy of the pre-2026-09-01 design still
              standing: bordered cards, a bold title with a scope glyph, no
              date headings. It was still that on the day the operator said
              "just put that back on all chats pages", so it is not a separate
              surface with its own look, it is the third chats list.

              Grouped by ARCHIVED date, not by activity: the server returns this
              list most-recently-archived first (see `filtered` above), and a
              heading has to name the field its list is sorted by or it labels
              the wrong day. The row's time says the same thing for the same
              reason. */}
          {archivedGroups.map((g) => (
            <div key={g.key} className="ws-chatgroup" style={{ display: "grid", gap: "var(--space-2)" }}>
              <div className="ws-chatgroup__head">
                <SectionLabel>{g.label}</SectionLabel>
                <span className="ws-card__meta">{g.items.length}</span>
              </div>
              {g.items.map((t) => (
                <ChatRow
                  key={t.id}
                  thread={t}
                  time={archivedLabel(t.archived_at)}
                  onRestore={(x) => setPending({ id: x.id, title: x.title || "this chat", action: "restore" })}
                  onDelete={(x) => setPending({ id: x.id, title: x.title || "this chat", action: "delete" })}
                />
              ))}
            </div>
          ))}
          {more && <div ref={sentinel} aria-hidden style={{ height: 1 }} />}
        </>
      )}

      {/* Both actions ask, and they ask differently, because they are not the
          same kind of act. Restore is reversible and reads as a plain confirm;
          delete is the one destructive operation in the chat system and says
          so. */}
      <ConfirmDialog
        open={pending?.action === "restore"}
        busy={busy}
        danger={false}
        onCancel={() => setPending(null)}
        onConfirm={() => { void runPending(); }}
        title="Restore chat?"
        body={<span>"{pending?.title}" goes back to your chats, and you can send in it again.</span>}
        confirmLabel="Restore chat"
      />

      <ConfirmDialog
        open={pending?.action === "delete"}
        busy={busy}
        onCancel={() => setPending(null)}
        onConfirm={() => { void runPending(); }}
        title="Delete chat?"
        body={<span>This permanently deletes "{pending?.title}" and its messages. This can't be undone.</span>}
        confirmLabel="Delete chat"
      />
    </div>
  );
}
