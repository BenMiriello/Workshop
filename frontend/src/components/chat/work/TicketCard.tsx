import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { Link } from "react-router-dom";
import {
  ArrowUpRight, ChevronRight, FileDiff, Ticket as TicketGlyph,
} from "lucide-react";
import { Button, Card, Clamp, Notice, Skeleton, StatusDot, StatusPill, Tag, catFor, shortHttpError } from "@workshop/ui";
import { AgentAvatar } from "./AgentAvatar";
import { jobIsLive, runHref } from "../../../lib/jobs";

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
      <Card className="chat-card chat-ticket-card">
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
                {/* SENTENCE CASE, not the app's uppercase `SectionLabel` motif.
                    The 4a design writes these three as ordinary words, and this
                    card is a dense object inside a paragraph of prose rather
                    than a page section. Operator-directed, 2026-08-16, and it
                    applies to this card and the question view only: nothing
                    else changes and `SectionLabel` itself is untouched. */}
                <span className="chat-ticket-card__label">Task</span>
                {/* Clamped: a distilled spec is a page of text, and a card in a
                    transcript must not become one. */}
                <Clamp max={160}>
                  <p className="chat-ticket-card__task">{plan.body}</p>
                </Clamp>
              </section>
            )}

            {plan.groups.includes("ticket") && (
              <section className="chat-ticket-card__group">
                <span className="chat-ticket-card__label">Ticket</span>
                {/* AN INSET PANEL, not a `Props` list. The fields are read here
                    and edited on the ticket page, so they keep read-only ink;
                    what the panel adds is that they read as one object sitting
                    inside the card rather than as four loose lines of it. */}
                <div className="chat-ticket-card__inset">
                  {plan.props.map((p) => {
                    if (p.key === "key") {
                      return (
                        <div key={p.key} className="chat-ticket-card__field">
                          <span className="chat-ticket-card__fieldlabel">{p.label}</span>
                          <span className="chat-ticket-card__fieldvalue">
                            {p.scope}
                            {" / "}
                            <span className="ws-ticket-key">{p.text}</span>
                          </span>
                        </div>
                      );
                    }
                    if (p.key === "blocked_by") {
                      return (
                        <div key={p.key} className="chat-ticket-card__field">
                          <span className="chat-ticket-card__fieldlabel">{p.label}</span>
                          <span className="chat-ticket-card__fieldvalue">
                            {p.links.map((l, i) => (
                              <span key={l.id}>
                                {i > 0 && ", "}
                                <Link to={ticketHref(l.scope, l.id)}>
                                  {l.title || l.id.slice(0, 6)}
                                </Link>
                              </span>
                            ))}
                          </span>
                        </div>
                      );
                    }
                    if (p.key === "labels") {
                      return (
                        <div key={p.key} className="chat-ticket-card__field">
                          <span className="chat-ticket-card__fieldlabel">{p.label}</span>
                          {/* The design draws these as dot-chips, which is what
                              `Tag` already is: a categorical identity, hued by
                              `catFor` so one label keeps one colour everywhere. */}
                          <span className="chat-ticket-card__tags">
                            {p.labels.map((l) => (
                              <Tag key={l} hue={catFor(l)} variant="neutral">{l}</Tag>
                            ))}
                          </span>
                        </div>
                      );
                    }
                    return (
                      <div key={p.key} className="chat-ticket-card__field">
                        <span className="chat-ticket-card__fieldlabel">{p.label}</span>
                        <span className="chat-ticket-card__fieldvalue">{p.text}</span>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {plan.run && (
              <section className="chat-ticket-card__group">
                {/* THE RUN'S OWN SECTION, labelled, because its status is not the
                    ticket's. ADR 0009 allows two entities in one view only when
                    they are separated like this, and never in the same row. */}
                <span className="chat-ticket-card__label">Run</span>
                <div className="chat-ticket-card__inset">
                  {/* THE AGENT, THEN THE HANDLE, THEN THE CLOCK PUSHED RIGHT,
                      exactly as 4a draws it. The state word sits before the
                      clock because it is the RUN's, and ADR 0009 keeps it out
                      of the header where the ticket's own status lives. */}
                  <div className="chat-ticket-card__runline">
                    <AgentAvatar name={plan.run.agent} />
                    <span className="chat-ticket-card__agent">{plan.run.agent || "an agent"}</span>
                    {/* The design's `8f2c`. We have no run numbering either, so
                        this is the same six-hex handle `ticketKey` gives a
                        ticket, for one vocabulary of short ids. */}
                    <span className="ws-ticket-key chat-ticket-card__runid">
                      {plan.run.id.slice(0, 6)}
                    </span>
                    <span className="chat-ticket-card__runspacer" />
                    <StatusDot status={plan.run.kind}>{plan.run.label}</StatusDot>
                    {plan.run.clock && (
                      <span className={plan.run.clock.live ? "chat-ticket-card__elapsed" : "chat-ticket-card__meta"}>
                        {plan.run.clock.text}
                      </span>
                    )}
                  </div>
                  {/* THE DESIGN'S SECOND LINE IS A LIVE TOOL CALL, and there is
                      no such data: tool events belong to a chat turn, a job
                      record carries no step list, and there is no run stream to
                      read one from. The receipt is what we do have about the
                      same work, so it takes the slot. Deviation 1 of the plan. */}
                  {plan.run.receipt && (
                    <div className="chat-ticket-card__runstep">
                      <FileDiff size={12} strokeWidth={1.8} aria-hidden />
                      {receiptText(plan.run.receipt)}
                    </div>
                  )}
                </div>
              </section>
            )}

            {plan.actions.length > 0 && (
              <div className="chat-ticket-card__actions">
                {/* CHIPS, not ghost buttons. A ghost button is an outline, and
                    four outlined rectangles at the foot of a card inside a
                    paragraph read as a form. The two that navigate carry the
                    arrow glyph 4a draws on them; `Run now` does not, because it
                    writes rather than going anywhere. */}
                {plan.actions.map((a) => {
                  if (a === "open") {
                    return (
                      <Link
                        key={a}
                        className="chat-ticket-card__chip"
                        to={ticketHref(ticketScope, id)}
                      >
                        {TICKET_CARD_ACTION_LABEL.open}
                        <ArrowUpRight size={13} strokeWidth={1.8} aria-hidden />
                      </Link>
                    );
                  }
                  if (a === "viewRun") {
                    return plan.run ? (
                      <Link
                        key={a}
                        className="chat-ticket-card__chip"
                        to={runHref(plan.run.id)}
                      >
                        {TICKET_CARD_ACTION_LABEL.viewRun}
                        <ArrowUpRight size={13} strokeWidth={1.8} aria-hidden />
                      </Link>
                    ) : null;
                  }
                  return (
                    <button
                      key={a}
                      type="button"
                      className="chat-ticket-card__chip"
                      disabled={runBusy}
                      onClick={() => void runNow()}
                    >
                      {runBusy ? "Starting" : TICKET_CARD_ACTION_LABEL.runNow}
                    </button>
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
