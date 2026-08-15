// The chat's work state: one poll, one copy, every reader.
//
// Replaces two pollers (`useInboxItems` at 20s against /api/inbox, and the
// drawer's ticket list at 15s against /api/tasks) and forestalls the ones each
// tracking card in the transcript would otherwise have opened for its ticket
// and its run. Three cards would have been eight polls describing overlapping
// facts, and a badge, a panel row and a card would have been free to disagree
// about one ticket for up to twenty seconds at a time.
//
// The behaviours below were all learned by the surfaces this replaces and are
// deliberately carried over rather than rewritten: a failed poll keeps the last
// good state, an operator action removes its row optimistically and re-reads to
// correct itself, a refused write says so instead of letting the row silently
// reappear, and a half-typed answer survives the panel closing.

import { useCallback, useMemo, useState } from "react";
import { usePoll } from "../../../lib/usePoll";
import { shortHttpError } from "../../../lib/httpError";
import { jobIsLive } from "../../../lib/jobs";
import { EMPTY_WORK, type WorkPayload, type WorkTicket } from "./workTypes";

export type SnoozePreset = "hour" | "tomorrow";

export type ChatWork = {
  data: WorkPayload;
  loading: boolean;
  err: string | null;
  /** Nothing has been read yet, as distinct from having read an empty payload. */
  cold: boolean;
  reload: () => void;
  /** The ticket a card is tracking, from the shared copy. */
  ticket: (id: string) => WorkTicket | undefined;
  /** Every ticket in the payload, filed and scope, deduped. */
  tickets: WorkTicket[];
  busy: string | null;
  answer: (itemId: string, text: string) => Promise<void>;
  dismiss: (itemId: string) => Promise<void>;
  snooze: (itemId: string, preset: SnoozePreset) => Promise<void>;
  /** Draft answers, kept per item so closing the panel does not discard one. */
  drafts: Record<string, string>;
  setDraft: (itemId: string, text: string) => void;
};

// Twenty seconds is under the point where the operator notices a lag on work
// that agents do on their own schedule. Five is what every other live surface
// in the app uses while something is actually running, and a run in flight is
// the only time this data changes fast enough to warrant it.
const IDLE_MS = 20000;
const LIVE_MS = 5000;

export function useChatWork(threadId: string | null, opts: { enabled?: boolean } = {}): ChatWork {
  const { enabled = true } = opts;
  const [data, setData] = useState<WorkPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const url = threadId ? `/api/chat/threads/${encodeURIComponent(threadId)}/work` : null;

  const load = useCallback(async () => {
    // A blank chat has no thread to scope to. Reading anything unfiltered here
    // would count work belonging to conversations that are not on screen.
    if (!url) { setData(null); return; }
    setLoading(true);
    try {
      const r = await fetch(url);
      if (!r.ok) {
        setErr(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")).trim());
        setData((v) => v ?? EMPTY_WORK);
        return;
      }
      setData(await r.json());
      setErr(null);
    } catch (e) {
      // A dropped poll leaves the last good state on screen. A panel that
      // empties itself on a network blip is worse than one that is stale.
      setErr(String(e));
      setData((v) => v ?? EMPTY_WORK);
    } finally {
      setLoading(false);
    }
  }, [url]);

  const tickets = useMemo(() => [...(data?.filed ?? []), ...(data?.scope ?? [])], [data]);
  // The cadence follows the data: a run in flight is the only time any of this
  // changes fast enough to be worth five seconds. Changing the interval re-arms
  // the timer, which is correct here because the value only flips when a run
  // starts or ends, not on every render.
  const live = tickets.some((t) => jobIsLive(t.run?.status));

  usePoll(load, live ? LIVE_MS : IDLE_MS, { enabled: enabled && !!url });

  const byId = useMemo(() => {
    const m = new Map<string, WorkTicket>();
    for (const t of tickets) m.set(t.id, t);
    return m;
  }, [tickets]);

  // One write path for all three item actions. They differ only in the endpoint
  // and in what to say when the write is refused, and the optimistic removal,
  // the re-read and the error handling were copied between them before.
  const act = useCallback(async (
    itemId: string,
    what: string,
    path: string,
    body: Record<string, unknown>,
  ) => {
    setBusy(itemId);
    // The operator acted, so the row goes now. The re-read below corrects the
    // rare case where the write was refused.
    setData((v) => (v ? { ...v, inbox: v.inbox.filter((i) => i.id !== itemId) } : v));
    try {
      const r = await fetch(`/api/inbox/${encodeURIComponent(itemId)}/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      // Answering a task-derived item unblocks the TICKET, and that write can be
      // refused (a proposal already granted, a ticket that moved on). Say so:
      // the re-read puts the row back, and a row reappearing with no explanation
      // is what made the old attention surfaces untrustworthy.
      if (!r.ok) {
        setErr(`Couldn't ${what}: ${shortHttpError(r.status, r.statusText, await r.text().catch(() => ""))}`.trim());
      } else {
        setErr(null);
        setDrafts((d) => { const { [itemId]: _gone, ...rest } = d; return rest; });
      }
    } catch (e) {
      setErr(`Couldn't ${what}: ${String(e)}`);
    } finally {
      setBusy(null);
      void load();
    }
  }, [load]);

  const answer = useCallback((id: string, text: string) =>
    act(id, "send that answer", "resolve", { answer: text, dismissed: false }), [act]);

  const dismiss = useCallback((id: string) =>
    act(id, "dismiss that", "resolve", { answer: null, dismissed: true }), [act]);

  // Snooze is deferral, not closure: the item stays open on the server and comes
  // back on its own, or sooner if the work it describes changes.
  const snooze = useCallback((id: string, preset: SnoozePreset) =>
    act(id, "snooze that", "snooze", { preset }), [act]);

  const setDraft = useCallback((id: string, text: string) => {
    setDrafts((d) => ({ ...d, [id]: text }));
  }, []);

  return {
    data: data ?? EMPTY_WORK,
    loading,
    err,
    cold: data === null,
    reload: useCallback(() => { void load(); }, [load]),
    ticket: useCallback((id: string) => byId.get(id), [byId]),
    tickets,
    busy,
    answer,
    dismiss,
    snooze,
    drafts,
    setDraft,
  };
}
