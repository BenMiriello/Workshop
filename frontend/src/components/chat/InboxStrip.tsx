import { useCallback, useEffect, useState } from "react";
import { usePoll } from "../../lib/usePoll";
import { useNavigate } from "react-router-dom";
import {
  Check, X, MessageCircleQuestion, ClipboardCheck, Info, ChevronRight,
  ListTodo, Play, Clock,
} from "lucide-react";
import { Button, Input } from "@workshop/ui";
import { ticketHref } from "../../lib/tasks";
import {
  inboxElsewhere, inboxUrl, type InboxCounts, type InboxMode,
} from "../../lib/inbox";
import ChatProblemBlock from "./ChatProblemBlock";
import { shortHttpError } from "../../lib/httpError";

// Items an agent raised that need the operator: the "Needs you" zone of the
// chat's one work drawer (WorkDrawer).
//
// NOT typed by entity. A finished run, a blocked agent and a proposal awaiting
// approval are one kind of thing (something wants you) with three verbs, which
// is what makes a single list coherent instead of a pile of mixed nouns:
//
//   question  the agent is stuck and is asking rather than guessing  -> answer
//   review    the agent proposes something                           -> approve / reject
//   notify    FYI, safe to ignore                                    -> dismiss
//
// SHAPE. This is a zone inside a panel behind a toolbar toggle, not an inline
// banner. The first cut rendered every open item inline at full size, always:
// on a phone that pushed the log and the composer off-screen entirely, and with
// no cap it grew without bound. An interrupt surface has to be bounded in every
// direction — count, height, and row height — or it stops being a signal and
// becomes the page.
//
// STATE LIVES IN THE HOOK, NOT IN THIS COMPONENT. `useInboxItems` is called by
// WorkDrawer, which stays mounted whether or not the drawer is open, so the
// poll keeps running and the toolbar badge stays live without the operator
// opening anything to find out whether it should. Expanded row and half-typed
// answers live there for the same reason: closing the drawer must not throw an
// answer away.
export type InboxItem = {
  id: string;
  kind: "notify" | "question" | "review";
  agent: string;
  scope: string;
  title: string;
  body: string;
  options: string[];
  chat_id: string | null;
  // What this item is ABOUT. An inbox row that cannot reach the work it
  // describes makes the operator go find it by hand, which is most of the
  // reason the old attention surfaces went unread.
  ticket_id: string | null;
  run_id: string | null;
  // Deferred until this instant. The server filters snoozed rows out of the
  // listing AND out of the counts, so this never arrives set on a live row; it
  // is here because the row is the server's item and the type should say so.
  snoozed_until: string | null;
};

// How long "later" is. The server owns the arithmetic so every surface defers
// by the same amount.
type SnoozePreset = "hour" | "tomorrow";

const KIND_ICON = {
  question: MessageCircleQuestion,
  review: ClipboardCheck,
  notify: Info,
} as const;

// At most this many rows are rendered. The rest are reachable through the
// footer link rather than by scrolling a list that has no natural end.
const MAX_ROWS = 4;

export type Inbox = {
  // null until the first read lands, which is what separates "loading" from
  // "nothing waiting".
  items: InboxItem[] | null;
  err: string | null;
  busy: string | null;
  // True while a read is in flight, including the polls after the first one.
  // Distinct from `items === null`: that is "nothing has ever loaded".
  loading: boolean;
  // Rows on screen right now, optimistically short by one the moment the
  // operator answers something.
  total: number;
  // The server's per-kind counts for THIS view, and how many more are open
  // outside it. Counts are uncapped and lag an optimistic removal by one
  // reload, so they answer "how much is there", never "what is on screen".
  counts: InboxCounts | null;
  elsewhere: number;
  expanded: string | null;
  setExpanded: (id: string | null) => void;
  answers: Record<string, string>;
  setAnswer: (id: string, text: string) => void;
  reload: () => void;
  resolve: (id: string, answer: string | null, dismissed: boolean) => Promise<void>;
  snooze: (id: string, preset: SnoozePreset) => Promise<void>;
};

