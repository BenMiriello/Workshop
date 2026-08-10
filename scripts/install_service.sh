#!/usr/bin/env bash
# Install (or re-install) the Workshop backend as a systemd --user service.
#
# Idempotent: safe to run repeatedly. It substitutes this checkout's path into
# the unit template, installs it under ~/.config/systemd/user/, enables linger so
# it survives logout/reboot without an active session, frees :8000 from any
# ad-hoc nohup backend, then enables + starts the service and verifies health.
#
# Reproducible by design: nothing here is specific to the host it was written on.
# Anyone who clones the repo and runs `bash scripts/install_service.sh` gets the
# same setup. Run it as the user who should own the service (NOT root/sudo).
set -euo pipefail

SERVICE=workshop-backend.service
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/infra/systemd/$SERVICE"
UNIT_DIR="$HOME/.config/systemd/user"
DEST="$UNIT_DIR/$SERVICE"
SECRETS_DIR="$HOME/.config/workshop"
SECRETS="$SECRETS_DIR/secrets.env"
SECRETS_EXAMPLE="$REPO_ROOT/infra/systemd/secrets.env.example"
VENV_UVICORN="$REPO_ROOT/backend/.venv/bin/uvicorn"

if [ "$(id -u)" = "0" ]; then
  echo "Refusing to run as root. Run as the user that should own the service." >&2
  exit 1
fi
[ -f "$TEMPLATE" ] || { echo "Missing unit template: $TEMPLATE" >&2; exit 1; }

if [ ! -x "$VENV_UVICORN" ]; then
  echo "Backend venv not found at $VENV_UVICORN" >&2
  echo "Create it first:" >&2
  echo "  cd $REPO_ROOT/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Seed secrets file (optional values) from the committed example, mode 0600.
mkdir -p "$SECRETS_DIR"
if [ ! -f "$SECRETS" ]; then
  cp "$SECRETS_EXAMPLE" "$SECRETS"
  chmod 600 "$SECRETS"
  echo "Created $SECRETS from example — fill in optional keys as needed."
fi

# Render the unit: substitute @WORKSHOP_ROOT@ with this checkout's absolute path.
mkdir -p "$UNIT_DIR"
sed "s#@WORKSHOP_ROOT@#$REPO_ROOT#g" "$TEMPLATE" > "$DEST"
echo "Installed unit -> $DEST (WORKSHOP_ROOT=$REPO_ROOT)"

# Survive reboot/logout without a login session.
if command -v loginctl >/dev/null 2>&1; then
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    loginctl enable-linger "$USER" 2>/dev/null \
      || echo "Note: could not enable linger automatically. Run: sudo loginctl enable-linger $USER"
  fi
fi

# Free :8000 from any ad-hoc nohup backend so the service can bind it.
#
# Skip the service's OWN process: the pattern below matches the systemd-managed
# backend just as well as an ad-hoc one, so an unguarded pkill here kills a
# healthy backend — and with it any chat turn in flight — on what is supposed to
# be an idempotent re-install. Only ad-hoc PIDs (not the unit's MainPID) qualify.
main_pid="$(systemctl --user show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0)"
adhoc=""
for p in $(pgrep -f 'uvicorn main:app.*8000' 2>/dev/null || true); do
  [ "$p" = "${main_pid:-0}" ] && continue
  adhoc="$adhoc $p"
done
if [ -n "$adhoc" ]; then
  echo "Stopping ad-hoc backend on :8000 (pids:$adhoc) ..."
  kill $adhoc 2>/dev/null || true
  sleep 1
fi

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE"

# Verify.
ok=""
for _ in $(seq 1 15); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/agents/ || true)"
  if [ "$code" = "200" ]; then ok=1; break; fi
  sleep 1
done
if [ -n "$ok" ]; then
  echo "OK: workshop-backend active, /agents/ -> 200."
  systemctl --user --no-pager status "$SERVICE" | head -5
else
  echo "Service started but /agents/ not healthy yet. Recent logs:" >&2
  journalctl --user -u "$SERVICE" --no-pager -n 20 >&2 || true
  exit 1
fi
