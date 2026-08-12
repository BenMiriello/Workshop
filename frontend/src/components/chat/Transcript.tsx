import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Bot, ChevronRight, Wrench } from "lucide-react";
import { Markdown } from "@workshop/ui/markdown";
import type { ChatSegment } from "../../lib/chat";
import ApprovalBlock from "./ApprovalBlock";
import { agentBrief, agentChipLine, agentSummary } from "./segments";
import {
  buildTurnBlocks, groupDetail, groupSummary, isFailed, isRunning, runningSeconds,
  splitRun, stepDuration, turnDuration, turnWorkSummary, type TurnItem,
} from "./grouping";
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

// ---- Tool call: compact by default, tap to expand -------------------------
// One dense row (icon · name · truncated input). Tapping reveals the full input
// (a command / path / pattern). Mobile-first: the preview truncates with an
// ellipsis so a tool call never blows out the bubble width.
// STATE DECIDES DISCLOSURE, not a preference. A step that failed opens itself
// and says why; a step still running says how long it has been going, but only
// past the 10s attention limit, so a fast call never flickers a counter. Every
// other step is one collapsed row. See grouping.ts and ADR 0015 §3/§5.
function ToolChip({ seg, nowMs }: { seg: Extract<ChatSegment, { t: "tool" }>; nowMs: number }) {
  const failed = isFailed(seg);
  const running = isRunning(seg);
  const [open, setOpen] = useState(failed);
  const panelId = useId();
  const detail = failed && seg.error ? seg.error : seg.input;
  const hasDetail = !!detail;
  const secs = runningSeconds(seg, nowMs);
  const took = stepDuration(seg);
  return (
    <div className={`chat-tool${open ? " is-open" : ""}${failed ? " is-failed" : ""}${running ? " is-running" : ""}`}>
      <button
        type="button"
        className="chat-tool__head"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        aria-controls={hasDetail ? panelId : undefined}
        title={hasDetail ? detail : seg.name}
      >
        {hasDetail && <ChevronRight size={13} aria-hidden className="chat-tool__chev" />}
        <Wrench size={12} aria-hidden className="chat-tool__icon" />
        <span className="chat-tool__name">{seg.name}</span>
        {!!seg.input && <span className="chat-tool__preview ws-mono">{seg.input}</span>}
        {/* The clock rides on the STEP, not the turn. A single total for the
            whole turn reads "3h 41m" on a long one and describes nothing; what
            the reader wants to know is whether the thing happening now is
            stuck. */}
        {secs > 0 && <span className="chat-tool__time">{secs}s</span>}
        {!running && !!took && <span className="chat-tool__time">{took}</span>}
        {failed && <span className="chat-tool__failed">failed</span>}
      </button>
      {open && hasDetail && <pre id={panelId} className="chat-tool__detail ws-mono">{detail}</pre>}
    </div>
  );
}

