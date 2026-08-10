**STATUS: acted on, 2026-08-10. This is reference now, not an open finding.**
The fix shipped: `--permission-prompt-tool stdio` is coupled with the resolved
mode in the argv at `backend/services/build_streaming.py:1080-1081`, pinned by
`backend/tests/test_permission_channel.py`, and the mode is plumbed through
`backend/routes/chat.py` `_resolve_permission_mode` and
`frontend/src/components/chat/permissionModes.ts`. Nothing hardcodes
`bypassPermissions` any more; the default is `auto`
(`backend/services/model_caps.py:88`). Keep this note for the CLI behaviour it
documents, which is undocumented upstream and still true.

Measured against `claude 2.1.143` on 2026-08-09 while designing per-chat effort/mode (`docs/chat-effort-and-mode-2026-08-09.md`).

**Passing `--permission-mode default` (or acceptEdits/plan) in headless `-p` mode WITHOUT `--permission-prompt-tool stdio` silently auto-denies every gated tool call.** The tool_result reads "Claude requested permissions to write to X, but you haven't granted it yet", the turn still ends `subtype: success`, and the `result` event carries a `permission_denials` array. Nothing errors. This is why the backend hardcoded `bypassPermissions` at `build_streaming.py:781` and `llm_router.py:472` until 2026-08-10 (see the status banner above; those line numbers are pre-fix).

**With `--permission-prompt-tool stdio`** (undocumented in `claude --help`, but functional) plus `--input-format stream-json` and an `initialize` control_request sent first, the CLI emits:

```json
{"type":"control_request","request_id":"...","request":{"subtype":"can_use_tool","tool_name":"Write","display_name":"Write","input":{...},"description":"hello.txt","permission_suggestions":[{"type":"setMode","mode":"acceptEdits","destination":"session"}],"tool_use_id":"toolu_..."}}
```

answered on stdin with `{"type":"control_response","response":{"subtype":"success","request_id":"...","response":{"behavior":"allow","updatedInput":{...}}}}`. `{"behavior":"deny","message":"..."}` also works and the model reads the message.

Also verified: `--effort` is validated against one fixed list (low/medium/high/xhigh/max) for EVERY model, including haiku-4-5. The CLI does no per-model gating, so meaningfulness per model is ours to table. And `plan` mode is enforced CLI-side (it refused a direct write), but the model does not reliably call `ExitPlanMode` on trivial prompts.