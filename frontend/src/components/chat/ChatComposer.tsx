import type { RefObject } from "react";
import { AudioLines, BotMessageSquare, CornerDownLeft, Maximize2, Minimize2, PhoneOff, Square } from "lucide-react";
import { Menu, MenuItem } from "@workshop/ui";
import type { ChatMsg } from "../../lib/chat";
import type { VoiceSnapshot, VoicePrefs } from "../../lib/voiceSession";
import AttachmentChips, { AttachButton } from "./AttachmentChips";
import type { Attachment } from "./attachments";
import { filesFromTransfer } from "./useAttachments";
import CommandPicker, { type PickerOption } from "./CommandPicker";
import ContextMeter from "./ContextMeter";
import { VoiceStrip, VoiceCluster } from "./VoiceControls";
import { useVoiceComposer } from "./useVoiceComposer";
import { modelLabel, orderModels } from "./models";
import EffortMeter from "./EffortMeter";
import { permissionModeDef } from "./permissionModes";
import { keepFocusOnTap } from "./keepFocus";

// Everything below the log: the type-ahead pickers, the voice strip, the input,
// and the per-turn controls.
//
// The controls here are all PER-TURN knobs — model, voice, send, stop —
// which is why the model picker lives here and not in the header beside the
// things that change the whole surface (scope, which chat you are in).
//
// One rule runs through every button in this file: tapping an action never
// blurs the textarea. On mobile a blur closes the keyboard, the layout jumps,
// and the tap lands where the button no longer is — the "send needs two taps"
// bug. It is enforced ONCE, by `keepFocusOnTap` on the action row, not by a
// handler per button; the per-button version cancelled `mousedown`, which on
// touch arrives after focus has already moved and so did nothing.

