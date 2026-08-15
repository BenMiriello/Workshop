import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Ticket as TicketGlyph } from "lucide-react";
import {
  Button, Card, Clamp, Notice, Prop, Props, SectionLabel, Skeleton, StatusDot,
  StatusPill,
} from "@workshop/ui";
import { jobIsLive, runHref } from "../../../lib/jobs";
import { shortHttpError } from "../../../lib/httpError";
import { ticketHref, type Task } from "../../../lib/tasks";
import type { Pickup } from "../../../lib/scopePickup";
import {
  DELETED_TEXT, TICKET_CARD_ACTION_LABEL, receiptText, ticketCardPlan,
  type TicketCardNote,
} from "./ticketCard";
import type { InboxItem, WorkTicket } from "./workTypes";

// THE TICKET CARD IN THE ANSWER. One ticket, the work it names, and how that
// work is going, rendered where the filing happened.
//
// It replaces two surfaces at once: the one-line "Filed a ticket in X" heading
// this card used to show, and the `FiledBar` above the transcript that said the
// same thing a second time. The bar is gone; a filing is a thing that happened
// in the conversation, so it is reported in the conversation.
//
// WHAT RENDERS IS DECIDED IN `ticketCard.ts`, not here. This file is the
// markup for a plan; the ten states it can be in are a pure function with a
// test, because there is no DOM harness in this repo and a state nobody can run
// is a state nobody has checked.
//
// THE COLLAPSED HEADER AND A PANEL ROW ARE ONE OBJECT SEEN TWICE, and this is
// how they stay one. It is deliberately NOT `TicketRow`: a row is a Link whose
// whole line navigates, and a card header is a disclosure that opens the card
// under it, so sharing the component would mean a control that does two
// incompatible things. What IS shared is everything that carries meaning: the
// same title ink and truncation (`chat-workpanel__title`), the same status
// vocabulary (`taskStatusKind` / `taskStatusLabel`), and the toolbar's own
// ticket glyph. The plan (§4) states the two compositions: a row is dot, key,
// title and the state word; a collapsed card is glyph, title, pill and chevron.
//
// ONE READER, WITH ONE FALLBACK. Every card reads the chat's shared work store
// through the context below rather than polling for its own ticket. Three cards
// polling a ticket and a run each would be eight requests describing overlapping
// facts, and the badge, a panel row and a card would be free to disagree for
// twenty seconds at a time. The single exception is the plan's own: a card whose
// ticket is absent from the payload does ONE direct read to find out whether it
// is late or gone.

/** The slice of the chat's work store a card needs.
 *
 *  Narrow on purpose. The store's object identity changes on every render of
 *  the chat, so handing the whole thing to a context would re-render every card
 *  in the transcript on every keystroke, which is the render chain this app has
 *  already been bitten by. Each field here is stable until the data actually
 *  moves. */
export type ChatWorkAccess = {
  ticket: (id: string) => WorkTicket | undefined;
  inbox: InboxItem[];
  /** Nothing has been read yet, as distinct from having read an empty payload. */
  cold: boolean;
  reload: () => void;
  /** Opens the Work panel on one inbox item. */
  openQuestion: (itemId: string) => void;
};

const ChatWorkContext = createContext<ChatWorkAccess | null>(null);
export const ChatWorkProvider = ChatWorkContext.Provider;

/** One second, and only while this card's run is actually going. A card with a
 *  finished run arms no timer at all. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

type Probe = { key: string; ticket: WorkTicket | null; deleted: boolean; error: string | null };

/** The one line under the card.
 *
 *  CLIENT-RENDERED FROM TRACKED STATE, and that is the point: an agent's
 *  question does not land in the transcript (D4), so this says it from the
 *  store instead of a message being written into the log. */
function CardNote({ note, onAnswer }: {
  note: TicketCardNote;
  onAnswer?: (itemId: string) => void;
}) {
  return (
    <p className="chat-ticket-card__note">
      {note.text}
      {note.kind === "waiting" && onAnswer && (
        <>
          {". "}
          <button
            type="button"
            className="chat-ticket-card__answer"
            onClick={() => onAnswer(note.itemId)}
          >
            Answer
          </button>
        </>
      )}
    </p>
  );
}