// A merged run of read-only steps: one row saying what it did and to what.
//
// The objects stay on the row. A collapsed summary that hides its object is the
// documented failure mode (microsoft/vscode issue 292985: `rm -rf important_dir`
// collapsed to "Executed command in terminal"), and the rule that keeps writes
// and commands out of a group exists for the same reason.
function GroupChip({ item, nowMs }: { item: Extract<TurnItem, { t: "group" }>; nowMs: number }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={`chat-group${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="chat-group__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={id}
      >
        <ChevronRight size={13} aria-hidden className="chat-group__chev" />
        <Wrench size={12} aria-hidden className="chat-group__icon" />
        <span className="chat-group__name">{groupSummary(item)}</span>
        <span className="chat-group__preview ws-mono">{groupDetail(item)}</span>
      </button>
      <div id={id} hidden={!open}>
        {open && item.steps.map((s, i) => <ToolChip key={s.id || i} seg={s} nowMs={nowMs} />)}
      </div>
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
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { setOpen(!!expanded); }, [expanded]);

  // Does the content run PAST the clamp by enough to be worth clamping?
  // Measured against the full content height (`scrollHeight`, which reports the
  // whole block even while it is clamped), so it is correct whether the block is
  // open or collapsed — the most recent turn opens expanded and still needs its
  // Show less. Re-runs on content change and, via the observer, on any width
  // change that re-wraps the text.
  //
  // The threshold is previewLines + 1, not previewLines: clamping a block that
  // is only ONE line over hides one line and adds a Show more row in its place —
  // no shorter, just different. So it only clamps once there are 2+ lines to
  // hide. (Line-clamp itself, in CSS, always ends on a whole line box, so a
  // clamped block never cuts mid-line.)
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const lh = parseFloat(getComputedStyle(el).lineHeight) || 20;
      setOverflowing(el.scrollHeight > lh * (cfg.previewLines + 1) + 2);
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
        className={`chat-clamp${clamped ? " is-clamped" : ""}`}
        style={{ "--clamp-lines": cfg.previewLines } as CSSProperties}
      >
        {render(source)}
      </div>
      {overflowing && (
        <button
          type="button"
          className="chat-more chat-footrow"
          onClick={() => setOpen((o) => !o)}
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
  return (
    <div className={`chat-agent${open ? " is-open" : ""}${seg.done ? "" : " is-running"}`}>
      <button
        type="button"
        className="chat-agent__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={line}
      >
        <ChevronRight size={13} aria-hidden className="chat-agent__chev" />
        <Bot size={12} aria-hidden className="chat-agent__icon" />
        <span className="chat-agent__name">{seg.name}</span>
        <span className="chat-agent__preview">{line}</span>
        <span className="chat-agent__meta">{agentSummary(seg)}</span>
      </button>
      {open && (
        <div className="chat-agent__body">
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
        </div>
      )}
    </div>
  );
}

export type AnswerPermission = (a: {
  requestId: string; allow: boolean; message?: string; applySuggestion?: boolean;
}) => Promise<void>;

// One contiguous stretch of machine work, between two things the model said.
//
// The cap is PER RUN, not per prose block: a turn that wrote no prose at all is
// common (one that ends on a write, one the operator stopped), and pegging
// density to how chatty the model was would show more rows for the same work
// when narrated than when not.
function StepRun({ items, cfg, nowMs, onAnswerPermission }: {
  items: TurnItem[]; cfg: ModelDisplay; nowMs: number;
  onAnswerPermission?: AnswerPermission;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const { shown, hidden } = splitRun(items);
  const renderItem = (item: TurnItem, i: number) =>
    item.t === "group"
      ? <GroupChip key={`g${i}`} item={item} nowMs={nowMs} />
      : item.seg.t === "tool"
        ? <ToolChip key={item.seg.id || `s${i}`} seg={item.seg} nowMs={nowMs} />
        : item.seg.t === "agent"
          ? <AgentChip key={`a${i}`} seg={item.seg} cfg={cfg} nowMs={nowMs} />
          : item.seg.t === "approval"
            ? <ApprovalBlock key={item.seg.id || `p${i}`} seg={item.seg}
                             onAnswer={onAnswerPermission ?? (async () => {})} />
            : null;
  return (
    <div className="chat-work">
      {hidden.length > 0 && (
        <>
          <button
            type="button"
            className={`chat-work__toggle${open ? " is-open" : ""}`}
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-controls={id}
          >
            <ChevronRight size={13} aria-hidden className="chat-work__chev" />
            {open ? "Hide earlier steps" : `${hidden.length} earlier step${hidden.length > 1 ? "s" : ""}`}
          </button>
          <div id={id} className="chat-work__list" hidden={!open}>
            {open && hidden.map(renderItem)}
          </div>
        </>
      )}
      <div className="chat-work__list">{shown.map(renderItem)}</div>
    </div>
  );
}

// The work of a SETTLED turn, behind one line.
//
// A turn the reader has already read gets one summary and nothing else until
// they ask. Drawing every group of every historical turn would be a density
// regression on a long thread: today an old turn shows exactly one line for all
// of its work, and that is the right amount.
function SettledWork({ work, cfg, nowMs, tookMs, onAnswerPermission }: {
  work: ChatSegment[]; cfg: ModelDisplay; nowMs: number; tookMs?: number;
  onAnswerPermission?: AnswerPermission;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
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
  // A turn blocked on a decision is never summarised away, even when it is no
  // longer the latest: the buttons are the only thing that can restart it.
  const pending = work.some((s) => s.t === "approval" && s.state === "pending");
  if (pending) return <TurnWork work={work} cfg={cfg} nowMs={nowMs} onAnswerPermission={onAnswerPermission} />;
  return (
    <div className="chat-work">
      <button
        type="button"
        className={`chat-work__toggle${open ? " is-open" : ""}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={id}
      >
        <ChevronRight size={13} aria-hidden className="chat-work__chev" />
        {open ? "Hide steps" : summary}
      </button>
      <div id={id} hidden={!open}>
        {open && <TurnWork work={work} cfg={cfg} nowMs={nowMs} onAnswerPermission={onAnswerPermission} />}
      </div>
    </div>
  );
}

// A turn's work as blocks: prose the model wrote, and the runs of steps between
// them. There is no wrapper around the whole thing, because there never was one
// worth naming: the old `.chat-work` div was whatever remained after the answer
// was taken off the end. See ADR 0015 §1.
function TurnWork({ work, cfg, nowMs, expanded, onAnswerPermission }: {
  work: ChatSegment[]; cfg: ModelDisplay; nowMs: number;
  // The most recent turn reads in full, the same rule the answer already uses.
  // Narration on a live turn is there precisely so progress is legible as it
  // happens, so it must not arrive pre-clamped.
  expanded?: boolean;
  onAnswerPermission?: AnswerPermission;
}) {
  return (
    <>
      {buildTurnBlocks(work).map((b, i) =>
        b.t === "prose"
          // Interim prose is CONVERSATION, and renders as prose. It goes
          // through WorkText, not AnswerBody, deliberately: a ```ui-card``` the
          // model wrote halfway through a turn and then talked itself out of
          // must not be a live control, and card outcomes persist server-side.
          ? <WorkText key={`p${i}`} text={b.text} cfg={cfg} expanded={expanded} />
          : <StepRun key={`r${i}`} items={b.items} cfg={cfg} nowMs={nowMs}
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
      {work.length > 0 && (isLatest
        ? <TurnWork work={work} cfg={cfg} nowMs={clock} expanded onAnswerPermission={onAnswerPermission} />
        : <SettledWork work={work} cfg={cfg} nowMs={clock} tookMs={tookMs}
                       onAnswerPermission={onAnswerPermission} />)}
      {answer.map((s, i) =>
        s.t === "text" && s.text.trim() ? (
          <AnswerBody key={i} source={s.text} cfg={cfg} expanded={isLatest} cardCtx={cardCtx} streaming={streaming} />
        ) : null,
      )}
    </>
  );
}
