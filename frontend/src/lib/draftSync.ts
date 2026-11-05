// The unsent text in a composer.
//
// ONE RULE, and everything here is it: A DRAFT BELONGS TO EXACTLY ONE
// CONVERSATION, AND IT ENDS WHEN THAT TEXT IS SENT.
//
// Both halves were broken, in ways that were reported separately for months and
// were the same bug wearing two coats:
//
//   "I type a message and it shows up in another chat."
//     Every async path here applied its result to `keyRef.current` — the key as
//     of WHENEVER THE REQUEST RESOLVED, not the key it was asked about. Switch
//     chats while a draft fetch is in flight and chat A's text was written into
//     chat B's composer and chat B's storage. Same class of mistake as reading a
//     storage key off a ref React reassigns during render, which is what
//     destroyed the message queue on a thread switch.
//
//   "I send a message and it stays in the box", and its stranger cousin,
//   "I start a new chat and a message I already sent is waiting in it."
//     The composer cleared the draft under the key it held AFTER the send. But
//     sending the first message of a blank chat CHANGES that key: the chat has
//     just become a thread, so the clear landed on `<ctx>::<new id>` (empty
//     anyway) and `<ctx>::new` kept the text forever. Every later blank chat
//     then adopted it. Meanwhile a debounced write from the last keystroke, or
//     an adopt arriving from another surface, could put text back after a clear
//     that had gone through correctly.
//
// So:
//
//   1. THE KEY IS CAPTURED, NEVER RE-READ. Every async apply checks that the
//      key it was asked about is still the key on screen, and drops its result
//      otherwise. There is no path that writes to "wherever we are now".
//   2. THE SERVER CLEARS ON SEND. The send carries `draft_key`, and the backend
//      clears exactly that draft when it accepts the message — under the key it
//      was typed in, whatever the composer's key has become by then. It stores
//      a tombstone with the clear's timestamp, so a write already in flight
//      from before the clear is rejected on ordering rather than resurrecting
//      sent text. See backend/services/draft_store.py.
//   3. NOTHING OLDER THAN A CLEAR IS EVER ADOPTED. The old code approximated
//      this with an eight-second "just sent" window. A window is a guess; the
//      cleared timestamp is the fact.
//
// The three layers below are unchanged in spirit and all still earn their place:
// localStorage for instant restore with the backend down, a BroadcastChannel so
// two tabs on the same chat agree without a round trip, and the server as the
// durable cross-device copy and the arbiter.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ACTIVE_MS, BROADCAST_DEBOUNCE_MS, SERVER_DEBOUNCE_MS, cleanSettings,
  channel, CLIENT_ID, lsRead, lsWrite, serverClear, serverGet, serverPut, subs,
  type DraftAttachment, type DraftPayload, type DraftSettings, type DraftValue, type Sub,
} from "./draftWire";
import { joinAppend } from "./speech/chatTargetsText";

export type { DraftAttachment, DraftSettings } from "./draftWire";

/** Drop-in replacement for useState<string>("") for a chat composer.
 *
 *  `clearForSend` is what the composer calls when the operator hits enter. It
 *  empties the box immediately (they pressed send; it must look sent) and marks
 *  the key cleared so nothing older can put the text back. It does NOT have to
 *  reach the server for that to hold: the send itself carries the key and the
 *  server clears it there, which is the copy that matters. */
