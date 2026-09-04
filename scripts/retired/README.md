# Retired gates

A gate that was wired into `reviewer.sh` and is not any more. Kept rather than
deleted for one reason: each of these was believed for a while, and the record
of HOW it was wrong is the useful part.

## `check_pluggable_apps.sh` — retired 2026-09-04

Inspected five files (`main.py`, `llm_router.py`, `chat_context.py`,
`app_registry.py`, `Apps.tsx`) and printed `pluggable-apps: clean`. It did not
look at `frontend/src/pages/apps/` (61 files), `backend/services/`,
`backend/routes/`, `backend/mcp/`, or `agents/apps/`, so it reported clean for
five weeks while 143 tracked files of one operator's apps sat in the tree, and
that green light is a large part of why app separation was believed done on
2026-07-12 and was not done until 2026-09-04.

**The lesson is not "it was too narrow".** It is that a gate which cannot see
the violation is worse than no gate, because it converts an unsolved problem
into a solved-looking one. Two comments in `reviewer.sh` already cited it BY
NAME as the reason they run unconditionally — the file was distrusted in
writing, by name, and left wired in anyway.

Replaced by two checks that cannot be scoped away:
`scripts/check_no_app_names.py` (unconditional, every tracked file) and
`scripts/check_fresh_clone.sh` (clones the repo and asks what came out).
