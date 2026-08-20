# Compaction on both paths: CLI (dev) and API (production) behind one adapter seam

Status: implementation plan, 2026-08-20. Builds on
`docs/platform-redesign/context-compaction-research-2026-08-20.md` (read that first;
this doc does not repeat its economics/survey sections, only what's new from going
deeper on the CLI-vs-API fork and the adapter design). Scope per the brief: Workshop's
chat backend runs the Claude Code CLI headless today (`backend/services/build_streaming.py`
`_build_cmd`, CLI 2.1.143 installed); the future production path is the raw Claude API,
per-token; dev stays on the CLI, cheaper under subscription auth. The operator wants
"regular compact" to work the same way on both, native compaction retained where it
genuinely exists, built behind provider adapters so a Codex adapter can be added later.

---

## (a) The fork: can headless CLI compaction be triggered at all

**Answer: no, not the manual command, and Workshop's own code already documents this
correctly. There is a second, narrower question the existing docstring does not
address - automatic (threshold-triggered) compaction - and that one is genuinely
open, not closed.**

### The manual `/compact` command: confirmed dead in headless mode, from two independent sources

1. **Workshop's own empirical test**, recorded in the docstring at
 `backend/routes/chat.py:1759` (`compact_thread`): a leading `/compact` sent as a turn
 over `--input-format stream-json` is parsed into a local-command block that nothing
 executes. Measured 2026-08-12T05:10:18Z: the CLI session recorded
 `user <command-name>/compact</command-name>` followed by
 `system local_command` with a `local-command-caveat` telling the model not to
 respond, returned in ~2 seconds, no `compact_boundary`, window unchanged. A real
 `/compact` run the same night in an interactive terminal took 136 seconds and
 reported `492981 -> 14880` tokens. This is a direct, dated, on-this-codebase
 measurement, not a general claim.
2. **Anthropic's own current docs corroborate it structurally**, fetched for this report
 (`code.claude.com/docs/en/commands`, `code.claude.com/docs/en/headless`, current as
 of 2026-08-20). The commands reference carries no `-p`/headless availability note for
 `/compact` or `/clear` at all, in explicit contrast to `/model`, `/effort`, `/fast`,
 `/color`, and `/rename`, each of which states outright that it "accept\[s] the value
 as an argument" in `-p` mode and names the CLI version that added it (v2.1.205). The
 headless-mode page repeats the same asymmetry from the other direction: "Built-in
 commands that only run in the terminal interface, such as `/login`, aren't available
 in `-p` mode," and then lists the commands that *do* work as an explicit, closed set
 (`/model`, `/effort`, `/fast`, `/color`, `/rename`, plus `/config key=value`).
 `/compact` and `/clear` are absent from that set on both pages. Two independently
 maintained pages agreeing by omission, against a page that is otherwise careful to
 flag every `-p`-compatible command explicitly, is about as strong as documentation
 silence gets without a page that says the word "no."
3. **The control-protocol list Workshop's docstring cites (initialize, set_model,
 rename_session, read_file, interrupt, set_permission_mode, set_max_thinking_tokens,
 mcp_oauth_callback_url, file_suggestions, get_usage, set_color) is stale but the
 conclusion survives.** Fetching the current TypeScript Agent SDK reference
 (`code.claude.com/docs/en/agent-sdk/typescript`) turned up a different, apparently
 newer list built from the `Query` object's own methods: `initialize`, `set_model`,
 `set_permission_mode`, `set_max_thinking_tokens` (marked deprecated),
 `apply_flag_settings`, `interrupt`, `read_file`, **`get_context_usage`**,
 `reconnect_mcp_server`, `toggle_mcp_server`, `set_mcp_servers`, `stop_task`. Neither
 list contains anything named `compact` or `summarize`. `get_context_usage` is new
 since Workshop's CLI 2.1.143 and worth picking up separately (it's a read, not a
 trigger - see the "build now" section) but it does not close this gap; there is
 still no control-protocol request that starts a compaction.

