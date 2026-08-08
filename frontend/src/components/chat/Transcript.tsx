import { useEffect, useState, type ReactNode } from "react";
import { Bot, ChevronRight, Wrench } from "lucide-react";
import { Markdown } from "../../ui";
import type { ChatSegment } from "../../lib/chat";
import { agentSummary, recentWorkStepCount } from "./segments";
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

// First-several + last-few line elision with Show more/less. `render` turns a
// source slice into nodes (markdown for assistant text, plain for user text).
// `expanded` seeds the open state (the most recent turn opens fully).
export function ElidedContent({
  source, render, cfg, expanded,
}: {
  source: string;
  render: (s: string) => ReactNode;
  cfg: ModelDisplay;
  expanded?: boolean;
}) {
  const lines = source.split("\n");
  // Estimate *visual* lines: a long paragraph with no newlines still wraps to
  // many lines, so count wrapped rows (~CPL chars each), not just "\n"s.
  const CPL = 46; // ~chars per line on a phone; over-counts a little on desktop
  const visualLines = lines.reduce((n, l) => n + Math.max(1, Math.ceil(l.length / CPL)), 0);
  const elide = visualLines > Math.max(cfg.elideOverLines, cfg.headLines + cfg.tailLines + 1);
  const [open, setOpen] = useState(!!expanded);
  useEffect(() => { setOpen(!!expanded); }, [expanded]);
  if (!elide) return <>{render(source)}</>;
  if (open) {
    return (
      <>
        {render(source)}
        <button type="button" className="chat-more" onClick={() => setOpen(false)}>Show less</button>
      </>
    );
  }
  // Prefer slicing on real line breaks (lists, paragraphs, code); fall back to
  // characters when it's a few long wrapped lines so the preview still trims.
  let headSrc: string, tailSrc: string, label: string;
  if (lines.length >= cfg.headLines + cfg.tailLines + 2) {
    headSrc = lines.slice(0, cfg.headLines).join("\n");
    tailSrc = lines.slice(-cfg.tailLines).join("\n");
    label = `Show ${lines.length - cfg.headLines - cfg.tailLines} more lines`;
  } else {
    headSrc = source.slice(0, cfg.headLines * CPL);
    tailSrc = source.slice(-cfg.tailLines * CPL);
    label = "Show more";
  }
  // A visible "···" divider marks the cut; the whole line expands on click.
  return (
    <>
      {render(headSrc)}
      <button type="button" className="chat-ellipsis" onClick={() => setOpen(true)}>
        <span className="chat-ellipsis__dots" aria-hidden>•••</span>
        <span className="chat-ellipsis__label">{label}</span>
      </button>
      {render(tailSrc)}
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

// ---- Subagent: one row for the whole delegation ---------------------------
// A subagent exists to keep its working context OUT of this conversation, so it
// renders as one row, not as a run of steps the turn appears to have taken
// itself. The row says who was asked and what for; opening it shows what the
// delegate actually did and what it reported back. Same collapse language as
// ToolChip, one level in, so a nested step never reads as a top-level one.
function AgentChip({ seg }: { seg: Extract<ChatSegment, { t: "agent" }> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`chat-agent${open ? " is-open" : ""}${seg.done ? "" : " is-running"}`}>
      <button
        type="button"
        className="chat-agent__head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        title={seg.input || seg.name}
      >
        <ChevronRight size={13} aria-hidden className="chat-agent__chev" />
        <Bot size={12} aria-hidden className="chat-agent__icon" />
        <span className="chat-agent__name">{seg.name}</span>
        {seg.input && <span className="chat-agent__preview">{seg.input}</span>}
        <span className="chat-agent__meta">{agentSummary(seg)}</span>
      </button>
      {open && (
        <div className="chat-agent__body">
          {seg.steps.length > 0 && (
            <div className="chat-agent__steps">
              {seg.steps.map((s, i) =>
                s.t === "tool"
                  ? <ToolChip key={i} name={s.name} input={s.input} />
                  : s.t === "text" && s.text.trim()
                    ? <div key={i} className="chat-work__text"><Markdown>{s.text}</Markdown></div>
                    : null,
              )}
            </div>
          )}
          {seg.result && (
            <div className="chat-agent__result">
              <span className="chat-agent__result-label">Reported back</span>
              <Markdown>{seg.result}</Markdown>
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
function WorkSteps({ work, isLatest, cfg }: { work: ChatSegment[]; isLatest: boolean; cfg: ModelDisplay }) {
  const [open, setOpen] = useState(false);
  const inlineCount = recentWorkStepCount(work, isLatest, cfg.recentSteps, cfg.recentSteps * 4);
  const older = inlineCount > 0 ? work.slice(0, Math.max(0, work.length - inlineCount)) : work;
  const recent = inlineCount > 0 ? work.slice(Math.max(0, work.length - inlineCount)) : [];
  const olderTools = older.filter((s) => s.t === "tool" || s.t === "agent").length;
  const renderSeg = (s: ChatSegment, i: number) =>
    s.t === "tool"
      ? <ToolChip key={i} name={s.name} input={s.input} />
      : s.t === "agent"
        ? <AgentChip key={i} seg={s} />
        : (s.text.trim() ? <div key={i} className="chat-work__text"><Markdown>{s.text}</Markdown></div> : null);
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
            {open ? "Hide" : "Show"} {older.length} earlier step{older.length > 1 ? "s" : ""}
            {olderTools > 0 ? ` · ${olderTools} tool${olderTools > 1 ? "s" : ""}` : ""}
          </button>
          {open && <div className="chat-work__list">{older.map(renderSeg)}</div>}
        </>
      )}
      {recent.length > 0 && <div className="chat-work__list">{recent.map(renderSeg)}</div>}
    </div>
  );
}

// An assistant turn: split the trailing run of text (the answer) from the work
// (tools + interim thinking). The answer renders fully on the most recent turn
// and is elided on older ones; the work collapses per WorkSteps.
export function AssistantTurn({
  segments, isLatest, model, cardCtx, streaming,
}: {
  segments: ChatSegment[];
  isLatest: boolean;
  model?: string;
  cardCtx: UiCardCtx;
  streaming?: boolean;
}) {
  const cfg = modelDisplay(model);
  let cut = segments.length;
  while (cut > 0 && segments[cut - 1].t === "text") cut--;
  const work = segments.slice(0, cut);
  const answer = segments.slice(cut);
  return (
    <>
      {work.length > 0 && <WorkSteps work={work} isLatest={isLatest} cfg={cfg} />}
      {answer.map((s, i) =>
        s.t === "text" && s.text.trim() ? (
          <AnswerBody key={i} source={s.text} cfg={cfg} expanded={isLatest} cardCtx={cardCtx} streaming={streaming} />
        ) : null,
      )}
    </>
  );
}
