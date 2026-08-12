# 0015. Transcript structure is Turn/Group/Step, and injected content carries an origin

- Status: accepted
- Date: 2026-08-12

## Context

Two problems, one root cause: nothing in the system names what a piece of
non-conversational content *is*.

**Display.** The chat log has four event kinds (`services/chat_log.py:97`:
`message`, `run`, `boundary`, `title`). There is no system message kind. What
reads on screen as "a system block" is `meta.segments` rendered by
`AssistantTurn`, which splits off the **trailing run of text segments** as the
answer (`frontend/src/components/chat/Transcript.tsx:341-344`) and puts
everything before it into one `.chat-work` div (`:287`). That div is not a block
type. It is the leftover, and a turn that ends on a tool call has no answer at
all, so all of its prose lands inside it. Separately, `meta.notes` renders as one
bare `.chat-note` div per string outside the bubble (`MessageList.tsx:143-145`),
using the same class as `meta.stopped` (`:141`), so "Saved a note to workshop"
and "Stopped by operator" are visually identical. (`meta.status` and
`meta.timeout` already have their own treatments, and are the model for what the
other two need.) The inconsistency is real: one of those is a *region*, the other
is an *effect*, and the code names neither.

Three consequences follow. `tool` segments carried no id, no result, no status
and no duration, and the tool_result parser never extracted `is_error`
(`services/build_streaming.py:619-623`), so a settled turn could not tell a
command that failed from a file that was read; there was no "succeeded"
predicate anywhere in the pipeline. Nested agent steps rendered only `tool` and
`text`, so nested subagents and nested approvals were silently dropped. And
`started_at` / `ended_at`, set in `commit_run` (`services/chat_store.py:1096`,
`:1109`), were rendered nowhere, so the only clock was a live total anchored to
the whole run, which reads "3h 41m" on a long turn and says nothing.

**Provenance.** There is no system parameter and no message array. Every turn
hands the Claude Code CLI one flat string as a single `role: "user"` message
(`build_streaming.py:53`, `:1465`). Platform preamble, persona, learnings, notes,
other agents' words and the replayed transcript all arrive undifferentiated
except by an `=== HEADING ===` convention that has no closing delimiter and does
not escape its own marker.

The concrete defect: `routes/chat.py` replayed history as `"Operator"` / `"You"`.
`meta.agent` is stored on every such message (`chat_store.py:1118`) and the
@mention path already renders it correctly (`chat.py:1968`), but the main path
dropped it, so a reply written by a mentioned agent was replayed to the assistant
as its own words. Three more places held real provenance and stripped it at the
render site: `learnings.provenance[]` (`services/learnings.py:362`),
`tasks.source.{kind,ref}` (`services/tasks.py:38`), and the compaction summary,
which is written by Haiku (`routes/chat.py:993`) and re-injected as neutral fact.
Knowledge notes had no author field at all.

That this matters is already proven in the codebase: `REALTIME_CHANNEL`
(`services/chat_context.py:760`) exists only because the model correctly refused
a mid-turn operator message it could not attribute. The fix was a paragraph of
prose arguing the channel is first-party. That is provenance handled by argument
instead of by structure.

**Prior art.** Surveyed: Claude Code, VS Code/Copilot agent mode, Cursor, Cline,
Roo Code, Continue, opencode, Zed, Devin, v0, Replit, Aider, AutoGen, CrewAI,
LangGraph, plus activity-feed guidance (Linear, Stream, GitHub Actions,
Buildkite, Carbon, Primer, NN/g). Two findings shaped the structure most: NN/g's
"designs that go beyond 2 disclosure levels typically have low usability" (our
shape already spent three), and Linear's rule that machine events are the
collapsible interstitial *between* human landmarks. One negative finding governs
every summary line: `microsoft/vscode` issue 292985, where `rm -rf important_dir`
collapsed to "Executed command in terminal".

## Decision

### 0. A tool step carries its own outcome. Everything else depends on this.

