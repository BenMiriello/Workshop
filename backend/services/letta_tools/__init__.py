"""Tool implementations, formerly bound to Letta.

Letta was retired in the 2026-07-15 Stage 9 teardown and fully decommissioned
2026-08-16 (memory-consolidation-2026-08-16.md, decision D8): the health probe,
env vars and port reservation that pointed at it are gone, and so is the
tool-registration registry that used to exist here (it targeted Letta's
`tools.upsert_from_function`, which nothing calls any more).

The functions themselves are ordinary code, unrelated to Letta as a runtime.
They are reached today through the dispatch table in `routes/tools.py`
(`POST /tools/{name}`), which imports `workshop`, `local`, `cloud`, `notify`
and `health` directly — those five submodules are live and unaffected by this
file's own history.
"""
from __future__ import annotations
