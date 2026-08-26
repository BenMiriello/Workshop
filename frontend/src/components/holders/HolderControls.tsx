import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Send, Trash2 } from "lucide-react";
import {
  Button, ConfirmDialog, Notice, Row, Skeleton, Stack, StatusPill, Textarea,
  shortHttpError, timeAgo,
} from "@workshop/ui";
import {
  PREEMPT_HUMAN_ACTION, activityLine, holderName, holderStatus, leaseLine,
  offerableVerbs, preemptWarning, subjectLine,
  type Caller, type ControlAccepted, type ControlRequest, type DirectoryEntry,
} from "../../lib/holders";

// WHAT YOU CAN DO ABOUT A SESSION THAT IS ALREADY WORKING. Queue, steer, take
// over (workspace-ownership-and-leases-2026-08-22.md section 8), rendered
// wherever a holder is named: the directory sheet and the ref pill's dialog.
//
// THE HONEST COPY IS SERVED, NOT WRITTEN HERE. `POST /control/{id}/requests`
// answers with `detail`, and the backend spells those strings once
// (`services/control.py` STEER_COPY_*) precisely so a client cannot promise
// something this system does not deliver. A steer reaches the running turn; when
// the model ACTS on it is not observable, and measured behaviour is that it
// finishes the plan it is on first. So the result line below is the route's
// string, verbatim, and the only copy this file writes about a steer is the
// button's own help, which says the same thing in advance.
//
// QUEUE IS THE DEFAULT AND READS AS ONE: it is first, and it is the primary
// button. Steer sits beside it as an ordinary one. Nothing is ranked, because
// section 8 has no priority field: the verb is the urgency.
//
// PREEMPT IS BEHIND A ConfirmDialog AND CARRIES `human_action`. That is not
// decoration: the route refuses a preempt that does not name the live human
// interaction which authorized it (403), which is how "an agent may never
// preempt another agent" is enforced by the shape of the request rather than by
// a permission matrix.

// A session's queue is a handful of entries in practice; past this the panel
// stops being a control and becomes a list, and the count says the rest.
const MAX_PENDING = 8;

type Busy = null | "queue" | "steer" | "preempt";