The wire shape, mirrored in `build_streaming._seg_tool` / `_close_tool` and
`frontend/src/components/chat/segments.ts`:

```
{ t: "tool", name, input, id?, at?, ok?, ms?, error? }
```

`at` (epoch seconds) is present only while the call is open, so it is both the
start of the step's clock and the fact that it is still running. `ok` is set when
the result lands. `error` is kept only on failure.

**Legacy rows read as succeeded and groupable.** Every stored segment predates
these fields. If `ok === undefined` meant "not known to have succeeded", nothing
would group and nothing would collapse on any existing thread, and the redesign
would be invisible where there is most to gain. An unknown outcome is also what
an interrupted turn leaves behind, and painting those red would be a lie.

### 1. Three display levels: Turn, Group, Step. There is no activity region.

A turn is an ordered list of blocks: **prose** (the model's own words) and
**runs** of machine steps between them. The activity region is deleted; its two
jobs (own the fold, mark the work/answer boundary) belong to the run and to the
trailing text respectively.

A turn with no prose at all is common (one that ends on a write, one the operator
stopped) and is simply one run. Prose is a landmark when there is one, not a
required frame.

Interim prose is conversation and renders as prose. It renders through
`WorkText`, not `AnswerBody`: a `ui-card` the model wrote halfway through a turn
and then talked itself out of must not become a live control, and card outcomes
persist server-side.

Disclosure is two levels: expand a group into its steps, expand a step into its
detail. The length clamp on prose is the same level applied to text.

### 2. Grouping merges a window, not a consecutive run.

Merge read-only steps within one turn when they are completed, not failed, and on
the allowlist (`Read`, `NotebookRead`, `Grep`, `Glob`, `WebFetch`, `WebSearch`).

**The window is interleave-tolerant.** "Merge consecutive steps of the same
class" is the rule every product states and it collapses to nothing on the
commonest exploration shape there is: forty reads alternating with forty greps
has no two adjacent steps of one class, so a strict reading produces eighty rows.
A window of groupable steps accumulates until something ungroupable ends it, and
each class in that window emits one row in first-appearance order. Within a
window, display order is by class; across windows, time order is exact. Nothing
groupable can hop over a write or a command, which is what makes that safe.

Window breakers, each its own row: any write or command (`Edit`, `Write`,
`Bash`, `NotebookEdit`, anything unrecognised including MCP tools), anything
failed, anything running, any subagent, any approval, any prose.

Summary grammar is verb + count + noun, with the objects on the same row
(`Read 4 files`, then the paths). A generic verb with the object hidden is
prohibited. A group of one renders as the plain step.

**The cap is per run, not per prose block**: 8 rows, then one `+N earlier steps`
control, never a batched loader. Per prose block would be undefined for a turn
with no prose and would make density a function of how chatty the model was.

Grouping is pure derived logic (`components/chat/grouping.ts`), computed inside
the memoized per-message boundary and never in the transcript selector, which is
where an equivalent cost was already found and fixed once.

### 3. Disclosure is a function of state.

| State | Rendering |
|---|---|
| running | own row, live counter past 10s |
| completed, succeeded | merged into its group |
| completed, failed | own row, open, never merged |
| pending approval | own row, never folded, never summarised away |
| settled turn | the whole turn's work behind ONE line |

A settled turn showing one line per group would be a density regression: today an
old turn shows exactly one line for all of its work, and that is the right amount
for something already read. It shows `12 steps · 2 edits · 1 failed` and opens to
the blocks. A turn blocked on a decision is never summarised away, latest or not.

Two scroll rules: never auto-expand content above the viewport, and scroll on
collapse only, never on expand, and only when already pinned to bottom.

### 4. Steps are what it did. Receipts are what changed. Status is how it ended.

- A **step** is an action inside a turn. It folds.
- A **receipt** is a durable effect of the action pipeline: a note written, a
  ticket filed, a preference captured. Receipts render as one turn-footer row
  outside every fold, because a turn folded six messages back still needs to
  show that it wrote something. `meta.notes: string[]` gains
  `meta.receipts: {kind, label, href?}[]`; `notes` stays and is read as untyped
  receipts, because every stored turn has it.
- A **refused** effect is a fourth kind, not a receipt. "(a triager agent may
  not file tickets)" is not a step (it never ran), not a receipt (nothing
  changed), and not turn status (the turn succeeded). It carries `kind: "warn"`.