// `mode` defaults to "chat" so the drawer's call site reads as it always has.
// "global" is the /work Inbox tab: every scope, no chat filter, which the
// backend serves as `list_items(chat_scope=None)`. It is a MODE rather than a
// null chat id because null already means "no thread yet, read nothing" — see
// lib/inbox.
export function useInboxItems(chatId: string | null, mode: InboxMode = "chat"): Inbox {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [counts, setCounts] = useState<InboxCounts | null>(null);
  const [globalCounts, setGlobalCounts] = useState<InboxCounts | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // One row at a time is expanded. A collapsed row is one line; expanding is
  // how you read the detail and reach the actions. Without this every row
  // carries its body and its buttons and four of them fill a phone screen.
  const [expanded, setExpanded] = useState<string | null>(null);

  const url = inboxUrl(chatId, mode);

  const load = useCallback(async () => {
    // No thread yet, nothing to scope to. Reading the unfiltered inbox here
    // would count items belonging to conversations that are not on screen.
    if (!url) { setItems(null); setCounts(null); setGlobalCounts(null); return; }
    setLoading(true);
    try {
      const r = await fetch(url);
      if (!r.ok) {
        setErr(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")).trim());
        setItems((v) => v ?? []);
        return;
      }
      const j = await r.json();
      setErr(null);
      setItems(j.items || []);
      setCounts(j.counts || null);
      setGlobalCounts(j.global_counts || null);
    } catch (e) {
      // A failed poll leaves the last good state on screen rather than blanking
      // the panel: an inbox that empties itself on a network blip is worse than
      // one that is briefly stale.
      setErr(String(e));
      setItems((v) => v ?? []);
    } finally {
      setLoading(false);
    }
  }, [url]);

  // Agents act on their own schedule, so this polls. 20s is under the point
  // where the operator would notice a lag; usePoll supplies the pause-while-
  // hidden and the catch-up read on return that this hand-rolled twice.
  //
  // `immediate: false` because usePoll reads `fn` through a ref and so cannot
  // see the chat change; the effect below owns both the first read and the read
  // after a blank chat becomes a real thread. Without it a freshly created
  // thread showed "Loading…" for a full interval.
  usePoll(load, 20000, { immediate: false });
  useEffect(() => { void load(); }, [load]);

  const resolve = useCallback(async (id: string, answer: string | null, dismissed: boolean) => {
    const what = dismissed ? "dismiss that" : "send that answer";
    setBusy(id);
    // Optimistic: the operator acted, so the row goes now. A re-fetch follows,
    // which corrects the rare case where the write failed.
    setItems((v) => (v || []).filter((i) => i.id !== id));
    setExpanded((e) => (e === id ? null : e));
    try {
      const r = await fetch(`/api/inbox/${encodeURIComponent(id)}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer, dismissed }),
      });
      // An answer to a task-derived item unblocks the TASK, and that write can
      // be refused (a proposal already granted, a task that moved on). Say so:
      // the re-fetch below puts the row back, and a row reappearing with no
      // explanation is the thing that made this surface untrustworthy.
      if (!r.ok) setErr(`Couldn't ${what}: ${shortHttpError(r.status, r.statusText, await r.text().catch(() => ""))}`.trim());
      else setErr(null);
    } catch (e) {
      setErr(`Couldn't ${what}: ${String(e)}`);
    } finally {
      setBusy(null);
      void load();
    }
  }, [load]);

  // Snooze is deferral, not closure: the item stays open on the server and
  // comes back on its own, or sooner if the work it describes moves. The row
  // goes now for the same reason a resolved one does, and the re-fetch below
  // corrects the rare case where the write failed.
  const snooze = useCallback(async (id: string, preset: SnoozePreset) => {
    setBusy(id);
    setItems((v) => (v || []).filter((i) => i.id !== id));
    setExpanded((e) => (e === id ? null : e));
    try {
      const r = await fetch(`/api/inbox/${encodeURIComponent(id)}/snooze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset }),
      });
      if (!r.ok) setErr(`Couldn't snooze that: ${shortHttpError(r.status, r.statusText, await r.text().catch(() => ""))}`.trim());
      else setErr(null);
    } catch (e) {
      setErr(`Couldn't snooze that: ${String(e)}`);
    } finally {
      setBusy(null);
      void load();
    }
  }, [load]);

  const setAnswer = useCallback((id: string, text: string) => {
    setAnswers((a) => ({ ...a, [id]: text }));
  }, []);

  const reload = useCallback(() => { void load(); }, [load]);

  const total = items?.length ?? 0;

  return {
    items, err, busy, loading, total, counts,
    elsewhere: inboxElsewhere(counts, globalCounts, mode),
    expanded, setExpanded, answers, setAnswer,
    reload, resolve, snooze,
  };
}

// The rows themselves. The drawer owns the panel, the heading and the layer it
// floats on; this owns one zone inside it.
export default function InboxStrip({
  inbox,
  onOpenGlobal,
}: {
  inbox: Inbox;
  onOpenGlobal: () => void;
}) {
  const {
    items, err, busy, total, elsewhere,
    expanded, setExpanded, answers, setAnswer, reload, resolve, snooze,
  } = inbox;
  const navigate = useNavigate();

  const shown = (items || []).slice(0, MAX_ROWS);
  const overflow = total - shown.length;

  return (
    <>
      {err && items && items.length === 0 ? (
        <ChatProblemBlock
          problem={{ text: `Couldn't load the inbox: ${err}`, endpoint: "/api/inbox", retry: reload }}
        />
      ) : items === null ? (
        <div className="chat-inbox__empty">Loading…</div>
      ) : total === 0 ? (
        <div className="chat-inbox__empty">
          Nothing waiting on you. Agents raise questions and reviews here.
        </div>
      ) : (
        <ul className="chat-inbox__list">
          {/* The list is showing real rows, so this is the poll or a refused
              answer failing UNDER them — the same failure the empty branch
              above states, said the same way rather than as a bare red line
              with no endpoint and no retry. */}
          {err && (
            <li>
              <ChatProblemBlock problem={{ text: err, endpoint: "/api/inbox", retry: reload }} />
            </li>
          )}
          {shown.map((it) => {
            const Icon = KIND_ICON[it.kind] ?? Info;
            const isOpen = expanded === it.id;
            return (
              <li key={it.id} className={`chat-inbox__item chat-inbox__item--${it.kind}`}>
                <div className="chat-inbox__row">
                  <button
                    type="button"
                    className="chat-inbox__toggle"
                    onClick={() => setExpanded(isOpen ? null : it.id)}
                    aria-expanded={isOpen}
                  >
                    <Icon size={14} strokeWidth={1.8} aria-hidden className="chat-inbox__kind" />
                    <span className="chat-inbox__agent">{it.agent}</span>
                    <span className="chat-inbox__title">{it.title}</span>
                    <ChevronRight
                      size={14}
                      aria-hidden
                      className={`chat-inbox__caret${isOpen ? " is-open" : ""}`}
                    />
                  </button>
                  <button
                    type="button"
                    className="chat-inbox__dismiss"
                    onClick={() => void snooze(it.id, "hour")}
                    disabled={busy === it.id}
                    title="Snooze for an hour"
                    aria-label={`Snooze for an hour: ${it.title}`}
                  >
                    <Clock size={14} aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="chat-inbox__dismiss"
                    onClick={() => void resolve(it.id, null, true)}
                    title="Dismiss"
                    aria-label={`Dismiss: ${it.title}`}
                  >
                    <X size={14} aria-hidden />
                  </button>
                </div>

                {isOpen && (
                  <div className="chat-inbox__detail">
                    {it.body && <p className="chat-inbox__body">{it.body}</p>}

                    {/* Where the thing being asked about actually lives. */}
                    {(it.ticket_id || it.run_id) && (
                      <div className="chat-inbox__actions">
                        {it.ticket_id && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => navigate(ticketHref(it.scope, it.ticket_id!))}
                          >
                            <ListTodo size={14} aria-hidden /> Open ticket
                          </Button>
                        )}
                        {it.run_id && (
                          <Button size="sm" variant="ghost"
                                  onClick={() => navigate(`/runs/${encodeURIComponent(it.run_id!)}`)}>
                            <Play size={14} aria-hidden /> Open run
                          </Button>
                        )}
                      </div>
                    )}

                    {it.kind === "question" && (
                      <div className="chat-inbox__actions">
                        {it.options.length > 0 ? (
                          it.options.map((o) => (
                            <Button key={o} size="sm" variant="ghost"
                                    onClick={() => void resolve(it.id, o, false)}>
                              {o}
                            </Button>
                          ))
                        ) : (
                          <>
                            <Input
                              value={answers[it.id] || ""}
                              onChange={(e) => setAnswer(it.id, e.target.value)}
                              placeholder="Answer…"
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && (answers[it.id] || "").trim()) {
                                  void resolve(it.id, answers[it.id].trim(), false);
                                }
                              }}
                            />
                            <Button
                              size="sm"
                              variant="primary"
                              disabled={!(answers[it.id] || "").trim() || busy === it.id}
                              onClick={() => void resolve(it.id, (answers[it.id] || "").trim(), false)}
                            >
                              Send
                            </Button>
                          </>
                        )}
                      </div>
                    )}

                    {it.kind === "review" && (
                      <div className="chat-inbox__actions">
                        {/* "Approve", not "approved". The backend reads this
                            answer to decide whether to perform the write the
                            review proposed; when the two strings drifted apart
                            the row closed and nothing was written. The backend
                            accepts both spellings now, and a backend test pins
                            it, but this is still the word the option lists
                            elsewhere in the inbox use. */}
                        <Button size="sm" variant="primary" onClick={() => void resolve(it.id, "Approve", false)}>
                          <Check size={14} aria-hidden /> Approve
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => void resolve(it.id, "Reject", false)}>
                          Reject
                        </Button>
                      </div>
                    )}

                    <div className="chat-inbox__actions">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy === it.id}
                        onClick={() => void snooze(it.id, "tomorrow")}
                      >
                        <Clock size={14} aria-hidden /> Snooze until tomorrow
                      </Button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {(overflow > 0 || elsewhere > 0) && (
        <button type="button" className="chat-inbox__more" onClick={onOpenGlobal}>
          {overflow > 0 && `${overflow} more here`}
          {overflow > 0 && elsewhere > 0 && " · "}
          {elsewhere > 0 && `${elsewhere} in other scopes`}
          <ChevronRight size={13} strokeWidth={1.8} aria-hidden />
        </button>
      )}
    </>
  );
}