**Consequence, stated plainly per the brief: the dev/CLI path cannot ever trigger
native manual compaction from headless mode. It must keep a custom fold. "Native
compact" in the literal sense (Claude Code's own `/compact` machinery) is only
reachable on the future API path, via the API's own separate, documented
`compact_20260112` feature - which is not the same mechanism as Claude Code's
`/compact` at all, just a same-named idea implemented independently by a different
team surface. "Same behavior on both paths" therefore has to mean same OPERATION
(user presses Compact, the window shrinks, a summary appears, nothing is deleted from
the transcript) and same UX, implemented by two adapters with genuinely different
internals - not the same underlying mechanism running twice. Say this in the UI/docs
too if it ever comes up: the CLI adapter's "Compact" is Workshop's own summarizer; the
API adapter's "Compact" will eventually be Anthropic's.**

### The narrower, genuinely open question: automatic compaction inside a `--resume`d session

This is new ground the existing docstring and the companion research doc don't cover,
and it matters for a specific reason: Workshop's build/project chats do not resend the
full transcript on a resumed turn. `_resumed_prompt`
(`backend/routes/chat.py:2492`) sends only a small delta (stop-rule reminder, mode
block, contract-refresh if the digest moved, runtime briefing or a live-context diff) and
explicitly relies on the CLI's own resumed session to already hold "the persona, the
history, and everything the first turn assembled." That means, for exactly the sessions
Workshop resumes turn over turn, the CLI process really is accumulating its own internal
context across turns via `--resume`, even though Workshop kills and respawns the OS
process after every single turn (`_retire_turn_proc`, `build_streaming.py:1669`, "the CLI
does not exit when it finishes a turn... somebody has to retire it"). A resumed process
reconstructs its context from Claude Code's own on-disk session store before doing
anything else, so if that reconstructed context is large, automatic (not manual,
not slash-command) compaction is architecturally free to fire during that
reconstruction, inside a single turn's process lifetime, with nobody having typed
`/compact`.

Evidence this mechanism is real and is not gated to interactive terminals:

- `code.claude.com/docs/en/hooks`: the `PreCompact`/`PostCompact` hook matcher table
 lists two matcher values, `manual` and `auto` - i.e. Anthropic's own hook system
 treats automatic, threshold-triggered compaction as a first-class trigger distinct
 from the slash command, not an implementation detail of it.
- `code.claude.com/docs/en/env-vars`: `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` is documented as
 applying "to both main conversations and subagents," with no stated interactive-only
 restriction; `CLAUDE_CODE_AUTO_COMPACT_WINDOW` likewise carries no such restriction.
 Both are described as harness-level knobs, not terminal-UI knobs.
- Hooks in general are documented to run "wherever Claude Code runs: sessions in the
 terminal, IDE extensions, the Desktop app, and Claude Code on the web all fire the
 same hook events" - headless `-p` is conspicuously not named in that list either way,
 which this report treats as undetermined rather than as a denial (the list reads like
 an enumeration of *interactive* surfaces, not an exhaustive one).

**This is not confirmed either way for Workshop's specific `--resume` pattern.** The
2026-08-12 measurement in `compact_thread`'s docstring tested the manual command only.
No test in the codebase (`backend/tests/test_chat_compact.py` or elsewhere) appears to
have run a resumed session long enough, with a large enough reconstructed context, to
observe whether Claude Code's own auto-compact fires inside it. Two things follow:

1. **Do not assume it happens.** Nothing in this research found a positive confirmation,
 and the CLI could equally well skip the check on the reconstruction path, or reset
 its window tracking on `--resume`, or a dozen other undocumented behaviors - this is
 exactly the kind of internal Claude Code doesn't publish (see the companion doc's Q1
 caveat).
2. **Do not assume it doesn't happen either**, and specifically do not build anything
 that would break silently if it does (e.g. code that assumes the CLI's resumed
 context always equals what Workshop last sent it, byte for byte). Section (c) below
 proposes a cheap, real way to observe this rather than guess.

---

## (b) The provider-adapter interface

### What other frameworks do, surveyed for this specific seam