- **Status** (`stopped`, `timeout`, `error`) is how the turn ended and renders as
  a quiet line, not in a note-shaped card.

A file write is a step, not a receipt. Receipts are effects of the fenced-block
action pipeline only, so nothing draws twice.

### 5. Time belongs to the innermost running thing.

A running step past 10 seconds carries a live counter on its own row (below that
threshold nothing renders, because a counter on a fast call is flicker). A
finished step shows its duration once it exceeds a second.

The turn's total goes on the settled work summary line, not in a row of its own,
and only past 30 seconds: a number under every reply is noise, and the question a
total answers only exists after a wait. It is computed only when **both**
`started_at` and `ended_at` are present. `transcriptBlocks.ts` strips `ended_at`
from every non-tail slice of an interrupted reply and leaves `started_at`, so a
half-pair would print the same wrong figure on five slices and a fallback to
"now" would make five settled turns tick forever.

### 6. Approvals stay inline, pinned, and keep their decision visible.

A pending approval is never folded and never summarised away. A resolved approval
collapses to one line that keeps which decision was made.

A permission request and a question to the operator are different things, because
blocking is the axis: a permission halts the loop, a question need not. This
record settles the distinction; both the question widget and grouped
multiple-pending approvals are deferred (see Consequences) rather than specified
loosely here.

### 7. A delegated subagent is a step. An addressed agent is a speaker.

No surveyed first-party product gives a delegated sub-agent a top-level byline
inside one orchestrated thread; Copilot Studio documents why. A subagent our turn
delegated to renders as one collapsed row owned by the parent turn, expanding to
its brief, its steps and its report. Its steps group by the same rule as the
parent's, and nested subagents and nested approvals render rather than being
dropped.

An agent the operator addressed by `@mention` is not that: it is answering the
operator directly, so it keeps its own message and its `meta.agent` byline. The
distinction is who addressed it, and it governs both display and replay.

### 8. Injected content carries an origin, in the text, and it is not shown.

Origin is who made a piece of content, orthogonal to where it displays:
`operator`, `self`, `agent:<slug>`, `tool:<name>`, `platform`, `harness`,
`derived:<model>` (`services/origin.py`).

Because every turn is one flat string, provenance must be encoded in text; there
is no other channel that survives a session reset, and resets are routine
(`chat.py:786`, `build_streaming.py:1954`).

We do not tag everything. The reason is **not** token cost, which is under 40
tokens on a prompt already carrying a persona and a transcript tail. It is that
envelope noise degrades instruction-following, so tagging buys nothing where
position already answers the question. Three cases get tagged:

1. **Content attributed to the model that it did not write.** The replay uses
   `meta.agent`, matching what the @mention path already does. This one is a
   correctness fix to a name, not a tag, and costs nothing.
2. **Content another model generated, presented as fact.** The compaction summary
   and the `chat-distilled` note both name Haiku as their author.
3. **Content from other agents or stores arriving unlabelled.** Learnings keep
   their `source`, the task digest keeps `source.kind`, and recalled notes keep
   their author. Each suppresses the unmarked majority (`anon`, `chat`, `self`)
   rather than spending a label to say nothing.

**Blocks are never closed.** Adding `=== END X ===` to some blocks and not others
is worse than adding it to none: a marker that appears sometimes cannot be relied
on. Every block is `=== NAME ===` on one line, and `origin.heading()` is the
constructor for the ones that carry an origin; the rest stay literal and produce
the identical shape.

