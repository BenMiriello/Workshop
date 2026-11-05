import { useSyncExternalStore } from "react";
import { ArrowDown, ArrowUp, Highlighter } from "lucide-react";
import { Button } from "@workshop/ui";
import { speechPrefs, useSpeechPrefs } from "../../../lib/speech/prefs";
import { follow, goToReading } from "./readFollow";

/** Highlight on the read aloud player: a setting that stays
 *  (readHighlight.ts). Turning it on goes to the sentence being read and
 *  follows it (readFollow.ts). */
export function HighlightButton() {
  const { readHighlight: on } = useSpeechPrefs();
  const toggle = () => {
    speechPrefs.set({ readHighlight: !on });
    if (!on) goToReading();
  };
  return (
    <Button variant="ghost" size="sm" icon className="ws-speech-player__highlight" aria-pressed={on} onClick={toggle}
      aria-label="Highlight what is read" title={on ? "Stop highlighting" : "Highlight what is read"}>
      <Highlighter aria-hidden />
    </Button>
  );
}

/** Over the player while the page is scrolled away from the sentence being
 *  read and it is out of view: goes back to it and follows again, its arrow
 *  pointing to it. */
export function BackToReading({ placed }: { placed: boolean }) {
  const { readHighlight: on } = useSpeechPrefs();
  const state = useSyncExternalStore(follow.subscribe, follow.getState);
  if (!on || !placed || state.on || !state.away) return null;
  const Icon = state.away === "up" ? ArrowUp : ArrowDown;
  return (
    <Button size="sm" className="ws-speech-player__back" onClick={goToReading}>
      <Icon aria-hidden /> Back to reading
    </Button>
  );
}
