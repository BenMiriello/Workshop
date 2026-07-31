// Chat composer draft persistence + cross-surface sync.
//
// Three deliberately-simple layers (single-user box — no locks, no multi-writer
// ceremony):
//   1. localStorage (synchronous) — instant per-browser restore. Written on
//      every keystroke and read on mount, so a reload/remount never wipes what
//      you were typing, even with the server down or offline. This is the fix
//      for "the app refreshed and lost my draft".
//   2. BroadcastChannel — live sync across tabs / apps in the SAME browser (the
//      build page, the framed runner, another agent tab), no backend round-trip.
//   3. server store (/api/chat/drafts) — durable + cross-DEVICE mirror; a
//      debounced PUT, plus a GET on mount and on window focus to pull what
//      another device left.
//
// Conflict rule (one rule, last-write-wins by timestamp): adopt an incoming
// draft only if it is newer AND this surface hasn't had a keystroke within
// ACTIVE_MS (so we never clobber text you're actively typing). A per-tab
// client_id suppresses our own echoes; the server rejects strictly-older writes.

import { useCallback, useEffect, useRef, useState } from "react";

type DraftPayload = { key: string; text: string; ts: number; client_id: string };

const LS_PREFIX = "ws-chat-draft:";
const ACTIVE_MS = 3000; // "actively editing" window after a keystroke
const BROADCAST_DEBOUNCE_MS = 120;
const SERVER_DEBOUNCE_MS = 600;
const JUST_SENT_MS = 8000;  // adopts are refused this long after a send

// Stable per-tab id — one per page load is enough to suppress self-echo.
const CLIENT_ID = Math.random().toString(36).slice(2) + Date.now().toString(36);

const channel: BroadcastChannel | null =
  typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("ws-chat-drafts") : null;

// Per-key subscribers — each mounted chat surface for that context_key.
type Sub = (p: DraftPayload) => void;
const subs = new Map<string, Set<Sub>>();

function emitLocal(p: DraftPayload) {
  subs.get(p.key)?.forEach((fn) => fn(p));
}

if (channel) {
  channel.onmessage = (e) => {
    const p = e.data as DraftPayload;
    if (p && p.key) emitLocal(p);
  };
}

function lsRead(key: string): { text: string; ts: number } {
  try {
    const raw = localStorage.getItem(LS_PREFIX + key);
    if (raw) {
      const v = JSON.parse(raw);
      if (typeof v?.text === "string") return { text: v.text, ts: Number(v.ts) || 0 };
    }
  } catch { /* ignore */ }
  return { text: "", ts: 0 };
}

function lsWrite(key: string, text: string, ts: number) {
  try {
    if (text) localStorage.setItem(LS_PREFIX + key, JSON.stringify({ text, ts }));
    else localStorage.removeItem(LS_PREFIX + key);
  } catch { /* quota / disabled — server + state still hold it */ }
}

async function serverGet(key: string): Promise<{ text: string; ts: number } | null> {
  try {
    const r = await fetch(`/api/chat/drafts/${encodeURIComponent(key)}`);
    if (!r.ok) return null;
    const v = await r.json();
    return { text: typeof v?.text === "string" ? v.text : "", ts: Number(v?.ts) || 0 };
  } catch { return null; }
}

