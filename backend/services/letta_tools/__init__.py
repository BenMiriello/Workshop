"""Autonomous-agent tool bindings (Letta era; retained as reference registry).

These Python functions were registered as Letta tools at FastAPI startup via
the supervisor's `register_tools()` call. Letta was retired in the 2026-07-15
Stage 9 teardown, so the registry below is no longer walked by any live code
path (verified by grep: only `routes/tools.py` mentions it as a "keep in sync"
comment). The functions themselves are still reachable via the dispatch table
in `routes/tools.py`, which is how the mediated tool-call surface actually
works today.

Kept for two reasons:
- documents which tools each role-carrying agent SHOULD have access to when
  the assistant-loop runtime is genuinely built (dispatch_triggers.sh:493);
- points would-be readers at the currently-live path.

Design rules (per operator guidance):
  - Tools that talk to local resources (TODO.md, file system, notify queue)
    are fast and synchronous. They run inside Letta's sandbox per-call.
  - Tools that talk to cloud Claude (`run_claude_code`) are slow (~30s-5min)
    and BLOCK the Letta /messages turn while running. Caller (supervisor) sets
    a generous timeout on the parent /messages call.
  - Tools default to redaction-on for any cloud escalation. Caller passes
    `redact=False` to bypass (rare — only when literal PII values are needed
    by Claude to take a real-world action).

Tool registration uses Letta's `tools.upsert_from_function(func=...)` which
parses Google-style docstrings into the JSON schema the agent sees. Each
docstring MUST follow Google format exactly (`Args:` block with type hints,
`Returns:` block) or registration will silently produce a tool with no args.

The functions below are the SOURCE for tool registration. They also run as
plain Python if imported directly — useful for testing.
"""
from . import workshop, cloud, local, notify, health, sandbox_wrappers as sb


# Module-level list of all functions to register with Letta. Each entry maps
# tool_name -> (callable, [agent_handles_to_attach_to]).
#
# IMPORTANT: register the SANDBOX-WRAPPER versions, not the host impls. The
# wrappers are thin HTTP proxies that hit /tools/{name} on the host backend.
# The host backend (routes/tools.py) dispatches to the real impls in
# workshop/local/cloud/notify/health. This is the only way Letta tools can
# reach host files + host localhost (the Letta sandbox is isolated).
TOOL_REGISTRY = {
    # workshop — TODO + file ops
    "wkshp_read_todo":        (sb.wkshp_read_todo,        ["masini"]),
    "wkshp_propose_task":     (sb.wkshp_propose_task,     ["masini", "plaid-watcher", "email-triager"]),
    "wkshp_write_log":        (sb.wkshp_write_log,        ["masini", "plaid-watcher", "email-triager"]),
    "wkshp_mark_blocked":     (sb.wkshp_mark_blocked,     ["masini"]),
    # local cognition — Ollama-backed quick decisions
    "local_classify":         (sb.local_classify,         ["masini", "plaid-watcher", "email-triager"]),
    "local_route_decision":   (sb.local_route_decision,   ["masini"]),
    "local_summarize_short":  (sb.local_summarize_short,  ["masini", "plaid-watcher", "email-triager"]),
    # cloud escalation — claude -p with optional redaction
    "cloud_run_claude":       (sb.cloud_run_claude,       ["masini"]),
    "cloud_run_ralph":        (sb.cloud_run_ralph,        ["masini"]),
    # notification
    "notify_operator":        (sb.notify_operator,        ["masini", "plaid-watcher", "email-triager"]),
    # health
    "system_health":          (sb.system_health,          ["masini"]),
}
