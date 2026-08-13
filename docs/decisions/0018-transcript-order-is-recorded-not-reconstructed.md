# 0018. Transcript order is recorded, not reconstructed

Date: 2026-08-12
Status: accepted

## Context

Chat message sequencing has been reported broken, by the operator, at least six
separate times across 2026-08-09 to 2026-08-12. Each report was answered with a
fix to the symptom, each fix was correct in isolation, and the next report came
anyway. That pattern is the evidence that the defect is in the design and not in
any of the six sites.

### What the log actually stores

`.runtime/users/<uid>/chat/<thread>/log.jsonl` is an append-only event log with
a monotonic `seq`. Its event kinds are `message`, `run`, `boundary`, `title`,
`scope` (`chat_log.KINDS`). Everything the operator does is a point event with a
seq, and so is the run lifecycle: a real thread reads

```
seq 58  run     b640b80d  started
seq 59  message user      "Okay, this seems better..."
seq 60  message user      "The other really nice thing..."   during_run=b640b80d at_unit=10
seq 61  message assistant                                     turn_id=b640b80d  85 segments
seq 62  run     b640b80d  finished
```

**An assistant turn is an interval, and the log stores it as a single point
event at the moment it ends.** Seq 61 is after both of the messages that were
sent during it. The log therefore cannot express where seq 60 sits inside seq
61, which is the only question the transcript has to answer.

(Note in passing: seq 58 precedes seq 59, so the log also records a run as
starting before the message that started it. Nothing renders `run` events
positionally today, so it is invisible, but it is the same defect: the log's
order is not a faithful record of what happened.)

### The compensation, and why it keeps failing

Because the interval is not representable, the true order is **reconstructed at
render time** from a second coordinate bolted onto the *user* message:
`meta.at_unit`, an integer index into the reply's segment list, recorded when
the message arrives and interpreted by the client against the *final* segment
list. Four distinct failure modes follow, and each one has been reported:

1. **The coordinate is recorded against a buffer that keeps changing.** A
   subagent is one top-level segment that grows internally; `unit_count` counts
   `1 + len(steps)`. An index recorded against that buffer stops meaning the
   same boundary the moment any segment before it grows, and `snapCut`
   (`transcriptUnits.ts:78`) can only paper over it.

   Measured, not argued. Two delegations open at once (this system launches
   parallel subagents routinely), A launched first, B second, and a message sent
   after both:

   ```
   at_unit recorded at send time: 5
   split at send time:            [[A, B], []]      both above the message  (correct)
   split once A has grown:        [[A], [B]]        B is now BELOW it       (wrong)
   ```

   B was launched before the message was typed and must stay above it. It does
   not: as A keeps working, the recorded index slides into A and snaps to A's
   end, and B falls through to the far side of the operator's message. Nothing
   about B changed. This is live today.
2. **The coordinate is read from either of two sources that disagree.**
   `_cut_point` (`routes/chat.py:3125`) reads the live in-memory turn when this
   process holds it, and a throttled on-disk snapshot when it does not. The
   snapshot lags, so the fallback yields a *lower* index, which renders the
   message above output it did see.
3. **The coordinate has a degenerate value with no representation.**
   `at_unit: 0` is legal and common: it is every message sent in the first
   seconds of a turn. It splits the reply into an empty leading slice and the
   rest. An empty slice renders as nothing, so the message floated to the top of
   the run, above the working indicator that had been on screen before it was
   typed. Reported 2026-08-12.