function serverPut(p: DraftPayload) {
  try {
    void fetch(`/api/chat/drafts/${encodeURIComponent(p.key)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: p.text, ts: p.ts, client_id: p.client_id }),
      keepalive: true,
    });
  } catch { /* offline — localStorage already holds it */ }
}

// Drop-in replacement for useState<string>("") for a chat composer. Returns the
// current draft for `contextKey` and a setter that persists + syncs it.
export function useDraft(contextKey: string): [string, (v: string) => void, () => void] {
  const [draft, setDraftState] = useState<string>(() => lsRead(contextKey).text);

  const tsRef = useRef<number>(lsRead(contextKey).ts); // ts of our current value
  const lastEditRef = useRef<number>(0);               // wall-clock of last local keystroke
  const keyRef = useRef<string>(contextKey);
  keyRef.current = contextKey;
  const bcTimer = useRef<number | undefined>(undefined);
  const srvTimer = useRef<number | undefined>(undefined);
  const pendingSrv = useRef<DraftPayload | null>(null);
  // When the composer was last cleared by SEND. Any adopt within this window is
  // a stale copy racing a deliberate clear, and is refused.
  const clearedAtRef = useRef<number>(0);

  // Apply an incoming value (from another surface/device) without re-broadcasting.
  const adopt = useCallback((text: string, ts: number) => {
    tsRef.current = ts;
    setDraftState(text);
    lsWrite(keyRef.current, text, ts);
  }, []);

  const editing = () => Date.now() - lastEditRef.current < ACTIVE_MS;
  // A send is stickier than a keystroke: cross-device echoes can arrive well
  // after the editing window, and restoring sent text is worse than dropping a
  // late edit from elsewhere.
  const justSent = () => Date.now() - clearedAtRef.current < JUST_SENT_MS;

  // Load this key's draft on mount / when switching contexts, then reconcile
  // with the server (adopt if the server copy is newer and we're not typing).
  useEffect(() => {
    const local = lsRead(contextKey);
    tsRef.current = local.ts;
    lastEditRef.current = 0;
    setDraftState(local.text);
    let cancelled = false;
    serverGet(contextKey).then((s) => {
      if (cancelled || !s) return;
      if (s.ts > tsRef.current && !editing() && !justSent()) adopt(s.text, s.ts);
    });
    return () => { cancelled = true; };
  }, [contextKey, adopt]);

  // Live updates: same-browser via BroadcastChannel, cross-device on focus.
  useEffect(() => {
    const sub: Sub = (p) => {
      if (p.client_id === CLIENT_ID) return; // our own echo
      if (p.ts <= tsRef.current) return;      // not newer
      if (editing()) return;                  // we're typing — keep ours
      if (justSent()) return;                 // just sent — never restore it
      adopt(p.text, p.ts);
    };
    let set = subs.get(contextKey);
    if (!set) { set = new Set(); subs.set(contextKey, set); }
    set.add(sub);

    const onFocus = () => {
      if (document.visibilityState === "hidden") return;
      serverGet(keyRef.current).then((s) => {
        if (s && s.ts > tsRef.current && !editing() && !justSent()) adopt(s.text, s.ts);
      });
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      set!.delete(sub);
      if (set!.size === 0) subs.delete(contextKey);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [contextKey, adopt]);

  // Flush any pending server write when the tab is hidden / unmounts so a draft
  // typed in the last debounce window still reaches the durable store.
  useEffect(() => {
    const flush = () => {
      const p = pendingSrv.current;
      if (p) { pendingSrv.current = null; serverPut(p); }
    };
    window.addEventListener("pagehide", flush);
    return () => { flush(); window.removeEventListener("pagehide", flush); };
  }, []);

  const setDraft = useCallback((v: string) => {
    const key = keyRef.current; // capture so a context switch can't misroute the write
    const ts = Date.now();
    tsRef.current = ts;
    lastEditRef.current = ts;
    setDraftState(v);
    lsWrite(key, v, ts); // synchronous — never lose a keystroke on reload

    window.clearTimeout(bcTimer.current);
    bcTimer.current = window.setTimeout(() => {
      channel?.postMessage({ key, text: v, ts, client_id: CLIENT_ID } as DraftPayload);
    }, BROADCAST_DEBOUNCE_MS);

    pendingSrv.current = { key, text: v, ts, client_id: CLIENT_ID };
    window.clearTimeout(srvTimer.current);
    srvTimer.current = window.setTimeout(() => {
      const p = pendingSrv.current;
      pendingSrv.current = null;
      if (p) serverPut(p);
    }, SERVER_DEBOUNCE_MS);
  }, []);

  // Clearing on SEND is different from clearing by editing, and has to be
  // immediate + authoritative.
  //
  // setDraft only SCHEDULES its writes: the broadcast is debounced 120ms and
  // the server PUT 600ms. That leaves windows where the just-sent text is
  // still the newest copy somewhere else — another tab or device that has not
  // heard about the clear yet, or a server copy the clear has not reached —
  // and the adopt paths (focus, visibilitychange, BroadcastChannel) will
  // happily restore it once the 3s "actively editing" guard lapses. The result
  // is a message that sends and then reappears in the composer, occasionally,
  // which is exactly what it looks like from outside.
  //
  // So: write through synchronously to every surface, and hold a guard that
  // refuses any adopt until the clear has certainly propagated. A send is a
  // deliberate act; nothing arriving afterwards should be able to undo it.
  const clearNow = useCallback(() => {
    const key = keyRef.current;
    const ts = Date.now();
    tsRef.current = ts;
    lastEditRef.current = ts; // also holds the editing() guard
    clearedAtRef.current = ts;
    setDraftState("");
    lsWrite(key, "", ts);
    window.clearTimeout(bcTimer.current);
    window.clearTimeout(srvTimer.current);
    pendingSrv.current = null;
    channel?.postMessage({ key, text: "", ts, client_id: CLIENT_ID } as DraftPayload);
    void serverPut({ key, text: "", ts, client_id: CLIENT_ID });
  }, []);

  return [draft, setDraft, clearNow];
}
