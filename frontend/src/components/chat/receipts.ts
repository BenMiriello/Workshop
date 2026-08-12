// WHAT A TURN CHANGED OUTSIDE THE CHAT, and where to go and look at it.
//
// A receipt is not a step (steps fold away with the rest of a turn's work) and
// not a status (that is how the turn ended). The rules for reading one are here
// rather than in the component so they can be executed: there is no DOM harness
// in this repo, so a pure module beside the renderer is how a rule gets tested.

export type ReceiptRef = { scope: string; id: string; title?: string };
export type Receipt = { kind: string; label: string; ref?: ReceiptRef };

/** The receipts on a message, typed. `notes` is the pre-typing form and every
 *  stored turn has it, so it is read as untyped receipts rather than lost.
 *
 *  The parenthesis rule is the write side's (build_streaming.build_receipts):
 *  every failure string these helpers produce is parenthesised. Without it a
 *  stored refusal, "(a triager agent may not file tickets)", reads back as
 *  though it succeeded. */
export function receiptItems(meta?: {
  receipts?: Receipt[];
  notes?: string[];
}): Receipt[] {
  if (meta?.receipts?.length) return meta.receipts;
  return (meta?.notes || []).map((label) => ({
    kind: label.trim().startsWith("(") ? "warn" : "note",
    label,
  }));
}

// The one sentence apply_note_block produces for a written note. Parsed ONLY as
// a fallback for receipts stored before refs existed: every note ever written by
// this system has a receipt, and without this every one of them stays a dead
// sentence forever. New receipts carry a real ref and never reach this.
const LEGACY_NOTE = /^Saved a note to (.+?)\.?$/;

/** Where a receipt's object can be opened, or null when there is nowhere to go.
 *
 *  A warning never links: a refusal or a parse failure produced nothing, and a
 *  link to a note that was never written is worse than no link. */
export function receiptHref(r: Receipt): string | null {
  if (r.kind === "warn") return null;
  if (r.kind !== "note") return null;
  // The exact note, when the write recorded which one it made.
  if (r.ref?.scope && r.ref.id) {
    return `/memory?scope=${encodeURIComponent(r.ref.scope)}&note=${encodeURIComponent(r.ref.id)}`;
  }
  // Legacy: the scope's note list. Not the note itself, but the right shelf.
  const m = LEGACY_NOTE.exec(r.label.trim());
  return m ? `/memory?scope=${encodeURIComponent(m[1])}` : null;
}

/** What the row says. The note's own title when there is one, because "Saved a
 *  note to workshop" describes the machine's bookkeeping and the title
 *  describes the thing the operator might want to open. */
export function receiptLabel(r: Receipt): string {
  return r.ref?.title || r.label;
}
