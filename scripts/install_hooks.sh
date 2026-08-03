#!/usr/bin/env bash
# install_hooks.sh — point git at the repo's tracked hooks.
#
# Git hooks live in .git/hooks, which is NOT tracked, so a hook written there is
# invisible to review, absent from every other clone, and silently lost. Pointing
# core.hooksPath at a tracked .githooks/ directory makes the automation part of
# the repo: readable in a diff, present after a clone, and one command to enable.
#
# Idempotent. check_design.sh warns when this has not been run, so a tree where
# the automation is off says so instead of quietly drifting.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

chmod +x .githooks/* 2>/dev/null || true
git config core.hooksPath .githooks
echo "core.hooksPath = $(git config core.hooksPath)"
echo "hooks installed:"
for h in .githooks/*; do [ -f "$h" ] && echo "  $(basename "$h")"; done