export function useDraft(key: string): {
  draft: string;
  setDraft: (v: string) => void;
  /** Files attached to this composer, restored with its text. */
  attachments: DraftAttachment[];
  setAttachments: (a: DraftAttachment[]) => void;
  /** What was picked before a message existed. Meaningful for a blank chat only;
   *  a chat that has a thread keeps these on the thread. */
  settings: DraftSettings;
  /** Merge a pick in. A blank value removes the field: it was never chosen. */
  setSettings: (patch: DraftSettings) => void;
  clearForSend: () => void;
  /** Move what is in the box to another key and empty this one, for a chat that
   *  changes scope under the cursor. Settings stay behind: model lists differ. */
  carryTo: (toKey: string) => void;
} {
  const [draft, setDraftState] = useState<string>(() => lsRead(key).text);
  const [attachments, setAttachmentsState] = useState<DraftAttachment[]>(() => lsRead(key).attachments);
  const [settings, setSettingsState] = useState<DraftSettings>(() => lsRead(key).settings);
  const settingsRef = useRef<DraftSettings>(settings);
  settingsRef.current = settings;
  const attachmentsRef = useRef<DraftAttachment[]>(attachments);
  attachmentsRef.current = attachments;
  // `setAttachments` writes the CURRENT text alongside the new file list, and it
  // is called from callbacks that may have closed over an older render.
  const draftRef = useRef<string>(draft);
  draftRef.current = draft;

  const tsRef = useRef<number>(lsRead(key).ts); // ts of our current value
  const lastEditRef = useRef<number>(0);        // wall-clock of last local keystroke
  // THE KEY THIS HOOK IS CURRENTLY SHOWING. Read only to compare against the key
  // an async result was asked about — never to decide where to write.
  const keyRef = useRef<string>(key);
  keyRef.current = key;
  const bcTimer = useRef<number | undefined>(undefined);
  const srvTimer = useRef<number | undefined>(undefined);
  const pendingSrv = useRef<DraftPayload | null>(null);
  // When each key was last cleared by a send. Per key, not one number: the
  // operator sends in one chat and switches to another, and a clear in the first
  // says nothing about the second.
  const clearedAtRef = useRef<Map<string, number>>(new Map());

  /** Apply a value that came from somewhere else, if it is still relevant.
   *
   *  `forKey` is what the caller ASKED ABOUT. If the composer has moved on, the
   *  answer belongs to a conversation that is no longer on screen and applying
   *  it would show one chat's text in another. */
  const adopt = useCallback((forKey: string, v: DraftValue) => {
    if (keyRef.current !== forKey) return;
    if (v.ts <= (clearedAtRef.current.get(forKey) ?? 0)) return;
    tsRef.current = v.ts;
    setDraftState(v.text);
    setAttachmentsState(v.attachments);
    setSettingsState(v.settings);
    lsWrite(forKey, v.text, v.ts, v.attachments, v.settings);
  }, []);

  const editing = () => Date.now() - lastEditRef.current < ACTIVE_MS;

  // Load this key's draft on mount / when the key changes, then reconcile with
  // the server (adopt if the server copy is newer and we are not typing).
  useEffect(() => {
    const local = lsRead(key);
    tsRef.current = local.ts;
    lastEditRef.current = 0;
    setDraftState(local.text);
    setAttachmentsState(local.attachments);
    setSettingsState(local.settings);
    let cancelled = false;
    void serverGet(key).then((s) => {
      if (cancelled || !s) return;
      if (s.ts > tsRef.current && !editing()) adopt(key, s);
    });
    return () => { cancelled = true; };
  }, [key, adopt]);

  // Live updates: same-browser via BroadcastChannel, cross-device on focus.
  useEffect(() => {
    const sub: Sub = (p) => {
      // Words added from outside (dictation into this chat while its composer
      // was elsewhere) go on the end of whatever is in the box, typing or not.
      // Every copy computes the same text from the same base, so only a box
      // that held something else publishes its result.
      if (p.append !== undefined) {
        if (keyRef.current !== p.key) return;
        const next = joinAppend(draftRef.current, p.append);
        clearedAtRef.current.delete(p.key);
        if (next !== p.text) { setDraftRef.current(next); return; }
        tsRef.current = Math.max(tsRef.current, p.ts);
        draftRef.current = next;
        setDraftState(next);
        return;
      }
      if (p.client_id === CLIENT_ID) return; // our own echo
      if (p.ts <= tsRef.current) return;      // not newer
      if (editing()) return;                  // we are typing — keep ours
      adopt(p.key, {
        text: p.text, ts: p.ts, attachments: p.attachments || [],
        settings: cleanSettings(p.settings),
      });
    };
    let set = subs.get(key);
    if (!set) { set = new Set(); subs.set(key, set); }
    set.add(sub);

    const onFocus = () => {
      if (document.visibilityState === "hidden") return;
      // Asked about THIS key, and applied only if it is still this key.
      void serverGet(key).then((s) => {
        if (s && s.ts > tsRef.current && !editing()) adopt(key, s);
      });
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      set!.delete(sub);
      if (set!.size === 0) subs.delete(key);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [key, adopt]);

  // Flush a pending server write when the tab is hidden or unmounts, so a draft
  // typed in the last debounce window still reaches the durable store.
  useEffect(() => {
    const flush = () => {
      const p = pendingSrv.current;
      if (p) { pendingSrv.current = null; serverPut(p); }
    };
    window.addEventListener("pagehide", flush);
    return () => { flush(); window.removeEventListener("pagehide", flush); };
  }, []);

  // ONE WRITE PATH for both halves of a draft, so text and files can never be
  // stored under different keys or at inconsistent times.
  const write = useCallback((text: string, atts: DraftAttachment[], sets: DraftSettings) => {
    // Captured, so a key change mid-debounce cannot misroute the write.
    const forKey = keyRef.current;
    const ts = Date.now();
    tsRef.current = ts;
    lastEditRef.current = ts;
    lsWrite(forKey, text, ts, atts, sets); // synchronous — never lose a keystroke on reload
    // Typing again is the operator taking this composer back after a send. Only
    // a write WITH CONTENT is that. An empty write also flows through here (the
    // attachment list emptying right after a send is the live case), and letting
    // it delete the tombstone re-opened the exact window the tombstone closes:
    // the next stale adopt restored the sent message to the box.
    if (text || atts.length > 0) clearedAtRef.current.delete(forKey);
    // A settings-only write is deliberately NOT typing: it leaves the tombstone
    // alone for the same reason an empty write does. A pick made on a blank chat
    // after a send must not re-open the window the tombstone closes.

    const payload: DraftPayload = {
      key: forKey, text, ts, client_id: CLIENT_ID, attachments: atts, settings: sets,
    };
    window.clearTimeout(bcTimer.current);
    bcTimer.current = window.setTimeout(() => {
      channel?.postMessage(payload);
    }, BROADCAST_DEBOUNCE_MS);

    pendingSrv.current = payload;
    window.clearTimeout(srvTimer.current);
    srvTimer.current = window.setTimeout(() => {
      const p = pendingSrv.current;
      pendingSrv.current = null;
      if (p) serverPut(p);
    }, SERVER_DEBOUNCE_MS);
  }, []);

  const setDraft = useCallback((v: string) => {
    setDraftState(v);
    // Before the render, so a `setAttachments` in the same tick stores this text.
    draftRef.current = v;
    write(v, attachmentsRef.current, settingsRef.current);
  }, [write]);
  const setDraftRef = useRef(setDraft);
  setDraftRef.current = setDraft;

  /** The composer's files changed: one arrived, or one was removed. Stored with
   *  the text, because they are one thing — what is in the box and not sent. */
  const setAttachments = useCallback((a: DraftAttachment[]) => {
    setAttachmentsState(a);
    attachmentsRef.current = a;
    write(draftRef.current, a, settingsRef.current);
  }, [write]);

  /** The whole record is last-write-wins, so a device that picks before its read
   *  of the server returns can overwrite a newer pick made elsewhere. Text has
   *  always behaved this way; settings accept it for the same reason. */
  const setSettings = useCallback((patch: DraftSettings) => {
    const next = cleanSettings({ ...settingsRef.current, ...patch });
    for (const [k, v] of Object.entries(patch)) if (!v) delete (next as Record<string, string>)[k];
    settingsRef.current = next;
    setSettingsState(next);
    write(draftRef.current, attachmentsRef.current, next);
  }, [write]);

  /** The operator hit enter. Empty the box and make sure nothing puts it back.
   *
   *  The SERVER is what durably clears this draft — the send carries the key, so
   *  the clear lands on the composer the text was typed in even though this
   *  chat may be about to become a different key entirely. What happens here is
   *  the local half: the box empties in the same tick (they pressed send), the
   *  scheduled writes from the last keystrokes are cancelled so none of them
   *  lands after the clear, and this key is marked cleared at `ts` so no adopt
   *  older than that is applied. */
  const clearForSend = useCallback(() => {
    const forKey = keyRef.current;
    const ts = Date.now();
    tsRef.current = ts;
    lastEditRef.current = ts;
    clearedAtRef.current.set(forKey, ts);
    setDraftState("");
    setAttachmentsState([]);
    attachmentsRef.current = [];
    setSettingsState({});
    settingsRef.current = {};
    // The refs are this hook's own snapshot of "what is in the box", and the
    // box just emptied. draftRef is otherwise only reassigned during render,
    // so a callback running in the SAME tick as the send (files.take() does)
    // still saw the sent text here and `write` re-published it under this key,
    // tombstone deleted, server copy scheduled. That one stale ref is how a
    // sent message reappeared in the composer days later: 2026-08-14, the
    // new-chat composer showing the previous new chat's opening message.
    draftRef.current = "";
    lsWrite(forKey, "", ts, [], {});
    window.clearTimeout(bcTimer.current);
    window.clearTimeout(srvTimer.current);
    pendingSrv.current = null;
    channel?.postMessage({ key: forKey, text: "", ts, client_id: CLIENT_ID, attachments: [] } as DraftPayload);
    // Sent here as well as by the server: the server's clear is authoritative
    // and lands when the message is accepted, and this one covers the window
    // before that — including a send that never reaches the server at all,
    // where the text lives on in the outbox and must not ALSO be in the box.
    // Retried, unlike a keystroke write, because nothing comes after it to put
    // it right. See serverClear.
    serverClear({ key: forKey, text: "", ts, client_id: CLIENT_ID, attachments: [] });
  }, []);

  const carryTo = useCallback((toKey: string) => {
    const text = draftRef.current;
    const atts = attachmentsRef.current;
    if (!text && atts.length === 0) return;
    const moved: DraftPayload = {
      key: toKey, text, ts: Date.now(), client_id: CLIENT_ID, attachments: atts,
    };
    lsWrite(toKey, text, moved.ts, atts, {});
    channel?.postMessage(moved);
    serverPut(moved);
    clearForSend();
  }, [clearForSend]);

  return { draft, setDraft, attachments, setAttachments, settings, setSettings, clearForSend, carryTo };
}