export default function TicketCard({
  id, scope, filedTitle, filedNote, pickup,
}: {
  id: string;
  /** Where the server said it filed, which is not always where it was asked to. */
  scope: string;
  /** The title the filing recorded, so a cold card still names its subject. */
  filedTitle: string;
  /** The one fact about the filing the ticket cannot re-derive, or null. */
  filedNote: string | null;
  pickup: Pickup | null;
}) {
  const access = useContext(ChatWorkContext);
  const [open, setOpen] = useState(false);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);

  const stored = access?.ticket(id);
  const probeKey = `${scope}|${id}`;
  // Without a provider there is no shared store to wait for, so read directly.
  const cold = access ? access.cold : false;
  const probedRef = useRef<string | null>(null);

  // A card whose ticket is absent from the shared payload reads it directly,
  // ONCE. That is the plan's only second reader, and it is what tells a ticket
  // that has not been polled yet apart from one that has been deleted.
  //
  // The "already asked" mark is a REF, not state, and `access` is deliberately
  // not a dependency. Both for the same reason: the store hands out a new
  // object on every poll, so either one would re-run this effect while the
  // fetch was in flight and its own cleanup would cancel the answer.
  useEffect(() => {
    if (stored || cold || !id) return;
    if (probedRef.current === probeKey) return;
    probedRef.current = probeKey;
    let cancelled = false;
    fetch(`/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(id)}`)
      .then(async (r) => {
        if (cancelled) return;
        if (r.status === 404) {
          setProbe({ key: probeKey, ticket: null, deleted: true, error: null });
          return;
        }
        if (!r.ok) {
          setProbe({
            key: probeKey, ticket: null, deleted: false,
            error: shortHttpError(r.status, r.statusText, await r.text().catch(() => "")).trim(),
          });
          return;
        }
        const task = (await r.json()) as Task;
        // No run: this route does not carry one. The shared payload does, and
        // it is the next poll away.
        setProbe({ key: probeKey, ticket: { ...task, run: null }, deleted: false, error: null });
      })
      .catch((e) => {
        if (!cancelled) {
          setProbe({ key: probeKey, ticket: null, deleted: false, error: String(e) });
        }
      });
    return () => { cancelled = true; };
  }, [stored, cold, id, scope, probeKey]);

  // The store answered, so the direct read is spent. Releasing the mark is what
  // lets a ticket deleted while the chat is open be re-read and reported,
  // instead of the card holding the last copy it saw forever.
  useEffect(() => {
    if (!stored) return;
    probedRef.current = null;
    setProbe((p) => (p ? null : p));
  }, [stored]);

  const ticket = stored ?? probe?.ticket ?? null;
  const inbox = access?.inbox;
  const waiting = useMemo(
    () => inbox?.find((i) => i.ticket_id === id) ?? null,
    [inbox, id],
  );
  const nowMs = useNow(jobIsLive(ticket?.run?.status));

  const plan = ticketCardPlan({
    id,
    scope,
    ticket,
    filedTitle,
    deleted: probe?.deleted ?? false,
    // ONLY THIS CARD'S OWN READ. The shared store's `err` also carries a refused
    // inbox write, and reporting that as "couldn't read this ticket" would blame
    // the wrong thing; the panel says it where it happened.
    error: probe?.error ?? null,
    pickup,
    waiting,
    filedNote,
    nowMs,
  });

  const ticketScope = ticket?.scope || scope;
  const reload = access?.reload;

  const runNow = useCallback(async () => {
    setRunBusy(true);
    setRunErr(null);
    try {
      const r = await fetch(
        `/api/tasks/${encodeURIComponent(ticketScope)}/${encodeURIComponent(id)}/run`,
        { method: "POST" },
      );
      if (!r.ok) {
        setRunErr(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")).trim());
      } else {
        reload?.();
      }
    } catch (e) {
      setRunErr(String(e));
    } finally {
      setRunBusy(false);
    }
  }, [ticketScope, id, reload]);

  return (
    <>
      <Card className="chat-ticket-card">
        {/* A LINE IN A PARAGRAPH OF PROSE. Glyph, title, one pill, chevron, and
            nothing else: this sits inside an answer and has to read as part of
            the sentence that produced it. */}
        <button
          type="button"
          className="chat-ticket-card__head"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <TicketGlyph size={14} strokeWidth={1.8} aria-hidden className="chat-ticket-card__glyph" />
          <span className="ws-truncate chat-workpanel__title">{plan.title}</span>
          {plan.status && (
            <StatusPill status={plan.status.kind}>{plan.status.label}</StatusPill>
          )}
          <ChevronRight
            size={14}
            strokeWidth={1.8}
            aria-hidden
            className={`chat-ticket-card__caret${open ? " is-open" : ""}`}
          />
        </button>

        {/* A LINE, NOT A REGION. The card around it is still on screen, so a
            deleted ticket and a failed read are both sentences beside content
            rather than a 120px dashed panel in the middle of an answer. */}
        {plan.phase === "deleted" && <Notice tone="warn">{DELETED_TEXT}</Notice>}
        {plan.problem && (
          <Notice
            tone="error"
            detail={plan.problem}
            action={reload && (
              <Button size="sm" variant="ghost" onClick={() => reload()}>Retry</Button>
            )}
          >
            Couldn't read this ticket.
          </Notice>
        )}
        {/* A refused run is its own sentence. It says what did not happen, not
            what could not be read, because those have different answers. */}
        {runErr && <Notice tone="error" detail={runErr}>Couldn't start a run.</Notice>}

        {open && plan.phase !== "deleted" && (
          <div className="chat-ticket-card__body">
            {plan.phase === "cold" && <Skeleton width="70%" />}

            {plan.groups.includes("task") && (
              <section className="chat-ticket-card__group">
                <SectionLabel>Task</SectionLabel>
                {/* Clamped: a distilled spec is a page of text, and a card in a
                    transcript must not become one. */}
                <Clamp max={160}>
                  <p className="chat-ticket-card__task">{plan.body}</p>
                </Clamp>
              </section>
            )}

            {plan.groups.includes("ticket") && (
              <section className="chat-ticket-card__group">
                <SectionLabel>Ticket</SectionLabel>
                {/* `Props`, not a column of `Field`s: these are read here and
                    edited on the ticket page, and form ink on a read-only value
                    is what made every rail look like a disabled form. */}
                <Props>
                  {plan.props.map((p) => {
                    if (p.key === "key") {
                      return (
                        <Prop key={p.key} label={p.label}>
                          <span className="ws-muted">{p.scope}</span>
                          {" / "}
                          <span className="ws-ticket-key">{p.text}</span>
                        </Prop>
                      );
                    }
                    if (p.key === "blocked_by") {
                      return (
                        <Prop key={p.key} label={p.label}>
                          {p.links.map((l, i) => (
                            <span key={l.id}>
                              {i > 0 && ", "}
                              <Link to={ticketHref(l.scope, l.id)}>{l.title || l.id.slice(0, 6)}</Link>
                            </span>
                          ))}
                        </Prop>
                      );
                    }
                    if (p.key === "labels") {
                      return <Prop key={p.key} label={p.label}>{p.labels.join(", ")}</Prop>;
                    }
                    return <Prop key={p.key} label={p.label}>{p.text}</Prop>;
                  })}
                </Props>
              </section>
            )}

            {plan.run && (
              <section className="chat-ticket-card__group">
                {/* THE RUN'S OWN SECTION, labelled, because its status is not the
                    ticket's. ADR 0009 allows two entities in one view only when
                    they are separated like this, and never in the same row. */}
                <SectionLabel>Run</SectionLabel>
                <div className="chat-ticket-card__run">
                  <StatusDot status={plan.run.kind}>{plan.run.label}</StatusDot>
                  <span className="chat-ticket-card__meta">
                    {[plan.run.agent, plan.run.trigger].filter(Boolean).join(" · ")}
                  </span>
                  {plan.run.clock && (
                    <span className={plan.run.clock.live ? "chat-ticket-card__elapsed" : "chat-ticket-card__meta"}>
                      {plan.run.clock.text}
                    </span>
                  )}
                </div>
                {plan.run.receipt && (
                  <div className="chat-ticket-card__meta">{receiptText(plan.run.receipt)}</div>
                )}
              </section>
            )}

            {plan.actions.length > 0 && (
              <div className="chat-ticket-card__actions">
                {plan.actions.map((a) => {
                  if (a === "open") {
                    return (
                      <Link
                        key={a}
                        className="ws-btn ws-btn--ghost ws-btn--sm"
                        to={ticketHref(ticketScope, id)}
                      >
                        {TICKET_CARD_ACTION_LABEL.open}
                      </Link>
                    );
                  }
                  if (a === "viewRun") {
                    return plan.run ? (
                      <Link
                        key={a}
                        className="ws-btn ws-btn--ghost ws-btn--sm"
                        to={runHref(plan.run.id)}
                      >
                        {TICKET_CARD_ACTION_LABEL.viewRun}
                      </Link>
                    ) : null;
                  }
                  return (
                    <Button
                      key={a}
                      size="sm"
                      variant="ghost"
                      loading={runBusy}
                      disabled={runBusy}
                      onClick={() => void runNow()}
                    >
                      {TICKET_CARD_ACTION_LABEL.runNow}
                    </Button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </Card>

      {plan.note && <CardNote note={plan.note} onAnswer={access?.openQuestion} />}
    </>
  );
}
