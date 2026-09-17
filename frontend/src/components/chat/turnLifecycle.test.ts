import { describe, expect, it } from "vitest";
import type { ChatSegment } from "../../lib/chat";
import {
  deriveTurnLifecycle, indicatorLabel, isBusy, isCompacting, runningTurnId,
  showIndicator, type TurnLifecycle,
} from "./turnLifecycle";

// The rule the chat's turn state is made of, executed rather than watched.
// There is no render harness in this tree (frontend/CLAUDE.md), so this file is
// the whole verification for "is a turn running and what does it say" — which is
// exactly why the rule was extracted out of the hooks in the first place.

const quiet = {
  activeTurn: null,
  liveShown: false,
  starting: false,
  stopping: false,
  reconnecting: false,
  detached: false,
};

const run = (over: Partial<{
  turnId: string; segments: ChatSegment[]; startedMs: number | null; command: string;
}> = {}) => ({
  turnId: "t1", segments: [] as ChatSegment[], startedMs: 1_000_000, ...over,
});

describe("every arm of the union is constructible", () => {
  it("is idle when nothing is happening", () => {
    expect(deriveTurnLifecycle(quiet)).toEqual({ kind: "idle" });
  });

  it("is starting when the POST is out and the server has not named a turn", () => {
    expect(deriveTurnLifecycle({ ...quiet, starting: true })).toEqual({ kind: "starting" });
  });

  it("is running once the server names a turn, carrying that turn's own facts", () => {
    const lc = deriveTurnLifecycle({ ...quiet, activeTurn: run(), liveShown: true });
    expect(lc).toEqual({
      kind: "running", turnId: "t1", watching: "attached", segments: [],
      startedMs: 1_000_000, shown: true,
    });
  });

  it("is stopping once a stop request is out against a live run", () => {
    const lc = deriveTurnLifecycle({ ...quiet, activeTurn: run(), stopping: true });
    expect(lc).toEqual({ kind: "stopping", turnId: "t1" });
  });

  it("does not go to stopping with no run to stop", () => {
    // `stopping` is client-local and outlives nothing: the flag can still be up
    // for a beat after the server retires the run, and claiming a stop over an
    // empty thread would put "Stopping" on a chat with nothing in flight.
    expect(deriveTurnLifecycle({ ...quiet, stopping: true })).toEqual({ kind: "idle" });
    expect(deriveTurnLifecycle({ ...quiet, stopping: true, starting: true }))
      .toEqual({ kind: "starting" });
  });
});

describe("precedence", () => {
  it("a stop outranks the run it is stopping", () => {
    // A reconnect during a stop is still a stop as far as the reader is
    // concerned: they asked for it to end and are waiting to hear that it did.
    const lc = deriveTurnLifecycle({
      ...quiet, activeTurn: run(), stopping: true, reconnecting: true, liveShown: true,
    });
    expect(lc.kind).toBe("stopping");
    expect(indicatorLabel(lc)).toBe("Stopping");
  });

  it("a named run outranks a send that has not been answered yet", () => {
    // Both are true for the length of one render when a send's POST returns:
    // `starting` has not been cleared and `activeTurn` is already set. The run
    // is the more specific fact and it is the one with an id.
    const lc = deriveTurnLifecycle({ ...quiet, activeTurn: run(), starting: true });
    expect(lc.kind).toBe("running");
  });

  it("prefers reconnecting over detached, because it is the more specific fact", () => {
    // Moved from sendPlan.test.ts, where it was asserted on `turnPhase`. The two
    // flags are not mutually exclusive in useTurnStream, so collapsing them into
    // one `watching` applies the precedence at the derivation instead of at the
    // label — same order, same words.
    const lc = deriveTurnLifecycle({
      ...quiet, activeTurn: run(), reconnecting: true, detached: true,
    });
    expect(lc.kind === "running" && lc.watching).toBe("reconnecting");
    expect(indicatorLabel(lc)).toBe("Reconnecting");
  });
});

