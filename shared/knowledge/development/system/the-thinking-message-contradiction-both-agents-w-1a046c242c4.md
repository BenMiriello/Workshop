<!-- author: self; chat: dfd97edc0a0d40218974b8f152a2bf1f -->
Measured 2026-08-27 across all 148 stored threads (632 replies) and a live 2276-line `stream.jsonl`.

**Why two agents gave opposite answers about thinking messages, and neither lied.**

In the RAW CLI stream, thinking blocks DO appear structurally: the live turn `4b8dcd5b` carried 4 `content_block:thinking`, 24 `thinking_delta` and 4 `signature_delta` events. So an agent grepping the raw stream correctly reports "there ARE thinking messages."

In STORED thread data there is **1 thinking segment across 632 replies (0.16%)**. So an agent querying the store correctly reports "there are NO thinking messages."

The reconciling fact: **the thinking blocks arrive with EMPTY text.** Every one of the 4 thinking blocks in that live turn assembled to `len=0`. `_seg_thinking` in `build_streaming.py` opens with `if not s: return`, so an empty thinking block never becomes a segment. The blocks are real; their content is not.

Consequence, unchanged: the **"Thought fold" (26ae0a2, 2026-08-23) is inert.** It hides a channel that carries nothing. That is why hiding thinking "did nothing" -- confirmed, not assumed.

Do not reopen this by grepping one layer. The question "are there thinking messages" is underspecified; ask "are there thinking SEGMENTS in stored replies" (no, 0.16%) or "are there thinking BLOCKS on the wire" (yes, but empty).