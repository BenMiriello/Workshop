// The dictation bar: one pill-shaped row, on every page while dictation is on
// and while a notice is showing. Left to right: the grip to drag it, the state
// dot, the Actions button (chat actions and their history), the Mode button
// (how spoken commands are heard), where the words are going (its menu changes
// that), the word count, the newest words when they are not in a field on
// screen (or the notice in their place), Pause or Resume, and Stop.
//
// Its width does not move as words arrive: the count has room for its digits
// and the newest words sit in a slot of their own width. Dragged elsewhere, it
// leaves its place in the dock held by an empty slot, which is where it snaps
// back to (useBarDrag.ts).

import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { ArrowUpRight, GripVertical, Loader2, Pause, Play, Square, Zap } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@workshop/ui";
import { openChat, shownIn } from "../../../lib/speech/chatTargets";
import { useOpenChats } from "../../chat/openChats";
import { AllActions } from "./ActionHistoryView";
import { chatModel } from "./chatActions";
import { ChatPicker } from "./ChatPicker";
import { dictation, type DictationState } from "./dictation";
import { chatPlace, type Destination } from "./destination";
import { documentShown } from "./frames";
import { wordCount } from "./dictationState";
import { ModelAsk } from "./ModelAsk";
import { ActionMenu, KIND_ICON, MODE_ICON, ModeMenu, PlaceMenu } from "./PillMenu";
import { modeLabel } from "../../../lib/speech/commandsApi";
import { PiecesDialog } from "./PiecesDialog";
import { PillNotice } from "./PillNotice";
import { keepFocus } from "./press";
import { sinkFor } from "./sinkFor";
import { useBarDrag } from "./useBarDrag";

const NEWEST = 90;

function status(s: DictationState): string | null {
  if (s.phase === "starting") return "Starting";
  if (s.phase === "finishing") return "Finishing";
  if (!s.connected) return s.everConnected ? "Reconnecting" : "Connecting";
  if (s.paused) return "Paused";
  return null;
}

// Whether words written now land somewhere on screen: the field itself, or
// the chat's composer, in a page that is showing. Asked at each render, which
// a change of page or of the chats open brings, so it is never left over from
// the last words written.
function onScreen(d: Destination): boolean {
  const sink = sinkFor(d).sink;
  return sink.kind === "live" && documentShown(sink.target.el.ownerDocument);
}

// Where words go is not on screen: the clipboard, a chat that is not open, or
// a field that left the page. Then the bar shows the newest of them.
function offScreen(s: DictationState): boolean {
  const d = s.into;
  if (!d) return false;
  return d.kind === "clipboard" || !onScreen(d) || (d.kind === "field" && !!s.kept);
}

// Where the place is, beside its name, when that is not simply "here": a chat
// in the side panel or not open, a field waiting to come back or hidden.
function placeWord(s: DictationState): string | null {
  const d = s.into;
  if (!d || d.kind === "clipboard") return null;
  if (d.kind === "chat") {
    const v = shownIn(d.chat);
    return v === "beside" ? "beside" : v ? null : "not open";
  }
  if (s.waiting) return "waiting";
  return onScreen(d) ? null : "not shown";
}

function newest(s: DictationState): string {
  const all = [s.kept ?? s.said, s.open].filter(Boolean).join(" ");
  return all.length > NEWEST ? all.slice(all.length - NEWEST) : all;
}

/** Open where the words are going, when it is not on screen. */
function Where({ s }: { s: DictationState }) {
  const navigate = useNavigate();
  const d = s.into;
  const away = !!d && (d.kind === "field" || d.kind === "chat") && !onScreen(d);
  const open = away && d.kind === "field" ? () => navigate(d.href)
    : away && d.kind === "chat" ? () => openChat(d.chat)
    : null;
  if (!open) return null;
  return (
    <Button size="sm" variant="ghost" onClick={open} title={`Open ${d!.name}`}>
      <ArrowUpRight aria-hidden /> Open
    </Button>
  );
}

function ModeIcon({ mode }: { mode: keyof typeof MODE_ICON }) {
  const Icon = MODE_ICON[mode];
  return <Icon aria-hidden />;
}

