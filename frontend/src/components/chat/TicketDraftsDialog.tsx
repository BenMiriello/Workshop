import { useCallback, useEffect, useState } from "react";
import { ChevronRight, RefreshCw, Trash2 } from "lucide-react";
import {
  Modal, Button, Stack, Row, Tag, Field, Input, Textarea,
  StateBlock, Skeleton, catFor,
  Notice,
} from "@workshop/ui";
import { scopeLabel } from "../../lib/tasks";
import { shortHttpError } from "../../lib/httpError";

// Turning a conversation into tickets, as a review step.
//
// What this replaces: one button that squashed the last 12 messages into a
// single paragraph and filed it, unseen. A conversation does not contain one
// ticket. It contains some number of them, often zero, mixed with things that
// were discussed and dropped, work already finished, and questions that got
// answered. So this proposes 0..N, each self-contained, and the operator
// decides which are real.
//
// A proposal IS a draft ticket. It used to be a thread-local row in its own
// store, which made "draft" mean two different things; now scanning files real
// tickets with status `draft`, so they dedup against existing work, carry the
// readiness checklist, and show up in Work like anything else awaiting the
// operator. Nothing runs a draft: the sweep skips it and the run endpoint
// refuses it, so proposing is always safe.
//
// Scanning costs a model call and tens of seconds, so this never re-scans on its
// own; the thread remembers when it last did, to report staleness.
//
// A DRAFT ROW AND A WORK PANEL ROW READ AS ONE SYSTEM, and this is how they
// stay one (tickets-in-chat-2026-08-15 §3: the dialog is kept, "its rows adopt
// the new ticket row idiom").
//
// SHARED, because it carries meaning and a draft has the same meaning to carry:
// the row geometry and hover language (`chat-workpanel__row`), the title's ink
// and truncation (`ws-truncate chat-workpanel__title`), the neutral control ink
// on the caret (`chat-workpanel__icon`), and ONE meta line of plain text joined
// with " · " (`chat-workpanel__meta`) instead of a rack of chips. That last one
// is the rule from frontend/CLAUDE.md, "a record page is not a list page": what
// you scan a list for belongs in words, not in a badge.
//
// NOT SHARED, deliberately:
//
//   - `TicketRow` itself. A row is a Link whose whole line navigates to the
//     ticket page. A draft has no page to navigate to, and this line is a
//     disclosure that opens an EDITOR under it, so sharing the component would
//     mean one control doing two incompatible things. Same call `TicketCard`
//     made for the collapsed card header, for the same reason.
//   - `StatusDot` and `ws-ticket-key`. A draft has no status and no key yet.
//     Rendering a dot for `draft` or a key from an id the operator has never
//     seen would be a hue and a handle that say nothing, which fails the
//     "does it vary / is it real" tests before it fails anything else.
//   - Read-only-ness. §3a makes drafts the one editable exception in the chat
//     surface: an unapproved draft is not yet a ticket, and its body is the
//     entire spec the worker will read.
export type Draft = {
  id: string;
  title: string;
  body: string;
  status: string;
  labels?: string[];
  dedup?: string;
};

type Payload = {
  drafts: Draft[];
  scope?: string;
  generated_at: string | null;
  messages_since: number;
};

// `confidence:likely` etc. rides as a label rather than a schema field.
function confidenceOf(d: Draft): string | null {
  const tag = (d.labels ?? []).find((l) => l.startsWith("confidence:"));
  return tag ? tag.slice("confidence:".length) : null;
}

/** The first line of a draft's body, for the row's second line. */
function firstLine(body: string): string {
  return (body || "").split("\n").map((s) => s.trim()).find(Boolean) ?? "";
}

/** The row's meta line: what the panel row says as a state word, said with the
 *  facts a draft actually has. Same shape, plain text joined with " · ".
 *
 *  The scope is not in it. Every draft in this dialog files into the same one
 *  and the header already names it, so repeating it per row tints the list
 *  without labelling anything. */
function draftMeta(d: Draft): string[] {
  const merged = d.dedup === "merge" || d.dedup === "exact";
  const confidence = confidenceOf(d);
  return [
    merged ? "merged into existing" : null,
    confidence ? `confidence: ${confidence}` : null,
    firstLine(d.body) || null,
  ].filter((s): s is string => Boolean(s));
}

