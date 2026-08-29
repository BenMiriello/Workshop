#!/usr/bin/env bash
# check_dev_mode_off.sh — the development app is absent from the catalog on a
# production install.
#
# This is the acceptance criterion of ticket 2d6e7597366e4541ab2e52e05aae43dd:
# "Do not report this done on a count, a status line or a doc.  The check is
# the evidence."
#
# WHAT THIS PROVES.  With WORKSHOP_DEV_MODE unset, the service layer must:
#   (a) exclude dev_only=True rows from GET /apps (list_apps)
#   (b) return None from get_app / get_app_by_slug for a dev_only=True app
#   (c) pass dev_only=False to the repo so the SQL itself is filtered
#
# The tests in backend/tests/test_dev_only_gate.py exercise all three paths
# against a patched database layer, so no real Postgres connection is needed.
# Every test uses an autouse fixture that deletes WORKSHOP_DEV_MODE and
# asserts dev_mode_on() is False, so the gate cannot pass on the operator's
# dev box by accident.
#
# MODES
#
#   bash scripts/check_dev_mode_off.sh              run the tests
#   bash scripts/check_dev_mode_off.sh --since REV  same (--since is accepted
#                                                   but ignored; this is a
#                                                   functional test, not delta)
#
# Exit 0 = all assertions hold.
# Exit 1 = a test failed.
# Exit 2 = the backend venv or the test file is not present (cannot run).

set -u
WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Accept (and ignore) --since so reviewer.sh's _delta_check can call this
# the same way it calls every other script.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since|--since=*) shift; [[ "${1:-}" != --* && -n "${1:-}" ]] && shift || true ;;
    *) shift ;;
  esac
done

VENV="$WORKSHOP/backend/.venv/bin/python"
TEST_FILE="$WORKSHOP/backend/tests/test_dev_only_gate.py"

if [[ ! -x "$VENV" ]]; then
  echo "check_dev_mode_off: backend venv not found at $VENV" >&2
  exit 2
fi
if [[ ! -f "$TEST_FILE" ]]; then
  echo "check_dev_mode_off: test file not found at $TEST_FILE" >&2
  exit 2
fi

exec "$VENV" -m pytest "$TEST_FILE" -x -q 2>&1
