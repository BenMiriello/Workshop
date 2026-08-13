# Workshop autonomous-agent learnings

This file is the Reflexion-pattern persistent reflection buffer. The
work-reviewer subagent and work_verifier.sh append entries here when they
catch a stub, a faulty `[x]`, or any other failure pattern. The SessionStart
hook injects the contents at the top of every new worker tick's context, so
the next worker sees the failure pattern and doesn't repeat it.

Append-only. Keep entries one-line and pattern-focused, not incident-focused.
Order: most recent at top.

## Failure patterns the worker has hit before

- DO NOT write UI without following the Workshop Design System. ALL frontend work obeys `frontend/CLAUDE.md`: use the `src/ui` primitives (Button, Card, StatusPill, Field, Modal, Table, StateBlock, …), no inline hex colors (tokens/primitives only), lucide icons never emoji, the five status kinds (ok/running/stopped/warn/danger), the Edit-mode model for config/money writes, and ConfirmDialog for destructive actions. Run `bash scripts/check_design.sh --added HEAD` before claiming done — `reviewer.sh` fails on `design_violations` (new inline hex/emoji/not-allowed in changed .tsx). Design new screens this way from the start; don't defer systematization.
- DO NOT mark `[x]` when the task description still has "needs operator", "still needs", "deferred", or "pending until" — those are by definition not done. If you can't finish, the task is `[~] BLOCKED: <specific reason>`, not `[x]`.
- DO NOT write `# TODO: implement this` / `// TODO: ...` / `pass  # stub` / `raise NotImplementedError()` / `...` as a function body. If a function is too complex for the current tick, mark the TASK `[~] BLOCKED: too-complex-for-single-tick — break into N sub-tasks` and propose the sub-tasks under `## Proposed`. Stub bodies in committed code are forbidden.
- DO NOT write "Here you would do X" / "User can extend this by Y" / "Left as exercise" in code comments. These are placeholder prose pretending to be implementation. If the task requires X to actually work, do X — or split the task.
- DO NOT claim "verified end-to-end" / "tested" without an actual curl, run, or test invocation in the same tick. If verification was skipped, say "shipped, verification deferred to next tick" — don't claim what you didn't do.
- DO NOT pick up a new task before reviewer.sh approves the current one. The completion gate is mandatory; bypassing it is what created the "marked done but caveats hiding" failure mode.
- DO NOT leave a backend route added to a file without wiring it in `backend/main.py` (`app.include_router(...)`). Untested routes that 404 because they aren't wired are not "done".
- DO NOT add a frontend page without adding it to `App.tsx` routes AND a nav link. Orphaned pages that never render are not "done".
- DO NOT modify a file you didn't read first this tick. The "modified since read" tooling errors usually mean a parallel writer (cron + supervisor + you) is racing.
- DO NOT claim a service restart succeeded if you used `systemctl` and got classifier-blocked. Use `nohup` for long-running services. State the actual outcome.
- Ralph: worker finished cleanly but forgot to emit <promise>COMPLETE</promise> sentinel — always end the final iteration with that exact string on its own line — 2026-05-23T01:23Z [src:ralph sha:ed821201c9d1]
- Ralph rejected: py_typecheck_fail — 2026-05-23T02:00Z [src:ralph sha:c8aa7fce0032]
- Ralph rejected: ts_typecheck_fail — 2026-05-23T02:04Z [src:ralph sha:556e56b10480]
- Ralph: worker completed the task but left its changes UNCOMMITTED — the loop auto-committed them. Always commit before emitting the sentinel (prompt rule 5); the completion gate reads the git log, not the working tree. [src:ralph sha:2d5f46b89a07]
