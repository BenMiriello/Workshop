<!-- author: self; chat: 1a6cf9dda32f467295f46c97594f4e3c -->
Built 2026-08-20. Lets a chat model spawn a no-ticket background agent that reports back and continues the chat.

Flow:
- Model calls MCP tool `spawn_background(prompt, label)` on the workshop server (backend/mcp/workshop_query_server.py). Tool is offered ONLY when WORKSHOP_QUERY_THREAD is bound (a real chat), gated in _tool_list().
- The thread id reaches the MCP subprocess via app_registry.set_current_query_thread(), pinned at build_streaming's turn-start seam beside owner/scope, surfaced as env WORKSHOP_QUERY_THREAD in app_registry._platform_mcp_servers.
- _run_spawn reads WORKSHOP_QUERY_THREAD/OWNER/SCOPE env and calls services/background_runs.launch(thread, owner, scope, prompt, label).
- launch(): jobs.start(kind='background', chat_id=thread, binding={label,scope}), writes prompt to .runtime/background/<job>/prompt.txt, Popens detached `python -m services.background_runs run ...`, records pid.
- runner run(): _run_claude() invokes `claude -p <prompt> --output-format json --max-turns 40 --mcp-config <workshop>` (auth via ~/.claude credential fallback, like ralph), parses .result/.usage; _deliver() posts an autonomous assistant message meta kind='background_result' and calls chat_drain.request_continuation(); jobs.finish().
- Continuation loop (already live) drains the seed and starts the chat's next turn.

Driver-agnostic except _run_claude (the one leg that shells to claude -p; on the API path it calls the API instead). Verified live: a real claude -p runner turn posted its result and queued the continuation. Bound-thread gating + launch + delivery unit-tested in tests/test_background_runs.py. spawn tool is offered to real chats only after the backend serves the thread pin (restart of 2026-08-20 ~13:16).