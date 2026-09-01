#!/usr/bin/env bash
# done.sh — the one command that answers "is this done", with an exit code.
#
#   bash scripts/done.sh                 judge this turn's work
#   bash scripts/done.sh --shot /memory  ...and screenshot a route as evidence
#   bash scripts/done.sh --json          print the evidence, not the summary
#
# Exit 0 = done. Exit 1 = not done, and it says exactly what is missing.
#
# It composes the gates that already exist rather than inventing checks:
# `apply.sh --check` (did the change reach the surface the operator looks at),
# the stub scan, `reviewer.sh` whole when the working tree holds only this
# turn's work (its tools scoped to this turn's files when it does not, because
# then its whole-tree verdict is neither attributable nor cheap), and `shoot.sh`
# when the change is visible.
# Every command it runs is recorded with its argv, exit code and output in
# .runtime/done-evidence.json. A summary sentence is not evidence and this gate
# does not accept one.
#
# It judges THIS TURN's files, not the whole working tree: several agents share
# this checkout and being blamed for a stranger's half-written file is how a
# gate gets switched off. When another agent's uncommitted work is what is
# holding the build, it says so and does not block.
#
# `--files a b c` pins the set by hand, for a caller that already knows it (a
# queue runner, a test). It is not a way around the gate: the Stop hook does not
# read it, it recomputes the turn's set and its fingerprint itself, so evidence
# produced over a narrower set simply does not match and the turn stays blocked.
#
# The same script is wired as a Stop hook (`--stop-hook`, see
# .claude/settings.json), so an unattended run cannot end by declaring itself
# finished. The hook does not re-run this gate — it recomputes the fingerprint
# of the turn's files and checks that the evidence on disk describes exactly
# that state, which costs about a fifth of a second.
#
# Rationale, and why this is a mechanism rather than another paragraph in
# CLAUDE.md: see the header of scripts/done_gate.py.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Already inside a gate run (reviewer.sh -> the test suite -> a test that calls
# this). Returning immediately is the same reentry break check_backend_tests.py
# needed when the suite ran the reviewer that ran the suite.
[ "${WORKSHOP_DONE_GATE_RUNNING:-0}" = "1" ] && exit 0

PY="$ROOT/backend/.venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
[ -n "$PY" ] || { echo "done.sh: no python3 available" >&2; exit 2; }

exec "$PY" "$ROOT/scripts/done_gate.py" "$@"
