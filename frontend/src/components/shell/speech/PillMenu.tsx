// The dictation bar's three menus. Opened from where the words are going:
// change the destination (the clipboard, the focused field, an open chat, a
// recent place, any chat). Opened from the Actions button: what can be done to
// a chat by voice, each showing the phrase that does it, and below them the
// history of actions taken, to undo or redo (ActionHistoryView.tsx). Opened
// from the Mode button: the three modes (dictationMode.ts), then numbered
// labels on the page and the Commands list.

import { useSyncExternalStore } from "react";
import { useNavigate } from "react-router-dom";
import {
  Blend, Clipboard, Command, Cpu, ListOrdered, ListTree, MessageSquare, PanelRight, Search, Send, Settings2, SquarePen, Tags,
  TextCursorInput, Type, type LucideIcon,
} from "lucide-react";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@workshop/ui";
import { openChats } from "../../../lib/speech/chatTargets";
import { phraseFor, SPEECH_MODES, useSpeechCommands, type SpeechMode } from "../../../lib/speech/commandsApi";
import { recentPlaces } from "../../../lib/speech/recentPlaces";
import { HistorySection } from "./ActionHistoryView";
import { dictation, type DictationState } from "./dictation";
import { chatPlace, CLIPBOARD, fromRecent, placeKey, samePlace, type Destination } from "./destination";
import { pageLabels } from "./labels/pageLabels";
import { ctxOf, VOICE_ACTIONS } from "./voiceActions";
import { showCommands } from "./commandsList";
import { runCommand } from "./voiceCommands";

const RECENT_SHOWN = 5;

export const KIND_ICON: Record<Destination["kind"], LucideIcon> = {
  field: TextCursorInput, chat: MessageSquare, clipboard: Clipboard,
};

function act(name: string): void {
  runCommand(dictation, { name, arg: null, words: "", from: 0, to: 0 });
}

function Place({ d, into }: { d: Destination; into: Destination | null }) {
  const Icon = KIND_ICON[d.kind];
  return (
    <MenuItem icon={<Icon aria-hidden />} checked={samePlace(d, into)} onSelect={() => dictation.chooseDestination(d)}>
      {d.name}
    </MenuItem>
  );
}

export function places(s: DictationState): Destination[] {
  const list: Destination[] = [CLIPBOARD];
  const add = (d: Destination) => { if (!list.some((x) => samePlace(x, d))) list.push(d); };
  if (s.into) add(s.into);
  if (s.here) add(s.here);
  openChats().forEach((c) => add(chatPlace(c)));
  recentPlaces.get().slice(0, RECENT_SHOWN).forEach((r) => add(fromRecent(r)));
  return list;
}

type Trigger = React.ReactElement<Record<string, unknown>>;

export function PlaceMenu({ state, trigger, onChooseChat, onPieces }: {
  state: DictationState;
  trigger: Trigger;
  onChooseChat: () => void;
  onPieces: () => void;
}) {
  const n = state.pieces.length;
  return (
    <Menu trigger={trigger} label="Where text goes" side="top">
      <MenuLabel>Send text to</MenuLabel>
      {places(state).map((d) => <Place key={placeKey(d)} d={d} into={state.into} />)}
      <MenuItem icon={<Search aria-hidden />} onSelect={onChooseChat}>Choose a chat…</MenuItem>
      {n > 0 && <MenuSeparator />}
      {n > 0 && (
        <MenuItem icon={<ListTree aria-hidden />} onSelect={onPieces}>
          {`Where words went (${n})…`}
        </MenuItem>
      )}
    </Menu>
  );
}

export function ActionMenu({ state, trigger, onModel, onAll }: {
  state: DictationState;
  trigger: Trigger;
  onModel: () => void;
  onAll: () => void;
}) {
  const navigate = useNavigate();
  const { data: commands } = useSpeechCommands();
  const say = (id: string) => {
    const p = phraseFor(commands, id);
    return p ? `Say "${p}"` : undefined;
  };
  const chat = state.into?.kind === "chat";
  return (
    <Menu trigger={trigger} label="Actions" side="top">
      <MenuLabel>Actions</MenuLabel>
      {chat && <MenuItem icon={<Send aria-hidden />} title={say("send")} onSelect={() => act("send")}>Send message</MenuItem>}
      <MenuItem icon={<SquarePen aria-hidden />} title={say("new_chat")} onSelect={() => act("new_chat")}>New chat</MenuItem>
      {chat && <MenuItem icon={<Cpu aria-hidden />} title={say("chat_model")} onSelect={onModel}>Model…</MenuItem>}
      {chat && <MenuItem icon={<PanelRight aria-hidden />} title={say("open_chat")} onSelect={() => act("open_chat")}>Open chat</MenuItem>}
      <MenuSeparator />
      <MenuItem icon={<Settings2 aria-hidden />} onSelect={() => navigate("/settings/speech#commands")}>
        Spoken commands…
      </MenuItem>
      <HistorySection onAll={onAll} />
    </Menu>
  );
}

export const MODE_ICON: Record<SpeechMode, LucideIcon> = {
  dictation: Type, mixed: Blend, commands: Command,
};

export function ModeMenu({ mode, trigger }: { mode: SpeechMode; trigger: Trigger }) {
  const labels = useSyncExternalStore(pageLabels.subscribe, pageLabels.get);
  const { data: commands } = useSpeechCommands();
  const say = (id: string) => {
    const p = phraseFor(commands, id, mode);
    return p ? `Say "${p}"` : undefined;
  };
  const SWITCH: Record<SpeechMode, string> = { dictation: "dictation_only", mixed: "mixed_mode", commands: "commands_only" };
  return (
    <Menu trigger={trigger} label="Mode" side="top">
      <MenuLabel>Mode</MenuLabel>
      {SPEECH_MODES.map((m) => {
        const Icon = MODE_ICON[m.id];
        return (
          <MenuItem key={m.id} icon={<Icon aria-hidden />} checked={m.id === mode} title={say(SWITCH[m.id])}
            onSelect={() => dictation.setMode(m.id)}>
            {m.label}
          </MenuItem>
        );
      })}
      <MenuSeparator />
      <MenuItem icon={<Tags aria-hidden />} checked={labels.on} title={say(labels.on ? "hide_labels" : "show_labels")}
        onSelect={() => pageLabels.toggle()}>
        Show labels
      </MenuItem>
      <MenuItem icon={<ListOrdered aria-hidden />} title={say("show_commands")}
        onSelect={() => void showCommands(ctxOf(dictation), VOICE_ACTIONS)}>
        Commands…
      </MenuItem>
    </Menu>
  );
}