4. **Two further coordinate systems are layered on.** Pending sends are placed
   by `afterSeq` (`transcriptBlocks.anchorIndex`), and whether the run is live at
   all is decided from `active_run`, which the server writes non-atomically
   against the log (`selectCommitted`'s `liveRunId` note). Both have produced
   their own reordering reports.

So: **four coordinate systems, reconciled by a 150-line render-time derivation,
because the log stores an interval as a point.** Every previous fix adjusted one
of the four. That is why the bug always came back somewhere else.

## Decision

**The server records the order at the moment it happens. The client splits on
the recorded position and does no arithmetic.**

When a message arrives during a run, the server writes the message's id onto the
segment it arrived after:

```json
{"t": "tool", "name": "Read", ..., "cut_after": ["01KZVVRKFKZT18H3W1YSED0TQF"]}
```

and, for the case where the run has emitted nothing at all, onto the turn
itself as `meta.cut_head`. A list, because several messages can land at the same
position.

### Why not a marker segment

The obvious form is a new segment type spliced into the stream
(`{"t": "mark", "ref": ...}`). It was designed that way first and rejected on
evidence: an inventory of every consumer that walks a segment list found **six
hard breaks** for an unrecognised `t`, two of which throw.
`threadStore.segWeight` falls through to the `agent` branch and dereferences
`s.steps`, crashing the reducer on the next snapshot; `chatScope.estimateContextTokens`
does the same inside render; `Transcript`'s answer/work split and
`build_streaming`'s post-action text replacement both scan backwards over
trailing text and would respectively hide and duplicate the reply's prose;
`grouping.buildTurnBlocks` would split one tool group into two and emit a row
that renders nothing; and `ChatSegment` / `StreamEvent` are closed unions.

An extra optional KEY on an existing segment has none of that surface. Every one
of those walkers already ignores keys it does not know. The recorded fact is
identical; only the carrier changes.

`cut_after` is a **position, not an index**, and that is what removes all four
failure modes:

- It cannot drift. "After this segment" stays true however much any other
  segment grows. The parallel-delegation case above is fixed by construction:
  the cut is written onto B, so nothing A does can move it. `snapCut` has
  nothing left to fix and loses its only non-legacy caller.
- It cannot be read from a lagging source. It lives on the same buffer that is
  snapshotted and committed, so wherever the segments are read from, the cut is
  on them.
- It has no degenerate value. "Nothing had been emitted" is `cut_head`, which is
  a position like any other rather than the integer 0 pretending to be one.
- Splitting on a recorded boundary is exact. Nothing is clamped, snapped, or
  forced non-decreasing.

### The rule this creates for anything that rewrites segments

A cut is attached to a segment, so **a segment carrying a cut may not be dropped
or replaced without carrying its cut forward**. Two places rewrite the list and
both must honour it: `build_streaming`'s trailing-text replacement after
`apply_actions`, and `_drop_text_segments` (the `/compact` path). This is the
one new invariant the design introduces, and it is stated here because it is the
way this decision would silently rot.

### What stays

**The working indicator still needs one rule**, and only one: a live run whose
leading slice is empty has produced nothing yet, and its indicator belongs on
that leading slice, because the run started before the message that follows it.
This is `workingSliceIndex`, and after this change it is the *only* placement
rule in the client that is not "render them in the order they are recorded in".

`afterSeq` for pending sends stays. A pending send is not in the log yet, so it
genuinely has no recorded position; anchoring it to the log's tail at send time
is the honest answer.

### The fallback, stated explicitly

A mark can only be written if this process holds the live turn. When it does not
(`build_streaming.get_turn` returns `None`), the server keeps writing `at_unit`
from the snapshot as it does today, and the client falls back to it. Precedence
is fixed and tested: **a mark wins; `at_unit` is consulted only for a message
that has no mark.** This is not two schemes in parallel. It is one scheme plus a
degraded path for the case where the primary is physically unavailable.

### Legacy data

Every reply already on disk has no marks, and its messages carry `at_unit`.
Those threads must keep rendering exactly as they do now.

Conversion happens **once, at the client's edge**, in a single adapter: given a
reply's segments and the messages held for it, return the segments with marks
inserted, using the existing unit math when and only when the segments carry no
marks of their own. Everything downstream of that adapter sees one shape. The
unit math is therefore quarantined in one function with one caller, and can be
deleted outright when no thread on disk still needs it.

Doing the conversion at the client edge rather than as a data migration is
deliberate: it rewrites nothing, it is reversible, and it keeps the transition
safe on a live system where the frontend reloads on save and the backend does
not restart until the end of a turn.

## Consequences

- `at_unit` / `at_segment` stop being the primary coordinate. They are not
  removed from storage: existing events carry them and the degraded path writes
  them.
- `transcriptUnits.ts` shrinks to the legacy adapter. `snapCut`, `splitAtUnits`,
  `unitOfSegmentIndex` and `cutPointOf` lose all non-legacy callers.
- A new segment type enters the stream, so every consumer that walks segments
  must ignore an unrecognised `t` rather than mis-count it. That inventory is
  the risk this change carries, and it was taken before the change was written.
- The client's ordering rule becomes statable in one sentence: **render the log
  in seq order; inside a reply, render its segments in order, and where a mark
  names a message, render that message there.**
