<!-- author: self; chat: 1ebc303f366e46eb81719f4dc57e745e -->
**WITHDRAWN 2026-08-27. Both of this note's headline claims are false.** Kept as
a file only because its title is cited in other notes and in the plan docs; do
not use it as evidence. The settled version is
[[the-cli-does-auto-compact-on-p-resume]] plus
docs/platform-redesign/chat-context-and-compaction-2026-08-25.md section 1.

What it said and what is actually true, re-measured 2026-08-27 on the installed
CLI 2.1.143 with Workshop's own spawn flags:

- "Manual /compact is dead in -p" -- FALSE. A `/compact` sent as a turn over
  `--input-format stream-json` runs: trigger:manual, 27,088 -> 2,104 tokens in
  17.4s, followed by `<local-command-stdout>Compacted </local-command-stdout>`.
  It takes focus instructions as an argument. The 2026-08-12 test that founded
  this claim was a FALSE NEGATIVE: it read the transcript about two seconds
  after sending, and every real compaction on record takes 12-190 seconds. The
  local-command block it treated as proof of non-execution is what a working
  /compact records too.
- "Automatic compaction does not fire either" -- FALSE, and already corrected:
  29 trigger:auto boundaries across headless runs, at 126k-153k pre-tokens.
- "The env knobs have no observable effect" -- TRUE, but the probe behind it was
  invalid (it passed a 5,000-token window; the documented range is 100K-1M).
  Re-tested with in-range values and an extreme percentage: still inert in -p.