**Injected bodies are escaped** against both structural conventions: a leading
`=== ` and the `Operator:` / `Assistant:` / `You:` speaker labels, each escaped
with one leading space at column zero only. A note that quotes a conversation, or
a live-context snapshot containing a heading, would otherwise be
indistinguishable from real structure. This is a correctness fix, not a security
control: notes are model-written and we write notes about prompt formats.

Origin never renders in the transcript.

### 9. Harness emissions are routed by lane at ingest.

Four lanes: **conversation**, **activity** (inside a turn), **rail** (what
happened to the thread, between turns, one line, never collapsible), and
**out-of-chat** (work that outlives the turn). Default is activity. Hiding a
block type must still leave a live placeholder, or the run looks hung.

| Harness emission | Lane |
|---|---|
| `system-reminder` (CLAUDE.md, memory, live context) | none in transcript |
| synthetic filler ("No response requested.") | dropped, by exact match only |
| synthetic error (session limit, auth) | turn error state |
| mid-turn operator message | conversation, cut at `at_unit` |
| background task-notification | out-of-chat, updates the launch step |
| hook output | activity, on the step it intercepted |
| permission request | activity, pinned |
| `tool_result` | closes its step; not rendered as content |
| compact boundary | rail |
| `run` started/finished/lost | none; drives the turn header |

**Thinking is dropped, deliberately, for now.** `llm_router` discards it and
`_segment_ingest` has no branch for it, so reasoning is invisible today. It is
neither prose (it must not read as an assertion) nor a step (nothing was
invoked), so it would need a third block type. Naming that here so its absence is
a decision rather than an oversight.

Detached background work never re-enters the transcript as new messages. It
updates the step that launched it, in place. Re-rendering a running process into
a later message would break the log's append-only send order and is prohibited.

**Voice turns are out of scope.** `voice_worker._persist_turn` writes segments
only when a tool ran and never writes timing, and `VoiceTurn.tsx` renders plain
bubbles that never reach `AssistantTurn`. Nothing here applies to them.

## Consequences

Settled, and not to be re-litigated: the activity region is gone and does not
return as a wrapper under another name; interim prose is prose and is not
interactive; a receipt is not a note-shaped div; the ticking clock is on the
step; a delegated subagent has no byline and an addressed agent keeps one;
provenance is encoded in prompt text rather than argued for in prose; blocks are
uniformly unclosed.

Easier: per-step timing and error states exist at all. Grouping is derived, so
the log stays canonical and the rules change without a migration. The lane table
gives every new harness emission a default home.

Harder: `build_streaming._segment_ingest` and `segments.ts:applyStreamEvent` are
deliberate mirrors and must change together or a reload rearranges a turn.
`tests/test_tool_outcomes.py` and `segments.test.ts` assert the same transitions
on each side; keep both. Backward compatibility on read is required for every
stored turn.

Deferred, explicitly, with nothing half-built:

- **The out-of-chat lane** (composer counter, in-place launch-step updates, the
  durable list surface). Depends on a background-process model that does not
  exist. Designed here, not implemented.
- **A first-class question block**, and **grouped multiple-pending approvals**.
  The distinction driving both is settled in §6; the widgets wait for a real use
  rather than being specified loosely.
- **A three-level verbosity setting.** §3 is the default behavior; an override is
  additive.
- **The prompt inspector.** §8's claim that provenance must be inspectable to be
  verified stands, and this is the gap in it: the origins are injected now and
  nothing displays them. The cheap version is one endpoint returning the
  assembled string.
- **Receipts across compaction.** `_fmt_turns` reads `content` only, so receipts
  in a folded range do not survive `/compact`.
- **`receipts[].href`.** Every producer knows the id of the thing it wrote
  (`apply_note_block` has the note id, `apply_learning_note` the hash) and none
  of them return it, so a receipt cannot yet link to what it created. The field
  is not declared until something writes it.