- **Vercel AI SDK** is the closest precedent to what Workshop needs, and it's
 instructive that it does NOT ship a native cross-provider compaction primitive: it
 ships a per-step hook, `prepareStep(options)`, that runs before every model call and
 receives the messages that step is about to send. Compaction is user code inside that
 hook (check estimated tokens, call `pruneMessages` or your own summarizer, return the
 edited message list) - i.e. the SDK's abstraction boundary is "you get a checkpoint
 before every call and the current message list; what you do there is provider-specific
 application code," not "the SDK compacts for you." [github.com/vercel/ai issue #7515,
 ai-sdk.dev/cookbook/guides/agent-context-compaction]
- **aider** doesn't standardize compaction across providers either; its cross-provider
 abstraction (LiteLLM routing to 100+ providers) covers model calls generically, and its
 context-fitting logic (the repo map's tree-sitter-ranked, token-budgeted file summary)
 is a separate, provider-agnostic layer that runs *before* the message list is built at
 all, sidestepping the "compact an existing history" problem for code context by never
 putting the whole repo in history in the first place. Aider's answer to "how do
 different providers compact" is closer to "don't overfill the window to begin with."
 [aider.chat/docs/repomap.html]
- **LangChain/LlamaIndex** (from the companion research doc, Q3): compaction is a
 `Memory`/`ChatSummaryMemoryBuffer` abstraction the framework owns, parameterized by
 "the configured LLM" - provider-agnostic in the sense that any chat model object can
 fill that slot, but the summarization *strategy itself* (iteratively summarize
 everything past a token limit) is one fixed algorithm the framework runs regardless of
 provider, i.e. these frameworks never had to reconcile "provider A has a native
 compact feature, provider B doesn't" because none of the providers they target ship
 one.
- **OpenHands / OpenCode / Codex CLI** (companion doc's Q1 cross-tool table): each
 implements its own compaction internally, tuned to its own harness, with no shared
 interface across tools at all - there is no known prior art for a cross-agent-CLI
 compaction adapter, which is unsurprising since nothing today needs to run the *same*
 compaction UX against two different backing mechanisms for the *same* provider, which
 is Workshop's actual situation.

**Conclusion for design: there is no existing "compaction adapter interface" to copy
wholesale. The closest fit is the Vercel AI SDK's `prepareStep` shape - a narrow,
per-turn hook that receives the current message/context state and returns a
(possibly rewritten) one - generalized slightly so a provider can also report whether
it has a genuinely native mechanism behind it, which none of the surveyed frameworks
needed because none of them had one.**

### Proposed interface

A `CompactionAdapter` per provider ("claude-cli", "claude-api" later, "codex" later).
Python, matching Workshop's backend; method names chosen to read the same as the
existing `chat.py` operations (`compact_thread`, `clear_thread_context`) so the route
layer barely changes:

```python
class CompactionAdapter(Protocol):
 """One implementation per provider. The route layer (chat.py) calls only this
 interface; nothing above it branches on which provider is live."""

 def capabilities(self) -> CompactionCapabilities:
 """Static facts the caller needs before deciding what button to show:
 - native_manual: bool # can this adapter ask the provider itself to compact?
 - native_auto: bool # does the provider do this on its own, uncontrolled?
 - can_evict_tool_output: bool # surgical eviction short of full summarization?
 - preserves_cache_prefix: bool # does compacting cost a full cache rebuild?
 """

 async def compact(self, thread: ThreadSnapshot, *, hint: str = "") -> CompactionResult:
 """The user-facing 'Compact' action. `hint` is the optional focus string
 (mirrors Claude Code's own `/compact <instructions>`). Returns
 (folded_count, summary_text | None, new_cut_position, session_reset: bool,
 error: str). Must be safe to call when there's nothing to fold (returns
 folded=0, no error) and must be cancellable mid-flight the same way
 `compact_thread`'s `thread_ops` registration already works today - that
 cancellation contract is provider-agnostic and lives above this interface,
 not inside each adapter."""

 async def clear(self, thread: ThreadSnapshot) -> ClearResult:
 """The user-facing 'Clear' action: drop context, keep the transcript. Both
 adapters implement this the same way conceptually Workshop's CLI adapter
 already does (cut position advances to 'now', live session/prefix
 detaches) even though the CLI has no native /clear equivalent here either."""

 async def evict_tool_output(self, thread: ThreadSnapshot) -> EvictionResult:
 """Optional, cheap, non-summarizing eviction of old tool results. No-ops
 (returns evicted=0) on an adapter whose capabilities() says
 can_evict_tool_output=False - the CLI adapter today. This is the seam the
 API adapter fills with clear_tool_uses_20250919 later; the CLI adapter has
 no equivalent primitive to call, so it stays a no-op, not a fake."""

 def prepare_turn(self, thread: ThreadSnapshot, outgoing: TurnPrompt) -> TurnPrompt:
 """The Vercel-style per-step checkpoint: called once, right before a turn's
 prompt/messages are finalized and sent. Provider-specific placement work
 happens here - the API adapter sets cache_control breakpoints; the CLI
 adapter is currently a pass-through (there is nothing to place, the CLI
 owns its own cache internally) but the hook exists now so cache-breakpoint
 logic has somewhere to live when the API adapter is built, instead of
 getting bolted onto chat.py later."""
```

Design notes:

- **`capabilities()` exists specifically to make the fork in (a) a queryable fact
 instead of an `if provider == "cli"` scattered through the route layer.** Today it
 returns `native_manual=False` for the CLI adapter (the fork's answer) and will return
 `True` for the API adapter once built. If the open question about auto-compact firing
 inside `--resume`d sessions is ever resolved positively, it becomes
 `native_auto=True, native_manual=False` for the CLI adapter - a real, representable
 state this interface already has room for, rather than a boolean that can't express
 "sometimes happens, can't be triggered."
- **`compact`/`clear`/`evict_tool_output` map directly onto the three real primitives in
 play across both providers**: Workshop's own fold (works everywhere), the API's
 `compact_20260112` (API-only), and the API's `clear_tool_uses_20250919` (API-only,
 no CLI equivalent at all - not even Workshop's own fold does this today, see (c)).
 Splitting them into three methods rather than one `compact(mode=...)` call is
 deliberate: they have different cost profiles (one is an LLM call, one is a metadata
 edit) and a caller (a future auto-compaction scheduler, say) needs to reach for the
 cheap one without paying for the expensive one.
- **`prepare_turn` is the one method that does nothing useful today and everything
 useful later.** It's included now, as a no-op pass-through on the CLI adapter, purely
 so the API adapter's cache-breakpoint logic (isolate the system prompt behind its own
 `cache_control`, per the companion doc's Q2 finding) has a designated home from day
 one instead of getting improvised into `_build_prompt` when the API migration lands.

### Where this seam sits relative to existing code

`chat.py`'s `compact_thread` currently calls `_fold_summary`/`_land_fold` directly. The
seam is: those two functions, largely unchanged, become the CLI adapter's `compact()`
and the internals it calls; the route handler calls `adapter.compact(...)` instead. See
(e) for the exact diff shape.

---

## (c) Build now (real, CLI-only, non-stub) vs. build later (waits for the API backend)

### Build now

1. **Extract the existing fold into the adapter shape**, with no behavior change beyond
 what's listed below. `_fold_summary`, `_land_fold`, `_land_fold_when_quiet`,
 `_detach_live_session`, `_reset_live_session` move (or get wrapped) into a
 `ClaudeCLIAdapter` implementing the Protocol in (b). This is mechanical but is the
 thing that makes "build the API adapter later" an addition instead of a rewrite of
 `chat.py`.
2. **Fix the retention unit - this is the one piece of real, user-visible behavior
 change to build now.** `_COMPACT_KEEP_TAIL = 6` (`chat.py:1877`) counts MESSAGE
 RECORDS, which the companion research doc's Q5 establishes is the wrong unit for
 three independently-sourced reasons: message count says nothing about tokens (what
 the context meter, the window, and any future billing actually measure); a tool-heavy
 agentic turn can be one message record holding dozens of content blocks, so "keep the
 last 6" can silently keep either a trivial sliver or a huge multi-tool turn depending
 on shape; and Anthropic's own context-awareness feature treats token budget, not
 message count, as the unit the model itself reasons about. Concretely: change
 `_fold_summary` (`chat.py:1914`) to walk `tail` from the end, summing an approximate
 token count per message (Workshop already estimates tokens elsewhere - 
 `_context_estimate`/`len(prompt)/4` at `chat.py:2169` is the existing approximation;
 reuse it per-message rather than inventing a second estimator), and stop including
 messages once a configured token floor (propose ~3-4K tokens, tunable, covering "the
 last real exchange" rather than "the last 6 records") is reached, with a floor of at
 least one full turn (all messages between two user sends) so a fold never cuts a turn
 in half. This directly fixes the concrete failure mode Q5 names: a thread whose last
 6 records are a single 50-tool-call turn currently forces the fold to summarize
 nothing (fold_n could be 0 despite the thread being enormous) while a thread whose
 last 6 records are six one-line replies over-retains almost nothing useful.
3. **Put `capabilities()` on the record now, even with one implementation.** Costs
 almost nothing (a static dict) and is what stops a future "does the CLI adapter
 support X" question from becoming a code archaeology exercise - it's the answer to
 (a) made machine-readable.
4. **Pick up `get_context_usage` from the current control protocol** (found live during
 this research, absent from the list Workshop's own docstring cites) as a straight
 replacement for whatever `context_report.py`'s `/context`-shelling-out mechanism does
 today, if that read is currently done by invoking the CLI as a subprocess
 (`_side_query_cwd`/`cached_context_report` in `chat.py:2179` onward) rather than
 through the control protocol directly. This is a real, shippable improvement
 independent of the compaction fork: a structured control-protocol read instead of
 parsing CLI output, on CLI 2.1.143 or whatever version actually exposes it (verify the
 version this shipped in before relying on it; this research found it in the current
 docs but did not pin an exact version number).
5. **Instrument the open question in (a).** Add a `PreCompact`/`PostCompact` hook entry
 to the `--settings` JSON `_build_cmd` already constructs (`build_streaming.py:1908`,
 which already injects a `PreToolUse` hook for the subagent guard - the same mechanism,
 one more entry) that logs when either fires, with the `manual`/`auto` matcher value.
 This is cheap (one more hook, no new subprocess) and turns the open question in (a)
 from "we don't know" into "we'll know the next time it happens," which is the honest
 answer available today.

### Build later (waits for the API backend to exist)

1. **The `ClaudeAPIAdapter` itself** - obviously gated on the chat backend actually
 speaking the raw Messages API at all, which per the brief is a separate, larger
 migration than this doc's scope.
2. **`compact_20260112` and `clear_tool_uses_20250919` wiring** - the adapter's
 `compact()` and `evict_tool_output()` bodies. Straightforward once the API backend
 exists; the request shapes are documented (see (d)) and don't need Workshop-side
 invention the way the CLI's fold did.
3. **Cache-breakpoint placement in `prepare_turn()`** - needs the API backend's actual
 request-building code to exist first (there's no `cache_control` to place until
 there's a `messages.create` call to place it in).
4. **Anything in the "Option C: Hybrid with retrieval" tier of the companion research
 doc** - explicitly out of scope for "build now" per that doc's own conclusion, and
 doubly so here since it's API-path-only.

---

## (d) How the API adapter will work when built

Grounded in the companion doc's Q2 findings and the `claude-api` skill reference,
restated as the adapter's actual method bodies:

- **`compact()`** sends `client.beta.messages.create(..., betas=["compact-2026-01-12"],
 context_management={"edits": [{"type": "compact_20260112"}]})`. The critical
 implementation detail, easy to get wrong: the response's `compaction` content block
 must be appended back into the stored message list verbatim (`response.content`, not
 just extracted text) on every subsequent request, or the next turn silently loses the
 compaction state and re-sends full history. This is a real bug class the CLI adapter
 has no equivalent of (nothing round-trips through Workshop's own message store the
 same way) and needs its own test.
- **`evict_tool_output()`** sends `context_management={"edits": [{"type":
 "clear_tool_uses_20250919"}]}` on the ordinary (non-beta-`compact`) request, no LLM
 call, no summary text - this is metadata-only clearing of old tool results down to a
 configurable keep-last-N floor, and per the companion doc's Q2/Q4 reading this is the
 method to call routinely (every turn or every few turns) with `compact()` reserved for
 genuine long-horizon events. `exclude_tools` should protect anything Workshop decides
 must never be silently dropped (a natural candidate: results from
 `mcp__workshop__search`, since those are exactly the retrieval-layer calls the
 companion doc's Option C leans on).
- **`prepare_turn()`** places one `cache_control` breakpoint at the end of the frozen
 system-prompt block (persona + capability facts + knowledge index + custom
 instructions - everything in `_build_prompt`'s stable prefix,
 `chat.py:1439` onward) and a second, separate breakpoint at the end of the messages it
 is about to send, mirroring Anthropic's own documented compaction guidance verbatim:
 isolate the frozen prefix behind its own breakpoint so a compaction event's write
 never also re-writes the stable system prompt. Concretely this means the API adapter's
 request builder needs the same "stable prefix vs. volatile tail" split
 `_build_prompt` already has structurally (preamble/persona/knowledge/custom-prompt vs.
 summary+history+this-message) - it's an existing seam in the code, not a new one to
 invent.
- **Answering the operator's precise question - can content be evicted without a full
 cache rebuild - yes, with one caveat.** Per Anthropic's documented cache-invalidation
 hierarchy (`tools` -> `system` -> `messages`, restated from the companion doc's Q2):
 editing message content invalidates the messages cache tier and nothing above it,
 provided tools and the system prompt are otherwise byte-identical and physically
 precede the messages in the render order. So `clear_tool_uses_20250919` and
 `compact_20260112` both only cost a rewrite of the messages tier, never the system/tools
 tiers, as long as the API adapter never lets anything volatile (a timestamp, an
 interpolated live-state string) leak into the system prompt itself - which is exactly
 the discipline `_resumed_prompt`'s "LIVE CONTEXT" block already tries to maintain by
 appending rather than editing in place (`chat.py:2540` onward). The caveat: this is
 "no *system-prompt* rebuild," not "no cost at all" - the messages tier itself still
 gets rewritten at whatever position the edit lands, and a section-targeted edit deep
 in the middle of a long history still drags every message after it along for the
 rewrite (companion doc's 4b), so `evict_tool_output`'s oldest-first, keep-last-N
 behavior (never edits deep-middle content, always trims from the old end forward) is
 the cache-safe shape and should not be redesigned into arbitrary mid-history edits
 without redoing this cost analysis.

---

## (e) Build sequence: files and functions

1. `backend/services/` - new `compaction_adapter.py`: the `CompactionAdapter` Protocol,
 `CompactionCapabilities`/`CompactionResult`/`ClearResult`/`EvictionResult` dataclasses,
 and a `get_adapter(provider_kind: str) -> CompactionAdapter` factory. No behavior,
 pure interface + one registration point.
2. `backend/routes/chat.py` - wrap the existing fold logic as `ClaudeCLIAdapter`:
 - `_fold_summary` (line 1914) becomes (or is called by) `ClaudeCLIAdapter.compact`'s
 compute half; keep the compute/land split (`_fold_summary` vs `_land_fold`,
 lines 1914/1951) exactly as-is - that split is load-bearing for the
 cancel-mid-summarize behavior `cancel_thread_operation` (line 1731) depends on, and
 nothing about the adapter refactor should touch it.
 - `_land_clear`/`clear_thread_context` (line 1983 onward) becomes
 `ClaudeCLIAdapter.clear`.
 - `evict_tool_output` on `ClaudeCLIAdapter` returns `EvictionResult(evicted=0)`
 unconditionally - a real, documented no-op, not a stub (it correctly reports "this
 adapter has no such primitive," which is a true fact, not an unfinished one).
 - `prepare_turn` on `ClaudeCLIAdapter` returns its input unchanged.
 - `compact_thread` (line 1759) and `clear_thread_context` (line 1983) route handlers
 call `adapter.compact(...)`/`adapter.clear(...)` instead of the module-level
 functions directly; `thread_ops.begin`/`end`/cancellation stays in the route
 handler, not the adapter, since that's orchestration Workshop owns regardless of
 provider.
3. `backend/routes/chat.py:1877` - change `_COMPACT_KEEP_TAIL = 6` to the token-aware
 walk described in (c)#2. This is the one behavior change in this pass; ship it with a
 before/after measurement on a real thread the way the docstring already documents the
 2026-08-12 measurements, so the change is checkable the same way the existing bugs
 were.
4. `backend/services/build_streaming.py:1908` - add the `PreCompact`/`PostCompact` hook
 entries to the `--settings` JSON alongside the existing `PreToolUse` subagent-guard
 hook, logging `matcher` (`manual`/`auto`) to close the open question in (a).
5. `backend/services/context_report.py` (wherever `cached_context_report` currently
 shells out) - evaluate replacing the CLI-output-parsing path with a `get_context_usage`
 control-protocol call, if the Agent SDK/control-protocol path is reachable from
 Workshop's subprocess-based invocation (it may not be: Workshop drives the CLI as a
 subprocess over stream-json, not through the Agent SDK library directly, so this needs
 a quick spike to confirm the control request is reachable at all from that side before
 committing to it - flag this explicitly as unverified, not assumed reachable).
6. Tests: extend `backend/tests/test_chat_compact.py` (already the home of the
 2026-08-12 CLI-session-drop measurement) with a case for the new token-aware
 `_COMPACT_KEEP_TAIL` behavior - specifically a fixture thread whose last "6 records"
 is one huge tool-heavy turn, asserting the fold now still retains a bounded token
 budget rather than either summarizing nothing or summarizing mid-turn.

---

## (f) Risks and operator decisions

**Risks**

- The token-aware retention change in (c)#2 changes what gets folded on real threads;
 a bad token estimate (Workshop's existing `len(prompt)/4` approximation is coarse) could
 under- or over-retain relative to today's behavior. Ship with the before/after
 measurement called for in (e)#3 before trusting it broadly.
- The `PreCompact`/`PostCompact` hook instrumentation in (e)#4 could itself reveal that
 automatic compaction *is* firing inside resumed sessions, silently, today, contradicting
 the coasting assumption "this only happens when the operator wants it to." If it fires,
 Workshop's own cut/summary bookkeeping (`cleared_through`, `summary`) would be
 unaware of it - the CLI would have shrunk its own internal context out from under a
 session Workshop still believes holds full history, which is exactly the kind of
 drift the existing `context_tokens` meter bug (`_detach_live_session`'s docstring,
 `chat.py:1713`) was built to prevent for the *manual* case. If auto-compact turns out
 to be real, this needs a follow-up fix, not just a "we now know" - likely detaching
 the live session the same way `_detach_live_session` does today, on the `PostCompact`
 hook firing with `manual=auto`, so Workshop's bookkeeping stays honest about it.
- `get_context_usage` and the newer control-protocol list were found via documentation
 fetched today (2026-08-20) against Workshop's CLI 2.1.143; verify the running version
 actually exposes it before building against it - the docstring's existing list was
 itself pinned to a specific version and going stale is exactly how it under-reported
 what's available now.

**Decisions for the operator**

1. **Is the (c)#2 token-floor number (proposed ~3-4K tokens, minimum one full turn) the
 right default. It's a real product-feel knob** (too low: constant re-summarization
 cost and cache churn per the companion doc's economics; too high: the fold rarely
 triggers and long threads stay expensive) **and this report has no usage data to
 tune it from** - same caveat the companion doc raised for the 5-minute-vs-1-hour
 cache TTL choice.
2. **Is it worth building the (e)#4 hook instrumentation now, given the CLI/API
 migration timeline is unclear**, or should the open question in (a) about
 `--resume`-path auto-compaction stay open until the API path is closer and this
 research is revisited anyway? The instrumentation is cheap either way (one hook
 entry, no new subprocess), so the case for waiting is only "why learn the answer to a
 question that might not matter once the API path ships," not cost.
