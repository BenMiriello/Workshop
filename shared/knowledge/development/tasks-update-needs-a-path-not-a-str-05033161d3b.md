---
name: tasks-update-needs-a-path-not-a-str
description: tasks.list_tasks accepts a str root but tasks.update requires a Path, and the failure is a confusing AttributeError deep in _locked
metadata:
  type: reference
---

`backend/services/tasks.py` is inconsistent about its `root` argument.

- `tasks.list_tasks(root)` works with a plain `str`.
- `tasks.update(root, ...)` requires a `pathlib.Path`. Passing a `str` raises
  `AttributeError: 'str' object has no attribute 'mkdir'` from `_locked` at
  tasks.py:356, four frames down, naming neither the argument nor the caller.

So the natural pattern of reading with a str and then writing with the same
variable crashes, and the traceback points at locking rather than at the type.

Always pass `Path(...)` to any `tasks` write. And verify a ticket write by
READING IT BACK, not by the absence of an exception: a sibling failure mode in
this CLI is a write that silently no-ops and returns `{}`.
