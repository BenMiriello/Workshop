#!/usr/bin/env bash
# backend_source_id.sh — the ONE definition of "which version of the backend
# source is this". The mirror of scripts/ui_source_id.sh, and for the same
# reason: everything that compares running code against source code calls THIS,
# so there is never a second opinion about what counts as an input.
#
# The running backend reports this id on GET /healthz (computed once at startup,
# see backend/main.py). scripts/apply.sh compares that against a fresh run of
# this script to decide whether a restart is owed. That comparison is EXACT and
# needs no bookkeeping file: the process states what it is, disk states what it
# should be.
#
# Content-hashed, not mtime-based, for the reason ui_source_id.sh gives: a
# `git checkout`, a `touch`, or a rebase moves every mtime without changing a
# byte of what would run, and an mtime check answers "restart owed" to all three.
# A needless backend restart is the one genuinely disruptive act in this system,
# so it must not fire on noise.
#
# Prints a 12-char hex id on stdout. Nothing else, so it composes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Hash RELATIVE paths so the same content in a worktree at another path yields
# the same id rather than a false mismatch.
cd "$REPO_ROOT"

# What uvicorn actually imports. requirements.txt counts because an installed
# dependency change alters behaviour just as surely as a source edit does.
#
# EXCLUDED, deliberately:
#   .venv, __pycache__   installed/compiled outputs, not source
#   .runtime             live state written BY the backend; including it would
#                        make the id change constantly and mean nothing
#   workshop_public_app  served by workshop-public.service, a different unit
#                        with its own lifecycle (apply.sh tracks it separately);
#                        including it here would bounce the main backend for an
#                        edge-only edit, which is disruption for no reason
#   tests/               NOT WHAT UVICORN IMPORTS, which is the rule this
#                        comment opens with. A test file cannot change what the
#                        server serves, so an edit to one has no business
#                        reporting the backend stale -- and it did: on
#                        2026-08-26 an uncommitted `tests/conftest.py` made the
#                        id differ, which made apply.sh want a restart, which
#                        its own uncommitted-work guard then refused, so a
#                        finished route sat unserved behind a test fixture
#                        nobody was running. Same shape as the orphaned
#                        docstring edit that blocked several agents on
#                        2026-08-23.
if [ ! -d backend ]; then
  echo "no-backend"
  exit 0
fi

{
  find backend \
    -type d \( -name .venv -o -name __pycache__ -o -name .runtime -o -name node_modules \) -prune -o \
    -type d -path backend/tests -prune -o \
    -type f \( -name '*.py' -o -name 'requirements*.txt' \) \
    ! -name 'workshop_public_app.py' \
    -print0 2>/dev/null
} | LC_ALL=C sort -z | xargs -0 sha1sum 2>/dev/null | sha1sum | cut -c1-12
