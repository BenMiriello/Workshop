// THE TURN STREAM'S VOCABULARY, IN ONE PLACE.
//
// The server names an SSE frame with the kind it pushed (`event: <kind>`, see
// routes/chat.py), and the client subscribes by that same string. Two halves,
// no contract between them: a kind the server starts sending that the client
// never subscribes to is not an error anywhere. Nothing fails to compile,
// nothing fails a test, no frame arrives to be dropped. It is silent, and it
// stays silent for as long as nobody looks.
//
// That is not hypothetical. `cut` — the marker saying where a mid-turn message
// landed, which is the WHOLE of decision 0018 — was pushed by the server and
// handled by the store's reducer from the day it shipped (2026-08-13), and no
// listener was ever added between them. So the tab that is STREAMING, the one
// the operator is looking at while a turn runs, never had a single cut marker
// and fell back to the drifting unit index that 0018 exists to replace. The
// committed transcript was correct and the live one was not, which is why it
// read as fixed and kept happening. Reported again 2026-08-14.
//
// So the vocabulary is declared here, every kind is given a disposition, and
// two things follow that did not before:
//
//   * `ConsumedWireKind` is derived from the ones that are NOT ignored, and
//     useTurnStream's handler table is a `Record` over it. A kind declared as
//     consumed with no handler does not compile, and the listeners are
//     registered by iterating that table, so a handler cannot be written and
//     left unregistered either.
//   * `streamWire.test.ts` reads the backend and asserts every kind it pushes
//     is declared here. A new server event fails a test naming it, instead of
//     being dropped on the floor.
//
// Adding a kind means adding it here first. If the client genuinely has nothing
// to do with it, say `ignored` and say why: a decision recorded is the thing
// that makes the next reader's silence deliberate rather than accidental.

/** What this client does with each frame the server can send. */
export const WIRE_KINDS = {
  /** Assistant text, appended to the live segments. */
  text: "delta",
  /** A tool call starting. */
  tool: "delta",
  /** A tool's result; only a subagent's own closes a segment. */
  tool_result: "delta",
  /** The turn is blocked asking for permission. */
  permission: "delta",
  /** That question was answered. */
  permission_resolved: "delta",
  /** WHERE A MID-TURN MESSAGE LANDED. The streaming tab builds its own copy of
   *  the segments from these frames and never sees the server's list until the
   *  turn commits, so without this frame the live view has no recorded position
   *  to split on at all. See docs/decisions/0018 and transcriptBlocks.ts. */
  cut: "delta",
  /** The model's report of how full its context window is. */
  context: "meter",
  /** The turn is committing; the socket settles and retires. */
  done: "lifecycle",
  /** The stream generator closed (synthesised by the route, not pushed). */
  end: "lifecycle",
  /** Server heartbeat, ~1s. Any frame proves liveness; this one exists so a
   *  quiet turn still does. */
  ping: "liveness",
  /** A turn-level error carrying a detail, as distinct from a socket drop. */
  error: "problem",
  /** The turn began. The client already knows: it dispatched `runStarted` when
   *  the operator hit send, before this could arrive. */
  started: "ignored",
  /** The CLI's end-of-turn result envelope. `done` drives the retirement and
   *  the committed reply carries the outcome; nothing here is rendered live. */
  result: "ignored",
  /** A journal checkpoint, for resuming a reattached turn server-side. */
  checkpoint: "ignored",
  /** The CLI's own metadata frames (compact boundaries and the like). Consumed
   *  by the server while it drives the turn; the client learns the outcome from
   *  the log. */
  meta: "ignored",
  /** The CLI echoing back a prompt it read. Deliberately never rendered: the
   *  operator's own messages are already in the log where they were sent. */
  user_echo: "ignored",
} as const;

export type WireKind = keyof typeof WIRE_KINDS;

/** The kinds the client must actually do something with. Deriving this rather
 *  than writing a second list is what makes the handler table exhaustive. */
export type ConsumedWireKind = {
  [K in WireKind]: (typeof WIRE_KINDS)[K] extends "ignored" ? never : K;
}[WireKind];

/** Every kind, for the contract test. */
export const ALL_WIRE_KINDS = Object.keys(WIRE_KINDS) as WireKind[];

/** The kinds a socket subscribes to. */
export const CONSUMED_WIRE_KINDS = ALL_WIRE_KINDS.filter(
  (k) => WIRE_KINDS[k] !== "ignored",
) as ConsumedWireKind[];