describe("the working indicator never goes quiet on a running turn", () => {
  // Moved from sendPlan.test.ts (`TURN_LABEL[turnPhase(...)]`), adapted to the
  // selector that replaced them. The wording is unchanged on purpose: this stage
  // was a representation change, not a rewording.
  const running = { ...quiet, activeTurn: run(), liveShown: true };

  it("says what a plain turn is doing", () => {
    expect(indicatorLabel(deriveTurnLifecycle(running))).toBe("Working");
  });

  it("does NOT name the step in flight: one word for a running turn", () => {
    // REVERSES THIS TEST'S EARLIER ASSERTION, which expected "Running" for a
    // live Bash step. The per-tool verb table is deleted (turnStatus.ts): the
    // step list underneath already names every tool exactly, so the indicator
    // was a blurrier second copy of it that changed every few hundred
    // milliseconds. Operator asked for the custom verbs to go twice; the first
    // time only one entry changed and the table stayed.
    const segments: ChatSegment[] = [{ t: "tool", name: "Bash", at: 1, input: {} } as ChatSegment];
    expect(indicatorLabel(deriveTurnLifecycle({ ...running, activeTurn: run({ segments }) })))
      .toBe("Working");
  });

  it("says the same one word whichever tool is in flight", () => {
    // The property that makes the table unreconstructable: no tool name may
    // change this string. Re-adding any per-tool verb fails here.
    for (const name of ["Bash", "Read", "Grep", "WebSearch", "mcp__workshop__search"]) {
      const segments: ChatSegment[] = [{ t: "tool", name, at: 1, input: {} } as ChatSegment];
      expect(indicatorLabel(deriveTurnLifecycle({ ...running, activeTurn: run({ segments }) })))
        .toBe("Working");
    }
  });

  it("still says a turn is blocked on the operator, which is a state not a verb", () => {
    // The one survivor of the delete. A pending approval is the case where the
    // operator has to act, so it must not read as ordinary work.
    const segments: ChatSegment[] = [{ t: "approval", id: "a", state: "pending" } as ChatSegment];
    expect(indicatorLabel(deriveTurnLifecycle({ ...running, activeTurn: run({ segments }) })))
      .toBe("Waiting");
  });

  it("says so while the socket is being retried", () => {
    expect(indicatorLabel(deriveTurnLifecycle({ ...running, reconnecting: true })))
      .toBe("Reconnecting");
  });

  it("still reports a run this tab has given up watching", () => {
    // The fifty-minute silence: out of reconnect budget, or stopped watching.
    // The run is in flight either way.
    expect(indicatorLabel(deriveTurnLifecycle({ ...running, detached: true })))
      .toBe("Still running");
  });

  it("says the connection state, not the work, once the socket is gone", () => {
    // A detached run's segments are stale by definition — the poll is what is
    // advancing them — so the label describes the CONNECTION instead.
    const segments: ChatSegment[] = [{ t: "tool", name: "Bash", at: 1, input: {} } as ChatSegment];
    expect(indicatorLabel(deriveTurnLifecycle({
      ...running, detached: true, activeTurn: run({ segments }),
    }))).toBe("Still running");
  });
});

// ---------------------------------------------------------------------------
// The three defects fixed in 2277cc60, as named regressions. Each was a
// disagreement between two derivations of one fact; the union is what makes
// them unrepresentable, and these are what say so.
// ---------------------------------------------------------------------------

describe("2277cc60 regressions", () => {
  it("does not say 'Still running' before a socket has ever opened", () => {
    // THE FLASH. At the top of a turn the tab is not attached YET: the run is
    // known active from the events page and the attach effect has not run, a
    // measured 60-140ms later. That window is not the detached case, and calling
    // it one made the indicator show the wording for a socket that ENDED at the
    // moment the operator started the turn. `detached` is a fact the stream
    // reports (only its give-up paths set it), so an un-attached start reads as
    // plain work.
    const lc = deriveTurnLifecycle({
      ...quiet, activeTurn: run(), liveShown: true, reconnecting: false, detached: false,
    });
    expect(lc.kind === "running" && lc.watching).toBe("attached");
    expect(indicatorLabel(lc)).toBe("Working");
  });

  it("is busy continuously across starting -> running, with no idle frame between", () => {
    // THE MESSAGE-DISAPPEARS FRAME. `starting` is client-local and `activeTurn`
    // is server state, and the handoff between them is not atomic: the POST
    // resolves, `starting` clears, and the sync that sets `activeTurn` may not
    // have landed. Every ordering of that handoff has to stay busy, because a
    // single false frame is what dropped the operator's own message out of the
    // transcript and put the empty state over an open chat.
    const send = { ...quiet, starting: true };                       // POST out
    const both = { ...quiet, starting: true, activeTurn: run() };    // overlap
    const named = { ...quiet, activeTurn: run(), liveShown: true };  // server answered
    for (const step of [send, both, named]) {
      expect(isBusy(deriveTurnLifecycle(step))).toBe(true);
    }
    // And the other ordering: the run is named while the POST is still open.
    expect(isBusy(deriveTurnLifecycle({ ...quiet, starting: true, activeTurn: run() }))).toBe(true);
  });

  it("shows no indicator for a run whose reply the log already holds, and stays busy", () => {
    // `turnActive === true` DOES NOT IMPLY AN INDICATOR IS ON SCREEN.
    // chat_log.read_thread reads `meta` before the events while commit_run
    // appends the reply first and clears `active_turn_id` after, so a read
    // landing in that gap returns the committed reply AND an `active_turn` still
    // naming the run that produced it. transcriptBlocks suppresses the live
    // block there (`isLiveShown`); the 1 Hz clock and the composer's streaming
    // affordances used to ignore that and animate nothing.
    const lc = deriveTurnLifecycle({ ...quiet, activeTurn: run(), liveShown: false });
    expect(lc.kind === "running" && lc.shown).toBe(false);
    expect(showIndicator(lc)).toBe(false);
    // Busy is the OTHER question and its answer does not change: the server
    // still has a run, so the placeholder must not claim the chat is empty.
    expect(isBusy(lc)).toBe(true);
  });
});

