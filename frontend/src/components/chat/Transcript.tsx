import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Bot, ChevronRight, Wrench } from "lucide-react";
import { ChatMarkdown as Markdown } from "./ChatMarkdown";
import type { ChatSegment } from "../../lib/chat";
import ApprovalBlock from "./ApprovalBlock";
import { agentBrief, agentChipLine, agentSummary } from "./segments";
import {
  blockedLabel, buildTurnBlocks, groupCount, groupLabel, hadErrorResult, isRunning,
  runningSeconds, stepAlert, stepDuration, turnDuration, turnWorkSummary, workNarrationPreview,
  type TurnItem,
} from "./grouping";
import { openInitial, onLive, onToggle } from "./disclosure";
import { Reveal } from "./Reveal";
import { lineBottoms, cutHeight, worthClamping } from "./clampCut";
import { UiCard, UiCardPending, splitUiCards, hasUiCard } from "./UiCard";
import type { UiCardCtx } from "./uiCardActions";
import { modelDisplay, type ModelDisplay } from "./models";

// How a turn renders. Purely presentational: given segments, produce the
// transcript. No chat state, no fetching, no effects beyond local open/closed.
//
// Extracted from Chat.tsx. These five components are what the operator actually
// looks at, and they were buried under 1,600 lines of transport and lifecycle,
// so "change how a tool call looks" meant opening the file that owns the SSE
// reconnect budget.

// ---- Tool call: one bare row, tap to expand -------------------------------
// One dense row (icon · name · truncated input). Tapping reveals the full input
// (a command / path / pattern). Mobile-first: the preview truncates with an
// ellipsis so a tool call never blows out the bubble width.
//
// THE LEAF HAS NO BOX. It is the same row whether it stands alone in a run or
// sits inside an opened group, so a step does not change weight by being
// grouped. The only framed thing in the transcript is a delegation. See
// GroupChip for why.
//
// A MARK MEANS YOU HAVE TO DO SOMETHING, so most error results carry none.
// Operator, 2026-08-12: the old mark fired on the API's is_error flag, which on
// his real history was 90% nonzero exit codes (`git status` exiting 1 to say
// there are changes) and 100% recovered from. Exactly ONE thing is marked now:
// the harness REFUSED the capability, so the operator is the remedy. Everything
// else keeps its error text, visible on opening the row, and says nothing in the
// collapsed line.
//
// AND THE MARK IS NEVER RED. It names the remedy ("needs approval", "tool
// disabled", "denied") in warn, because this is waiting on you, not broken. The
// red "stopped here" that used to sit beside it is gone entirely; see
// grouping.ts for why the idea behind it could not be made to work.
//
// AND IT IS MARKED, NOT EXPANDED. This used to auto-open, which is the rule
// GitHub Actions and VS Code both follow. Wrong here: a turn routinely carries
// several results the assistant handled and moved past, and each one unrolling
// mid-scroll is a page of noise about something already dealt with.
//
// A step still running says how long it has been going, but only past the 10s
// attention limit, so a fast call never flickers a counter. See grouping.ts.
function ToolChip({ seg, nowMs, arrived }: {
  seg: Extract<ChatSegment, { t: "tool" }>; nowMs: number;
  /** Issued while the reader was already looking: earns the one-second flash. */
  arrived?: boolean;
}) {
  const alert = stepAlert(seg);
  const running = isRunning(seg);
  const [open, setOpen] = useState(false);
  const panelId = useId();
  // The error text is still the useful thing to show on opening, marked or not:
  // a step is opened to find out what came back, and on an error result that IS
  // what came back.
  const detail = hadErrorResult(seg) && seg.error ? seg.error : seg.input;
  const hasDetail = !!detail;
  const secs = runningSeconds(seg, nowMs);
  const took = stepDuration(seg);
  return (
    <div className={`chat-tool${open ? " is-open" : ""}${alert ? ` is-${alert}` : ""}${running ? " is-running" : ""}`}>
      <button
        type="button"
        className="chat-tool__head"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        aria-controls={hasDetail ? panelId : undefined}
        title={hasDetail ? detail : seg.name}
      >
        {hasDetail && <ChevronRight size={13} aria-hidden className="chat-tool__chev" />}
        {/* THE ARRIVAL FLASH, on the icon. LIVE-transient: this call was issued
            while you were watching, so the icon carries the live tone for one
            second and settles to neutral. It does not violate "a control is not
            content" (which bans an IDENTITY hue on an affordance): the icon is
            neutral at rest and this is the transient half of LIVE, which the
            design system already defines. Gated on time, never on mount, for the
            same reason `.ws-arrive` is: a re-attach replays the turn from offset
            0 and opening a settled group mounts every step at once, and a
            mount-gated flash strobes in both cases. */}
        <Wrench size={12} aria-hidden
                className={`chat-tool__icon${arrived ? " chat-tool__icon--arrive" : ""}`} />
        <span className="chat-tool__name">{seg.name}</span>
        {!!seg.input && <span className="chat-tool__preview ws-mono">{seg.input}</span>}
        {/* The clock rides on the STEP, not the turn. A single total for the
            whole turn reads "3h 41m" on a long one and describes nothing; what
            the reader wants to know is whether the thing happening now is
            stuck. */}
        {secs > 0 && <span className="chat-tool__time">{secs}s</span>}
        {!running && !!took && <span className="chat-tool__time">{took}</span>}
        {alert && <span className="chat-tool__alert">{blockedLabel(seg)}</span>}
      </button>
      <Reveal open={open && hasDetail} id={panelId}>
        <pre className="chat-tool__detail ws-mono">{detail}</pre>
      </Reveal>
    </div>
  );
}

