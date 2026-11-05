#!/usr/bin/env bash
# Lists every listening TCP socket reachable from outside this host and fails on
# any the operator has not chosen. TCP only: an unconnected UDP socket is as often
# a client (resolver, mDNS, DHCP, tailscaled's own port) as a service, so listing
# them reports noise rather than doors.
#
# The intended shape is that the only way in is the tailnet: services bind
# loopback and remote access arrives through `tailscale serve`, which connects
# to 127.0.0.1. So a listener on loopback or on this host's tailnet address is
# fine, and anything else (0.0.0.0, [::], a LAN or public address) is a door.
#
# Doors the operator wants open (file sharing on the LAN, say) are declared in
# WORKSHOP_EXPOSURE_ALLOW, a space-separated list of ports, set in the instance
# config. A host firewall may still block an unlisted door; this check does not
# count on it, because Docker-published ports bypass ufw entirely.
#
# Exit 0 = nothing unexpected, 1 = at least one undeclared door.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
[ -f "$ROOT/scripts/lib/instance.sh" ] && . "$ROOT/scripts/lib/instance.sh"

allow=" ${WORKSHOP_EXPOSURE_ALLOW:-} "
tail_addrs=" $(tailscale ip 2>/dev/null | tr '\n' ' ') "

bad=0
while read -r local; do
  addr="${local%:*}"
  port="${local##*:}"
  addr="${addr#[}"; addr="${addr%]}"
  case "$addr" in
    127.*|::1|::ffff:127.*) continue ;;
  esac
  case "$tail_addrs" in *" $addr "*) continue ;; esac
  case "$allow" in *" $port "*) continue ;; esac
  printf '  OPEN  %s\n' "$local"
  bad=1
done < <(ss -ltnH | awk '{print $4}' | sed 's/%[^:]*:/:/' | sort -u)

if [ "$bad" -eq 0 ]; then
  echo "exposure: only loopback, the tailnet address and declared ports are listening"
else
  echo "exposure: the sockets above accept connections from off this host." \
       "Bind them to 127.0.0.1, or add the port to WORKSHOP_EXPOSURE_ALLOW if it is meant to be open."
fi
exit "$bad"