describe("selectors", () => {
  const cases: [string, TurnLifecycle, boolean, boolean, string | null][] = [
    // name,             lifecycle,                                    busy,  shown, turnId
    ["idle", { kind: "idle" }, false, false, null],
    ["starting", { kind: "starting" }, true, false, null],
    ["running shown", {
      kind: "running", turnId: "t1", watching: "attached", segments: [],
      startedMs: null, shown: true,
    }, true, true, "t1"],
    ["running hidden", {
      kind: "running", turnId: "t1", watching: "attached", segments: [],
      startedMs: null, shown: false,
    }, true, false, "t1"],
    ["stopping", { kind: "stopping", turnId: "t1" }, true, true, "t1"],
  ];

  for (const [name, lc, busy, shown, id] of cases) {
    it(`answers all three for ${name}`, () => {
      expect(isBusy(lc)).toBe(busy);
      expect(showIndicator(lc)).toBe(shown);
      expect(runningTurnId(lc)).toBe(id);
    });
  }

  it("keeps the indicator up while a stop is in flight", () => {
    // Ending a turn is not instant, and the one thing that must not happen is
    // the working line vanishing the moment Stop is pressed: that reads as the
    // run having ended, and it has not.
    expect(showIndicator({ kind: "stopping", turnId: "t1" })).toBe(true);
  });

  it("carries no turn id when nothing is running", () => {
    expect(runningTurnId(deriveTurnLifecycle(quiet))).toBeNull();
    expect(runningTurnId(deriveTurnLifecycle({ ...quiet, starting: true }))).toBeNull();
  });
});


// A COMPACTION SAYS SO. `/compact` stopped being a turn, the "Compacting" label
// was deleted as unreachable, and then `/compact` became a turn again and the
// label did not come back -- so a compaction has said "Working" since
// 2026-08-27. Reported 2026-09-16.
describe("the label for a compaction", () => {
  it("says Compacting while the chat's own /compact runs", () => {
    const lc = deriveTurnLifecycle({ ...quiet, activeTurn: run({ command: "/compact" }), liveShown: true });
    expect(indicatorLabel(lc)).toBe("Compacting");
    expect(isCompacting(lc)).toBe(true);
  });

  it("still says Working for an ordinary turn", () => {
    const lc = deriveTurnLifecycle({ ...quiet, activeTurn: run(), liveShown: true });
    expect(indicatorLabel(lc)).toBe("Working");
    expect(isCompacting(lc)).toBe(false);
  });

  it("does not claim a compaction for some other slash command", () => {
    const lc = deriveTurnLifecycle({ ...quiet, activeTurn: run({ command: "/model" }), liveShown: true });
    expect(indicatorLabel(lc)).toBe("Working");
    expect(isCompacting(lc)).toBe(false);
  });

  it("is outranked by stopping, which is what the operator just asked for", () => {
    const lc = deriveTurnLifecycle({
      ...quiet, activeTurn: run({ command: "/compact" }), liveShown: true, stopping: true,
    });
    expect(indicatorLabel(lc)).toBe("Stopping");
  });

  it("outranks the connection wording, because it is true in every tab", () => {
    // "Still running" describes THIS tab's socket. What the turn is doing is a
    // fact about the run, so a compaction watched from a detached tab is still a
    // compaction.
    const lc = deriveTurnLifecycle({
      ...quiet, activeTurn: run({ command: "/compact" }), liveShown: true, detached: true,
    });
    expect(indicatorLabel(lc)).toBe("Compacting");
  });

  it("says Reconnecting over it, because a dropped socket is the more urgent fact", () => {
    const lc = deriveTurnLifecycle({
      ...quiet, activeTurn: run({ command: "/compact" }), liveShown: true, reconnecting: true,
    });
    expect(indicatorLabel(lc)).toBe("Reconnecting");
  });
});
