import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Archive, ArchiveRestore, ChevronLeft, Filter, MessagesSquare, Plus, Trash2 } from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
  Field,
  Input,
  Notice,
  PageHeader,
  SectionLabel,
  Select,
  Skeleton,
  StateBlock,
  StatusDot,
  Tag,
  catFor,
} from "@workshop/ui";
import { chatApi, threadAgent, type ThreadSummary } from "../lib/chat";
import { newChatHref } from "../lib/chatSelection";
import { scopeKind, scopeLabel, scopeName } from "../components/chat/chatScope";
import { DEV_MODE } from "../lib/devMode";

// Cross-context chat discovery. Chat and Project are loosely coupled: a chat is
// its own object with an optional context binding (general / project / app /
// agent). This page lists every thread regardless of where it is bound, so a
// conversation is never lost just because you navigated away from its context.

// How a scope key reads, and what kind of place it is, come from chat/chatScope
// — the same functions the chat's own scope picker uses. They were duplicated
// here, which meant the list and the picker could name the same scope
// differently and neither would be wrong.

// Scope-kind view filter (C17). Commits on change (a view preference).
//
// The archive is NOT in this control. It was, and it was wrong twice over: it is
// not a scope kind, so it made a row of "which place" answers carry one "which
// list" answer; and the seventh chip pushed the row past a phone viewport, which
// the segmented primitive answers by widening the page rather than wrapping. The
// archive is a different list and now has its own route, reached from a button
// at the foot of this page.
type FilterKind = "all" | "development" | "workshop" | "project" | "app" | "agent";
// Dev is absent, not present-and-disabled, in a non-dev build — same rule
// devMode.ts applies to the scope itself: a disabled option still tells every
// user about a capability they do not have.
//
// It sorts ABOVE Workshop, matching `orderScopes` and the chat's own scope
// picker: Development is where a developer already is, so the root organising
// layer should not come first. It reads "Development", its real name: the
// abbreviation was there to save room in a row that no longer has to fit seven
// chips, and it was the only scope in the app called one thing here and another
// everywhere else.
const KIND_OPTIONS: { value: FilterKind; label: string }[] = [
  { value: "all", label: "Everything" },
  ...(DEV_MODE ? [{ value: "development" as const, label: "Development" }] : []),
  { value: "workshop", label: "Workshop" },
  { value: "project", label: "Projects" },
  { value: "app", label: "Apps" },
  { value: "agent", label: "Agents" },
];

// Cap the rendered list so a large history never mounts unbounded (design rule
// 9, overflow state). The filter narrows the list toward the cap.
const RENDER_CAP = 100;

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