/** WAS THIS STEP ISSUED WHILE THE READER WAS ALREADY LOOKING. The gate for the
 *  tool icon's arrival flash, and the exact counterpart of
 *  `useArrivedAfterMount` in MessageList.
 *
 *  It reads `seg.at`, which the backend sets when a call is issued and REMOVES
 *  when its result lands (see lib/chat.ts). That makes the gate self-limiting in
 *  a way a "new" flag would not be: a settled step carries no `at` at all, so
 *  history physically cannot flash, and only a call that is still open can.
 *  Comparing against mount time handles the rest: a call already running when
 *  the reader arrived is not news to them, and a replay from offset 0 re-sends
 *  calls whose start time is older than this mount. */
function useArrivedSteps(): (seg: ChatSegment) => boolean {
  const mountedAt = useRef(Date.now());
  return (seg: ChatSegment) =>
    seg.t === "tool" && typeof seg.at === "number" && seg.at * 1000 > mountedAt.current;
}

// A run of adjacent tool calls, behind one count.
//
// ONE BOX IN THE TRANSCRIPT, AND IT IS THE SUBAGENT. Operator, 2026-08-12,
// having seen both arrangements: originally the STEP carried the border and the
// inset background and the group carried nothing, which made one call look
// heavier than a run of them; then the frame moved to the group, which was the
// wrong fix. A frame is a strong claim, and only a delegation earns it, because
// only a delegation holds a whole separate context.
//
// So a group discloses the quiet way: a chevron and a count on the row, and the
// steps indented behind a vertical rail when open. Same for an opened step's
// output. The rail was already this file's idiom for a delegate's own steps; it
// is now the only nesting mark there is, at every depth.
//
// A COUNT, NOT A DESCRIPTION, plus the distinct tool names. See grouping.ts:
// what the steps DID is never summarised, because a summary can be wrong about
// what it hid. Opening the group shows every step, in order, individually.
//
// IT OPENS WHILE ITS WORK RUNS AND CLOSES AGAIN WHEN THAT WORK FINISHES, so a
// long turn does not leave a trail of opened groups behind it. It used to open
// on the way past and stay open, which is why a turn watched live looked
// nothing like the same turn opened afterwards.
//
// The automatic close must never fight the operator, so the open flag carries
// its provenance and anything automatic may only move a disclosure the machine
// itself last moved. The rule is `disclosure.ts`, pure and tested there;
// everything here is wiring.
// OPEN WHILE THE TURN IS INSIDE IT, SHUT THE MOMENT ANYTHING FOLLOWS IT.
// `current` is exactly that: this group holds the turn's position, so nothing
// outside it exists yet. A tool call joins the group and it stays open, because
// that is still inside; prose, a delegation, an approval or the turn ending are
// all outside, and any of them closes it at once. Operator's rule, 2026-08-12.
//
// WHY `current` AND NOT "a step in here is running". Within one run of tool
// calls there is a gap between a result landing and the next call being issued,
// and in that gap no step is running. Closing on that alone would shut the group
// and reopen it between every pair of calls, which is a flicker, not a
// disclosure. `groupIsRunning` used to be OR-ed in here and is gone: it could
// only ever hold a group open after the turn had moved past it, which is the
// one thing this rule is for. (Zero occurrences in the operator's history —
// every tool call in 449 stored replies has its result — so removing it changes
// nothing that ever happened, and removes a way for it to.)
function GroupChip({ item, nowMs, defaultOpen, arrived, current }: {
  item: Extract<TurnItem, { t: "group" }>; nowMs: number; defaultOpen?: boolean;
  arrived?: (seg: ChatSegment) => boolean; current?: boolean;
}) {
  const live = !!current;
  const [disc, setDisc] = useState(() => openInitial(live, !!defaultOpen, Date.now()));
  const id = useId();
  useEffect(() => { setDisc((prev) => onLive(prev, live, Date.now())); }, [live]);
  // A GROUP OF ONE IS NOT A GROUP, ON SCREEN. It wears no header and no rail: it
  // is just the row, exactly as before. What it does NOT do any more is become a
  // different component when a second call lands beside it. See
  // grouping.closeGroup for why that mattered.
  //
  // The steps therefore stay in one slot, inside this same Reveal, at every
  // size. That is the whole point: moving them between parents would remount
  // them just as surely as swapping the component did, and the row the operator
  // is reading would still lose its place.
  const bare = item.steps.length === 1;
  const open = bare || disc.open;
  return (
    <div className={`chat-group${bare ? " is-bare" : ""}${open ? " is-open" : ""}${live ? " is-running" : ""}`}>
      {!bare && (
        <button
          type="button"
          className="chat-group__head"
          onClick={() => setDisc((d) => onToggle(d, Date.now()))}
          aria-expanded={disc.open}
          aria-controls={id}
        >
          <ChevronRight size={13} aria-hidden className="chat-group__chev" />
          <Wrench size={12} aria-hidden className="chat-group__icon" />
          {/* Names lead, count trails. The names take the flexible, ellipsising
              slot because a long list of them is what has to give on a narrow
              row, and the count never does: it is three characters and it is
              the part that says the label is not the whole story. */}
          <span className="chat-group__tools">{groupLabel(item)}</span>
          {!!groupCount(item) && (
            <span className="chat-group__count">{groupCount(item)}</span>
          )}
        </button>
      )}
      {/* A change animates if the state it is leaving was on screen long enough
          to have been seen. So a group closing because the turn moved past it
          animates, and the open-then-shut of a re-attach replay (milliseconds
          apart, for every group in the turn at once) does not. See
          disclosure.ts.

          A bare group has no control to animate and nothing to collapse into, so
          it is always open and never animates. */}
      <Reveal open={open} id={id} className={bare ? undefined : "chat-group__body"}
              animate={!bare && disc.animate}>
        {item.steps.map((s, i) => (
          <ToolChip key={s.id || i} seg={s} nowMs={nowMs} arrived={arrived?.(s)} />
        ))}
      </Reveal>
    </div>
  );
}

