// The system clipboard for the web UI (shared/native-bridge.ts `clipboard`).
// The web view's own clipboard writes only inside a user gesture; dictation
// writes the clipboard when it ends, which may be after the gesture is gone.
//
// The watch: iOS gives no copy events and no reads from the background, so
// with "everything" the shell looks once each time it comes to the front and
// saves a copy made elsewhere since it last left (ClipboardCapture.swift,
// which the Save Clipboard to Workshop action shares). Leaving the front
// marks the clipboard seen, so a copy made inside Workshop, which the page
// reports itself, is not saved twice.
import { AppState } from "react-native";

import type { ClipboardWatchMode } from "@shared/native-bridge";

import WorkshopClipboard from "../modules/workshop-clipboard";
import { API_BASE } from "./config";

function mod() {
  if (!WorkshopClipboard) throw new Error("this build has no clipboard module");
  return WorkshopClipboard;
}

export async function write(text: unknown): Promise<boolean> {
  if (typeof text !== "string") throw new Error("clipboard.write takes a string");
  return mod().setString(text);
}

export async function watchGet(): Promise<ClipboardWatchMode> {
  return mod().watchGet();
}

export async function watchSet(mode: unknown): Promise<ClipboardWatchMode> {
  if (mode !== "workshop" && mode !== "everything") {
    throw new Error('clipboard.watch.set takes "workshop" or "everything"');
  }
  return mod().watchSet(mode);
}

function capture(): void {
  WorkshopClipboard?.captureNew().catch((e) => {
    console.warn("[clipboard] copy not saved:", e instanceof Error ? e.message : e);
  });
}

function share(): void {
  WorkshopClipboard?.share().catch((e) => {
    console.warn("[clipboard] sign-in not shared with the keyboard:", e instanceof Error ? e.message : e);
  });
}

/** Starts the watch for the shell's lifetime, and keeps the keyboard's copy
 *  of the sign-in current. Returns the function that stops. */
export function startWatch(): () => void {
  const clip = WorkshopClipboard;
  if (!clip?.captureNew) return () => {};
  void clip.configure(API_BASE).then(share);
  if (AppState.currentState === "active") capture();
  const sub = AppState.addEventListener("change", (state) => {
    if (state === "active") {
      capture();
      share();
    } else if (state === "background") {
      void clip.markSeen();
      share();
    }
  });
  return () => sub.remove();
}
