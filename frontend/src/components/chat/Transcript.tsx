import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Bot, ChevronRight, Wrench } from "lucide-react";
import { Markdown } from "@workshop/ui/markdown";
import type { ChatSegment } from "../../lib/chat";
import ApprovalBlock from "./ApprovalBlock";
import { agentBrief, agentChipLine, agentSummary, recentWorkStepCount } from "./segments";
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
function ToolChip({ name, input }: { name: string; input: string }) {
  const [open, setOpen] = useState(false);
  const hasDetail = !!input;
  return (
    <div className={`chat-tool${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="chat-tool__head"
        onClick={() => hasDetail && setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        title={hasDetail ? input : name}
      >
        {hasDetail && <ChevronRight size={13} aria-hidden className="chat-tool__chev" />}
        <Wrench size={12} aria-hidden className="chat-tool__icon" />
        <span className="chat-tool__name">{name}</span>
        {hasDetail && <span className="chat-tool__preview ws-mono">{input}</span>}
      </button>
      {open && hasDetail && <pre className="chat-tool__detail ws-mono">{input}</pre>}
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
// THE THREE LEVELS OF DISCLOSURE, and which control owns which:
//   1. the whole work block   — one toggle at the TOP of the bubble (WorkSteps)
//   2. one item inside it     — each tool, subagent, and text block opens itself
//   3. one message's length   — Show more/less at the BOTTOM of that message
// Level 1 sits at the top rather than the bottom because the work is ABOVE the
// answer: a bottom control that expanded it would insert content above itself,
// pushing the button and the whole answer down and losing the reader's place.
//
// This component is level 3 applied where it was missing. Interim text rendered
// in full however long it was — the one place in the transcript where "a message
// shortens itself" was not true, which is what made levels 1 and 3 look like
// overlapping versions of the same control instead of different scopes.
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
function AgentChip({ seg, cfg }: { seg: Extract<ChatSegment, { t: "agent" }>; cfg: ModelDisplay }) {
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
            <div className="chat-agent__steps">
              {seg.steps.map((s, i) =>
                s.t === "tool"
                  ? <ToolChip key={i} name={s.name} input={s.input} />
                  : s.t === "text" && s.text.trim()
                    ? <WorkText key={i} text={s.text} cfg={cfg} />
                    : null,
              )}
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

// The "work" of an assistant turn — its tool calls, subagents, and interim text.
// On the most recent turn the latest few steps stay inline and older ones fold
// into a re-openable accordion (widened to sweep in a nearby text checkpoint —
// see recentWorkStepCount); on older turns all of it folds away (just the
// answer shows). Tool calls and subagents stay individually expandable.
function WorkSteps({ work, isLatest, cfg, onAnswerPermission }: {
  work: ChatSegment[]; isLatest: boolean; cfg: ModelDisplay;
  onAnswerPermission?: AnswerPermission;
}) {
  const [open, setOpen] = useState(false);
  const inlineCount = recentWorkStepCount(work, isLatest, cfg.recentSteps, cfg.recentSteps * 4);
  // A PENDING approval is never foldable. The turn is blocked on it, so hiding
  // it behind "3 steps hidden" would hide the only control that can restart the
  // run — the operator would be looking at a chat that had simply stopped.
  const firstPending = work.findIndex((s) => s.t === "approval" && s.state === "pending");
  const keepFrom = firstPending >= 0
    ? Math.min(firstPending, Math.max(0, work.length - Math.max(inlineCount, 0)))
    : Math.max(0, work.length - inlineCount);
  const older = inlineCount > 0 || firstPending >= 0 ? work.slice(0, keepFrom) : work;
  const recent = inlineCount > 0 || firstPending >= 0 ? work.slice(keepFrom) : [];
  const olderTools = older.filter((s) => s.t === "tool" || s.t === "agent").length;
  // `expanded` follows the rule the answer already uses: the most recent turn
  // reads in full. The steps kept inline on a live turn are there precisely so
  // progress is legible as it happens, so a long text checkpoint among them must
  // not arrive pre-collapsed. Folded-away steps open collapsed — you went looking
  // for them, and a wall of text is not what you were looking for.
  const renderSeg = (s: ChatSegment, i: number, expanded: boolean) =>
    s.t === "tool"
      ? <ToolChip key={i} name={s.name} input={s.input} />
      : s.t === "agent"
        ? <AgentChip key={i} seg={s} cfg={cfg} />
        : s.t === "approval"
          ? <ApprovalBlock key={s.id || i} seg={s} onAnswer={onAnswerPermission ?? (async () => {})} />
          : (s.text.trim() ? <WorkText key={i} text={s.text} cfg={cfg} expanded={expanded} /> : null);
  return (
    <div className="chat-work">
      {older.length > 0 && (
        <>
          <button
            type="button"
            className={`chat-work__toggle${open ? " is-open" : ""}`}
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
          >
            <ChevronRight size={13} aria-hidden className="chat-work__chev" />
            {/* Same grammar as every other collapsed thing in the transcript:
                say what is hidden, not what to do about it. The verb differs from
                a message's "Show less" on purpose — this hides a LIST of steps,
                a message shortens PROSE, and the chevron marks it as the
                section-level control rather than a message-level one. */}
            {open
              ? "Hide steps"
              : `${older.length} step${older.length > 1 ? "s" : ""} hidden` +
                (olderTools > 0 ? ` · ${olderTools} tool${olderTools > 1 ? "s" : ""}` : "")}
          </button>
          {open && (
            <div className="chat-work__list">
              {older.map((s, i) => renderSeg(s, i, false))}
            </div>
          )}
        </>
      )}
      {recent.length > 0 && (
        <div className="chat-work__list">
          {recent.map((s, i) => renderSeg(s, i, isLatest))}
        </div>
      )}
    </div>
  );
}

// An assistant turn: split the trailing run of text (the answer) from the work
// (tools + interim thinking). The answer renders fully on the most recent turn
// and is elided on older ones; the work collapses per WorkSteps.
export type AnswerPermission = (a: {
  requestId: string; allow: boolean; message?: string; applySuggestion?: boolean;
}) => Promise<void>;

export function AssistantTurn({
  segments, isLatest, model, cardCtx, streaming, onAnswerPermission,
}: {
  segments: ChatSegment[];
  isLatest: boolean;
  model?: string;
  cardCtx: UiCardCtx;
  streaming?: boolean;
  onAnswerPermission?: AnswerPermission;
}) {
  const cfg = modelDisplay(model);
  let cut = segments.length;
  while (cut > 0 && segments[cut - 1].t === "text") cut--;
  const work = segments.slice(0, cut);
  const answer = segments.slice(cut);
  return (
    <>
      {work.length > 0 && (
        <WorkSteps work={work} isLatest={isLatest} cfg={cfg} onAnswerPermission={onAnswerPermission} />
      )}
      {answer.map((s, i) =>
        s.t === "text" && s.text.trim() ? (
          <AnswerBody key={i} source={s.text} cfg={cfg} expanded={isLatest} cardCtx={cardCtx} streaming={streaming} />
        ) : null,
      )}
    </>
  );
}
