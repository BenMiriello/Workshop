import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Archive, ArchiveRestore, ArrowDownWideNarrow, ChevronLeft, MessagesSquare, MoreVertical, Plus, Trash2 } from "lucide-react";
import {
  Button,
  Card,
  ConfirmDialog,
  Field,
  Input,
  Menu,
  MenuItem,
  MenuSeparator,
  Notice,
  PageHeader,
  SectionLabel,
  Select,
  Skeleton,
  StateBlock,
  Tag,
  catFor,
} from "@workshop/ui";
import { chatApi, threadAgent, type ThreadSummary } from "../lib/chat";
import { newChatHref } from "../lib/chatSelection";
import { scopeLabel, scopeName } from "../components/chat/chatScope";

// Cross-context chat discovery. Chat and Project are loosely coupled: a chat is
// its own object with an optional context binding (general / project / app /
// agent). This page lists every thread regardless of where it is bound, so a
// conversation is never lost just because you navigated away from its context.

// How a scope key reads, and what kind of place it is, come from chat/chatScope
// — the same functions the chat's own scope picker uses. They were duplicated
// here, which meant the list and the picker could name the same scope
// differently and neither would be wrong.

// How the list is ordered. Recent groups the rows by date; the other two are a
// single flat list. Commits on change (a view preference).
type Sort = "recent" | "oldest" | "messages";

// Recency buckets, the mockup's date headings. A chat's bucket comes from the
// same field the Recent sort orders by (updated_at), so a row's heading and its
// position always agree.
const BUCKET_ORDER = ["today", "yesterday", "week", "month", "older"] as const;
function bucketFor(iso?: string | null): { key: string; label: string } {
  const t = iso ? new Date(iso) : null;
  if (!t || Number.isNaN(t.getTime())) return { key: "older", label: "Earlier" };
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = startOfDay(new Date());
  const day = 86_400_000;
  const ts = startOfDay(t);
  if (ts >= today) return { key: "today", label: "Today" };
  if (ts >= today - day) return { key: "yesterday", label: "Yesterday" };
  if (ts >= today - 7 * day) return { key: "week", label: "Earlier this week" };
  if (ts >= today - 30 * day) return { key: "month", label: "Earlier this month" };
  return { key: "older", label: "Earlier" };
}