export default function TicketDraftsDialog({
  open,
  chatId,
  onClose,
}: {
  open: boolean;
  chatId: string | null;
  onClose: () => void;
}) {
  const [payload, setPayload] = useState<Payload | null>(null);
  const [scope, setScope] = useState<{ scope: string; runnable: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [proposing, setProposing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // APPROVING REPORTS HERE, in the dialog, and nowhere else.
  //
  // It used to raise the `FiledBar` above the transcript, which is deleted: a
  // filing now says so on the ticket card in the answer that produced it. This
  // path has no card, because nothing in the transcript proposed these drafts,
  // so the report belongs to the surface the operator is looking at. The Work
  // panel's "This chat" section carries it from there.
  const [approved, setApproved] = useState<string | null>(null);
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
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
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
    // A report describes the last thing the operator did in this dialog. Opening
    // it again is a new visit, so it does not carry the old one in.
    setApproved(null);
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

  // Approving is the operator agreeing the work should happen. It is the only
  // thing that makes a ticket eligible to run, and it writes the real ticket —
  // there is nothing left to "file", because the draft already IS the ticket.
  const approve = useCallback(async (d: Draft, run: boolean) => {
    if (!chatId || !payload?.scope) return;
    setBusy(d.id);
    try {
      const r = await fetch(
        `/api/tasks/${encodeURIComponent(payload.scope)}/${encodeURIComponent(d.id)}/approve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run }),
        },
      );
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const j = await r.json();
      const where = scopeLabel(payload.scope);
      setApproved(j.run?.job_id
        ? `"${d.title}" is filed in ${where} and running.`
        : `"${d.title}" is filed in ${where}.`);
      await loadStored();
    } catch (e) {
      setErr(`Couldn't approve "${d.title}": ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }, [chatId, payload, loadStored]);

  const drafts = payload?.drafts ?? [];

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
            <span className="tdraft__note">Files into</span>
            <Tag hue={catFor(scope.scope)}>{scopeLabel(scope.scope)}</Tag>
            {!scope.runnable && (
              <span className="tdraft__note">
                this scope can't run work directly
              </span>
            )}
          </Row>
        )}

        {payload && payload.messages_since > 0 && drafts.length > 0 && (
          <span className="tdraft__note">
            {payload.messages_since} message{payload.messages_since === 1 ? "" : "s"} since these were
            drafted. Re-read the chat to refresh them.
          </span>
        )}

        {approved && <Notice>{approved}</Notice>}

        {err && (
          <Notice
            tone="error"
            detail={err}
            action={<Button size="sm" variant="ghost" onClick={() => void propose()}>Try again</Button>}
          >
            Couldn't draft tickets from this chat.
          </Notice>
        )}

        {proposing ? (
          <Stack gap="sm">
            <Skeleton />
            <span className="tdraft__reading">
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
              const meta = draftMeta(d);
              return (
                <li key={d.id} className="tdraft">
                  <button
                    type="button"
                    className="chat-workpanel__row"
                    onClick={() => setExpanded(isOpen ? null : d.id)}
                    aria-expanded={isOpen}
                    title={d.title}
                  >
                    <ChevronRight size={14} strokeWidth={1.8} aria-hidden
                                  className={`chat-workpanel__icon tdraft__caret${isOpen ? " is-open" : ""}`} />
                    <span className="chat-workpanel__rowbody">
                      <span className="chat-workpanel__line">
                        <span className="ws-truncate chat-workpanel__title">{d.title}</span>
                      </span>
                      {meta.length > 0 && (
                        <span className="chat-workpanel__meta">{meta.join(" · ")}</span>
                      )}
                    </span>
                  </button>

                  {isOpen && (
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
                      <Row wrap style={{ gap: "var(--space-2)" }}>
                        <Button size="sm" variant="primary" disabled={busy === d.id || !d.title.trim()}
                                onClick={() => void approve(d, false)}>
                          {busy === d.id ? "Approving…" : "Approve"}
                        </Button>
                        {scope?.runnable && (
                          <Button size="sm" variant="ghost" disabled={busy === d.id || !d.title.trim()}
                                  onClick={() => void approve(d, true)}>
                            Approve and run now
                          </Button>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => void discard(d.id)}>
                          <Trash2 size={14} aria-hidden /> Discard
                        </Button>
                      </Row>
                    </div>
                  )}

                </li>
              );
            })}
          </ul>
        )}

        {drafts.length > 0 && (
          <span className="tdraft__note">
            {drafts.length} waiting for approval. Nothing runs them until you approve;
            they are visible in Tickets either way.
          </span>
        )}
      </Stack>
    </Modal>
  );
}