// Length elision: clamp a long block to its first N AS-RENDERED lines and END
// it there, with a Show more/less control on the bottom row. `render` turns the
// source into nodes (markdown for assistant text, plain for user text);
// `expanded` seeds the open state (the most recent turn opens fully).
//
// AS-RENDERED, not by character count. The cut is CSS `max-height` in `lh`
// units (`.chat-clamp.is-clamped`), so it lands on the Nth line the reader
// actually sees at the current width — a wrapped paragraph and a list of short
// lines both cut at N visual rows, and the cut re-flows for free when the
// column width changes. A character estimate could not do either; it was the
// reason a two-sentence message sometimes truncated and a wall of short lines
// did not. The only thing JS still decides is WHETHER the block overflowed the
// clamp (so the control appears only when there is more to show), measured
// against the same line count and kept current by a ResizeObserver.
//
// END truncation, not the middle. There is no mid-block ellipsis or "N lines
// hidden" divider any more: the block simply stops at the clamp and one control
// on the last row opens it. Simpler, and it reads like every other "show more".
export function ElidedContent({
  source, render, cfg, expanded,
}: {
  source: string;
  render: (s: string) => ReactNode;
  cfg: ModelDisplay;
  expanded?: boolean;
}) {
  const [open, setOpen] = useState(!!expanded);
  const [overflowing, setOverflowing] = useState(false);
  /** The measured height that ends on a whole line. Null means do not clamp. */
  const [clampH, setClampH] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  // Programmatic, so it does not animate: this fires as a turn stops being the
  // latest one, which happens to every message in the log as history loads.
  useEffect(() => { setOpen(!!expanded); }, [expanded]);

  // SHOW MORE / LESS, ANIMATED, IN TWO PHASES.
  //
  // A clamp's open height is not a value CSS knows, so both ends are made
  // concrete for the length of the toggle and only for that. Phase one, on the
  // click: measure the full height into --clamp-open and turn the transition
  // on, WITHOUT changing the state. Phase two, one frame later: flip the state,
  // so there is a start value to move from. Flipping in the same commit would
  // move max-height from `none`, which does not interpolate, and the whole
  // thing would jump. Then clear both, so nothing is capped afterwards and a
  // message that keeps streaming can never be clipped by a stale measurement.
  const [anim, setAnim] = useState(false);
  const animRef = useRef(false);
  animRef.current = anim;
  const [pending, setPending] = useState<boolean | null>(null);
  const toggle = () => {
    const el = ref.current;
    if (!el) { setOpen((o) => !o); return; }
    el.style.setProperty("--clamp-open", `${el.scrollHeight}px`);
    setAnim(true);
    setPending(!open);
  };
  useEffect(() => {
    if (pending === null) return;
    const r = requestAnimationFrame(() => { setOpen(pending); setPending(null); });
    return () => cancelAnimationFrame(r);
  }, [pending]);
  useEffect(() => {
    if (!anim) return;
    const el = ref.current;
    const done = () => { setAnim(false); el?.style.removeProperty("--clamp-open"); };
    // transitionend is the real signal; the timer is the backstop for the case
    // where the two heights are close enough that no transition fires at all.
    el?.addEventListener("transitionend", done, { once: true });
    const t = setTimeout(done, 600);
    return () => { clearTimeout(t); el?.removeEventListener("transitionend", done); };
  }, [anim]);

  // WHERE THE CUT GOES, measured, never estimated.
  //
  // This used to be `-webkit-line-clamp: N` with `max-height: calc(N * 1lh)`
  // under it. N line heights is only N lines if every line sits on one
  // line-height grid, and in markdown nothing does: paragraph margins, a
  // heading at a different size, a list, a table all move the real lines off
  // it, so the cap landed mid-line and half a row of text showed above the
  // fold. Reported repeatedly, and it was always the same defect.
  //
  // A Range over the contents reports one rect per line box actually laid out,
  // so the bottom of the Nth is exactly where the block should end, whatever
  // produced those lines. Clipping still happens, but now it clips at a place
  // where there is nothing to clip. See clampCut.ts.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      // NOT WHILE THE TOGGLE IS ANIMATING. The observer watches this element,
      // whose height is what the animation changes, so it would fire every
      // frame and run the range walk each time. The lines have not moved, only
      // the cap over them, so there is nothing to re-measure.
      if (animRef.current) return;
      // Cheap gate next. The range walk is a full layout read and this runs on
      // every token of a streaming reply; most blocks are nowhere near the fold.
      const lh = parseFloat(getComputedStyle(el).lineHeight) || 20;
      if (el.scrollHeight <= lh * (cfg.previewLines + 1) + 2) {
        setOverflowing(false);
        setClampH(null);
        return;
      }
      const range = document.createRange();
      range.selectNodeContents(el);
      // Rects describe LAYOUT, so they cover the whole block even while it is
      // clipped: overflow hides paint, it does not shorten the content.
      const bottoms = lineBottoms(Array.from(range.getClientRects()));
      setOverflowing(worthClamping(bottoms.length, cfg.previewLines));
      setClampH(cutHeight(bottoms, el.getBoundingClientRect().top, cfg.previewLines));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [source, cfg.previewLines]);

  const clamped = overflowing && !open;
  return (
    <>
      <div
        ref={ref}
        className={`chat-clamp${clamped ? " is-clamped" : ""}${anim ? " is-animating" : ""}`}
        style={{ "--clamp-h": clampH === null ? undefined : `${clampH}px` } as CSSProperties}
      >
        {render(source)}
      </div>
      {overflowing && (
        <button
          type="button"
          className="chat-more chat-footrow"
          onClick={toggle}
        >
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </>
  );
}

// The answer body of an assistant turn. When it contains no ```ui-card block it
// behaves exactly as before (elided markdown). When it does, the text is split on
// the fenced blocks: surrounding markdown renders normally and each block renders
// as an interactive <UiCard/>, interleaved in order. A ui-card message is
// intentionally interactive, so its markdown runs are not elided (the card must
// stay fully visible). Works identically for a live-streamed answer and a stored
// message on reload, because both flow through here. While the answer is still
// streaming, a not-yet-closed ```ui-card fence renders as a skeleton placeholder
// instead of flashing the half-written JSON; once the stream ends (or on a stored
// message) an unclosed fence falls back to plain markdown.
export function AnswerBody({
  source, cfg, expanded, cardCtx, streaming,
}: {
  source: string;
  cfg: ModelDisplay;
  expanded?: boolean;
  cardCtx: UiCardCtx;
  streaming?: boolean;
}) {
  if (!hasUiCard(source)) {
    return <ElidedContent source={source} cfg={cfg} expanded={expanded} render={(s) => <Markdown>{s}</Markdown>} />;
  }
  const parts = splitUiCards(source);
  return (
    <>
      {parts.map((p, i) => {
        if (p.t === "md") return p.text.trim() ? <Markdown key={i}>{p.text}</Markdown> : null;
        if (p.t === "open") {
          return streaming ? <UiCardPending key={i} /> : <Markdown key={i}>{p.text}</Markdown>;
        }
        return <UiCard key={i} spec={p.spec} raw={p.raw} ctx={cardCtx} />;
      })}
    </>
  );
}

// An interim text block inside the work section.
//
// TWO LEVELS OF DISCLOSURE, which is the limit worth spending:
//   1. a group or a run  — one toggle, opening onto the steps it counted
//   2. one item          — each step, subagent and approval opens itself
// A message's length clamp is not a third level; it is the same level applied
// to text, with Show more/less at the BOTTOM of that message.
//
// This component is that clamp applied where it was missing. Interim text
// rendered in full however long it was, the one place in the transcript where
// "a message shortens itself" was not true.
function WorkText({ text, cfg, expanded }: { text: string; cfg: ModelDisplay; expanded?: boolean }) {
  return (
    <div className="chat-work__text">
      <ElidedContent source={text} cfg={cfg} expanded={expanded} render={(s) => <Markdown>{s}</Markdown>} />
    </div>
  );
}

// ---- Subagent: one row for the whole delegation ---------------------------
// A subagent exists to keep its working context OUT of this conversation, so it
// renders as one row, not as a run of steps the turn appears to have taken
// itself. Same collapse language as ToolChip, one level in, so a nested step
// never reads as a top-level one.
//
// COLLAPSED IS EXACTLY ONE LINE: the task. Not the brief — the brief is a page
// of instructions, and printing it into the row made a single delegation the
// loudest thing in a turn while saying the least about it. The one line comes
// from agentChipLine (newlines folded there, so the string itself cannot wrap)
// and is ellipsised at the reader's real width by .chat-agent__preview.
//
// OPEN SHOWS THE BRIEF, then the steps, then the report. That is the whole
// reason the chip is a disclosure control: the row says what was delegated, and
// opening it says what the delegate was actually told, did, and answered.
function AgentChip({ seg, cfg, nowMs }: {
  seg: Extract<ChatSegment, { t: "agent" }>; cfg: ModelDisplay; nowMs: number;
}) {
  const [open, setOpen] = useState(false);
  const line = agentChipLine(seg);
  const brief = agentBrief(seg);
  // NOTHING TO OPEN IS NOT AN EMPTY PANEL. All three of the body's parts are
  // conditional, and a delegation that has only just been issued has none of
  // them (agentBrief returns "" when the detail is absent or is only the chip
  // line again). Opening it then produced a padded strip under a hairline with
  // nothing in it. Same rule ToolChip already applies with `hasDetail`.
  const hasBody = !!brief || seg.steps.length > 0 || !!seg.result;
  return (
    <div className={`chat-agent${open && hasBody ? " is-open" : ""}${seg.done ? "" : " is-running"}`}>
      <button
        type="button"
        className="chat-agent__head"
        onClick={() => hasBody && setOpen((o) => !o)}
        aria-expanded={hasBody ? open : undefined}
        title={line}
      >
        <ChevronRight size={13} aria-hidden className="chat-agent__chev" />
        <Bot size={12} aria-hidden className="chat-agent__icon" />
        <span className="chat-agent__name">{seg.name}</span>
        <span className="chat-agent__preview">{line}</span>
        <span className="chat-agent__meta">{agentSummary(seg)}</span>
      </button>
      <Reveal open={open && hasBody} className="chat-agent__body">
        <>
          {brief && (
            <div className="chat-agent__brief">
              <span className="chat-agent__result-label">Brief</span>
              {/* Long by nature (the backend caps it at 8000 chars), so it gets
                  the same length treatment as any other long block rather than
                  a scroll box of its own. */}
              <ElidedContent source={brief} cfg={cfg} render={(s) => <Markdown>{s}</Markdown>} />
            </div>
          )}
          {seg.steps.length > 0 && (
            // A delegate's own steps, grouped by the same rule as the parent's.
            // Nested subagents and nested approvals render here too: they used
            // to be dropped on the floor by a two-branch map, so a delegation
            // that itself delegated showed a step count that did not match the
            // steps it listed.
            <div className="chat-agent__steps">
              <TurnWork work={seg.steps} cfg={cfg} nowMs={nowMs} />
            </div>
          )}
          {seg.result && (
            <div className="chat-agent__result">
              <span className="chat-agent__result-label">Reported back</span>
              {/* What a subagent reports back is a message like any other, and
                  can be long — same length treatment as the rest. */}
              <ElidedContent source={seg.result} cfg={cfg} render={(s) => <Markdown>{s}</Markdown>} />
            </div>
          )}
        </>
      </Reveal>
    </div>
  );
}

export type AnswerPermission = (a: {
  requestId: string; allow: boolean; message?: string; applySuggestion?: boolean;
}) => Promise<void>;

// One contiguous stretch of machine work, between two things the model said.
//
// NO ROW CAP. There used to be one (keep the last 8 items, hide the rest behind
// "N earlier steps", with pinning rules so a failure or a pending approval could
// never be the thing hidden). It existed because every tool call was its own
// top-level row, so a long stretch of work ran off the screen. Now a stretch of
// tool calls IS one row until it is opened, which is the same problem solved at
// the right level: a run's length is the number of groups, subagents and
// approvals in it, and those are the things the reader wants to see. A second
// truncation on top of the grouping would hide work for no gain and bring back
// the pinning rules to stop it hiding the wrong work.
function StepRun({ items, cfg, nowMs, openGroups, arrived, current, onAnswerPermission }: {
  items: TurnItem[]; cfg: ModelDisplay; nowMs: number; openGroups?: boolean;
  arrived?: (seg: ChatSegment) => boolean;
  // This run is the trailing one of a turn still streaming, so its LAST item is
  // the work in progress.
  current?: boolean;
  onAnswerPermission?: AnswerPermission;
}) {
  // KEY ON THE STEP, NOT ON ITS POSITION. A live run grows, so index k means a
  // different item after every new step, and a chip the operator had opened to
  // read would hand its open state to whatever took its slot. Tool and approval
  // rows carry an id; a group takes its first step's id (a group only ever grows
  // at its tail) and a delegation has one of its own.
  // A tool call never arrives as a bare `step` any more: every run of them is a
  // group, including a run of one, which is what stops a row being rebuilt as a
  // different component when a second call lands. `step` is now exactly the two
  // things that were always their own row, a delegation and an approval.
  const renderItem = (item: TurnItem, i: number) =>
    item.t === "group"
      ? <GroupChip key={item.steps[0]?.id || `g${i}`} item={item} nowMs={nowMs}
                   defaultOpen={openGroups} arrived={arrived}
                   current={current && i === items.length - 1} />
      : item.seg.t === "agent"
        ? <AgentChip key={item.seg.id || `a${i}`} seg={item.seg} cfg={cfg} nowMs={nowMs} />
        : item.seg.t === "approval"
          ? <ApprovalBlock key={item.seg.id || `p${i}`} seg={item.seg}
                           onAnswer={onAnswerPermission ?? (async () => {})} />
          : null;
  return (
    <div className="chat-work">
      <div className="chat-work__list">{items.map(renderItem)}</div>
    </div>
  );
}

// The work of a SETTLED turn, behind one line.
//
// A turn the reader has already read gets one summary and nothing else until
// they ask. Drawing every group of every historical turn would be a density
// regression on a long thread: today an old turn shows exactly one line for all
// of its work, and that is the right amount.
function SettledWork({
  work, cfg, nowMs, tookMs, streaming, expanded, onAnswerPermission,
}: {
  work: ChatSegment[]; cfg: ModelDisplay; nowMs: number; tookMs?: number;
  streaming?: boolean;
  expanded?: boolean;
  onAnswerPermission?: AnswerPermission;
}) {
  // THE SAME PROVENANCE RULE THE GROUPS USE, because this is the same kind of
  // disclosure: the machine opens it while the turn is being written and closes
  // it again when the turn settles, and the operator's touch takes it away from
  // the machine permanently.
  //
  // It used to be `useState(!!streaming)`, and a `useState` initialiser runs
  // ONCE. So a turn watched live opened its work section and never closed it:
  // the docstring said "it settles closed on the next turn" and nothing in the
  // component could ever do that. Every turn the operator sat through stayed
  // expanded for the rest of the session, which is the "older tool call groups
  // should default to closed" report of 2026-08-17.
  const [disc, setDisc] = useState(() => openInitial(!!streaming, false, Date.now()));
  const id = useId();
  useEffect(() => { setDisc((prev) => onLive(prev, !!streaming, Date.now())); }, [streaming]);
  const open = disc.open;
  // THE TURN'S TOTAL LIVES HERE, on the line that already summarises the turn's
  // work, rather than in a footer row of its own. A turn nobody waited on does
  // not mention its duration at all: a number under every reply is noise, and
  // the question a total answers ("was that slow?") only exists past a wait.
  //
  // The work summary comes FIRST and is never omitted. A duration on its own
  // would label the control "45s", which says nothing about what opening it
  // shows.
  const summary = [turnWorkSummary(work) || "Steps", turnDuration(tookMs)]
    .filter(Boolean).join(" · ");
  // WHAT WAS SAID, not just what was done. A pure step count told the reader
  // nothing was worth reading; when the turn narrated mid-run, the collapsed
  // row now leads with a taste of it and keeps the count as trailing meta,
  // the same split AgentChip already uses (name leads, meta trails). Empty for
  // a turn that only made tool calls, so that case reads exactly as before.
  const preview = workNarrationPreview(work);
  // A turn blocked on a decision is never summarised away, even when it is no
  // longer the latest: the buttons are the only thing that can restart it.
  const pending = work.some((s) => s.t === "approval" && s.state === "pending");
  if (pending) {
    return <TurnWork work={work} cfg={cfg} nowMs={nowMs} expanded={expanded}
                     streaming={streaming} onAnswerPermission={onAnswerPermission} />;
  }
  return (
    <div className="chat-work">
      <button
        type="button"
        className={`chat-work__toggle${open ? " is-open" : ""}`}
        onClick={() => setDisc((d) => onToggle(d, Date.now()))}
        aria-expanded={open}
        aria-controls={id}
        title={!open && preview ? preview : undefined}
      >
        <ChevronRight size={13} aria-hidden className="chat-work__chev" />
        {open ? (
          "Hide steps"
        ) : preview ? (
          <>
            <span className="chat-work__preview">{preview}</span>
            <span className="chat-work__meta">{summary}</span>
          </>
        ) : (
          summary
        )}
      </button>
      {/* ONE CLICK, NOT TWO, BUT ONLY ON A SETTLED TURN. Opening a turn the
          reader has already read opens its groups with it: the turn line
          already says "12 steps", so a group inside it saying "6 steps" is the
          same disclosure twice, and the reader who opened the turn has asked
          for exactly what the group would hide. Operator-agreed, 2026-08-12.

          `!streaming` is the 2026-08-17 correction. This used to be a bare
          `openGroups`, so it was true WHILE THE TURN RAN too, and that forced
          every group of a live turn open at mount. A group opened that way was
          never opened by `live`, so `disclosure.onLive` had nothing to close --
          it only moves a disclosure on a live TRANSITION -- and the whole run
          of them stayed open behind the turn as it went. Only the trailing
          group ever closed itself, which is the "they don't collapse when the
          group is done" report.

          A live turn's groups now manage themselves: `current` opens the one
          holding the turn's position and closes it the moment anything follows
          it, which is the earliest point the wire can say that group is
          finished. Nothing is lost for the settled case, because `Reveal`
          unmounts its children when it closes, so the groups of a turn watched
          live are remounted from this flag the next time it is opened. */}
      <Reveal open={open} id={id} animate={disc.animate}>
        <TurnWork work={work} cfg={cfg} nowMs={nowMs} openGroups={!streaming}
                  expanded={expanded} streaming={streaming}
                  onAnswerPermission={onAnswerPermission} />
      </Reveal>
    </div>
  );
}

// A turn's work as blocks: prose the model wrote, and the runs of steps between
// them. There is no wrapper around the whole thing, because there never was one
// worth naming: the old `.chat-work` div was whatever remained after the answer
// was taken off the end. See ADR 0015 §1.
function TurnWork({ work, cfg, nowMs, expanded, openGroups, streaming, onAnswerPermission }: {
  work: ChatSegment[]; cfg: ModelDisplay; nowMs: number;
  // The most recent turn reads in full, the same rule the answer already uses.
  // Narration on a live turn is there precisely so progress is legible as it
  // happens, so it must not arrive pre-clamped.
  expanded?: boolean;
  // Draw the step groups already open. Set when the reader has opened a settled
  // turn, so that one click reaches the steps rather than two.
  openGroups?: boolean;
  // This turn is still being written, so its trailing run holds work in flight.
  streaming?: boolean;
  onAnswerPermission?: AnswerPermission;
}) {
  const arrived = useArrivedSteps();
  const blocks = buildTurnBlocks(work);
  // The trailing run, which is where work in flight is. Not simply the last
  // block: a turn narrating as it goes ends on prose, and the run before that
  // prose is finished, not current.
  let lastRun = -1;
  blocks.forEach((b, i) => { if (b.t === "run") lastRun = i; });
  const endsOnRun = blocks.length > 0 && blocks[blocks.length - 1].t === "run";
  return (
    <>
      {blocks.map((b, i) =>
        b.t === "prose"
          // Interim prose is CONVERSATION, and renders as prose. It goes
          // through WorkText, not AnswerBody, deliberately: a ```ui-card``` the
          // model wrote halfway through a turn and then talked itself out of
          // must not be a live control, and card outcomes persist server-side.
          ? <WorkText key={`p${i}`} text={b.text} cfg={cfg} expanded={expanded} />
          : <StepRun key={`r${i}`} items={b.items} cfg={cfg} nowMs={nowMs} openGroups={openGroups}
                     arrived={arrived}
                     current={!!streaming && endsOnRun && i === lastRun}
                     onAnswerPermission={onAnswerPermission} />)}
    </>
  );
}

// An assistant turn: the trailing run of text is the answer, everything before
// it is the work. The answer renders fully on the most recent turn and is
// elided on older ones.
export function AssistantTurn({
  segments, isLatest, model, cardCtx, streaming, onAnswerPermission, nowMs, tookMs,
}: {
  segments: ChatSegment[];
  isLatest: boolean;
  model?: string;
  cardCtx: UiCardCtx;
  streaming?: boolean;
  onAnswerPermission?: AnswerPermission;
  // The 1Hz clock, passed down rather than owned here so the whole transcript
  // shares one interval and memoized history is not re-rendered by it.
  nowMs?: number;
  // How long the whole turn took. Undefined unless BOTH ends are known: a reply
  // the operator interrupted renders as several blocks that all carry the same
  // started_at and only the last of which carries ended_at, so computing a
  // total from a half-pair would print the same wrong number on each of them.
  tookMs?: number;
}) {
  const cfg = modelDisplay(model);
  let cut = segments.length;
  while (cut > 0 && segments[cut - 1].t === "text") cut--;
  const work = segments.slice(0, cut);
  const answer = segments.slice(cut);
  const clock = nowMs ?? 0;
  return (
    <>
      {/* ONE TOGGLE AT THE TOP OF THE BUBBLE, on every turn, which is 4a's
          defining rule and the reason it was chosen over 3a ("right data model,
          wrong turn rendering"). The handoff states it directly: the turn splits
          at the last run of text, everything before is work, everything after is
          the answer, "with one toggle at the top of the bubble, that is the only
          thing that collapses a turn's steps, and it never touches the answer".

          The latest turn used to skip the toggle and render its work INTERLEAVED
          with the prose, so a turn's steps ran down the middle of its own answer
          and there was no single control for them. That is what made our chat
          look nothing like the design even though every part existed.

          It opens itself while the turn is still being written, because live
          progress has to be legible without a click, and settles closed once the
          turn is done. */}
      {work.length > 0 && (
        <SettledWork work={work} cfg={cfg} nowMs={clock} tookMs={tookMs}
                     streaming={streaming} expanded={isLatest}
                     onAnswerPermission={onAnswerPermission} />
      )}
      {answer.map((s, i) =>
        s.t === "text" && s.text.trim() ? (
          <AnswerBody key={i} source={s.text} cfg={cfg} expanded={isLatest} cardCtx={cardCtx} streaming={streaming} />
        ) : null,
      )}
    </>
  );
}
