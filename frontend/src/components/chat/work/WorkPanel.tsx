import {
  useCallback, useEffect, useRef, useState,
} from "react";
import { Link } from "react-router-dom";
import { ChevronRight, ListPlus, Settings2 } from "lucide-react";
import {
  FilterChip, Skeleton, Tag, catFor,
} from "@workshop/ui";
import { jobIsLive } from "../../../lib/jobs";
import { scopeName } from "../chatScope";
import { chatModeDef, type ChatModeId } from "../chatModes";
import ChatProblemBlock from "../ChatProblemBlock";
import QuestionView from "./QuestionView";
import { NeedsYouRow, TicketRow } from "./TicketRow";
import WorkSettings from "./WorkSettings";
import type { ChatWork } from "./useChatWork";
import {
  WORK_CHIPS, chipCount, defaultChip, emptyText, workList, type WorkChip,
} from "./workRows";

// THE CHAT'S ONE WORK SURFACE. Everything that is waiting, running, or was filed
// from this conversation, plus the policy that decides where the next piece of
// work goes. It replaces `WorkDrawer` and `InboxStrip`, and the decisions those
// two recorded are carried forward here rather than rediscovered:
//
//   THE BADGE COUNTS WHAT NEEDS THE OPERATOR, AND NOTHING ELSE. An open-ticket
//   count is ambient information wearing an alarm's clothing, and a badge is an
//   alarm. Forty open tickets is the normal state of a working queue, and a
//   permanent "40" on the toolbar teaches the operator to stop reading it.
//
//   THE TICKET ROWS ARE READ-ONLY, and that loses nothing: TicketDetail edits
//   every field the old inline drawer could, so a row here is a link to the
//   place that edits it. `TaskPanel` still serves the agent detail page with
//   create, delete and the review verbs; the chat simply stopped using it.
//
//   AN INTERRUPT SURFACE IS BOUNDED IN EVERY DIRECTION, or it stops being a
//   signal and becomes the page. Count (see MAX_ROWS in workRows), height (the
//   sheet caps at 40vh), and row height: a row is one line
//   plus one line of meta, never more.
//
//   A HALF-TYPED ANSWER SURVIVES CLOSING, because it lives in the hook keyed by
//   item id. That is what lets this close on an outside click with no confirm.
//
// ONE HOST: the sheet over the log, mounted inside .chat-log-wrap, at every
// width. It used to also render as a Popover anchored to the toolbar button on
// wide screens; that second host was retired 2026-08-22 because the mobile
// drawer is the correct design and the desktop dropdown off the right edge was
// a degradation.
//
// TOP-ANCHORED IS DELIBERATE AND MUST NOT BECOME A BOTTOM SHEET. This panel
// contains a text field, and an on-screen keyboard shrinks the viewport from the
// bottom: a bottom sheet with a field in it spends its life being pushed around
// by the keyboard it raises. It also floats OVER the log rather than pushing it
// down, which is the older half of the same rule.

export type WorkPanelProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Open ON this inbox item rather than on the list. Set when something
   *  outside the panel named a question: a ticket card's Answer link is the
   *  only caller today. Null is the ordinary case, and the caller clears it as
   *  the panel closes so reopening lands on the list again. */
  focusItem?: string | null;
  work: ChatWork;
  /** The chat's task scope, or null for a chat with no queue. */
  scope: string | null;
  chatMode: ChatModeId;
  onChooseChatMode: (mode: ChatModeId) => void;
  /** Opens the ticket drafts dialog. Absent on a chat with no thread yet. */
  onFileTicket?: () => void;
  onNavigate: (to: string) => void;
};

export default function WorkPanel(props: WorkPanelProps) {
  const { open, onOpenChange } = props;
  const sheetRef = useRef<HTMLDivElement>(null);

  // Escape closes and hands focus back to the button that opened it. Capture,
  // so it still fires from inside the answer field.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      // Read the button BEFORE closing: the sheet is what scopes the lookup to
      // this chat, and it is about to unmount.
      const back = sheetRef.current
        ?.closest(".chat-panel")
        ?.querySelector<HTMLElement>("[data-chat-workpanel-trigger]") ?? null;
      onOpenChange(false);
      back?.focus();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onOpenChange]);

  if (!open) return null;
  return (
    <>
      {/* Behind the panel, over the log. Dismisses on a press. */}
      <div
        className="chat-workpanel__scrim"
        aria-hidden
        onPointerDown={() => onOpenChange(false)}
      />
      <div ref={sheetRef} className="chat-workpanel chat-workpanel--sheet" role="dialog" aria-label="Tickets">
        <WorkBody {...props} />
      </div>
    </>
  );
}

