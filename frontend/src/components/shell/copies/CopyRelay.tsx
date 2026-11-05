// Every copy made in the Workshop is reported to the backend, which tells the
// apps subscribed to the "copied" event (backend/services/app_events.py).
//
// Two kinds of copy, on this document and on every same-origin app frame in it
// at any depth (an event inside a frame never reaches this window):
//
// - the DOM `copy` event, for a keyboard shortcut or a menu. The text is read
//   from the selection in the capture phase and replaced by whatever a handler
//   put on the clipboard if the event bubbles back out with some. A handler
//   that stops the event still has its selection reported, a tick later.
// - a write through the document's `navigator.clipboard`, for a copy made from
//   code (any app's copy button, the kit's `copyText`), which fires no DOM
//   event at all (`clipboardWrites.ts`).
//
// In the Mac app, with "Save copies from" set to anything copied on the Mac,
// the app itself reports copies made in other apps (macos ClipboardBridge),
// page open or not; this relay never sees them.
//
// Where a copy goes is the device's choice (`lib/copyPrefs.ts`): reported or
// not, and onto the system clipboard or held in the Workshop. A held copy is
// cancelled before any page handler runs and pasted back by this relay into
// the Workshop field the paste is for, until the operator leaves the Workshop
// (`heldCopy.ts`). An app's write announced with `SYSTEM_COPY_EVENT` goes to
// the system clipboard either way.
//
// Fire and forget: a copy never waits on, or fails because of, the report.

import { useEffect } from "react";
import { SYSTEM_COPY_EVENT, copyPrefs } from "../../../lib/copyPrefs";
import { dictationKindOf } from "../../../lib/speech/targets";
import { watchDocuments } from "../sameOriginFrames";
import { holdCopyEvent, wrapWrites } from "./clipboardWrites";
import { copiedReport, fieldSelection } from "./copyReport";
import { listenForCopyPrefs } from "./copyPrefsBridge";
import { heldCopy } from "./heldCopy";

function report(text: string): void {
  heldCopy.took(text);
  if (!copyPrefs.get().save) return;
  const body = copiedReport(text, window.location.pathname);
  if (!body) return;
  fetch("/api/app-events/copied", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    keepalive: true,
  }).catch(() => {});
}

function selectedText(win: Window): string {
  const el = win.document.activeElement as HTMLInputElement | null;
  if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) {
    try {
      return fieldSelection(el.value, el.selectionStart, el.selectionEnd);
    } catch {
      return "";
    }
  }
  return win.getSelection()?.toString() ?? "";
}

/** A paste into a Workshop field while a held copy is the latest: the held
 *  text goes in instead of the system clipboard's. */
function pasteHeld(win: Window, e: ClipboardEvent): void {
  const text = copyPrefs.held() ? heldCopy.current() : null;
  const doc = win.document;
  if (text == null || !dictationKindOf(doc.activeElement)) return;
  if (!doc.execCommand("insertText", false, text)) return;
  e.preventDefault();
  e.stopImmediatePropagation();
}

/** Listen for copies on `win`. Returns the function that stops listening. */
function watch(win: Window): () => void {
  let pending: string | null = null;
  let toSystem = false;
  const flush = () => {
    if (pending != null) report(pending);
    pending = null;
  };
  const onCopyStart = (e: Event) => {
    pending = selectedText(win);
    if (copyPrefs.held()) holdCopyEvent(e as ClipboardEvent, (text) => { pending = text; });
    win.setTimeout(flush, 0);
  };
  const onCopyEnd = (e: Event) => {
    const placed = (e as ClipboardEvent).clipboardData?.getData("text/plain");
    if (placed) pending = placed;
    flush();
  };
  const onSystemCopy = () => {
    toSystem = true;
    win.setTimeout(() => { toSystem = false; }, 0);
  };
  const hold = () => {
    const held = copyPrefs.held() && !toSystem;
    toSystem = false;
    return held;
  };
  const onPaste = (e: Event) => pasteHeld(win, e as ClipboardEvent);
  const clip = win.navigator.clipboard;
  const unwrap = clip ? wrapWrites(clip, report, hold) : () => {};
  win.addEventListener("copy", onCopyStart, true);
  win.addEventListener("copy", onCopyEnd);
  win.addEventListener("paste", onPaste, true);
  win.addEventListener(SYSTEM_COPY_EVENT, onSystemCopy);
  return () => {
    win.removeEventListener("copy", onCopyStart, true);
    win.removeEventListener("copy", onCopyEnd);
    win.removeEventListener("paste", onPaste, true);
    win.removeEventListener(SYSTEM_COPY_EVENT, onSystemCopy);
    unwrap();
  };
}

/** Marks the Workshop left when another app comes to the front or the page
 *  is hidden; focus moving into an app frame is not leaving. */
function watchLeaving(): () => void {
  const onBlur = () => window.setTimeout(() => { if (!document.hasFocus()) heldCopy.left(); }, 0);
  const onVisibility = () => { if (document.hidden) heldCopy.left(); };
  window.addEventListener("blur", onBlur);
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    window.removeEventListener("blur", onBlur);
    document.removeEventListener("visibilitychange", onVisibility);
  };
}

export function CopyRelay() {
  useEffect(() => watchDocuments((doc) => {
    const win = doc.defaultView;
    return win ? watch(win) : () => {};
  }), []);
  useEffect(watchLeaving, []);
  useEffect(() => {
    void copyPrefs.load();
    return listenForCopyPrefs();
  }, []);
  return null;
}
