import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Check, X, MessageCircleQuestion, ClipboardCheck, Info, ChevronRight,
  ListTodo, Play,
} from "lucide-react";
import { Button, Input, StateBlock } from "../../ui";
import { ticketHref } from "../../lib/tasks";

// Items an agent raised that need the operator.
//
// NOT typed by entity. A finished run, a blocked agent and a proposal awaiting
// approval are one kind of thing (something wants you) with three verbs, which
// is what makes a single list coherent instead of a pile of mixed nouns:
//
//   question  the agent is stuck and is asking rather than guessing  -> answer
//   review    the agent proposes something                           -> approve / reject
//   notify    FYI, safe to ignore                                    -> dismiss
//
// SHAPE. This is a panel behind a toolbar toggle, not an inline banner. The
// first cut rendered every open item inline at full size, always: on a phone
// that pushed the log and the composer off-screen entirely, and with no cap it
// grew without bound. An interrupt surface has to be bounded in every
// direction — count, height, and row height — or it stops being a signal and
// becomes the page.
//
// Even when closed the component stays mounted, polling and reporting its count
// upward, so the toolbar badge is live without the operator opening anything to
// find out whether it should be.
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
};

type Counts = { notify: number; question: number; review: number };

const KIND_ICON = {
  question: MessageCircleQuestion,
  review: ClipboardCheck,
  notify: Info,
} as const;

// At most this many rows are rendered. The rest are reachable through the
// footer link rather than by scrolling a list that has no natural end.
const MAX_ROWS = 4;

export default function InboxStrip({
  chatId,
  open,
  onCountChange,
  onClose,
  onOpenGlobal,
}: {
  chatId: string | null;
  open: boolean;
  onCountChange: (n: number) => void;
  onClose: () => void;
  onOpenGlobal: () => void;
}) {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [globalCounts, setGlobalCounts] = useState<Counts | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // One row at a time is expanded. A collapsed row is one line; expanding is
  // how you read the detail and reach the actions. Without this every row
  // carries its body and its buttons and four of them fill a phone screen.
  const [expanded, setExpanded] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    const q = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : "";
    try {
      const r = await fetch(`/api/inbox${q}`);
      if (!r.ok) { setErr(`/api/inbox → ${r.status}`); setItems([]); return; }
      const j = await r.json();
      setErr(null);
      setItems(j.items || []);
      setGlobalCounts(j.global_counts || null);
    } catch (e) {
      // A failed poll leaves the last good state on screen rather than blanking
      // the panel: an inbox that empties itself on a network blip is worse than
      // one that is briefly stale.
      setErr(String(e));
      setItems((v) => v ?? []);
    }
  }, [chatId]);

  useEffect(() => {
    void load();
    // Agents act on their own schedule, so this polls. 20s is under the point
    // where the operator would notice a lag; it pauses while the tab is hidden.
    const t = window.setInterval(() => { if (!document.hidden) void load(); }, 20000);
    const onVis = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, [load]);

  useEffect(() => { onCountChange(items ? items.length : 0); }, [items, onCountChange]);

  // Escape closes, matching every other transient panel in the app.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const resolve = useCallback(async (id: string, answer: string | null, dismissed: boolean) => {
    setBusy(id);
    // Optimistic: the operator acted, so the row goes now. A re-fetch follows,
    // which corrects the rare case where the write failed.
    setItems((v) => (v || []).filter((i) => i.id !== id));
    setExpanded((e) => (e === id ? null : e));
    try {
      await fetch(`/api/inbox/${encodeURIComponent(id)}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer, dismissed }),
      });
    } finally {
      setBusy(null);
      void load();
    }
  }, [load]);

  // Mounted-but-closed is the normal state: it keeps the badge count live and
  // renders nothing.
  if (!open) return null;

  const total = items?.length ?? 0;
  const shown = (items || []).slice(0, MAX_ROWS);
  const overflow = total - shown.length;
  const globalTotal = globalCounts
    ? globalCounts.notify + globalCounts.question + globalCounts.review
    : 0;
  const elsewhere = Math.max(0, globalTotal - total);

  return (
    <div className="chat-inbox" role="region" aria-label="Agents needing you">
      {err && items && items.length === 0 ? (
        <StateBlock
          variant="error"
          title="Couldn't load the inbox"
          hint={<><code>{err}</code></>}
          action={<Button size="sm" variant="ghost" onClick={() => void load()}>Retry</Button>}
        />
      ) : items === null ? (
        <div className="chat-inbox__empty">Loading…</div>
      ) : total === 0 ? (
        <div className="chat-inbox__empty">
          Nothing waiting on you. Agents raise questions and reviews here.
        </div>
      ) : (
        <ul className="chat-inbox__list">
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
                              onChange={(e) => setAnswers((a) => ({ ...a, [it.id]: e.target.value }))}
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
                        <Button size="sm" variant="primary" onClick={() => void resolve(it.id, "approved", false)}>
                          <Check size={14} aria-hidden /> Approve
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => void resolve(it.id, "rejected", false)}>
                          Reject
                        </Button>
                      </div>
                    )}
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
    </div>
  );
}
