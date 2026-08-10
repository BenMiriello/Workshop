#!/usr/bin/env bash
# Nightly security-regression check for this instance's financial / MCP setup.
#
# This is NOT multi-user "defensive cruft" — it is a drift detector. It asserts
# the security INVARIANTS established in the 2026-06-02 audit and screams if any
# of them silently regress (a chmod that got reset, a secret that leaked into a
# world-readable file, an auth dependency that got deleted, a firewall that got
# turned off). One fact per check. Exit 0 = all green, exit 1 = something drifted.
#
# Run nightly from cron; see docs/security-audit-2026-07-18.md.
# Run by hand:  bash ~/workshop/scripts/security_audit.sh
#
# Pairs with the invariants reference in the same document.
#
# Audits the host this install runs on: the service user, its config directory,
# the bound ports and the repo history. Every subject is resolved at runtime, so
# the same script is meaningful on any deployment.

set -uo pipefail
WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HOME_DIR="$HOME"
SVC_USER="$(id -un)"
CFG="${WORKSHOP_CONFIG_DIR:-$HOME_DIR/.config/workshop}"
FAILS=0
WARNS=0

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n     ↳ fix: %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }
warn() { printf '  \033[33mWARN\033[0m %s\n     ↳ %s\n' "$1" "$2"; WARNS=$((WARNS+1)); }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

# mode_is <file> <expected-octal>  -> pass/fail that a file is exactly that mode
require_mode() {
  local f="$1" want="$2" label="$3"
  if [ ! -e "$f" ]; then warn "$label: $f absent" "nothing to check, but expected to exist"; return; fi
  local m; m=$(stat -c '%a' "$f" 2>/dev/null)
  if [ "$m" = "$want" ]; then pass "$label mode $m"; else
    fail "$label mode is $m, expected $want ($f)" "chmod $want $f"; fi
}

hdr "1. Credential file permissions (must be 0600)"
require_mode "$CFG/plaid-auth.env"     600 "plaid-auth.env"
require_mode "$CFG/plaid-tokens.json"  600 "plaid-tokens.json"
require_mode "$CFG/google-auth.env"    600 "google-auth.env"
require_mode "$CFG/auth.env"           600 "auth.env"
require_mode "$HOME_DIR/.config/google-docs-mcp/token.json" 600 "google-docs token.json"
require_mode "$HOME_DIR/.claude/channels/discord/.env"      600 "discord .env"
require_mode "$HOME_DIR/.claude/channels/discord/access.json" 600 "discord access.json"

hdr "2. ~/.claude.json must not be group/world readable (holds OAuth secret)"
if [ -f "$HOME_DIR/.claude.json" ]; then
  m=$(stat -c '%a' "$HOME_DIR/.claude.json")
  case "$m" in
    600|400) pass ".claude.json mode $m" ;;
    *) fail ".claude.json mode $m exposes any embedded MCP secrets" "chmod 600 $HOME_DIR/.claude.json" ;;
  esac
fi
# Backups duplicate the same config — they must be locked down too.
if compgen -G "$HOME_DIR/.claude/backups/*" >/dev/null; then
  leaky=$(find "$HOME_DIR/.claude/backups" -type f ! -perm 600 2>/dev/null | wc -l)
  if [ "$leaky" -eq 0 ]; then pass "all .claude/backups files are 0600"; else
    fail "$leaky backup file(s) not 0600" "chmod 600 $HOME_DIR/.claude/backups/*"; fi
fi

hdr "3. No live secrets sitting in group/world-readable files under \$HOME"
# access-production tokens + Plaid secret + Google client secret are the crown jewels.
# Find any readable-by-group-or-other file that contains them.
# Match REAL secret material (token/secret + its long tail), not the bare prefix
# that appears as documentation in this repo's audit report and tooling.
leaks=$(grep -rlE 'access-production-[0-9a-f-]{20}|GOCSPX-[A-Za-z0-9_-]{20}|PLAID_SECRET=[a-f0-9]{20}' "$HOME_DIR" \
          --exclude-dir=node_modules --exclude-dir=.git \
          --exclude-dir=security --exclude="security-audit-*.md" 2>/dev/null \
        | while read -r f; do
            m=$(stat -c '%a' "$f" 2>/dev/null); [ -z "$m" ] && continue
            # flag if group (2nd digit) or other (3rd digit) has any read bit
            g=${m: -2:1}; o=${m: -1:1}
            if [ $(( g & 4 )) -ne 0 ] || [ $(( o & 4 )) -ne 0 ]; then echo "$f ($m)"; fi
          done)
if [ -z "$leaks" ]; then pass "no production tokens/secrets in group/world-readable files"; else
  fail "secret material in readable files:"$'\n'"$leaks" "chmod 600 the listed file(s) or move the secret to $CFG/*.env"; fi

hdr "4. Plaid environment + token sanity"
if [ -f "$CFG/plaid-auth.env" ]; then
  env_val=$(grep -E '^PLAID_ENV=' "$CFG/plaid-auth.env" | cut -d= -f2)
  pass "PLAID_ENV=${env_val:-production(default)} (production = real money; expected)"
fi