// How long ago a chat was last active, at the resolution a chat list is scanned
// at: recent conversations want minutes and hours, older ones settle to days and
// weeks. It reads the same field the list is sorted by (updated_at), so the time
// on a row and its position agree. This is the mockup's "when" column, carried in
// the meta line rather than a column of its own.
function relTime(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  if (days < 30) return `${Math.floor(days / 7)}w`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
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

export default function Chats() {
  const nav = useNavigate();
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [kind, setKind] = useState<FilterKind>("all");

  const load = useCallback(() => {
    setThreads(null);
    setErr(null);
    chatApi
      .listThreads()
      .then(setThreads)
      .catch((e) => {
        setErr(String(e));
        setThreads([]);
      });
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const newChat = () => nav(newChatHref("general"));

  // Most-recent first, then apply the context-kind and text filters.
  const sorted = useMemo(
    () =>
      threads
        ? [...threads].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
        : [],
    [threads],
  );

  const filtered = useMemo(() => {
    return sorted.filter((t) => {
      if (kind === "all") return true;
      // The Agents filter is keyed on WHO ANSWERS, which is what the operator
      // is looking for when they pick it. `threadAgent` is the one derivation
      // of that: an `agent:<slug>` chat, or a chat with an agent whose own scope
      // is gone. It used to match "carries a non-empty participants list", a
      // field nothing wrote except a migration and nothing read except this line.
      if (kind === "agent") return threadAgent(t) !== null;
      return scopeKind(t.context_key) === kind;
    });
  }, [sorted, kind]);

  const visible = filtered.slice(0, RENDER_CAP);

  const open = (t: ThreadSummary) =>
    nav(`/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`);

  return (
    <div>
      {/* Eyebrow then title, the mockup's header: the scope this list belongs
          to, then the page name. */}
      <SectionLabel>Workshop</SectionLabel>
      <PageHeader
        title="Chats"
        actions={
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
            {/* One Filter control carrying the scope, not a row of six chips.
                It commits on change, a view preference. */}
            <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
              <Filter size={14} aria-hidden />
              <span className="ws-muted">Filter</span>
              <Select
                aria-label="Filter chats by context"
                value={kind}
                onChange={(e) => setKind(e.target.value as FilterKind)}
              >
                {KIND_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </Select>
            </span>
            <Button variant="primary" onClick={newChat}>
              <Plus aria-hidden /> New chat
            </Button>
          </div>
        }
      />

      {threads === null ? (
        <div style={{ display: "grid", gap: "var(--space-3)" }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: 64 }} />
          ))}
        </div>
      ) : err ? (
        <StateBlock
          variant="error"
          title="Couldn't load chats"
          hint={<code>GET /api/chat/threads: {err}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
      ) : threads.length === 0 ? (
        <StateBlock
          title="No chats yet"
          hint="Start a conversation with the general assistant, or open chat inside any app or project."
          action={
            <Button variant="primary" size="sm" onClick={newChat}>
              <Plus aria-hidden /> New chat
            </Button>
          }
        />
      ) : filtered.length === 0 ? (
        <StateBlock
          title="No chats here"
          hint="Pick Everything in the filter to see chats from every scope."
        />
      ) : (
        <>
          <div style={{ display: "grid", gap: "var(--space-3)" }}>
            {visible.map((t) => (
              <Card
                key={t.id}
                interactive
                className="ws-chat-row"
                onClick={() => open(t)}
                // min-width:0 lets the two ellipsizing lines shrink instead of
                // setting the card's min-content width and overflowing the
                // viewport on mobile (the raw grid parent sets no min-width).
                style={{ cursor: "pointer", minWidth: 0 }}
              >
                {/* The mockup's row: the chat name with its scope tag on top, the
                    last message on one line beneath, the counts held off to the
                    right rather than trailing the snippet. No leading icon: every
                    row is a chat, so a chat glyph on each says nothing. */}
                <div className="ws-chat-row__main">
                  <div className="ws-chat-row__head">
                    <strong className="ws-chat-row__title">{t.title || "New chat"}</strong>
                    <Tag hue={catFor(t.context_key)}>{scopeName(t.context_key)}</Tag>
                  </div>
                  <p className="ws-muted ws-chat-row__snippet">{t.last_message || " "}</p>
                </div>
                {/* Right rail: a live dot while a turn is running, then the length
                    and the last activity, the mockup's two quiet meta columns. */}
                <div className="ws-chat-row__meta">
                  {t.running && <StatusDot status="running">running</StatusDot>}
                  <span className="ws-card__meta">{t.message_count} turns</span>
                  <span className="ws-card__meta ws-chat-row__when">{relTime(t.updated_at)}</span>
                </div>
              </Card>
            ))}
          </div>
          {filtered.length > RENDER_CAP && (
            <p className="ws-muted" style={{ margin: "var(--space-3) 0 0", textAlign: "center" }}>
              Showing {RENDER_CAP} of {filtered.length}. Refine the search or context filter to narrow the list.
            </p>
          )}
        </>
      )}

      {/* The way into the archive: one quiet control at the foot of the list,
          where you look after not finding what you came for. It is not a filter
          on this page, so it does not sit in the filter row. */}
      <div style={{ display: "flex", justifyContent: "center", marginTop: "var(--space-5)" }}>
        <Button size="sm" onClick={() => nav("/chats/archived")}>
          <Archive aria-hidden /> Archived chats
        </Button>
      </div>
    </div>
  );
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
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  // A failed restore or delete. Kept apart from `err` (which replaces the whole
  // list) because the list is still good: one row's action failed, and the row
  // is still there to try again.
  const [actionErr, setActionErr] = useState<{ text: string; endpoint: string } | null>(null);

  const load = useCallback(() => {
    setThreads(null);
    setErr(null);
    setActionErr(null);
    chatApi
      .listThreads(undefined, true)
      .then(setThreads)
      .catch((e) => {
        setErr(String(e));
        setThreads([]);
      });
  }, []);
  useEffect(() => {
    load();
  }, [load]);

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
      setThreads((list) => (list ? list.filter((t) => t.id !== id) : list));
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
  }, [pending]);

  // The server's order, which is most-recently-archived first: it answers "where
  // did the one I just archived go", where updated_at would sort by a
  // conversation's own last activity and bury it.
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (threads ?? []).filter((t) => matches(t, needle));
  }, [threads, q]);

  const visible = filtered.slice(0, RENDER_CAP);

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

      {threads === null ? (
        <div style={{ display: "grid", gap: "var(--space-3)" }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: 64 }} />
          ))}
        </div>
      ) : err ? (
        <StateBlock
          variant="error"
          title="Couldn't load the archive"
          hint={<code>GET /api/chat/threads?archived=true: {err}</code>}
          action={<Button size="sm" onClick={load}>Retry</Button>}
        />
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
              buttons, and both of them ask first. */}
          <div style={{ display: "grid", gap: "var(--space-2)" }}>
            {visible.map((t) => (
              <Card key={t.id} compact className="ws-chat-card" style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0, overflow: "hidden" }}>
                  <MessagesSquare size={14} aria-hidden style={{ flex: "none" }} />
                  <strong style={{ minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {t.title || "New chat"}
                  </strong>
                  <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flex: "none", marginLeft: "auto" }}>
                    <Tag hue={catFor(t.context_key)}>{scopeLabel(t.context_key)}</Tag>
                  </span>
                </div>
                <div className="ws-chat-card__last">
                  <p className="ws-muted ws-chat-card__snippet">{t.last_message || " "}</p>
                  <span className="ws-card__meta ws-chat-card__count">{t.message_count}</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap", marginTop: "var(--space-2)" }}>
                  <span className="ws-card__meta">{archivedLabel(t.archived_at)}</span>
                  <span style={{ display: "flex", gap: "var(--space-2)", marginLeft: "auto" }}>
                    <Button
                      size="sm"
                      onClick={() => setPending({ id: t.id, title: t.title || "this chat", action: "restore" })}
                    >
                      <ArchiveRestore aria-hidden /> Restore
                    </Button>
                    <Button
                      size="sm"
                      variant="danger"
                      onClick={() => setPending({ id: t.id, title: t.title || "this chat", action: "delete" })}
                    >
                      <Trash2 aria-hidden /> Delete
                    </Button>
                  </span>
                </div>
              </Card>
            ))}
          </div>
          {filtered.length > RENDER_CAP && (
            <p className="ws-muted" style={{ margin: "var(--space-3) 0 0", textAlign: "center" }}>
              Showing {RENDER_CAP} of {filtered.length}. Refine the search to narrow the list.
            </p>
          )}
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
