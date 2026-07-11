# Agent rules: {{title}}

- Read `PROJECT.md` first every turn.
- Update `TODO.md` as work progresses; check items off when done.
- Edit only within this directory.
- Every tool function needs a Google-style docstring (`Args:` + `Returns:`), or
  Letta registers it with no arguments. Keep tools self-contained: import inside
  the function so they run in Letta's sandbox.
- When you add a tool, import it in `tools/__init__.py` and add a `TOOL_REGISTRY`
  entry with the real agent handles it should attach to.
- No stubs (TODO/FIXME/NotImplementedError/pass # stub/...). Mark `[~] BLOCKED`
  if a task is too big to finish in one tick.