hdr "5. Plaid MCP must never leak access_token in tool output"
SRV="$HOME_DIR/workshop/backend/mcp/plaid/server.js"
if [ -f "$SRV" ]; then
  # Fail only if access_token appears on a line that also builds output (text/content/result/stringify).
  if grep -nE 'access_token' "$SRV" | grep -qE 'text|content|JSON\.stringify|result|push\('; then
    fail "server.js may emit access_token into tool output" "ensure access_token is only passed to Plaid client calls, never returned"
  else
    pass "server.js does not return access_token in tool results"
  fi
fi

hdr "6. Host-level isolation"
if id -nG "$SVC_USER" | grep -qw docker; then
  fail "$SVC_USER is in the 'docker' group = root-equivalent (defeats tenant isolation)" \
       "sudo gpasswd -d $SVC_USER docker; manage the letta/ollama compose from an admin account or via a narrow sudo rule"
else
  pass "$SVC_USER is NOT in the docker group"
fi

hdr "7. Host firewall (the only thing gating ~15 services bound to 0.0.0.0)"
if ufw status 2>/dev/null | grep -qi 'Status: active' || grep -qx 'ENABLED=yes' /etc/ufw/ufw.conf 2>/dev/null; then
  pass "ufw active"
elif command -v nft >/dev/null && nft list ruleset 2>/dev/null | grep -q 'hook input'; then
  pass "nftables input filtering present"
else
  fail "no active host firewall; every 0.0.0.0 service is reachable by the whole LAN" \
       "enable ufw: default deny incoming, allow loopback + tailscale0 + ssh from LAN"
fi

hdr "8. Backend financial/agent routers must be gated against off-host callers"
MAIN="$HOME_DIR/workshop/backend/main.py"
if [ -f "$MAIN" ]; then
  if grep -q 'from deps_auth import require_token' "$MAIN" \
     && grep -qE 'finance\.router.*dependencies=_SECURE' "$MAIN"; then
    pass "main.py gates finance/agent routers via require_token (loopback-trust)"
  else
    fail "finance/agent routers not gated by require_token in main.py" \
         "apply dependencies=[Depends(require_token)] to finance/agents/autonomy/backlog routers"
  fi
fi
# The gate fails OPEN if the token isn't configured — assert it exists.
if [ -s "$CFG/workshop-api.env" ] && grep -qE '^WORKSHOP_API_TOKEN=.+' "$CFG/workshop-api.env"; then
  pass "WORKSHOP_API_TOKEN is configured (gate fails closed for off-host)"
else
  fail "WORKSHOP_API_TOKEN not set — backend gate fails OPEN" "write WORKSHOP_API_TOKEN to $CFG/workshop-api.env and restart backend"
fi
# Vite proxies /api to the finance backend — it must not bind 0.0.0.0 (LAN).
VITE="$HOME_DIR/workshop/frontend/vite.config.ts"
if [ -f "$VITE" ]; then
  if grep -qE 'host:\s*"0\.0\.0\.0"' "$VITE"; then
    fail "Vite binds 0.0.0.0 — LAN can reach the finance backend via the /api proxy" "set server.host to 127.0.0.1 (remote access stays via Tailscale Serve)"
  else
    pass "Vite dev server not bound to 0.0.0.0"
  fi
fi

hdr "9. Backend must not be bound to a public interface"
if ss -tlnp 2>/dev/null | grep -qE '0\.0\.0\.0:8000|:::8000'; then
  fail "backend :8000 bound to 0.0.0.0 (LAN-reachable)" "bind uvicorn to 127.0.0.1"
else
  pass "backend :8000 not on 0.0.0.0"
fi

hdr "10. No secrets committed to the workshop git repo"
if git -C "$WORKSHOP" log --all -p -- . 2>/dev/null \
     | grep -qE 'access-production-|GOCSPX-|PLAID_SECRET=[A-Za-z0-9]'; then
  fail "secret material found in git history" "rotate the secret, then scrub history (git filter-repo)"
else
  pass "git history clean of known secret patterns"
fi

hdr "11. CRIT-1 egress controls (shorten the exfil leg of the trifecta)"
SET="$HOME_DIR/.claude/settings.json"
GUARD="$HOME_DIR/.claude/hooks/egress-guard.py"
if [ -x "$GUARD" ] && grep -q 'egress-guard.py' "$SET" 2>/dev/null; then
  pass "Bash egress guard wired into settings PreToolUse"
else
  fail "Bash egress guard missing/not wired" "ensure $GUARD exists and is in settings.json hooks.PreToolUse (matcher Bash)"
fi
if grep -q 'mcp__google-docs__sendEmail' "$SET" 2>/dev/null; then
  pass "email-send exfil tools denied in settings"
else
  fail "email-send tools not denied" "add mcp__google-docs__sendEmail / sendDraft to permissions.deny"
fi

printf '\n\033[1mSummary:\033[0m %d fail, %d warn — %s\n' "$FAILS" "$WARNS" \
  "$([ "$FAILS" -eq 0 ] && echo 'invariants hold' || echo 'REGRESSION DETECTED')"
[ "$FAILS" -eq 0 ]
