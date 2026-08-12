<!-- author: self -->
Files attached to a Workshop chat are delivered as native Anthropic content blocks (`image` / `document` / `text`) over the Claude Code CLI's `--input-format stream-json` stdin, NOT as filesystem paths for the agent to `Read`.

**Why paths do not work.** Only build providers get the full toolset. A data/assistant provider's surface is `provider.allowed_tools + WEB_TOOLS + SUBAGENT_TOOL` (`chat_context.effective_allowed_tools`), which does not include `Read`. A path in the prompt would therefore work in build chats and silently fail in every assistant chat.

**Verified against the CLI on 2026-08-11** (probe: pipe a stream-json user message on stdin, keep stdin open with a trailing sleep or the CLI exits before answering):
- a native `image` block is replayed and genuinely read (a solid `#FF8C00` PNG came back "Orange", unguessable from the prompt)
- a `document` block (base64 PDF) is read with no tool call (text present only inside the PDF came back verbatim)
- text files are inlined as a fenced `text` block instead, which needs no tool either

**Seams.** `services/chat_attachments.content_blocks()` builds the blocks; `build_streaming._user_message_line(text, blocks)` frames them; blocks go BEFORE the operator's text. Blocks are built at DELIVERY, not at `start_turn`, so base64 never enters the turn journal (`meta["attachments"]` holds refs only). An image block carries no filename, so `chat_attachments.describe()` appends an "Attached: a.png, b.md" line to the prompt only, never to the stored message.

**Storage.** `.runtime/users/<owner>/files/`, content-addressed at `blobs/<sha[:2]>/<sha>` with an append-only `index.jsonl`. One sha can back several attachment rows, so deleting a row only removes the blob when no other row references it.