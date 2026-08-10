import { useCallback, useEffect, useState } from "react";
import { ChevronRight, RefreshCw, Trash2, Check } from "lucide-react";
import {
  Modal, Button, Stack, Row, Tag, Badge, Field, Input, Textarea,
  StateBlock, Skeleton, catFor,
} from "../../ui";
import { scopeLabel } from "../../lib/tasks";

// Turning a conversation into tickets, as a review step.
//
// What this replaces: one button that squashed the last 12 messages into a
// single paragraph and filed it, unseen. A conversation does not contain one
// ticket. It contains some number of them, often zero, mixed with things that
// were discussed and dropped, work already finished, and questions that got
// answered. So this proposes 0..N, each self-contained, and the operator
// decides which are real.
//
// Drafts are STORED on the thread. Proposing costs a model call and tens of
// seconds; closing this dialog and reopening it must not silently redo the work
// or discard edits.
export type Draft = {
  id: string;
  title: string;
  body: string;
  acceptance: string;
  confidence: "decided" | "likely" | "speculative";
  filed_task_id: string | null;
};

type Payload = {
  drafts: Draft[];
  generated_at: string | null;
  messages_since: number;
};

export default function TicketDraftsDialog({
  open,
  chatId,
  onClose,
  onFiled,
}: {
  open: boolean;
  chatId: string | null;
  onClose: () => void;
  onFiled: (msg: { text: string; scope: string; ran: boolean; runError?: string }) => void;
}) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [scope, setScope] = useState<{ scope: string; runnable: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [proposing, setProposing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // One draft expanded at a time. A ticket body plus acceptance criteria is
  // several lines; four of those fully expanded is a wall, and the operator
  // asked for exactly this — show the line, expand for the detail.
  const [expanded, setExpanded] = useState<string | null>(null);

  const loadStored = useCallback(async () => {
    if (!chatId) return;
    try {
      const [d, s] = await Promise.all([
        fetch(`/api/chat/threads/${encodeURIComponent(chatId)}/ticket-drafts`).then((r) => r.json()),
        fetch(`/api/chat/threads/${encodeURIComponent(chatId)}/ticket-scope`).then((r) => r.json()),
      ]);
      setPayload(d);
      setScope(s);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, [chatId]);

  const propose = useCallback(async () => {
    if (!chatId) return;
    setProposing(true);
    setErr(null);
    try {
      const r = await fetch(`/api/chat/threads/${encodeURIComponent(chatId)}/ticket-drafts`, { method: "POST" });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      const j = await r.json();
      setPayload({ ...j, messages_since: 0 });
    } catch (e) {
      setErr(String(e));
    } finally {
      setProposing(false);
    }
  }, [chatId]);

  // On open: show what is stored. Only propose when there is nothing yet, so
  // reopening is instant and never silently re-derives.
  useEffect(() => {
    if (!open || !chatId) return;
    let cancelled = false;
    (async () => {
      await loadStored();
      if (cancelled) return;
      const r = await fetch(`/api/chat/threads/${encodeURIComponent(chatId)}/ticket-drafts`).then((x) => x.json());
      if (!cancelled && (!r.drafts || r.drafts.length === 0) && !r.generated_at) void propose();
    })();
    return () => { cancelled = true; };
  }, [open, chatId, loadStored, propose]);

  const patch = useCallback(async (id: string, body: Partial<Draft>) => {
    if (!chatId) return;
    setPayload((p) => (p ? { ...p, drafts: p.drafts.map((d) => (d.id === id ? { ...d, ...body } : d)) } : p));
    await fetch(`/api/chat/threads/${encodeURIComponent(chatId)}/ticket-drafts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => { /* the local edit stands; the next open re-reads */ });
  }, [chatId]);

  const discard = useCallback(async (id: string) => {
    if (!chatId) return;
    setPayload((p) => (p ? { ...p, drafts: p.drafts.filter((d) => d.id !== id) } : p));
    await fetch(`/api/chat/threads/${encodeURIComponent(chatId)}/ticket-drafts/${id}`, { method: "DELETE" })
      .catch(() => { /* removed locally either way */ });
  }, [chatId]);

  const file = useCallback(async (d: Draft, run: boolean) => {
    if (!chatId) return;
    setBusy(d.id);
    // The ticket text a worker sees is the whole draft, not just the title: it
    // has to stand alone. The body is already composed in the canonical shape
    // (Objective / Output / Start from / Boundaries) by services/ticket_format,
    // so acceptance is normally inside it and must not be appended twice. A draft
    // stored before that change carries a separate `acceptance` and a body that
    // states no output; that one still gets it, under the label the readiness
    // check actually looks for rather than the "Done when:" it never matched.
    const statesOutput = /^\s*(?:output|done|acceptance|verify|proof)\s*[:\-]/im.test(d.body || "");
    const text = [
      d.title,
      d.body,
      d.acceptance && !statesOutput ? `Output: ${d.acceptance}` : "",
    ].filter(Boolean).join("\n");
    try {
      const r = await fetch(`/api/chat/threads/${encodeURIComponent(chatId)}/spawn-task`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, run }),
      });
      if (!r.ok) throw new Error(`${r.status}`);
      const j = await r.json();
      await patch(d.id, { filed_task_id: j.task?.id ?? "filed" });
      onFiled({ text: d.title, scope: j.scope, ran: !!j.job_id, runError: j.run_error });
    } catch (e) {
      setErr(`Couldn't file "${d.title}": ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }, [chatId, patch, onFiled]);

  const drafts = payload?.drafts ?? [];
  const pending = drafts.filter((d) => !d.filed_task_id);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Tickets from this chat"
      size="lg"
      actions={
        <Row wrap style={{ gap: "var(--space-2)", justifyContent: "space-between", width: "100%" }}>
          <Button size="sm" variant="ghost" onClick={() => void propose()} disabled={proposing}>
            <RefreshCw size={14} aria-hidden /> {drafts.length ? "Re-read chat" : "Read chat"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onClose}>Done</Button>
        </Row>
      }
    >
      <Stack>
        {scope && (
          <Row wrap style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>Files into</span>
            <Tag hue={catFor(scope.scope)}>{scopeLabel(scope.scope)}</Tag>
            {!scope.runnable && (
              <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
                this scope can't run work directly
              </span>
            )}
          </Row>
        )}

        {payload && payload.messages_since > 0 && drafts.length > 0 && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            {payload.messages_since} message{payload.messages_since === 1 ? "" : "s"} since these were
            drafted. Re-read the chat to refresh them.
          </span>
        )}

        {err && (
          <StateBlock
            variant="error"
            title="Something went wrong"
            hint={<span>{err}</span>}
            action={<Button size="sm" variant="ghost" onClick={() => void propose()}>Try again</Button>}
          />
        )}

        {proposing ? (
          <Stack gap="sm">
            <Skeleton />
            <span className="ws-muted" style={{ fontSize: "var(--fs-sm)" }}>
              Reading the conversation and working out what it actually asks for. A few seconds.
            </span>
          </Stack>
        ) : drafts.length === 0 ? (
          <StateBlock
            title="Nothing here needs a ticket"
            hint={
              <span>
                Nothing in this conversation was decided or asked for yet. That is a normal
                answer, not a failure. Keep talking and re-read the chat when something lands.
              </span>
            }
          />
        ) : (
          <ul className="tdraft__list">
            {drafts.map((d) => {
              const isOpen = expanded === d.id;
              const filed = !!d.filed_task_id;
              return (
                <li key={d.id} className={`tdraft${filed ? " is-filed" : ""}`}>
                  <div className="tdraft__row">
                    <button
                      type="button"
                      className="tdraft__toggle"
                      onClick={() => setExpanded(isOpen ? null : d.id)}
                      aria-expanded={isOpen}
                    >
                      <ChevronRight size={14} aria-hidden
                                    className={`tdraft__caret${isOpen ? " is-open" : ""}`} />
                      <span className="tdraft__title">{d.title}</span>
                      {filed ? (
                        <Badge><Check size={12} aria-hidden /> filed</Badge>
                      ) : (
                        <Badge>{d.confidence}</Badge>
                      )}
                    </button>
                  </div>

                  {isOpen && !filed && (
                    <div className="tdraft__detail">
                      <Field label="Title" htmlFor={`t-${d.id}`}>
                        <Input id={`t-${d.id}`} value={d.title}
                               onChange={(e) => void patch(d.id, { title: e.target.value })} />
                      </Field>
                      <Field
                        label="What to do"
                        htmlFor={`b-${d.id}`}
                        help="A worker sees only this, never the chat. It has to make sense on its own."
                      >
                        <Textarea id={`b-${d.id}`} rows={4} value={d.body}
                                  onChange={(e) => void patch(d.id, { body: e.target.value })} />
                      </Field>
                      <Field label="Done when" htmlFor={`a-${d.id}`}>
                        <Input id={`a-${d.id}`} value={d.acceptance}
                               onChange={(e) => void patch(d.id, { acceptance: e.target.value })} />
                      </Field>
                      <Row wrap style={{ gap: "var(--space-2)" }}>
                        <Button size="sm" variant="primary" disabled={busy === d.id || !d.title.trim()}
                                onClick={() => void file(d, false)}>
                          {busy === d.id ? "Filing…" : "File ticket"}
                        </Button>
                        {scope?.runnable && (
                          <Button size="sm" variant="ghost" disabled={busy === d.id || !d.title.trim()}
                                  onClick={() => void file(d, true)}>
                            File and run now
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => void discard(d.id)}>
                          <Trash2 size={14} aria-hidden /> Discard
                        </Button>
                      </Row>
                    </div>
                  )}

                  {isOpen && filed && (
                    <div className="tdraft__detail">
                      <p className="ws-muted" style={{ fontSize: "var(--fs-sm)", margin: 0 }}>
                        Filed. It is tracked in Work now.
                      </p>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}

        {pending.length > 0 && (
          <span className="ws-muted" style={{ fontSize: "var(--fs-xs)" }}>
            {pending.length} not filed yet. They stay here until you file or discard them.
          </span>
        )}
      </Stack>
    </Modal>
  );
}
