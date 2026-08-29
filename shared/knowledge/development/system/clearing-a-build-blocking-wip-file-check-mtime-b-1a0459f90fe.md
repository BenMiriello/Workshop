<!-- author: self; chat: 05496adf046447b7bef13a3cf64ff81f -->
`apply.sh` refuses to build the origin when ANY uncommitted file sits under `frontend/src`, `packages/ui`, or the backend. On a shared checkout this means one agent's stray file silently freezes delivery for everyone, and the operator keeps seeing old code.

When you hit this, do not just leave it, and do not blanket-commit it either. Check the file's mtime first:

- **Minutes or hours old = abandoned.** Read the diff, run the gates (typecheck, design, tests), and commit it as its own isolated commit that says in the message it is another session's finished WIP. This is what unblocks the build.
- **Seconds old = an agent is mid-write.** Leave it. Committing a partially-written module, or restarting the backend onto one, ships a broken state. The staleness resolves itself when that agent commits.

Worked example 2026-08-27: `Birds.tsx` + `birds/queries.ts` (hours old, finished, committed, bundle rebuilt) and `backend/routes/chat.py` (docstring-only, committed), versus `backend/services/compaction_adapter.py` (written 1 second earlier, correctly left alone).

Related: the frontend bundle and the backend are gated separately, so a backend blocker does not stop you from getting a UI change onto the operator's origin.