<!-- author: self; chat: 05496adf046447b7bef13a3cf64ff81f -->
Measured 2026-08-26 across real CLI session files under ~/.claude/projects/-workspace/*.jsonl: 29 genuine trigger:auto compact_boundary events across 28 headless (entrypoint=sdk-cli, cwd=/workspace) sessions, firing at preTokens ~126k-153k and collapsing to postTokens ~6k-20k, dated 2026-08-22..2026-08-26. So compaction_adapter.capabilities() native_auto=True is CORRECT.

This CONTRADICTS shared/knowledge/development/headless-claude-p-has-no-native-compaction-manua-*.md, whose probe drove context only to ~40k using CLAUDE_CODE_AUTO_COMPACT_WINDOW / CLAUDE_AUTOCOMPACT_PCT_OVERRIDE (which have no effect in -p) and so never reached the real ~126k threshold. The note's narrow facts (env knobs dead, manual /compact dead in -p) hold; its generalization 'automatic compaction does NOT fire' is wrong.

Retention map (evidence-backed): image/document/text all RETAINED across a plain --resume turn (the CLI replays its on-disk transcript; verified with a 434KB jpeg present across 40+ later turns). Across ANY compaction (Workshop fold OR the CLI's native auto one) images and documents are DROPPED; only text survives (tail verbatim + summarized head). Primary defect: the fresh-session rebuild (routes/chat.py _assemble_full_prompt/_build_prompt) is text-only and only attaches the current turn's meta['attachments'] (build_streaming.py ~2485), so no prior image/document is re-emitted. The shipped resume self-heal narrowing only reduces how often that rebuild is entered. See [[uploaded-images-visible-one-turn]].
**Extended 2026-08-27 (re-measured on the installed 2.1.143, Workshop's own spawn flags).**
Auto-compaction confirmed independently: re-scanning all 5,329 session transcripts on
this box gives 45 compact_boundary records, 29 trigger:auto, each attributed to the run
it happened in (entrypoint is recorded per record, so a file resumed by a second run
cannot be misread as one session). All 29 are sdk-cli, 126,003-153,059 pre-tokens.

MANUAL compaction also works in -p, which every prior note and doc denied: a /compact
sent as a turn over --input-format stream-json ran (trigger:manual, 27,088 -> 2,104,
17.4s, `<local-command-stdout>Compacted </local-command-stdout>`), and it accepts focus
instructions as `<command-args>`. The 2026-08-12 "it parses to an unexecuted
local-command block" finding was a false negative from reading the transcript ~2s in;
real compactions take 12-190s. Corroborating: three trigger:manual boundaries fired in
sdk-cli runs on 2026-08-12 itself. So compaction_adapter.capabilities() is wrong in the
other direction now: native_manual should be True.

The env knobs are inert, but the probe that "proved" it was invalid (5,000-token window
against a documented 100K-1M range). Re-tested at window=100,000 with pct=25 and pct=1:
still zero boundaries. `--autocompact` is not a flag on this build; `/autocompact` as a
turn answers "isn't available in this environment", which is how the CLI refuses a
command it does not support -- and therefore evidence that /compact is supported.

Images: ~200 tokens each, they DO survive plain resumed turns (verified by asking about
a shape and colour never previously discussed), and they are gone after a compaction
(IMAGE NOT VISIBLE). Loss is at compaction and at the text-only fresh-session rebuild,
not at the turn boundary.
