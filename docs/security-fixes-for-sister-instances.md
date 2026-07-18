# Applying the 2026-07-18 security fixes to a sister Workshop instance

There is more than one Workshop instance on the shared host (e.g. the `simonsays` sister instance).
The 2026-07-18 audit was performed against the `clawd-a` instance, but most findings are properties
of the **codebase** and the **per-instance host config**, so a sister instance needs its own pass.
This doc says exactly what travels automatically vs. what each instance must apply by hand.

## How the fixes reach another instance

- **Code + these docs travel via git.** The repo remote is `github.com/BenMiriello/Workshop.git`. If
  the sister instance is a clone of the same repo, a normal `git pull` (after the clawd-a commits are
  pushed to the remote) brings every code fix AND every report/runbook/posture doc listed below.
  NOTE: as of writing, the clawd-a fixes are committed locally but **not yet pushed** — the operator
  pushes when ready.
- **Per-user config does NOT travel via git** — it lives outside the repo (`~/.claude/…`,
  `~/.config/systemd/user/…`, file permissions). Each instance's operator must apply Part B itself.
- **No shared drop location:** on this host, `clawd-a` cannot write into another user's home (`0700`)
  or a shared dir (`/opt/shared`, `/opt/clawd-house` are not clawd-a-writable); only `/tmp` is world-
  writable and that is not a durable channel. So the intended handoff is **git** (or the operator
  copying files manually).

## Part A — comes free with `git pull` (code changes)

Once pulled, these are active after the instance rebuilds/restarts its backend:
- `backend/workshop_public_app.py` — public-edge blocklist fix (`/auto-pr`) + `/docs` gating.
- `backend/deps_auth.py` — `require_internal_caller` narrowed to loopback + Docker bridge.
- `backend/routes/auth.py` — registration fail-closed default.
- `backend/main.py` — `/docs`/`/openapi.json` off unless `WORKSHOP_ENABLE_DOCS=1`.
- `backend/services/letta_tools/cloud.py` — recursive-claude clamp + depth guard.
- `backend/services/compose_gen.py` — `no-new-privileges` on generated app containers.
- `shared/native-bridge.ts`, `mobile/*` — WebView bridge write-only + origin-lock + debug gating.
- `docs/security-*.md` — the reports, posture, and runbook.

After pulling: `cd frontend && npm run build` (if the instance serves a prebuilt bundle), restart the
backend, and confirm `WORKSHOP_REGISTRATION_OPEN` is set the way that instance wants (default is now
closed).

## Part B — per-instance config each operator MUST apply by hand

These are NOT in the repo; the sister instance has its own copies:

1. **Bind the backend to loopback.** Edit that instance's backend systemd unit / launch command:
   `--host 0.0.0.0` → `--host 127.0.0.1`. (The audit found the sister instance's `:8000`/`:8010`/
   `:8012` on `0.0.0.0` with `/docs` open — bind them loopback and reach them via Tailscale Serve.)
2. **Egress guard WebFetch coverage.** If the instance uses the shared `~/.claude/hooks/egress-guard.py`
   it already has it; if it has its own copy, port the `webfetch_exfil_risk` addition and add a
   `WebFetch` matcher to its `~/.claude/settings.json` PreToolUse hooks.
3. **File permissions.** `chmod 600` its own `.runtime/auth.db` and any `.env`; `chmod -R go=` its
   `~/.claude/file-history`.
4. **The agent boundary (docker).** Same root-cause as clawd-a: if that user is in the `docker` group
   and runs autonomous agents, the same host-root exposure applies — follow Part 1 of
   `security-remediation-runbook.md` (rootless docker + drop the group) for that user.
5. **Its own network exposures.** Run the runbook's Part 2 checks against that instance's services
   (its Letta `:8284` answered `/v1` unauthenticated; any of its ports on `0.0.0.0`).

## Part C — recommended

Have the sister instance run its OWN audit pass (the method is in the dated `security-audit-*.md`
reports): the code fixes close the shared-code issues, but host config, ports, secrets, and the
docker boundary are per-user and must be verified on that instance directly.
