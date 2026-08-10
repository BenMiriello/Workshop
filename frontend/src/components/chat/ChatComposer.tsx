import type { RefObject } from "react";
import { AudioLines, ChevronDown, CornerDownLeft, PhoneOff, Slash, Square } from "lucide-react";
import { Menu, MenuItem } from "../../ui";
import type { ChatMsg } from "../../lib/chat";
import type { VoiceSnapshot, VoicePrefs } from "../../lib/voiceSession";
import CommandPicker, { type PickerOption } from "./CommandPicker";
import ContextMeter from "./ContextMeter";
import { VoiceStrip, VoiceCluster } from "./VoiceControls";
import { useVoiceComposer } from "./useVoiceComposer";
import { modelLabel, orderModels } from "./models";
import EffortMeter from "./EffortMeter";
import { permissionModeDef } from "./permissionModes";

// Everything below the log: the type-ahead pickers, the voice strip, the input,
// and the per-turn controls.
//
// The controls here are all PER-TURN knobs — model, voice, slash, send, stop —
// which is why the model picker lives here and not in the header beside the
// things that change the whole surface (scope, which chat you are in).
//
// One rule runs through every button in this file: `onMouseDown` calls
// preventDefault so tapping an action never blurs the textarea. On mobile a blur
// closes the keyboard, the layout jumps, and the tap lands where the button no
// longer is — the "send needs two taps" bug.

export default function ChatComposer({
  composerRef, inputRef,
  input, onChange, onKeyDown, placeholder,
  focused, onFocusChange,
  // Type-ahead
  slashOptions, slashSelected, showSlash, onSlashHover, onSlashPick, onToggleSlash,
  mentionOptions, mentionSelected, showMentions, onMentionHover, onMentionPick,
  // Turn controls
  streaming, onSend, onStop, canSend,
  modelOptions, selectedModel, onSelectModel,
  effortLevels, effectiveEffort, onSelectEffort,
  modeOptions, effectivePermissionMode, onSelectPermissionMode,
  // Context meter
  activeId, messages, ctxNonce,
  // Voice
  voiceOn, onToggleVoice, isBuild,
  vsnap, vprefs, vsession, orbRef, onUpdatePrefs, onOpenVoiceSettings,
}: {
  composerRef: RefObject<HTMLDivElement>;
  inputRef: RefObject<HTMLTextAreaElement>;
  input: string;
  onChange: (v: string) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  placeholder: string;
  focused: boolean;
  onFocusChange: (v: boolean) => void;
  slashOptions: PickerOption[];
  slashSelected: number;
  showSlash: boolean;
  onSlashHover: (i: number) => void;
  onSlashPick: (key: string) => void;
  onToggleSlash: () => void;
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
}) {
  const voice = useVoiceComposer(vsnap, vprefs, vsession);

  return (
    <div
      ref={composerRef}
      className={`chat-composer${streaming ? " is-streaming" : ""}${focused ? " is-focused" : ""}`}
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
      {showSlash && (
        <CommandPicker
          options={slashOptions}
          selected={slashSelected}
          ariaLabel="Slash commands"
          onHover={onSlashHover}
          onPick={onSlashPick}
        />
      )}
      {voiceOn && <VoiceStrip status={voice.status} onRetry={() => vsession.retry()} />}

      <textarea
        ref={inputRef}
        className="chat-input"
        rows={1}
        value={input}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => onFocusChange(true)}
        onBlur={() => onFocusChange(false)}
      />

      <div className="chat-actions">
        {!voiceOn && (
          <ContextMeter
            activeId={activeId}
            streaming={streaming}
            messages={messages}
            draft={input}
            model={selectedModel}
            nonce={ctxNonce}
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
            // Same reasoning as EffortMeter, just beside it: this sits early/left
            // in the row, so the popover opens rightward, not leftward off the
            // edge of a narrow phone screen.
            align="start"
            side="top"
            trigger={
              <button
                className="chat-mode"
                onMouseDown={(e) => e.preventDefault()}
                title={`${permissionModeDef(effectivePermissionMode).label}. ${permissionModeDef(effectivePermissionMode).hint}`}
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
            element in a row of quiet icon buttons. */}
        {modelOptions.length > 1 ? (
          <Menu
            label="Model"
            align="end"
            side="top"
            trigger={
              <button className="chat-model" onMouseDown={(e) => e.preventDefault()} title="Model for this conversation">
                <span className="chat-model__val">{modelLabel(selectedModel)}</span>
                <ChevronDown size={14} aria-hidden className="chat-model__caret" />
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

        {/* Distinct glyph on/off so the exit toggle never reads as a twin of the
            listening orb's AudioLines: AudioLines invites voice, PhoneOff leaves it. */}
        <button
          className={`chat-slash-btn${voiceOn ? " is-active" : ""}`}
          onMouseDown={(e) => e.preventDefault()}
          onClick={onToggleVoice}
          aria-pressed={voiceOn}
          aria-label={voiceOn ? "Exit voice mode" : "Voice mode"}
          title={voiceOn ? "Exit voice mode" : (isBuild ? "Voice mode: build by talking" : "Voice mode: talk instead of type")}
        >
          {voiceOn ? <PhoneOff size={16} aria-hidden /> : <AudioLines size={16} aria-hidden />}
        </button>

        {!voiceOn && (
          <button
            className={`chat-slash-btn${showSlash ? " is-active" : ""}`}
            onMouseDown={(e) => e.preventDefault()}
            onClick={onToggleSlash}
            aria-label="Slash commands"
            aria-expanded={showSlash}
            title="Slash commands"
          >
            <Slash size={16} aria-hidden />
          </button>
        )}

        {/* Stop sits left of Send: Send is the primary, most-reached-for control
            (every turn ends at it), so it stays rightmost and closest to Enter's
            physical position; Stop is the occasional one and sits beside it
            rather than past it. */}
        {streaming && (
          <button
            className="chat-stop"
            onMouseDown={(e) => e.preventDefault()}
            onClick={onStop}
            aria-label="Stop the turn"
            title="Stop the turn"
          >
            <Square size={16} aria-hidden />
          </button>
        )}
        <button
          className="chat-send"
          onMouseDown={(e) => e.preventDefault()}
          onClick={onSend}
          disabled={!canSend}
          aria-label="Send (Enter)"
          title={streaming ? "Send (queues behind the current turn)" : "Send (Enter)"}
        >
          <CornerDownLeft size={16} aria-hidden />
        </button>
      </div>
    </div>
  );
}
