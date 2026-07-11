# {{title}}

> {{purpose}}

## Goal

A Letta tool package: Python functions the autonomous agents can call. Tools live
in `tools/` as plain functions with Google-style docstrings; `tools/__init__.py`
lists them in `TOOL_REGISTRY`; `register.py` upserts them into Letta and attaches
them to the named agents.

## Constraints

- Each tool function keeps a Google-format docstring (`Args:` + `Returns:`) or
  Letta registers it with no arguments.
- Tools should be self-contained (import inside the function) so they run in
  Letta's isolated sandbox.
- `TOOL_REGISTRY` names the agent handles a tool attaches to. Use real agent
  names (e.g. `workshop-overseer`).
- Registration is idempotent; safe to re-run.

## Open questions

- Which agents should own these tools?
- Do any tools need host resources the Letta sandbox can't reach directly (if so,
  they need a host-side proxy, like the Workshop's sandbox_wrappers)?
- What is the real work {{purpose}} implies beyond the example tool?

## Register

    LETTA_URL=http://127.0.0.1:8283 python register.py