/** `showError`: the error has no chip to show it beside, so the bar says it. */
export function DictationPill({ state: s, showError }: { state: DictationState; showError: boolean }) {
  useLocation();
  useOpenChats();
  const ref = useRef<HTMLDivElement>(null);
  const slot = useRef<HTMLDivElement>(null);
  const { spot, dragging, home, gliding, onPointerDown } = useBarDrag(ref, slot);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  useLayoutEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (r && (r.width !== size?.width || r.height !== size?.height)) setSize({ width: r.width, height: r.height });
  });
  const [choosing, setChoosing] = useState(false);
  const [asking, setAsking] = useState(false);
  const [all, setAll] = useState(false);
  const [pieces, setPieces] = useState(false);
  const active = dictation.busy;
  const error = s.phase === "error" && showError && !s.blocked ? s.error : null;
  if (!active && !s.notice && !error) return null;

  const d = s.into;
  const Icon = d ? KIND_ICON[d.kind] : null;
  const word = status(s);
  const live = s.phase === "listening" && s.connected && !s.paused;
  const style = spot ? ({ position: "fixed", left: spot.left, top: spot.top } as CSSProperties) : undefined;
  const chat = d?.kind === "chat" ? d.chat : null;
  const at = placeWord(s);
  const count = s.mode === "commands" && s.phase === "listening" ? "Not writing"
    : word ?? wordCount([s.said, s.pending].filter(Boolean).join(" "));

  const held = home ?? size;
  return (
    <>
      {spot && held && (
        <div ref={slot} className="ws-dictpill-slot" style={{ width: held.width, height: held.height }} aria-hidden />
      )}
      <div
        ref={ref}
        className={"ws-dictpill" + (spot ? " ws-dictpill--free" : "") + (dragging ? " is-dragging" : "")
        + (gliding ? " is-gliding" : "")
        + (s.notice || error ? " has-notice" : "")}
        style={style}
        data-phase={s.phase}
        data-dictation="off"
        role="region"
        aria-label="Dictation"
        onPointerDown={keepFocus}
        onMouseDown={keepFocus}
      >
        <span className="ws-dictpill__grip" onPointerDown={onPointerDown} aria-hidden title="Drag to move">
          <GripVertical />
        </span>
        {active && (
          <span className="ws-dictpill__state" style={{ "--level": s.level.toFixed(2) } as CSSProperties}>
            {live ? <span className="ws-dictpill__dot" aria-hidden />
              : s.paused && s.phase === "listening" ? <Pause aria-hidden />
              : <Loader2 className="ws-spin" aria-hidden />}
          </span>
        )}
        {active && (
          <ActionMenu
            state={s}
            onModel={() => setAsking(true)}
            onAll={() => setAll(true)}
            trigger={(
              <Button size="sm" variant="ghost" icon className="ws-dictpill__actions" aria-label="Actions" title="Actions">
                <Zap aria-hidden />
              </Button>
            )}
          />
        )}
        {active && s.mode && (
          <ModeMenu
            mode={s.mode}
            trigger={(
              <button type="button" className="ws-dictpill__where ws-dictpill__mode" aria-label={`Mode: ${modeLabel(s.mode)}`}
                title={`Mode: ${modeLabel(s.mode)}`}>
                <ModeIcon mode={s.mode} />
              </button>
            )}
          />
        )}
        {active && d && Icon && (
          <PlaceMenu
            state={s}
            onChooseChat={() => setChoosing(true)}
            onPieces={() => setPieces(true)}
            trigger={(
              <button type="button" className="ws-dictpill__where" title={at ? `${d.name} (${at})` : d.name}>
                <Icon aria-hidden />
                <span className="ws-dictpill__name">{d.name}</span>
                {at && <span className="ws-dictpill__at">{at}</span>}
              </button>
            )}
          />
        )}
        {active && <Where s={s} />}
        {active && <span className="ws-dictpill__count">{count}</span>}
        {error ? <span className="ws-dictpill-notice"><span className="ws-dictpill-notice__text" title={error}>{error}</span></span>
          : s.notice ? <PillNotice notice={s.notice} />
          : active && offScreen(s) ? <span className="ws-dictpill__newest" aria-hidden><span>{newest(s)}</span></span>
          : null}
        {s.phase === "listening" && (
          <Button size="sm" variant="ghost" icon className="ws-dictpill__pause" onClick={() => dictation.pause(!s.paused)}
            aria-label={s.paused ? "Resume" : "Pause"} title={s.paused ? "Resume" : "Pause"}>
            {s.paused ? <Play aria-hidden /> : <Pause aria-hidden />}
          </Button>
        )}
        {s.phase === "listening" && (
          <Button size="sm" variant="ghost" icon onClick={() => void dictation.stop(true)} aria-label="Stop" title="Stop (Esc)">
            <Square aria-hidden />
          </Button>
        )}
      </div>
      <ChatPicker
        open={choosing}
        onClose={() => setChoosing(false)}
        onPick={(t) => { dictation.chooseDestination(chatPlace(t)); setChoosing(false); }}
      />
      {chat && (
        <ModelAsk
          open={asking}
          onClose={() => setAsking(false)}
          onPick={(q) => chatModel(dictation, chat, q, null)}
        />
      )}
      <AllActions open={all} onClose={() => setAll(false)} />
      <PiecesDialog open={pieces} onClose={() => setPieces(false)} state={s} />
    </>
  );
}