export default function ChatComposer({
  composerRef, inputRef,
  input, onChange, onKeyDown, placeholder,
  focused, onFocusChange,
  expanded, onToggleExpand,
  // Type-ahead
  mentionOptions, mentionSelected, showMentions, onMentionHover, onMentionPick,
  // Turn controls
  streaming, onSend, onStop, canSend,
  modelOptions, selectedModel, onSelectModel,
  effortLevels, effectiveEffort, onSelectEffort,
  modeOptions, effectivePermissionMode, onSelectPermissionMode,
  // Context meter
  activeId, messages, ctxNonce,
  // Attachments
  attachments, attachNotice, onAttachFiles, onRemoveAttachment, onDismissAttachNotice,
  onPasteFiles,
  // Voice
  voiceOn, onToggleVoice, isBuild,
  vsnap, vprefs, vsession, orbRef, onUpdatePrefs, onOpenVoiceSettings, onOpenContext,
}: {
  composerRef: RefObject<HTMLDivElement>;
  inputRef: RefObject<HTMLTextAreaElement>;
  input: string;
  onChange: (v: string) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  placeholder: string;
  focused: boolean;
  onFocusChange: (v: boolean) => void;
  /** The input is held open at a tall fixed height instead of tracking content. */
  expanded: boolean;
  onToggleExpand: () => void;
  mentionOptions: PickerOption[];
  mentionSelected: number;
  showMentions: boolean;
  onMentionHover: (i: number) => void;
  onMentionPick: (key: string) => void;
  streaming: boolean;
  onSend: () => void;
  onStop: () => void;
  canSend: boolean;
  modelOptions: string[];
  selectedModel: string;
  onSelectModel: (id: string) => void;
  effortLevels: string[];
  effectiveEffort: string;
  onSelectEffort: (level: string) => void;
  modeOptions: string[];
  effectivePermissionMode: string;
  onSelectPermissionMode: (mode: string) => void;
  activeId: string | null;
  messages: ChatMsg[];
  ctxNonce: number;
  attachments: Attachment[];
  attachNotice: string | null;
  onAttachFiles: (files: File[]) => void;
  onRemoveAttachment: (localId: string) => void;
  onDismissAttachNotice: () => void;
  /** Files pasted into the textarea (a screenshot from the clipboard). */
  onPasteFiles: (files: File[]) => void;
  voiceOn: boolean;
  onToggleVoice: () => void;
  isBuild: boolean;
  vsnap: VoiceSnapshot;
  vprefs: VoicePrefs;
  vsession: {
    interrupt: () => void; cancelTurn: () => void;
    forceEndUtterance: () => void; retry: () => void;
  };
  orbRef: RefObject<HTMLButtonElement>;
  onUpdatePrefs: (p: Partial<VoicePrefs>) => void;
  onOpenVoiceSettings: () => void;
  /** Open the context breakdown from the meter. */
  onOpenContext: () => void;
}) {
  const voice = useVoiceComposer(vsnap, vprefs, vsession);

  return (
    <div
      ref={composerRef}
      className={`chat-composer${streaming ? " is-streaming" : ""}${focused ? " is-focused" : ""}${expanded ? " is-expanded" : ""}`}
    >
      {showMentions && (
        <CommandPicker
          options={mentionOptions}
          selected={mentionSelected}
          ariaLabel="Agents you can mention"
          onHover={onMentionHover}
          onPick={onMentionPick}
        />
      )}
      {voiceOn && <VoiceStrip status={voice.status} onRetry={() => vsession.retry()} />}

      {/* ABOVE the textarea, inside the composer's surface, so the files read as
          part of the message being written rather than as a separate tray. */}
      <AttachmentChips
        items={attachments}
        notice={attachNotice}
        onRemove={onRemoveAttachment}
        onDismissNotice={onDismissAttachNotice}
      />

      {/* The expand control belongs to the BOX, not to the action row below it.
          The row holds per-turn knobs (model, effort, send); how tall you want
          to type is a property of the input itself, so it sits on the input,
          top-right, in the corner every editor puts it in.

          It overlays rather than taking a column, and the textarea reserves the
          width back as right padding: overlaying WITHOUT the reservation means
          the first line of text runs under the button, which is the one place
          text is guaranteed to be when the box is collapsed to a single row.

          keepFocusOnTap sits on the wrapper, not the button, and the textarea is
          exempt inside it, so expanding never closes the mobile keyboard, and
          tapping the text still moves the caret. */}
      <div className="chat-input-wrap" onPointerDown={keepFocusOnTap}>
        <textarea
          ref={inputRef}
          className="chat-input"
          rows={1}
          value={input}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={onKeyDown}
          // Label the Return key on the soft keyboard with what it will actually
          // do. A fixed "enter" is right rather than lazy: this hint is drawn
          // ONLY by a virtual keyboard, and on a virtual keyboard Return makes a
          // new line (see chat/enterKey). A keyboard that would send on Return is
          // a physical one, which draws no hint at all.
          enterKeyHint="enter"
          onFocus={() => onFocusChange(true)}
          onBlur={() => onFocusChange(false)}
          // A pasted screenshot attaches instead of pasting its filename as text.
          // Only when the clipboard actually carries a file: ordinary text paste
          // must fall through untouched, which is why this does not preventDefault
          // unconditionally.
          onPaste={(e) => {
            const files = filesFromTransfer(e.clipboardData);
            if (!files.length) return;
            e.preventDefault();
            onPasteFiles(files);
          }}
        />
        <button
          className="chat-expand"
          onClick={onToggleExpand}
          aria-pressed={expanded}
          aria-label={expanded ? "Collapse the message box" : "Expand the message box"}
          title={expanded ? "Collapse the message box" : "Expand the message box"}
        >
          {expanded ? <Minimize2 size={14} aria-hidden /> : <Maximize2 size={14} aria-hidden />}
        </button>
      </div>

      {/* One handler for the whole row: a tap here never steals focus from the
          textarea, so it never spends itself closing the mobile keyboard
          instead of pressing the button. See keepFocus. */}
      <div className="chat-actions" onPointerDown={keepFocusOnTap}>
        {!voiceOn && (
          <ContextMeter
            activeId={activeId}
            streaming={streaming}
            messages={messages}
            draft={input}
            model={selectedModel}
            nonce={ctxNonce}
            onOpen={onOpenContext}
          />
        )}
        <span className="chat-actions__spacer" />

        {/* Effort sits left of the model because it reads as a modifier of it:
            the ladder's length is a property of the model beside it. */}
        <EffortMeter
          levels={effortLevels}
          value={effectiveEffort}
          onSelect={onSelectEffort}
        />

        {/* How much this chat asks before acting. Sits between effort and model
            because it is the same class of per-turn knob, and because the three
            read left to right as "how hard, how careful, which model". */}
        {modeOptions.length > 1 && (
          <Menu
            label="Mode"
            side="top"
            trigger={
              <button
                className="chat-mode"
                title={`Mode: ${permissionModeDef(effectivePermissionMode).label}. ${permissionModeDef(effectivePermissionMode).hint}`}
                aria-label={`Mode: ${permissionModeDef(effectivePermissionMode).label}`}
              >
                {(() => {
                  const Icon = permissionModeDef(effectivePermissionMode).Icon;
                  return <Icon size={14} aria-hidden />;
                })()}
                <span className="chat-mode__val">
                  {permissionModeDef(effectivePermissionMode).label}
                </span>
              </button>
            }
          >
            {modeOptions.map((id) => {
              const def = permissionModeDef(id);
              return (
                <MenuItem
                  key={id}
                  checked={id === effectivePermissionMode}
                  danger={def.danger}
                  title={def.hint}
                  icon={<def.Icon size={14} aria-hidden />}
                  onSelect={() => onSelectPermissionMode(id)}
                >
                  {def.label}
                </MenuItem>
              );
            })}
          </Menu>
        )}

        {/* Menu rather than <select>: the stock double-arrow was the loudest
            element in a row of quiet icon buttons.

            LEADING ICON, NO CARET. The caret was the last of that double-arrow
            idiom left in the row: effort and mode both open the same way and
            neither draws one, so it marked this control as a different kind of
            thing when it is not. The icon that replaces it sits where theirs
            sit, so the three read as one set. */}
        {modelOptions.length > 1 ? (
          <Menu
            label="Model"
            side="top"
            trigger={
              <button className="chat-model" title="Model for this conversation">
                <BotMessageSquare size={14} aria-hidden />
                <span className="chat-model__val">{modelLabel(selectedModel)}</span>
              </button>
            }
          >
            {orderModels(modelOptions).map((id) => (
              <MenuItem key={id} checked={id === selectedModel} onSelect={() => onSelectModel(id)}>
                {modelLabel(id)}
              </MenuItem>
            ))}
          </Menu>
        ) : modelOptions.length === 1 ? (
          <span className="chat-model is-static">
            <BotMessageSquare size={14} aria-hidden />
            <span className="chat-model__val">{modelLabel(modelOptions[0])}</span>
          </span>
        ) : null}

        {voiceOn && (
          <VoiceCluster
            orbRef={orbRef}
            orbState={vsnap.state}
            orbIcon={voice.orbIcon}
            orbLabel={voice.orbLabel}
            onOrb={voice.orbAction}
            micEnabled={vprefs.mic_enabled}
            onToggleMic={() => onUpdatePrefs({ mic_enabled: !vprefs.mic_enabled })}
            onOpenSettings={onOpenVoiceSettings}
            errored={voice.errored}
          />
        )}

        {/* Attach sits immediately LEFT of voice, where the operator asked for
            it: the two are the same class of control, things you add to a
            message that are not the message. */}
        <AttachButton onFiles={onAttachFiles} />

        {/* Distinct glyph on/off so the exit toggle never reads as a twin of the
            listening orb's AudioLines: AudioLines invites voice, PhoneOff leaves it. */}
        <button
          className={`chat-slash-btn${voiceOn ? " is-active" : ""}`}
         
          onClick={onToggleVoice}
          aria-pressed={voiceOn}
          aria-label={voiceOn ? "Exit voice mode" : "Voice mode"}
          title={voiceOn ? "Exit voice mode" : (isBuild ? "Voice mode: build by talking" : "Voice mode: talk instead of type")}
        >
          {voiceOn ? <PhoneOff size={16} aria-hidden /> : <AudioLines size={16} aria-hidden />}
        </button>

        {/* Stop sits left of Send: Send is the primary, most-reached-for control
            (every turn ends at it), so it stays rightmost and closest to Enter's
            physical position; Stop is the occasional one and sits beside it
            rather than past it. */}
        {streaming && (
          <button
            className="chat-stop"
           
            onClick={onStop}
            aria-label="Stop the turn"
            title="Stop the turn"
          >
            <Square size={16} aria-hidden />
          </button>
        )}
        <button
          className="chat-send"
         
          onClick={onSend}
          disabled={!canSend}
          // Not "Send (Enter)" any more. Enter only sends on a physical
          // keyboard; on a phone it makes a new line, so the old label was a
          // false instruction on exactly the device that most needed the
          // button. The shortcut is standard enough not to need announcing.
          aria-label="Send"
          title={streaming ? "Send (queues behind the current turn)" : "Send"}
        >
          <CornerDownLeft size={16} aria-hidden />
        </button>
      </div>
    </div>
  );
}
