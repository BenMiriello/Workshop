# Security invariants — clawd-a financial / MCP setup

These are the rules the nightly check (`~/clawdbot/security/audit.sh`) enforces, and
the *patterns* behind them so we don't re-introduce the same class of bug. This is
the "lock in the pattern" companion to the 2026-06-02 audit. When you add a new
credential, MCP, or web route, re-read this and add a matching assertion to audit.sh.

## The threat model in one sentence
masini is an autonomous agent that holds **production** bank-read credentials, ingests
**untrusted content** (web, email, Discord, attachments), and can **send data out** —
so every secret it can reach and every endpoint that can drive it is part of the
financial attack surface. (This is the "lethal trifecta"; see audit report sources.)

## Invariants (one fact each)

1. **Secrets live in 0600 files under `~/.config/clawdbot/`, never inline in JSON config.**
   Pattern that bit us: the Google OAuth `client_secret` was inlined in `~/.claude.json`
   (mode 644) and got copied verbatim into ~60 mode-644 `file-history`/`tool-results`
   snapshots. *Rule:* MCP secrets go in an env file the server `source`s; the launcher
   reads the file, not the JSON. Mirror `plaid-auth.env`.

2. **Anything Claude Code persists (`file-history/`, `projects/`, `backups/`, `sessions/`)
   must not be group/other-readable.** It silently snapshots whatever files the agent
   touches — including token files. *Rule:* `chmod -R go-rwx` those trees; the audit
   greps them for real token tails.

3. **Plaid `access_token` never appears in MCP tool output.** The custom server returns
   `item_id` + institution only. *Rule:* any new tool that touches a token passes it to
   Plaid and drops it; never into `content[].text`.

4. **Plaid product scope stays minimal** (Transactions + Investments; no Auth/Identity)
   so a compromise can't pull account/routing numbers.

5. **Production tokens are rotatable on demand.** `node onboard.js rotate <item_id>`
   calls `/item/access_token/invalidate`. *Rule:* on any suspected disk/backup leak,
   rotate first, investigate second. Old token dies immediately; no bank re-auth.

6. **`clawd-a` is NOT in the `docker` group.** docker.sock = root = reads every other
   tenant's home and every credential file regardless of 0600. The box's whole isolation
   model assumes per-user 700 homes; docker-group membership nullifies it. *Rule:* clawd-a
   reaches letta/ollama over **HTTP only** (`127.0.0.1:8283`, `:11434`); it never needs
   the socket. Container lifecycle is owned by benmiriello or a narrow sudo rule.

7. **A host firewall is the gate, not app-layer logins.** Single-user box → don't bolt
   per-user auth onto personal dashboards (that's the cruft we avoid). Instead: ufw
   default-deny inbound, allow loopback + `tailscale0` + ssh-from-LAN. Services may bind
   `0.0.0.0`; the firewall makes that effectively tailnet+localhost.

8. **The Workshop backend (`:8000`) serves `/finance/*` (live Plaid data) and
   `/tools/cloud_run_*` (arbitrary agent execution).** It's localhost-bound but reachable
   by *every Docker container* via `host.docker.internal:8000`, and CORS is not an access
   control. *Rule:* these routers require a shared-secret bearer dependency, OR the docker
   bridge is firewalled off `:8000` except letta's subnet. The audit greps for the auth
   dependency and fails if it's removed.

9. **No secrets in git.** `.gitignore` covers `.env`/`.credentials.json`; audit greps
   `clawdbot` history for token tails.

## When you add something new
- New MCP with a secret → env file (0600), add a `require_mode` line to audit.sh §1.
- New web route touching finance or agent control → add auth, add a grep guard to §8.
- New service on `0.0.0.0` → it's covered by the firewall (§7); note it so we know it's intentional.
