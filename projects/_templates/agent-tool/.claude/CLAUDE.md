# {{title}}: house rules

A Letta tool package inside the Workshop.

- Tools live in `tools/` as plain functions with Google-style docstrings
  (`Args:` + `Returns:`). Letta parses the docstring into the tool schema, so the
  format is load-bearing, not cosmetic.
- Keep tools self-contained (import inside the function) so they run in Letta's
  isolated sandbox. A tool that needs host files or host localhost needs a
  host-side proxy (see the Workshop's `services/letta_tools/sandbox_wrappers.py`).
- `tools/__init__.py` `TOOL_REGISTRY` maps tool name -> (function, [agent names]).
- `register.py` upserts + attaches; it is idempotent, safe to re-run.
- No stubs. Finish the tool or mark it `[~] BLOCKED` in TODO.md.
