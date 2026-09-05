"""Move the JSON stores into Postgres (P2 of the data-model plan).

Prints a plan by default; writes only with `--apply`.

INSERT-ONCE, NOT SYNC. Every row is matched on a natural key and SKIPPED if it
already exists; nothing is ever updated. A second `--apply` therefore writes
zero rows, which is the property that makes it safe to re-run, but it does NOT
pick up edits made to the JSON store after the first run. That matters for P3:
if the file store is still being written while the database is read-shadowed,
the two will diverge and this script will not close the gap. Make it an upsert
keyed on `(external_source, external_id)` before relying on it for that.

    set -a; . ~/.config/workshop/secrets.env; set +a
    .venv/bin/python -m db.backfill            # show what would happen
    .venv/bin/python -m db.backfill --apply

IDEMPOTENCY KEYS. Tickets carry `external_source='workshop-json'` plus their old
uuid in `external_id`; users carry `legacy_user_id`; projects match on slug.
Those columns exist for external trackers and this is exactly that case: the
JSON store is the external system being migrated from. No migration-only
column was added.

WHAT MAPS TO WHAT

  the installation            -> one `organization`
  a task-store scope          -> a `project` (development, workshop)
  the eight hard-coded states -> `workflow_state` rows, per project
  `execution`                 -> `ticket_type` rows (Code / Judgement / External)
  `depends_on` / `relates_to` -> `ticket_link` rows
  `evidence` + `source`       -> `ticket_event` rows
  `comments`                  -> `comment` rows
  max_attempts/attempts/awaiting/assignee/job_ids/lease_until/
    last_heartbeat/claim_pid  -> `ticket` columns, direct (see `_exec_fields`)

Deliberately NOT migrated here: chat threads and messages, notes, the personal
stores. They are P6 and later, and folding them in would make this
unreviewable.

NO LONGER MIGRATED (def2cbf58df8, T4 of coherence-overhaul-2026-08-16): agents
and `jobs.jsonl` into `run` rows. Both tables are gone -- `agent` had zero
reads and zero writes ever, and `run` had 2,753 rows and zero readers once
`ticket.attempts` stopped being derived from it (e9c4a71f0d38). The one thing
either table's rows fed that anything still reads -- a ticket's own
`job_ids`/claim state -- comes straight off the JSON ticket record now (see
`_exec_fields`), the same place `attempts`/`awaiting`/`assignee`
already came from below. `jobs.jsonl` itself is untouched by this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.engine import owner_session, tenant_session  # noqa: E402
from db.ids import uuid7  # noqa: E402
from db.models import (  # noqa: E402
    Comment, Label, Membership, Organization,
    Project, Ticket, TicketEvent, TicketLabel, TicketLink, TicketType,
    UserAccount, WorkflowState,
)

ROOT = Path(os.environ.get("WORKSHOP_ROOT", Path(__file__).resolve().parents[2]))
LEGACY = "workshop-json"

# The status, type and priority vocabulary moved to `db/legacy_map.py` so the
# mirror (`db/ticket_sync.py`) and the read layer (`db/repo.py`) share ONE copy
# with this script. Three copies drifting is how a ticket changes status by
# being read and written back.
from db.legacy_map import STATES, TYPES, priority_band as _priority  # noqa: E402
# The exact coercions `ticket_sync._legacy_view` uses for these six fields,
# reused rather than restated: this script and the live mirror must not be
# able to disagree about what a JSON ticket record's execution/claim state
# means. `_positive_int_or_none`/`_non_negative_int` are "private" only in the
# sense of not being re-exported; `ticket_sync` is this script's sibling in
# the same package and the two already share `legacy_map` the same way.
from db.ticket_sync import _non_negative_int, _positive_int_or_none  # noqa: E402

_STATUS_TOKENS = {row[0] for row in STATES}


def _exec_fields(rec: dict) -> dict:
    """The eight execution/claim fields, straight off a JSON ticket record.

    Every one of these is a plain field on the record already -- none of them
    is derived from `jobs.jsonl` or from anything Run-shaped. `job_ids` in
    particular looks like it should come from the job log, but `tasks.claim()`
    appends to the ticket's OWN `job_ids` list on every claim (see
    `services/tasks.py`), so the record already carries its own history.
    `lease_until`/`last_heartbeat`/`claim_pid` are the same: whatever a live
    claim looks like right now is exactly what the record says.
    """
    return {
        "max_attempts": _positive_int_or_none(rec.get("max_attempts")),
        "attempts": _non_negative_int(rec.get("attempts")),
        "awaiting": (rec.get("awaiting") or None)
                    if isinstance(rec.get("awaiting"), dict) else None,
        "assignee": (str(rec.get("assignee")).strip()[:64] or None
                     if rec.get("assignee") else None),
        "job_ids": [str(j) for j in (rec.get("job_ids") or []) if j] or None,
        "lease_until": _iso(rec.get("lease_until")),
        "last_heartbeat": _iso(rec.get("last_heartbeat")),
        "claim_pid": (int(rec["claim_pid"])
                      if isinstance(rec.get("claim_pid"), int) else None),
    }


# JSON fields with no column of their own. They are POPULATED (scoped_by 112,
# provenance 56, touches 35, blocked_reason 23, acceptance 5, discovered_from 2)
# and were silently discarded by the first version of this script. They are
# preserved as ticket_event rows, which is what models.py always claimed
# happened. Nothing is dropped without being counted in the run summary.
#
# `max_attempts` used to be here too, preserved only as an event rather than a
# queryable field -- the same silent-drop this script's own docstring now calls
# out for the execution/claim fields generally. It gets a real column via
# `_exec_fields` instead, alongside `attempts`/`awaiting`/`assignee`/`job_ids`/
# `lease_until`/`last_heartbeat`/`claim_pid`, none of which were ever preserved
# even as an event.
_PRESERVED = ("scoped_by", "provenance", "touches", "acceptance",
              "discovered_from", "blocked_reason")

SCOPES = [
    ("development", "Development", "DEV", ROOT),
    ("workshop",    "Workshop",    "WS",  ROOT / "shared" / "queue"),
]


def _iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_tasks(store: Path) -> list[dict]:
    if not store.exists():
        return []
    payload = json.loads(store.read_text())
    return payload.get("tasks", payload) if isinstance(payload, dict) else payload


def _load_users(db: Path) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT id, email, name, created_at FROM users")]
    finally:
        con.close()


def plan() -> dict:
    counts = {"users": len(_load_users(ROOT / ".runtime" / "auth.db"))}
    for key, _, _, store_dir in SCOPES:
        counts[f"tickets:{key}"] = len(_load_tasks(store_dir / ".tasks.json"))
    reg = ROOT / ".runtime" / "projects.json"
    counts["projects(registry)"] = (
        len(json.loads(reg.read_text()).get("projects", {})) if reg.exists() else 0)
    return counts


def apply() -> dict:
    stats: dict[str, int] = {}
    users_by_legacy: dict[int, object] = {}
    # Counted, not silent. The first version of this script discarded eight
    # populated fields without recording that it had.
    preserved: Counter[str] = Counter()
    dropped: Counter[str] = Counter()

    # --- organization + users -------------------------------------------------
    # PROVISIONING RUNS ON THE OWNER CONNECTION. Both tables are RLS-protected
    # and the application role has no INSERT on either (migration 7bf9ae20fe0d),
    # because a bug in a request handler must not be able to create or destroy a
    # tenant: `organization` cascades to every tenant table. Creating an org is
    # an admin operation and this is a script, so it says so explicitly.
    with owner_session() as s:
        org = s.scalar(select(Organization).where(Organization.slug == "workshop"))
        if org is None:
            org = Organization(id=uuid7(), slug="workshop",
                               name=os.environ.get("WORKSHOP_OPERATOR_NAME") or "Workshop")
            s.add(org)
            s.flush()
        org_id = org.id

        for row in _load_users(ROOT / ".runtime" / "auth.db"):
            u = s.scalar(select(UserAccount)
                         .where(UserAccount.legacy_user_id == int(row["id"])))
            if u is None:
                u = UserAccount(id=uuid7(), email=row["email"], name=row.get("name"),
                                legacy_user_id=int(row["id"]),
                                created_at=_iso(row.get("created_at")))
                s.add(u)
                s.flush()
            users_by_legacy[int(row["id"])] = u.id
        stats["users"] = len(users_by_legacy)

    # Everything below is tenant-scoped and goes through the org's own session,
    # so the backfill exercises the same RLS path the application will.
    with tenant_session(org_id) as s:
        for uid in users_by_legacy.values():
            if not s.scalar(select(Membership).where(Membership.org_id == org_id,
                                                     Membership.user_id == uid)):
                s.add(Membership(org_id=org_id, user_id=uid, role="owner"))

        states: dict[tuple[str, str], object] = {}
        types: dict[tuple[str, str], object] = {}
        labels: dict[tuple[str, str], object] = {}
        tickets_by_legacy: dict[str, object] = {}
        n_tickets = n_links = n_events = n_comments = n_labels = n_parents = 0
        sole_user = next(iter(users_by_legacy.values()), None)

        for key, title, prefix, store_dir in SCOPES:
            proj = s.scalar(select(Project).where(Project.org_id == org_id,
                                                  Project.slug == key))
            if proj is None:
                proj = Project(id=uuid7(), org_id=org_id, slug=key, name=title,
                               key=prefix)
                s.add(proj)
                s.flush()

            for status, name, category, pos in STATES:
                st = s.scalar(select(WorkflowState).where(
                    WorkflowState.project_id == proj.id, WorkflowState.name == name))
                if st is None:
                    st = WorkflowState(id=uuid7(), org_id=org_id, project_id=proj.id,
                                       name=name, category=category, position=pos,
                                       is_default=(status == "todo"))
                    s.add(st)
                    s.flush()
                states[(key, status)] = st.id

            for slug, name, executable, desc in TYPES:
                ty = s.scalar(select(TicketType).where(
                    TicketType.project_id == proj.id, TicketType.name == name))
                if ty is None:
                    ty = TicketType(id=uuid7(), org_id=org_id, project_id=proj.id,
                                    name=name, description=desc,
                                    agent_executable=executable)
                    s.add(ty)
                    s.flush()
                types[(key, slug)] = ty.id

            rows = _load_tasks(store_dir / ".tasks.json")
            rows.sort(key=lambda r: str(r.get("created") or ""))
            number = proj.next_ticket_number

            for rec in rows:
                legacy = str(rec.get("id") or "")
                if not legacy:
                    # Without an id there is no idempotency key, and a SECOND
                    # such record would match the first on external_id='' and be
                    # silently dropped. Count it and move on.
                    dropped["no_id"] += 1
                    continue
                existing = s.scalar(select(Ticket).where(
                    Ticket.external_source == LEGACY, Ticket.external_id == legacy))
                if existing is not None:
                    tickets_by_legacy[legacy] = existing.id
                    continue

                status = rec.get("status") if rec.get("status") in _STATUS_TOKENS else "todo"
                closed = _iso(rec.get("updated")) if status in ("done", "cancelled") else None
                t = Ticket(
                    id=uuid7(), org_id=org_id, project_id=proj.id, number=number,
                    title=(rec.get("title") or "")[:255] or "(untitled)",
                    body=rec.get("body") or "",
                    state_id=states[(key, status)],
                    type_id=types.get((key, rec.get("execution"))),
                    priority=_priority(rec.get("priority")),
                    effort=rec.get("effort"),
                    created_at=_iso(rec.get("created")),
                    updated_at=_iso(rec.get("updated")),
                    closed_at=closed,
                    # `assignee_id` is ATTRIBUTION (a real person, an FK to
                    # `user_account`), distinct from the `assignee` string
                    # `_exec_fields` sets below (the DELEGATE the work model
                    # spells `operator`/`agent:<slug>`/unassigned -- see
                    # `Ticket.assignee`'s own docstring). There is exactly one
                    # user to attribute authorship to in this installation.
                    assignee_id=sole_user,
                    created_by_id=sole_user,
                    external_source=LEGACY, external_id=legacy,
                    **_exec_fields(rec),
                )
                s.add(t)
                s.flush()
                tickets_by_legacy[legacy] = t.id
                number += 1
                n_tickets += 1

                src = rec.get("source") or {}
                # `ck_*_actor_is_consistent` requires the id to match the type,
                # so a 'user' event without a user id is not expressible. When
                # the actor is not recoverable the honest answer is 'system'.
                human = src.get("kind") in ("operator", "chat") and sole_user
                s.add(TicketEvent(id=uuid7(), org_id=org_id, ticket_id=t.id,
                                  actor_type="user" if human else "system",
                                  actor_user_id=sole_user if human else None,
                                  verb="created",
                                  field="source", new_value=src.get("kind"),
                                  created_at=_iso(rec.get("created"))))
                n_events += 1

                # Fields with no column. Preserved rather than discarded.
                for field in _PRESERVED:
                    value = rec.get(field)
                    if value in (None, "", [], {}, 0):
                        continue
                    s.add(TicketEvent(
                        id=uuid7(), org_id=org_id, ticket_id=t.id,
                        actor_type="system", verb="migrated", field=field,
                        new_value=(value if isinstance(value, str)
                                   else json.dumps(value))[:8000],
                        created_at=_iso(rec.get("created"))))
                    n_events += 1
                    preserved[field] += 1

                # Labels have had tables since the first migration and were
                # never populated.
                for name in (rec.get("labels") or []):
                    name = str(name).strip()
                    if not name:
                        continue
                    lab = labels.get((key, name))
                    if lab is None:
                        lab = s.scalar(select(Label).where(
                            Label.org_id == org_id, Label.project_id == proj.id,
                            Label.name == name))
                        if lab is None:
                            lab = Label(id=uuid7(), org_id=org_id,
                                        project_id=proj.id, name=name)
                            s.add(lab)
                            s.flush()
                        labels[(key, name)] = lab
                    s.add(TicketLabel(ticket_id=t.id, label_id=lab.id, org_id=org_id))
                    n_labels += 1

                for ev in (rec.get("evidence") or []):
                    s.add(TicketEvent(id=uuid7(), org_id=org_id, ticket_id=t.id,
                                      actor_type="system", verb="recorded",
                                      field=ev.get("kind"),
                                      new_value=(ev.get("ref") or "")[:8000],
                                      created_at=_iso(ev.get("ts"))))
                    n_events += 1

                for c in (rec.get("comments") or []):
                    # The author is a ROLE, not a head count. "assistant" is an
                    # agent whether or not the org has one member; deriving it
                    # from `sole_user` recorded the wrong actor and lost the
                    # only signal that a machine wrote the comment.
                    author = (c.get("author") or "").strip().lower()
                    if author in ("assistant", "agent", "worker"):
                        atype, auser = "agent", None
                    elif author and sole_user:
                        atype, auser = "user", sole_user
                    else:
                        atype, auser = "system", None
                    s.add(Comment(id=uuid7(), org_id=org_id, ticket_id=t.id,
                                  author_type=atype, author_user_id=auser,
                                  body=c.get("text") or "",
                                  external_source=LEGACY,
                                  external_id=str(c.get("id") or "") or None,
                                  created_at=_iso(c.get("ts"))))
                    n_comments += 1

            proj.next_ticket_number = number
            s.flush()

        # Links need every ticket to exist first, so this is a second pass.
        for key, _, _, store_dir in SCOPES:
            for rec in _load_tasks(store_dir / ".tasks.json"):
                src_id = tickets_by_legacy.get(str(rec.get("id") or ""))
                if src_id is None:
                    continue

                # `parent` is 0% occupancy today, which is exactly why it was
                # missed: the code never read it, so the day someone starts
                # writing it the hierarchy would migrate as nothing. It needs
                # the second pass for the same reason links do (the parent may
                # sort after the child).
                parent_id = tickets_by_legacy.get(str(rec.get("parent") or ""))
                if parent_id is not None and parent_id != src_id:
                    s.execute(update(Ticket).where(Ticket.id == src_id)
                              .values(parent_id=parent_id))
                    n_parents += 1
                elif rec.get("parent"):
                    dropped["parent_unresolvable"] += 1

                for field, kind in (("depends_on", "blocked_by"),
                                    ("relates_to", "relates_to")):
                    for dep in (rec.get(field) or []):
                        dst = tickets_by_legacy.get(str(dep))
                        # A dependency on a ticket in another store is dropped
                        # rather than invented. The JSON scheduler already
                        # treated an unresolvable id as satisfied, so this
                        # changes no behaviour; it just stops pretending the
                        # edge was real.
                        if dst is None or dst == src_id:
                            continue
                        if s.scalar(select(TicketLink).where(
                                TicketLink.from_ticket_id == src_id,
                                TicketLink.to_ticket_id == dst,
                                TicketLink.kind == kind)):
                            continue
                        s.add(TicketLink(id=uuid7(), org_id=org_id,
                                         from_ticket_id=src_id, to_ticket_id=dst,
                                         kind=kind))
                        n_links += 1

        stats.update(tickets=n_tickets, links=n_links, labels=n_labels,
                     parents=n_parents, events=n_events, comments=n_comments,
                     org=str(org_id))
        for field, count in sorted(preserved.items()):
            stats[f"preserved:{field}"] = count
        for reason, count in sorted(dropped.items()):
            stats[f"DROPPED:{reason}"] = count
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write to the database (default: print the plan only)")
    ap.add_argument("--repair-events", action="store_true",
                    help="re-derive the noted/scoped ticket_event rows from the "
                         "JSON store (owner connection; see repair_derived_events)")
    ap.add_argument("--force", action="store_true",
                    help="run even though the JSON ticket stores are retired "
                         "(they are a frozen backup, not the source)")
    args = ap.parse_args()

    # THE SOURCE THIS READS IS NO LONGER THE STORE. Since ADR 0019 stage 2 a
    # relational scope writes Postgres and stops writing `.tasks.json`, so the
    # file it finds is whatever was there at cutover. Re-running the backfill
    # against it would push a frozen snapshot over live rows: every ticket
    # filed, closed or edited since would be reverted, silently and in one
    # command. The backfill was a one-time move and its own guard has to say so.
    if args.apply and not args.force:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from services import tasks as _tasks
        retired = [slug for slug, _n, _k, store_dir in SCOPES
                   if _tasks.is_relational_scope(store_dir)]
        if retired:
            print("REFUSING: the JSON ticket store is retired for "
                  f"{', '.join(retired)} (ADR 0019 stage 2). The file on disk is "
                  "a frozen backup from the cutover, and applying it would "
                  "revert every ticket written since.\n"
                  "Pass --force only if you have confirmed the file is newer "
                  "than the rows, which is not true in normal operation.")
            return 2

    if args.repair_events:
        print(repair_derived_events())
        return 0

    if not args.apply:
        print("PLAN (nothing written). Source rows found:")
        for k, v in plan().items():
            print(f"  {k:24} {v}")
        print("\nRe-run with --apply to write.")
        return 0

    for k, v in apply().items():
        print(f"  {k:24} {v}")
    return 0



def repair_derived_events() -> dict:
    """Re-derive the `noted`/`scoped` history rows from the JSON store.

    THESE ROWS ARE A PROJECTION, not history anyone authored. They are built
    from each ticket's `evidence` list and `scoped_by` field, so deleting and
    re-deriving them loses nothing while the JSON store still exists.

    Why it lives here and not in the mirror: the application role has no UPDATE
    or DELETE on `ticket_event`, because an audit log the app can rewrite is not
    an audit log. The database refused the in-app repair, which is the correct
    outcome. This runs on the OWNER connection, once, as a migration step.

    What it repairs: rows written before the mirror carried each evidence
    entry's own `ts` were stamped with the migration's clock instead, which
    loses the only ordering a worklog has.

    Safe to re-run: it rebuilds from the same source every time.
    """
    from sqlalchemy import text as _text

    from db.engine import owner_session

    stats = {"deleted": 0}
    with owner_session() as s:
        r = s.execute(_text(
            "DELETE FROM ticket_event WHERE verb IN ('noted','scoped')"))
        stats["deleted"] = r.rowcount
        s.commit()
    return stats

if __name__ == "__main__":
    raise SystemExit(main())
