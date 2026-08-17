import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Archive, ArchiveRestore, ChevronLeft, MessagesSquare, Plus, Trash2 } from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
  Field,
  Input,
  PageHeader,
  Segmented,
  Skeleton,
  StateBlock,
  Tag,
  catFor,
  type SegmentedOption,
} from "@workshop/ui";
import { chatApi, threadAgent, type ThreadSummary } from "../lib/chat";
import { newChatHref } from "../lib/chatSelection";
import { scopeKind, scopeLabel } from "../components/chat/chatScope";
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
const KIND_OPTIONS: SegmentedOption<FilterKind>[] = [
  { value: "all", label: "All" },
  ...(DEV_MODE ? [{ value: "development" as const, label: "Development" }] : []),
  { value: "workshop", label: "Workshop" },
  { value: "project", label: "Projects" },
  { value: "app", label: "Apps" },
  { value: "agent", label: "Agents" },
];

// Cap the rendered list so a large history never mounts unbounded (design rule
// 9, overflow state). The filter narrows the list toward the cap.
const RENDER_CAP = 100;

// The chip row scrolls sideways on a narrow screen instead of setting the page's
// width. `.ws-segmented` is an inline-flex with no wrap and an absolutely
// positioned thumb, so it cannot wrap; left alone it made the whole Chats page
// wider than a phone.
// The 2px of padding is for the thumb's shadow and the focus ring: `overflow-x`
// computes `overflow-y` to auto too, so a ring drawn at the edge would clip.
const CHIP_SCROLL: React.CSSProperties = {
  minWidth: 0,
  maxWidth: "100%",
  overflowX: "auto",
  padding: "2px",
};

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

export default function Chats() {
  const nav = useNavigate();
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
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
    const needle = q.trim().toLowerCase();
    return sorted.filter((t) => {
      if (kind !== "all") {
        const k = scopeKind(t.context_key);
        // The Agents filter is keyed on WHO ANSWERS, which is what the operator
        // is looking for when they pick it. `threadAgent` is the one derivation
        // of that: an `agent:<slug>` chat, or a chat with an agent whose own
        // scope is gone. It used to match "carries a non-empty participants
        // list", a field nothing wrote except a migration and nothing read
        // except this line.
        if (kind === "agent") {
          if (threadAgent(t) === null) return false;
        } else if (k !== kind) {
          return false;
        }
      }
      return matches(t, needle);
    });
  }, [sorted, q, kind]);

  const visible = filtered.slice(0, RENDER_CAP);

  const open = (t: ThreadSummary) =>
    nav(`/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`);

  return (
    <div>
      <PageHeader
        title="Chats"
        actions={
          <Button variant="primary" onClick={newChat}>
            <Plus aria-hidden /> New chat
          </Button>
        }
      />

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "var(--space-4)",
          alignItems: "flex-end",
          marginBottom: "var(--space-4)",
        }}
      >
        <div style={{ flex: "1 1 260px", maxWidth: 420 }}>
          <Field label="Search">
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter by title, context, or last message"
              spellCheck={false}
            />
          </Field>
        </div>
        <div style={CHIP_SCROLL}>
          <Field label="Context">
            <Segmented
              ariaLabel="Filter by context"
              options={KIND_OPTIONS}
              value={kind}
              onChange={setKind}
            />
          </Field>
        </div>
      </div>

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
          title="No matches"
          hint="Clear the search or pick All to see every chat."
        />
      ) : (
        <>
          <div style={{ display: "grid", gap: "var(--space-2)" }}>
            {visible.map((t) => (
              <Card
                key={t.id}
                interactive
                compact
                className="ws-chat-card"
                onClick={() => open(t)}
                // min-width:0 is what every other card grid sets on its items
                // (see .ws-*-grid > li in components.css). This grid is a raw
                // inline div, so the card must carry it itself, else a long
                // context tag or snippet sets the card's min-content width and
                // pushes it past the viewport on mobile (horizontal scroll).
                style={{ cursor: "pointer", minWidth: 0 }}
              >
                {/* One line: icon + title ellipsize, tag + count never shrink.
                    overflow:hidden + min-width:0 keep a long title/context from
                    wrapping the row tall or overflowing the viewport on mobile. */}
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0, overflow: "hidden" }}>
                  <MessagesSquare size={14} aria-hidden style={{ flex: "none" }} />
                  <strong style={{ minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {t.title || "New chat"}
                  </strong>
                  <span style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flex: "none", marginLeft: "auto" }}>
                    <Tag hue={catFor(t.context_key)}>{scopeLabel(t.context_key)}</Tag>
                    {/* Who answered, only when the scope tag beside it does not
                        already say so. An `agent:<slug>` chat reads
                        "Agent · <slug>" one tag to the left, and repeating it
                        would be the same fact twice. */}
                    {!t.context_key.startsWith("agent:") && threadAgent(t) && (
                      <Tag hue={catFor(`agent:${threadAgent(t)}`)}>{threadAgent(t)}</Tag>
                    )}
                  </span>
                </div>
                {/* The snippet is always rendered (even when empty) so every card
                    reserves the same 2-line height; the clamp trims longer
                    snippets with an ellipsis instead of growing the card.
                    The message count rides at the END of the snippet line, not up
                    in the title row: it describes the conversation's length, so it
                    belongs next to the conversation's last line, and the title row
                    is already carrying the scope tag. */}
                <div className="ws-chat-card__last">
                  <p className="ws-muted ws-chat-card__snippet">{t.last_message || " "}</p>
                  <span className="ws-card__meta ws-chat-card__count">{t.message_count}</span>
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
          <StateBlock
            variant="error"
            title="That didn't go through"
            hint={<>
              {actionErr.text} <code>{actionErr.endpoint}</code>
            </>}
            action={<Button size="sm" onClick={() => setActionErr(null)}>Dismiss</Button>}
          />
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