// The elapsed clock on a running row. ONE interval for the whole panel, armed
// only while something is actually live: a second-by-second re-render of a panel
// where nothing is moving is pure cost, and the panel only exists while it is
// open, so there is nothing to pause when it is not.
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

function WorkBody({
  work, scope, chatMode, onChooseChatMode, onFileTicket, onNavigate, onOpenChange,
  focusItem,
}: WorkPanelProps) {
  const [view, setView] = useState<"list" | "settings">("list");
  const [questionId, setQuestionId] = useState<string | null>(focusItem ?? null);
  // Null until the operator picks one, so the default can follow the first
  // payload in rather than being computed against the zeroes that are on screen
  // before it lands.
  const [chip, setChip] = useState<WorkChip | null>(null);
  const { data, cold, loading, err } = work;
  const counts = data.counts;
  const selected = chip ?? defaultChip(counts);

  useEffect(() => {
    if (cold || chip !== null) return;
    setChip(defaultChip(counts));
  }, [cold, chip, counts]);

  // Named from outside while the panel was ALREADY open. The initial state
  // above covers the ordinary case (the body mounts with the panel), and this
  // covers a card's Answer link pressed with the panel sitting on its list.
  useEffect(() => {
    if (!focusItem) return;
    setQuestionId(focusItem);
    setView("list");
  }, [focusItem]);

  const close = useCallback(() => onOpenChange(false), [onOpenChange]);
  const openSettings = useCallback(() => { setQuestionId(null); setView("settings"); }, []);
  const backToList = useCallback(() => { setQuestionId(null); setView("list"); }, []);

  // Read from the whole payload rather than from the rows on screen, so the
  // clock also ticks for the one ticket a question view is about. Above the
  // branches below because a hook has to be.
  const nowMs = useNow(work.tickets.some((t) => jobIsLive(t.run?.status)));

  // A LINE, NOT A REGION, and it is on every view. The rows are still on screen
  // (a failed poll keeps the last good state), and a dashed 120px panel inside a
  // 360px popover is the exact shape the operator objected to. It carries no
  // endpoint: the only move here is Retry, and naming the path was two thirds of
  // the notice's length the last time it was tried.
  //
  // It reports a REFUSED WRITE as well as a dropped poll. Answering removes the
  // row optimistically and moves on, so without this a refused answer would look
  // like an accepted one until the row silently came back.
  const problem = err
    ? <ChatProblemBlock problem={{ text: err, retry: work.reload }} />
    : null;

  const item = questionId ? data.inbox.find((i) => i.id === questionId) : undefined;
  if (item) {
    return (
      <>
        {problem}
        <QuestionView
          work={work}
          items={data.inbox}
          item={item}
          scope={scope}
          nowMs={nowMs}
          onSelect={setQuestionId}
          onBack={backToList}
          onClose={close}
          onNavigate={onNavigate}
        />
      </>
    );
  }

  if (view === "settings") {
    return (
      <>
        {problem}
        <WorkSettings
          scope={scope}
          chatMode={chatMode}
          onChooseChatMode={onChooseChatMode}
          onBack={backToList}
        />
      </>
    );
  }

  const list = workList(data, selected);
  const board = selected === "needs_you"
    // What the inbox tab used to list is blocked work across every scope, which
    // the one ticket list answers with a filter. A link onto a real query beats
    // a fourth surface, and that was the drawer's decision too.
    ? { to: "/tickets?status=blocked", label: "Open blocked tickets" }
    : scope
      ? {
          to: `/work?scope=${encodeURIComponent(scope)}`,
          label: `Open ${scopeName(scope)} board`,
        }
      : { to: "/tickets", label: "Open the board" };

  return (
    <>
      <div className="chat-workpanel__head">
        <span className="chat-workpanel__heading">Tickets</span>
        {scope && (
          <Tag hue={catFor(scope)} variant="neutral">{scopeName(scope)}</Tag>
        )}
        <span className="chat-workpanel__spacer" />
        {/* Filing this chat as a ticket used to sit in the three-dot menu, two
            controls away from the list of what it produces. It is an action on
            this chat's work, so it lives with this chat's work. */}
        {onFileTicket && (
          <button
            type="button"
            className="chat-workpanel__iconbtn"
            // The drafts dialog is a modal over the whole chat. Leaving the
            // panel open behind it would put two surfaces about the same work
            // on screen, one of them unreachable.
            onClick={() => { close(); onFileTicket(); }}
            title="File this chat as a ticket"
            aria-label="File this chat as a ticket"
          >
            <ListPlus size={14} strokeWidth={1.8} aria-hidden />
          </button>
        )}
        <button
          type="button"
          className="chat-workpanel__iconbtn"
          onClick={openSettings}
          title="Ticket settings"
          aria-label="Ticket settings"
        >
          <Settings2 size={14} strokeWidth={1.8} aria-hidden />
        </button>
      </div>

      {/* All four, always, including the zeroes. They are the chat's summary of
          its own work, and dropping an empty one would slide the other three
          sideways the moment an agent raised something. */}
      <div className="chat-workpanel__chips" role="group" aria-label="Which tickets to show">
        {WORK_CHIPS.map((c) => (
          <FilterChip
            key={c.id}
            className={`chat-workpanel__chip${c.id === selected ? " is-on" : ""}`}
            label={c.label}
            value={chipCount(counts, c.id)}
            onClick={() => setChip(c.id)}
          />
        ))}
      </div>

      {/* ONLY THE LIST SCROLLS. The head, the chips and the footer are the
          panel's chrome and stay put, because the footer carries the policy and
          the way to change it: with the whole panel scrolling, a scope with more
          than a handful of tickets pushed both below the fold, where nothing
          announces them and nobody scrolls a popover to look. Seen on the real
          screen at 21 tickets. */}
      <div className="chat-workpanel__scroll">
      {problem}

      {cold && loading ? (
        <div className="chat-workpanel__loading">
          <Skeleton /><Skeleton /><Skeleton />
        </div>
      ) : list.groups.length === 0 ? (
        // Prose, not a StateBlock. The panel is a 360px surface and the sentence
        // says what puts something here, which is the whole job of an empty
        // state; a bordered region around one sentence is the "huge and blocky"
        // shape this app spent a pass removing.
        <p className="chat-workpanel__empty">{emptyText(selected)}</p>
      ) : (
        list.groups.map((g) => (
          <div key={g.key} className="chat-workpanel__group">
            {/* THE HEADING CARRIES ITS COUNT ("This chat · 3"), which is what
                4a draws and what makes a heading worth its line: the chip above
                already named the filter, so the group's own size is the only
                thing left for it to say. A count of rows on screen, not of the
                payload: the row cap is stated by the board link below. */}
            {list.headed && (
              <div className="chat-workpanel__grouplabel">
                {g.label} · {g.rows.length}
              </div>
            )}
            <ul className="chat-workpanel__list">
              {g.rows.map((row) => (
                <li key={row.id} className="chat-workpanel__item">
                  {row.kind === "item" ? (
                    <NeedsYouRow item={row.item} onSelect={setQuestionId} />
                  ) : (
                    <TicketRow
                      ticket={row.ticket}
                      fallbackScope={scope}
                      nowMs={nowMs}
                      onFollow={close}
                    />
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}

      {/* The overflow state, and it belongs to the list it overflows. A popover
          does not paginate: the rest is one tap away on the board. */}
      <Link className="chat-workpanel__more" to={board.to} onClick={close}>
        {list.overflow > 0 ? `${board.label} (${list.overflow} more)` : board.label}
        <ChevronRight size={13} strokeWidth={1.8} aria-hidden />
      </Link>
      </div>

      {/* The policy in words, and the second way into the settings. Two ways in
          is deliberate: the gear is where you look for settings, and this is
          where you are when you find out the policy is not what you wanted. */}
      <div className="chat-workpanel__foot">
        {/* THE VALUE ALONE. "When work comes up:" was the question this answers,
            and the footer of a panel about work is not a place that question
            needs asking: 4a prints the setting and the way to change it. */}
        <span className="chat-workpanel__note">{chatModeDef(chatMode).action}</span>
        <button type="button" className="chat-workpanel__change" onClick={openSettings}>
          Change
        </button>
      </div>
    </>
  );
}