export default function HolderControls({
  entry, selfSessionId, caller = "operator", worktreeId, onChanged,
}: {
  entry: DirectoryEntry;
  /** The session this surface IS, when it is one. Null on a page: a page can
   *  queue and steer, but it has nobody to hand a lease to. */
  selfSessionId?: string | null;
  /** Who is asking. Always `operator` from a rendered control, because a person
   *  is clicking it; the parameter exists so the rule that an agent may not
   *  preempt is expressed rather than assumed. */
  caller?: Caller;
  /** The tree this request is about, when there is one. The route writes the
   *  `steered` event onto its timeline. */
  worktreeId?: string | null;
  onChanged?: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [accepted, setAccepted] = useState<ControlAccepted | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [pendingErr, setPendingErr] = useState<string | null>(null);
  // What the preempt route reported back, in its own words.
  const [takeover, setTakeover] = useState<string | null>(null);

  const sid = entry.session_id;
  const nowMs = Date.now();

  const qc = useQueryClient();
  // Memoised because `submit` below writes this exact entry from the route's
  // own answer, and a fresh array every render would rebuild that path.
  const controlKey = useMemo(() => ["control", sid], [sid]);

  const q = useQuery({
    queryKey: controlKey,
    queryFn: async (): Promise<ControlRequest[]> => {
      const r = await fetch(`/api/control/${encodeURIComponent(sid)}`);
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return ((await r.json()) as { requests?: ControlRequest[] }).requests ?? [];
    },
    refetchInterval: 15_000,
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read `usePoll` did on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });

  // Keyed by session, which the `useState` this replaces was not: a control
  // re-pointed at a different holder used to show the previous session's queue
  // until the next read landed. It shows the cold state for that window now.
  const pending = q.data ?? null;
  const { isError, error, dataUpdatedAt, errorUpdatedAt, refetch } = q;
  // ONE LINE FOR TWO KINDS OF FAILURE, unchanged: a dropped read and a refused
  // cancel share `pendingErr`, and whichever happened last is the one shown.
  // The read half is mirrored out of the query rather than derived, because a
  // refused cancel is not in the query at all.
  useEffect(() => {
    if (isError) setPendingErr(String(error));
    else if (dataUpdatedAt) setPendingErr(null);
  }, [isError, error, dataUpdatedAt, errorUpdatedAt]);

  const loadPending = useCallback(() => { void refetch(); }, [refetch]);

  const submit = useCallback(async (verb: "queue" | "steer") => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(verb);
    setErr(null);
    try {
      const r = await fetch(`/api/control/${encodeURIComponent(sid)}/requests`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: body, verb, actor: selfSessionId || "operator", worktree_id: worktreeId ?? null }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const out = (await r.json()) as ControlAccepted;
      setAccepted(out);
      setText("");
      // The route answers with the queue it just changed, so the list takes it
      // directly instead of re-reading. It keeps the cached read's timestamp: a
      // WRITE is not a fresh read, and moving `dataUpdatedAt` would run the
      // mirror above and clear a `pendingErr` no read has cleared.
      qc.setQueryData<ControlRequest[]>(
        controlKey,
        out.pending ?? [],
        { updatedAt: qc.getQueryState(controlKey)?.dataUpdatedAt },
      );
      onChanged?.();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }, [busy, controlKey, onChanged, qc, selfSessionId, sid, text, worktreeId]);

  const cancel = useCallback(async (requestId: string) => {
    try {
      const r = await fetch(
        `/api/control/${encodeURIComponent(sid)}/requests/${encodeURIComponent(requestId)}`,
        { method: "DELETE" },
      );
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      void loadPending();
    } catch (e) {
      setPendingErr(String(e));
    }
  }, [loadPending, sid]);

  const preempt = useCallback(async () => {
    setConfirm(false);
    if (!selfSessionId || !entry.subject_id) return;
    setBusy("preempt");
    setErr(null);
    try {
      const r = await fetch(`/api/control/${encodeURIComponent(sid)}/preempt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject_id: entry.subject_id,
          subject_kind: entry.subject_kind,
          new_holder_id: selfSessionId,
          new_holder_kind: "chat",
          actor: selfSessionId,
          human_action: PREEMPT_HUMAN_ACTION,
          reason: text.trim(),
          worktree_id: worktreeId ?? null,
        }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const out = (await r.json()) as { detail?: string; forced?: boolean };
      // The route's own account of what it did, including whether the holder
      // let go inside the grace or had to be stopped. Not paraphrased.
      setAccepted(null);
      setErr(null);
      setTakeover(out.detail || "The lease was taken over.");
      onChanged?.();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }, [entry.subject_id, entry.subject_kind, onChanged, selfSessionId, sid, text, worktreeId]);

  const verbs = offerableVerbs(entry, { selfSessionId, caller });
  const queue = verbs[0];
  const steer = verbs[1];
  const take = verbs[2];
  const status = holderStatus(entry, selfSessionId);
  const subject = subjectLine(entry);
  const shown = (pending ?? []).slice(0, MAX_PENDING);

  return (
    <Stack gap="sm">
      <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
        <StatusPill status={status.kind}>{status.label}</StatusPill>
        <span style={{ fontSize: "var(--fs-sm)" }}>{holderName(entry)}</span>
      </Row>
      <p className="ws-muted ws-wrap" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
        {activityLine(entry)}
        {subject ? ` · ${subject}` : ""}
        {entry.scope ? ` · ${entry.scope}` : ""}
      </p>
      <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
        {leaseLine(entry, nowMs)}
        {entry.last_beat_at ? ` Last beat ${timeAgo(entry.last_beat_at)}.` : ""}
      </p>

      {/* WHAT IS ALREADY WAITING, oldest first, because "queue" means nothing
          without it. FIFO and cancellable (section 8), so each row carries the
          way back out. All four states: cold, empty, failed, capped. */}
      <div>
        <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Waiting for this session</span>
        {pending === null && !pendingErr ? (
          <Skeleton width="55%" />
        ) : pendingErr && pending === null ? (
          <Notice tone="warn" detail={pendingErr} action={<Button size="sm" onClick={() => { void loadPending(); }}>Retry</Button>}>
            Couldn't read what is queued for this session.
          </Notice>
        ) : shown.length === 0 ? (
          <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
            Nothing is queued. Anything you add goes out when it finishes what it is on.
          </p>
        ) : (
          <Stack gap="sm">
            {shown.map((p) => (
              <Row key={p.id} between wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
                <span className="ws-truncate" style={{ fontSize: "var(--fs-xs)" }} title={p.text}>
                  {p.verb === "steer" ? "sent: " : ""}{p.text}
                </span>
                <Button size="sm" variant="ghost" onClick={() => { void cancel(p.id); }} title="Take this back out">
                  <Trash2 aria-hidden /> Cancel
                </Button>
              </Row>
            ))}
            {(pending ?? []).length > MAX_PENDING && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                Showing {MAX_PENDING} of {(pending ?? []).length} waiting.
              </span>
            )}
          </Stack>
        )}
      </div>

      {(queue.offered || steer.offered) && (
        <>
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            placeholder="What this session should do"
            aria-label={`Message for ${holderName(entry)}`}
            onKeyDown={(e) => {
              // This can render inside the chat's sheet, one element away from
              // the composer's own Enter handling. Newlines belong to this
              // field; sending is an explicit button, since which of two verbs
              // is meant is the whole decision here.
              e.stopPropagation();
            }}
          />
          <Row wrap style={{ gap: "var(--space-2)" }}>
            <Button
              size="sm"
              variant="primary"
              disabled={!text.trim() || busy != null || !queue.offered}
              loading={busy === "queue"}
              onClick={() => { void submit("queue"); }}
            >
              {queue.label}
            </Button>
            <Button
              size="sm"
              disabled={!text.trim() || busy != null || !steer.offered}
              loading={busy === "steer"}
              onClick={() => { void submit("steer"); }}
            >
              <Send aria-hidden /> {steer.label}
            </Button>
          </Row>
          {/* One line per verb, under the pair. Run together they read as a
              single sentence about neither, and the difference between the two
              IS the decision being made here. */}
          <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
            {queue.label}: {queue.help}
          </p>
          <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
            {steer.label}: {steer.help}
          </p>
        </>
      )}

      {/* THE ROUTE'S OWN ACCOUNT OF WHAT HAPPENED. A steer that could not reach
          a live turn is queued instead and says so, in the backend's words. */}
      {accepted && (
        <Notice tone="info">{accepted.detail}</Notice>
      )}
      {takeover && <Notice tone="warn">{takeover}</Notice>}
      {err && <Notice tone="error" detail={err}>That did not go through.</Notice>}

      {take.offered ? (
        <div>
          <Button
            size="sm"
            variant="danger-ghost"
            disabled={busy != null}
            loading={busy === "preempt"}
            onClick={() => setConfirm(true)}
          >
            <AlertTriangle aria-hidden /> {take.label}
          </Button>
          <p className="ws-muted" style={{ margin: "var(--space-1) 0 0", fontSize: "var(--fs-xs)" }}>
            {take.help}
          </p>
        </div>
      ) : (
        // The reason, not a dimmed button. A control that cannot be used says
        // why once and takes no room pretending otherwise.
        take.reason && (
          <p className="ws-muted" style={{ margin: 0, fontSize: "var(--fs-xs)" }}>
            Take over: {take.reason}
          </p>
        )
      )}

      <ConfirmDialog
        open={confirm}
        onCancel={() => setConfirm(false)}
        onConfirm={() => { void preempt(); }}
        title={`Take over from ${holderName(entry)}?`}
        body={preemptWarning(entry)}
        confirmLabel="Take over"
      />
    </Stack>
  );
}
