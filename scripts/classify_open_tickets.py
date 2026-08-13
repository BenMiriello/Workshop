#!/usr/bin/env python3
"""Propose an `execution` class for every open ticket, and park what no worker
can do.

WHY THIS EXISTS. `next_ready` orders `priority DESC, created ASC` and consults
nothing else, so on the day this was written the head of the platform queue was
`[SEC-C3] Verify inbound IPv6 firewall policy (root)` at priority 100. The top
ten were the whole 2026-07-18 security cohort; five say "needs root" in their own
body; the worker runs as an unprivileged service user and has none. An unattended
sweep would have spent its first five runs failing on work no agent can do.

Readiness is not the fix: SEC-C3 is well specified and simply undoable here. The
missing fact is a different one, so `tasks.execution` records it:

    code       a change to a tree this worker can write        sweep-eligible
    judgement  the output is a decision, a design or an audit  never
    external   needs root, another host user, a vendor console never
    None       not yet classified                             never (fail closed)

WHY PARK RATHER THAN MOVE. A move is a delete plus an add across two stores with
no transaction and no `move` verb in `tasks.py`. Parking is one field; it is the
existing dead-letter semantics; `next_ready` already ignores it; `state_type`
already maps it to `backlog`; and it is reversible with one click. The tickets
stay visible and stay findable.

WHAT IT DOES NOT COVER, so nobody has to discover it later:
  * Only `status == "todo"`. A `draft` approved into `todo` later carries
    `execution: None` and is therefore never picked up unattended, and nothing
    re-runs this script for it. Re-run it after approving drafts.
  * Only the `workshop` and dev scopes (see `_stores`), not `project:*`/`app:*`.

THIS IS AN OPERATION, NOT A SERVICE, and not an autonomous one. It PRINTS by
default and writes only under `--apply`, because the operator reviews the table
first. The heuristic is deliberately biased toward `external` and `judgement`:
misclassifying a `code` ticket as `judgement` means it waits for a human, which
is exactly the status quo and costs nothing. Misclassifying the other way is what
this whole exercise exists to prevent.

Usage:
    python3 scripts/classify_open_tickets.py                  # print the proposal
    python3 scripts/classify_open_tickets.py --apply          # write it
    python3 scripts/classify_open_tickets.py --scope development
    python3 scripts/classify_open_tickets.py --apply --no-park   # tag, never park
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

WORKSHOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSHOP / "backend"))

from services import task_scope, tasks  # noqa: E402

PARK_REASON = "host administration; not agent-executable"

# Needs a privilege, a host user, a vendor console or an operator secret that the
# worker does not have and cannot acquire. Word-boundary matched so "rootdir" or
# a passing mention of "password reset" in prose does not trip it.
#
# PHRASES, NOT THE BARE WORD `root`. This started as `\broot\b` with a
# negative lookbehind for "non-", and that was far too broad: "root" is everyday
# engineering vocabulary. Measured misclassifications, every one of them ordinary
# code work that `--apply` would have PARKED with a false blocked_reason:
#
#     "Fix the root cause of the duplicate-chat bug"
#     "Move the project root resolution into paths.py"
#     "Serve the doc root from dist"
#     "the store root is counted twice by a glob"
#     "Fix chown handling in the installer"
#
# This repo's own knowledge index has a note titled "Chat bugs need root-cause
# fixes"; a ticket quoting it would have been parked as host administration.
#
# THE TWO DIRECTIONS ARE NOT SYMMETRIC, which is why this one gets the precision.
# A missed `external` costs one wasted unattended run that fails and releases. A
# false `external` mutates status to `parked`, writes a reason that is not true,
# and (because `tasks.update` clears `awaiting` on any status outside
# blocked/in_review) silently discards a pending question. So `root` now has to
# appear in a phrase that actually means the privilege.
_EXTERNAL_BODY = re.compile(
    r"(?:"
    r"\bsudo\b"
    r"|\bas\s+root\b|\bneeds?\s+root\b|\brequires?\s+root\b|\broot\s+access\b"
    r"|\broot\s+privileges?\b|\broot-?only\b|\broot\s+password\b|\bbecome\s+root\b"
    r"|\brun\s+as\s+root\b|\broot\s+shell\b"
    r"|\bip6?tables\b|\bufw\b|\bgpasswd\b|\busermod\b|\bsudoers\b"
    r"|\bsystemctl\s+(?:enable|disable)\b"
    r"|\bsudo\s+cho(?:wn|wn)\b"
    r"|\bneeds?\s+the\s+operator\b|\ban?\s+operator\s+secret\b"
    r"|\banother\s+host\s+user\b|\bvendor\s+console\b|\bstack\s+owner\b"
    r")",
    re.IGNORECASE,
)
# A parenthetical privilege note ANYWHERE in the title, the convention the
# security audit used. Both of these are real and only one ends the line:
#   "[SEC-M3] sshd: disable password authentication (root)"
#   "[SEC-L5] Hygiene (root): Samba restrict anonymous ..."
# This was end-anchored, which missed the second and let a genuinely
# root-requiring ticket read as `code`. A bare "(root)" in a title is not
# ambiguous the way the loose word `root` in a body is, so it needs no context.
_EXTERNAL_TITLE = re.compile(r"\((?:root|operator|manual)\)", re.IGNORECASE)

# The output is a decision, a design or an audit: there is no diff that finishes
# it, so a reviewer gate cannot tell whether it is done.
_JUDGEMENT_VERB = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?"
    r"(audit|design|define|revisit|decide|research|reevaluate|re-evaluate|"
    r"consider|evaluate|assess|propose|plan|survey|review|discuss|"
    r"plan\s+out|plan\s+for|think\s+through)\b",
    re.IGNORECASE,
)


def classify(task: dict) -> tuple[str, str]:
    """`(execution, why)` for one ticket. Deterministic and order-sensitive:
    `external` is tested first because a ticket can be BOTH a design task and one
    that needs root, and the privilege is the fact that actually stops a run."""
    title = str(task.get("title") or "")
    body = str(task.get("body") or "")

    if _EXTERNAL_TITLE.search(title):
        return "external", "the title marks it as needing a privilege"
    m = _EXTERNAL_BODY.search(body) or _EXTERNAL_BODY.search(title)
    if m:
        return "external", f"names {m.group(0)!r}, which this worker does not have"

    m = _JUDGEMENT_VERB.match(title)
    if m:
        return "judgement", f"the title asks to {m.group(1).lower()}, not to change code"
    if task.get("effort") == "deep":
        return "judgement", "effort: deep, so the output is more likely a decision than a diff"

    return "code", "no privilege named and the title asks for a change"


def _stores(only: str | None) -> list[tuple[str, Path]]:
    """`workshop` plus every dev scope, deduplicated by RESOLVED path.

    NOT every store in the tree, which is what this used to claim. It is
    `["workshop"] + task_scope.dev_scopes()`, which today resolves to exactly two
    directories: `shared/queue` and the repo root. A `project:*` or `app:*` store
    is NOT classified, so its tickets keep `execution: None` and are invisible to
    unattended dispatch. That is the fail-closed direction, and it is correct
    behaviour rather than a bug, but it is not what "every non-scratch store"
    told a reader.

    Deduplication is not cosmetic. An earlier count of this same queue reported
    50 open tickets at 20% readiness, which read as the backlog degrading; it was
    the root store being counted twice by a glob. Two scope KEYS can legitimately
    resolve to one directory, and a ticket listed twice would be classified twice
    and reported as two.
    """
    keys = ["workshop"] + task_scope.dev_scopes()
    out: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for key in keys:
        if only and key != only:
            continue
        root = task_scope.root_for_context(key)
        if root is None:
            continue
        rp = Path(root).resolve()
        if rp in seen or not (rp / tasks.STORE_NAME).exists():
            continue
        seen.add(rp)
        out.append((key, Path(root)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the classification (default: print only)")
    ap.add_argument("--scope", default=None, help="limit to one scope key")
    ap.add_argument("--no-park", action="store_true",
                    help="with --apply, tag `external` tickets but leave them todo")
    ap.add_argument("--reclassify", action="store_true",
                    help="also re-propose for tickets that already carry one")
    args = ap.parse_args()

    stores = _stores(args.scope)
    if not stores:
        print("no stores found", file=sys.stderr)
        return 1

    rows: list[tuple[str, dict, str, str, bool]] = []
    for key, root in stores:
        for t in tasks.list_tasks(root):
            if t.get("status") != "todo":
                continue
            already = t.get("execution")
            if already and not args.reclassify:
                rows.append((key, t, already, "already classified", False))
                continue
            ex, why = classify(t)
            rows.append((key, t, ex, why, True))

    order = {"external": 0, "judgement": 1, "code": 2}
    rows.sort(key=lambda r: (order.get(r[2], 3), r[0], -int(r[1].get("priority") or 0)))

    width = max((len(r[0]) for r in rows), default=8)
    print(f"\n{len(rows)} open ticket(s) across {len(stores)} store(s)\n")
    for key, t, ex, why, fresh in rows:
        mark = " " if fresh else "="
        title = (t.get("title") or "")[:68]
        print(f"{mark} {ex:10} {key:{width}}  p{int(t.get('priority') or 0):<4} {title}")
        print(f"{'':>13}{'':{width}}   {why}")

    counts: dict[str, int] = {}
    for _, _, ex, _, _ in rows:
        counts[ex] = counts.get(ex, 0) + 1
    print("\n  " + "   ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    parkable = [r for r in rows if r[2] == "external" and r[4]]
    print(f"  would park: {len(parkable)}"
          + ("  (--no-park to skip)" if parkable and not args.no_park else ""))

    if not args.apply:
        print("\nnothing written. re-run with --apply to write this.\n")
        return 0

    wrote = parked = 0
    for key, t, ex, why, fresh in rows:
        if not fresh:
            continue
        root = dict(stores).get(key)
        if root is None:
            continue
        park = (ex == "external" and not args.no_park)
        try:
            tasks.update(root, t["id"], execution=ex,
                         **({"status": "parked", "blocked_reason": PARK_REASON}
                            if park else {}))
        except ValueError as e:
            print(f"  ! {t['id']}: {e}", file=sys.stderr)
            continue
        wrote += 1
        parked += bool(park)
    print(f"\nclassified {wrote}, parked {parked}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
