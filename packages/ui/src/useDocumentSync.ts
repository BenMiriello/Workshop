// The save loop behind a VersionedDocument. There is one text, and typing saves
// it: after a pause, on Done, when the page is hidden or left. Every save names
// the version it was typed on; when the source answers that a newer one exists,
// the two are merged word by word and the merge is saved on top. An open
// document re-reads the source when it is shown again and every few seconds,
// and takes the newer text in place when nothing typed here is waiting.
//
// Typing not yet confirmed is kept on this device with the text it was typed
// on, so a closed tab or a dead network loses nothing, and on the next open it
// is merged into what was saved since instead of shown over it.
import { useCallback, useEffect, useRef, useState } from "react";
import { messageOf } from "./httpError";
import type { DocumentDraft, DocumentSource, DocumentState } from "./documentSource";
import {
  draftOf, dropUnsent, mergeDrafts, readUnsent, sameDraft, thisDevice, writeUnsent,
} from "./documentSync";

const BLANK: DocumentDraft = { title: "", body: "", tags: [] };

/** Another writer changed the same words: theirs is in history. */
export interface Overlap {
  theirs: DocumentState;
  conflicts: number;
}

export type SyncStatus = "saved" | "saving" | "failed";

export function useDocumentSync({
  source, docKey, autosaveMs, pollMs, onSaved, onOpened,
}: {
  source: DocumentSource;
  docKey: string;
  autosaveMs: number;
  pollMs: number;
  onSaved?: (s: DocumentState) => void;
  /** The first load landed: whether there is anything to write in yet. */
  onOpened?: (s: DocumentState, draft: DocumentDraft) => void;
}) {
  const [state, setStateRaw] = useState<DocumentState | null>(null);
  const [draft, setDraftRaw] = useState<DocumentDraft>(BLANK);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<SyncStatus>("saved");
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [overlap, setOverlap] = useState<Overlap | null>(null);
  const [savedAt, setSavedAt] = useState(0);

  const base = useRef<DocumentState | null>(null);
  const acked = useRef<DocumentDraft>(BLANK);
  const draftRef = useRef<DocumentDraft>(BLANK);
  const inflight = useRef<Promise<boolean> | null>(null);
  const device = useRef(thisDevice());
  const live = useRef(true);
  const hooks = useRef({ onSaved, onOpened });
  hooks.current = { onSaved, onOpened };

  const setDraft = useCallback((next: DocumentDraft | ((d: DocumentDraft) => DocumentDraft)) => {
    const d = typeof next === "function" ? next(draftRef.current) : next;
    draftRef.current = d;
    setDraftRaw(d);
  }, []);

  /** The source's text at `s` becomes the base, and the shown text with it. */
  const adopt = useCallback((s: DocumentState) => {
    base.current = s;
    acked.current = draftOf(s);
    setStateRaw(s);
    setDraft(draftOf(s));
    dropUnsent(docKey);
  }, [docKey, setDraft]);

  const flush = useCallback((): Promise<boolean> => {
    if (inflight.current) return inflight.current;
    const run = (async () => {
      // Each lap saves what was typed during the last one; a lap that meets a
      // newer save merges it in first. Merges are capped, saves are not.
      for (let merges = 0; merges < 5;) {
        const s = base.current;
        const d = draftRef.current;
        if (!s || s.readOnly || sameDraft(d, acked.current)) { setStatus("saved"); return true; }
        setStatus("saving");
        let r;
        try {
          r = await source.save(d, s.version, { device: device.current });
        } catch (e) {
          setSaveErr(messageOf(e)); setStatus("failed");
          return false;
        }
        setSaveErr(null);
        if (r.ok) {
          base.current = r.state;
          acked.current = d;
          setStateRaw(r.state);
          if (sameDraft(draftRef.current, d)) dropUnsent(docKey);
          setSavedAt(Date.now());
          hooks.current.onSaved?.(r.state);
          continue;
        }
        const theirs = r.conflict;
        const m = mergeDrafts(draftOf(s), draftRef.current, draftOf(theirs));
        base.current = theirs;
        acked.current = draftOf(theirs);
        setStateRaw(theirs);
        setDraft(m.draft);
        if (m.conflicts) setOverlap({ theirs, conflicts: m.conflicts });
        merges++;
      }
      setSaveErr("it kept changing elsewhere while saving"); setStatus("failed");
      return false;
    })();
    inflight.current = run;
    return run.finally(() => { inflight.current = null; });
  }, [docKey, source, setDraft]);

  // Open: the source's text, with anything typed here and not yet confirmed
  // merged into it, and sent at once. That copy was typed in an earlier
  // session, so wherever the source was saved differently since, the source's
  // text wins: a stale copy never overwrites newer writing, however it got
  // here, and merely opening a document never deletes from it. A copy that
  // does not hold the text it was typed on is merged only onto that version.
  useEffect(() => {
    live.current = true;
    setStateRaw(null); setLoadErr(null); setOverlap(null); setSaveErr(null); setStatus("saved");
    (async () => {
      const { state: s } = await source.load();
      const u = readUnsent(docKey);
      let d = draftOf(s);
      if (u && !s.readOnly && !sameDraft(u.draft, d)) {
        const typedOn = u.base === s.version ? d : u.baseDraft;
        if (typedOn) d = mergeDrafts(typedOn, u.draft, d, "theirs").draft;
      }
      if (!live.current) return;
      base.current = s;
      acked.current = draftOf(s);
      setStateRaw(s);
      setDraft(d);
      hooks.current.onOpened?.(s, d);
      if (!sameDraft(d, acked.current)) void flush();
      else dropUnsent(docKey);
    })().catch((e) => { if (live.current) setLoadErr(messageOf(e)); });
    return () => { live.current = false; };
    // `source` is the host's object for this docKey; the key is what reloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey, attempt]);

  // Typing: kept on this device at once, saved after a pause.
  useEffect(() => {
    const s = base.current;
    if (!s || sameDraft(draft, acked.current)) return;
    writeUnsent(docKey, draft, s.version, draftOf(s));
    const t = setTimeout(() => void flush(), autosaveMs);
    return () => clearTimeout(t);
  }, [draft, docKey, autosaveMs, flush]);

  /** Read the source again; take newer text in place, or merge it in. */
  const refresh = useCallback(async () => {
    if (!base.current || inflight.current) return;
    if (!sameDraft(draftRef.current, acked.current)) { void flush(); return; }
    let s: DocumentState;
    try { ({ state: s } = await source.load()); } catch { return; }
    const b = base.current;
    if (!live.current || inflight.current || !b) return;
    if (s.version === b.version && !!s.readOnly === !!b.readOnly) return;
    if (sameDraft(draftRef.current, acked.current)) adopt(s);
    else void flush();
  }, [adopt, flush, source]);

  useEffect(() => {
    const shown = () => typeof document === "undefined" || document.visibilityState === "visible";
    const onVisibility = () => { if (shown()) void refresh(); else void flush(); };
    const onLeave = () => { void flush(); };
    const onBack = () => { void refresh(); };
    const tick = setInterval(() => { if (shown()) void refresh(); }, pollMs);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onLeave);
    window.addEventListener("focus", onBack);
    window.addEventListener("online", onBack);
    return () => {
      clearInterval(tick);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onLeave);
      window.removeEventListener("focus", onBack);
      window.removeEventListener("online", onBack);
      void flush();
    };
  }, [flush, refresh, pollMs]);

  const restore = useCallback(async (v: number) => {
    await flush();
    const s = await source.restore(v, { device: device.current });
    adopt(s);
    setOverlap(null);
    setSavedAt(Date.now());
    hooks.current.onSaved?.(s);
  }, [adopt, flush, source]);

  return {
    state, draft, setDraft, loadErr, retry: () => setAttempt((n) => n + 1),
    pending: state != null && !sameDraft(draft, acked.current),
    status, saveErr, savedAt, overlap, clearOverlap: () => setOverlap(null),
    flush, restore,
  };
}
