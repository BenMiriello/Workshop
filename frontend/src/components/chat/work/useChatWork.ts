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

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HttpError, shortHttpError } from "@workshop/ui";
import { jobIsLive } from "../../../lib/jobs";
import { EMPTY_WORK, type WorkPayload, type WorkTicket } from "./workTypes";
import { queryKeys } from "../../../lib/queryKeys";

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

/** Every ticket in a payload, filed and scope, in one list. */
function allTickets(data: WorkPayload | undefined): WorkTicket[] {
  return [...(data?.filed ?? []), ...(data?.scope ?? [])];
}

export function useChatWork(threadId: string | null, opts: { enabled?: boolean } = {}): ChatWork {
  const { enabled = true } = opts;
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const url = threadId ? `/api/chat/threads/${encodeURIComponent(threadId)}/work` : null;

  const qc = useQueryClient();
  // Memoised because the optimistic write below reads and rewrites this exact
  // entry, and a fresh array every render would rebuild that path every render.
  const workKey = useMemo(() => queryKeys.chat.work(threadId), [threadId]);

  const q = useQuery({
    queryKey: workKey,
    queryFn: async (): Promise<WorkPayload> => {
      const r = await fetch(url!);
      // As `HttpError`, so the one transient-failure judgement in the kit can
      // tell a restart-window 502 from a real refusal, and so `err` below is
      // still exactly the `shortHttpError` line it always was.
      if (!r.ok) {
        throw new HttpError(
          r.status,
          shortHttpError(r.status, r.statusText, await r.text().catch(() => "")).trim(),
        );
      }
      return r.json() as Promise<WorkPayload>;
    },
    // A blank chat has no thread to scope to. Reading anything unfiltered here
    // would count work belonging to conversations that are not on screen.
    enabled: enabled && !!url,
    // The cadence follows the data: a run in flight is the only time any of this
    // changes fast enough to be worth five seconds. This is the same rule the
    // poll it replaces applied, read off the payload instead of off a render.
    refetchInterval: (query) =>
      allTickets(query.state.data).some((t) => jobIsLive(t.run?.status)) ? LIVE_MS : IDLE_MS,
    // This read owns its own cadence, so nothing is gained by holding a window
    // over it. The app default (30s) would also swallow the catch-up read on
    // the way back from hidden, which is half of what made pausing a poll safe
    // to default to (see packages/ui/src/usePoll.ts).
    staleTime: 0,
    // `usePoll` DOES NOT RETRY: a failed tick is simply followed by the next
    // tick, at most twenty seconds later. Leaving the app's transient-retry
    // default on would put up to three extra requests in front of a cadence
    // that already re-reads on its own, which is more traffic than the code it
    // replaces, not less.
    retry: false,
  });

  const data = q.data;
  const tickets = useMemo(() => allTickets(data), [data]);

  // ONE LINE FOR TWO KINDS OF FAILURE, unchanged: a dropped read and a refused
  // write share `err`, and whichever happened last is the one shown. The read
  // half is mirrored out of the query here rather than derived, because a
  // refused write is not in the query at all and the two have to take turns.
  const { isError, error, dataUpdatedAt, errorUpdatedAt, refetch } = q;
  useEffect(() => {
    // A dropped poll leaves the last good state on screen. A panel that
    // empties itself on a network blip is worse than one that is stale.
    if (isError) setErr(error instanceof HttpError ? error.message : String(error));
    else if (dataUpdatedAt) setErr(null);
  }, [isError, error, dataUpdatedAt, errorUpdatedAt]);

  const reload = useCallback(() => {
    if (!url) return;
    void refetch();
  }, [url, refetch]);

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
    //
    // It keeps the cached read's timestamp, because an edit is not a fresh
    // READ: `err` above mirrors fetch outcomes off `dataUpdatedAt`, and moving
    // that would let removing a row read as a poll that succeeded.
    qc.setQueryData<WorkPayload>(
      workKey,
      (v) => (v ? { ...v, inbox: v.inbox.filter((i) => i.id !== itemId) } : v),
      { updatedAt: qc.getQueryState(workKey)?.dataUpdatedAt },
    );
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
      reload();
    }
  }, [reload, qc, workKey]);

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
    // True for every read in flight, poll included, exactly as the `loading`
    // flag this replaces was.
    loading: q.isFetching,
    err,
    // Nothing has been read yet. A FAILED read counts as read: the panel drops
    // to the empty payload and draws its rows, which is what `cold` gates.
    //
    // ONE DELIBERATE BEHAVIOUR CHANGE, recorded because the rule for this
    // migration was "no behaviour changes". The `useState` this replaces was
    // never cleared on a thread switch, so for one read the panel showed the
    // PREVIOUS thread's tickets under the new thread's transcript. Keyed by
    // thread, that window is a loading region instead. Restoring the old
    // behaviour would mean `placeholderData` carrying one chat's work into
    // another, which is the thing ContextMeter resets for a few lines away.
    // Returning to a thread inside `gcTime` is now an instant cache hit, which
    // is new and strictly additive.
    cold: data === undefined && !isError,
    reload,
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