// The model that answered, shortened to family and version the way the mockup
// shows it: "claude-opus-4-8" reads "opus 4.8".
function shortModel(m?: string | null): string {
  if (!m) return "";
  const s = m.replace(/^claude-/, "").replace(/-\d{6,}$/, "");
  const parts = s.split("-").filter(Boolean);
  if (parts.length === 0) return s;
  const ver = parts.slice(1).join(".");
  return ver ? `${parts[0]} ${ver}` : parts[0];
}

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
  const [sort, setSort] = useState<Sort>("recent");
  // A delete waiting on its confirm dialog, and a one-off action failure that
  // leaves the list on screen (a Notice, not a whole-page error block).
  const [pending, setPending] = useState<{ id: string; title: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<{ text: string; endpoint: string } | null>(null);

  const load = useCallback(() => {
    setThreads(null);
    setErr(null);
    setActionErr(null);
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

  const sorted = useMemo(() => {
    const list = threads ? [...threads] : [];
    if (sort === "oldest") list.sort((a, b) => (a.updated_at || "").localeCompare(b.updated_at || ""));
    else if (sort === "messages") list.sort((a, b) => (b.message_count || 0) - (a.message_count || 0));
    else list.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    return list;
  }, [threads, sort]);

  const visible = sorted.slice(0, RENDER_CAP);

  // Recent groups the rows under date headings; the other sorts are one flat
  // list, because "most messages" under "Today" would be a heading that lies.
  const groups = useMemo(() => {
    if (sort !== "recent") return [{ key: "all", label: "", items: visible }];
    const byKey = new Map<string, { key: string; label: string; items: ThreadSummary[] }>();
    for (const t of visible) {
      const b = bucketFor(t.updated_at);
      if (!byKey.has(b.key)) byKey.set(b.key, { key: b.key, label: b.label, items: [] });
      byKey.get(b.key)!.items.push(t);
    }
    return BUCKET_ORDER.filter((k) => byKey.has(k)).map((k) => byKey.get(k)!);
  }, [visible, sort]);

  const open = (t: ThreadSummary) =>
    nav(`/chat/${encodeURIComponent(t.context_key)}?thread=${encodeURIComponent(t.id)}`);

  // Archive is the one action on the row itself: frequent and reversible. It
  // drops the row locally rather than refetching the whole list to learn one
  // row left.
  const archiveOne = async (t: ThreadSummary) => {
    try {
      await chatApi.archiveThread(t.id);
      setThreads((list) => (list ? list.filter((x) => x.id !== t.id) : list));
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
      setThreads((list) => (list ? list.filter((x) => x.id !== pending.id) : list));
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

  return (
    <div>
      <PageHeader
        title="Chats"
        actions={
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
            {/* Sort, not a scope filter: this is the workshop's own chats page,
                and the page column already says which place you are in. Commits
                on change (a view preference). */}
            <span style={{ display: "inline-flex", alignItems: "center", gap: "var(--space-2)" }}>
              <ArrowDownWideNarrow size={14} aria-hidden />
              <Select aria-label="Sort chats" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                <option value="recent">Recent</option>
                <option value="oldest">Oldest</option>
                <option value="messages">Most messages</option>
              </Select>
            </span>
            <Button variant="primary" onClick={newChat}>
              <Plus aria-hidden /> New chat
            </Button>
          </div>
        }
      />

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
        <div style={{ display: "grid", gap: "var(--space-2)" }}>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} style={{ height: 52 }} />
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
      ) : (
        <>
          {groups.map((g) => (
            <div key={g.key} style={{ display: "grid", gap: "var(--space-2)" }}>
              {g.label && (
                <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", margin: "var(--space-4) 0 var(--space-1)" }}>
                  <SectionLabel>{g.label}</SectionLabel>
                  <span className="ws-card__meta">{g.items.length}</span>
                </div>
              )}
              {g.items.map((t) => {
                const agent = threadAgent(t);
                return (
                  <Card
                    key={t.id}
                    interactive
                    compact
                    className="ws-chat-row"
                    onClick={() => open(t)}
                    // min-width:0 lets the two ellipsizing lines shrink instead of
                    // setting the card's min-content width and overflowing the
                    // viewport on mobile (the raw grid parent sets no min-width).
                    style={{ cursor: "pointer", minWidth: 0 }}
                  >
                    {/* State dot, then name + scope + agent, the last message on
                        one line, and the quiet meta rail: model, length, age, and
                        the two row actions. This is the mockup's 5a row. */}
                    <span className="ws-chat-row__state" data-live={t.running ? "" : undefined} aria-hidden />
                    <div className="ws-chat-row__main">
                      <div className="ws-chat-row__head">
                        <strong className="ws-chat-row__title">{t.title || "New chat"}</strong>
                        <Tag hue={catFor(t.context_key)}>{scopeName(t.context_key)}</Tag>
                        {/* Who answered, only when the scope tag does not already
                            say so (an agent:<slug> chat already reads its name). */}
                        {!t.context_key.startsWith("agent:") && agent && (
                          <Tag hue={catFor(`agent:${agent}`)}>{agent}</Tag>
                        )}
                      </div>
                      <p className="ws-muted ws-chat-row__snippet">{t.last_message || " "}</p>
                    </div>
                    <div className="ws-chat-row__meta">
                      {t.model && <span className="ws-card__meta">{shortModel(t.model)}</span>}
                      <span className="ws-card__meta">{t.message_count} turns</span>
                      <span className="ws-card__meta ws-chat-row__when">{relTime(t.updated_at)}</span>
                      <button
                        type="button"
                        className="ws-chat-row__act"
                        title="Archive"
                        aria-label="Archive chat"
                        onClick={(e) => { e.stopPropagation(); void archiveOne(t); }}
                      >
                        <Archive size={14} aria-hidden />
                      </button>
                      {/* The menu opens on its own; the stop keeps its trigger
                          from also opening the chat behind it. */}
                      <span onClick={(e) => e.stopPropagation()}>
                        <Menu
                          label="Chat actions"
                          trigger={
                            <button type="button" className="ws-chat-row__act" title="More" aria-label="More chat actions">
                              <MoreVertical size={14} aria-hidden />
                            </button>
                          }
                        >
                          <MenuItem icon={<Archive size={14} aria-hidden />} onSelect={() => void archiveOne(t)}>
                            Archive
                          </MenuItem>
                          <MenuSeparator />
                          <MenuItem
                            danger
                            icon={<Trash2 size={14} aria-hidden />}
                            onSelect={() => setPending({ id: t.id, title: t.title || "this chat" })}
                          >
                            Delete
                          </MenuItem>
                        </Menu>
                      </span>
                    </div>
                  </Card>
                );
              })}
            </div>
          ))}
          {sorted.length > RENDER_CAP && (
            <p className="ws-muted" style={{ margin: "var(--space-4) 0 0", textAlign: "center" }}>
              Showing {RENDER_CAP} of {sorted.length}. This list will paginate as it grows.
            </p>
          )}
        </>
      )}

      {/* The way into the archive: one quiet control at the foot of the list,
          where you look after not finding what you came for. */}
      <div style={{ display: "flex", justifyContent: "center", marginTop: "var(--space-5)" }}>
        <Button size="sm" onClick={() => nav("/chats/archived")}>
          <Archive aria-hidden /> Archived chats
        </Button>
      </div>

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
