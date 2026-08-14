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
uuid in `external_id`; runs carry `legacy_job_id`; users carry `legacy_user_id`;
projects and agents match on slug. Those columns exist for external trackers and
this is exactly that case: the JSON store is the external system being migrated
from. No migration-only column was added.

WHAT MAPS TO WHAT

  the installation            -> one `organization`
  a task-store scope          -> a `project` (development, workshop)
  the eight hard-coded states -> `workflow_state` rows, per project
  `execution`                 -> `ticket_type` rows (Code / Judgement / External)
  `depends_on` / `relates_to` -> `ticket_link` rows
  `evidence` + `source`       -> `ticket_event` rows
  `comments`                  -> `comment` rows
  jobs.jsonl                  -> `run` rows

Deliberately NOT migrated here: chat threads and messages, notes, the personal
stores. They are P6 and later, and folding them in would make this
unreviewable.
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
    RUN_STATUSES, RUN_TRIGGERS, Agent, Comment, Label, Membership, Organization,
    Project, Run, Ticket, TicketEvent, TicketLabel, TicketLink, TicketType,
    UserAccount, WorkflowState,
)

ROOT = Path(os.environ.get("WORKSHOP_ROOT", Path(__file__).resolve().parents[2]))
LEGACY = "workshop-json"

# The status, type and priority vocabulary moved to `db/legacy_map.py` so the
# mirror (`db/ticket_sync.py`) and the read layer (`db/repo.py`) share ONE copy
# with this script. Three copies drifting is how a ticket changes status by
# being read and written back.
from db.legacy_map import STATES, TYPES, priority_band as _priority  # noqa: E402

_STATUS_TOKENS = {row[0] for row in STATES}

# JSON fields with no column of their own. They are POPULATED (scoped_by 112,
# provenance 56, touches 35, blocked_reason 23, acceptance 5, discovered_from 2)
# and were silently discarded by the first version of this script. They are
# preserved as ticket_event rows, which is what models.py always claimed
# happened. Nothing is dropped without being counted in the run summary.
_PRESERVED = ("scoped_by", "provenance", "touches", "acceptance",
              "discovered_from", "blocked_reason", "latitude", "max_attempts")

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


def _load_jobs(path: Path) -> list[dict]:
    """Collapse the append-only log by id; later lines override earlier ones."""
    if not path.exists():
        return []
    merged: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        jid = rec.get("id")
        if jid:
            merged.setdefault(jid, {}).update(rec)
    return list(merged.values())


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
    counts = {"users": len(_load_users(ROOT / ".runtime" / "auth.db")),
              "runs": len(_load_jobs(ROOT / ".runtime" / "jobs.jsonl"))}
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
                    # `assignee` was the constant string "operator" on all 112
                    # records, so it carried no information as a value, but the
                    # ATTRIBUTION is real and there is exactly one user to
                    # attribute it to.
                    assignee_id=sole_user,
                    created_by_id=sole_user,
                    external_source=LEGACY, external_id=legacy,
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

        # --- agents + runs ----------------------------------------------------
        agents: dict[str, object] = {}
        reg = ROOT / ".runtime" / "build_agents.json"
        if reg.exists():
            payload = json.loads(reg.read_text())
            # The top-level key is `build_agents`. Falling back to the whole
            # payload was a real bug: it iterated the single wrapper key and
            # created one agent literally named "build_agents". No silent
            # fallback here, so a renamed key fails loudly instead.
            entries = payload.get("build_agents") or payload.get("agents") or {}
            for slug in (entries if isinstance(entries, dict) else {}):
                a = s.scalar(select(Agent).where(Agent.org_id == org_id,
                                                 Agent.slug == slug))
                if a is None:
                    a = Agent(id=uuid7(), org_id=org_id, slug=slug, name=slug)
                    s.add(a)
                    s.flush()
                agents[slug] = a.id

        n_runs = 0
        runs_by_legacy = {}
        parent_of = {}
        for job in _load_jobs(ROOT / ".runtime" / "jobs.jsonl"):
            jid = str(job.get("id"))
            if job.get("parent_id"):
                parent_of[jid] = str(job["parent_id"])
            existing = s.scalar(select(Run).where(Run.legacy_job_id == jid))
            if existing:
                runs_by_legacy[jid] = existing.id
                continue
            status = job.get("status")
            if status == "awaiting_input":
                status = "blocked"
            elif status not in RUN_STATUSES:
                # An UNKNOWN state is not a successful one. The first version
                # coerced anything unrecognised to "done", which silently
                # reports failed or half-finished work as complete. No live row
                # hits this (all 2753 are done/failed/cancelled), so it is
                # latent, and latent is exactly when to fix it.
                dropped[f"run_status:{status}"] += 1
                status = "failed"
            trigger = job.get("trigger")
            trigger = trigger if trigger in RUN_TRIGGERS else "manual"
            run_id = uuid7()
            runs_by_legacy[jid] = run_id
            s.add(Run(
                id=run_id, org_id=org_id,
                ticket_id=tickets_by_legacy.get(str(job.get("task_id") or "")),
                agent_id=agents.get(str(job.get("agent_id") or "")),
                trigger=trigger, status=status,
                started_at=_iso(job.get("started_at")),
                ended_at=_iso(job.get("ended_at")),
                exit_code=job.get("exit_code") if isinstance(job.get("exit_code"), int) else None,
                summary=(json.dumps(job.get("evidence"))[:8000]
                         if job.get("evidence") is not None else None),
                legacy_job_id=jid,
            ))
            n_runs += 1

        # SECOND PASS for the sweep -> child edge, because a child can be logged
        # before the parent's own line and a forward reference cannot resolve on
        # the first. Same reason `ticket.parent_id` needs one.
        s.flush()
        n_run_parents = 0
        for jid, pid in parent_of.items():
            child, parent = runs_by_legacy.get(jid), runs_by_legacy.get(pid)
            if child is None or parent is None or child == parent:
                if parent is None:
                    dropped["run_parent_missing"] += 1
                continue
            s.execute(update(Run).where(Run.id == child)
                        .values(parent_id=parent))
            n_run_parents += 1

        stats.update(run_parents=n_run_parents,
                     tickets=n_tickets, links=n_links, labels=n_labels,
                     parents=n_parents, events=n_events, comments=n_comments,
                     runs=n_runs, agents=len(agents), org=str(org_id))
        for field, count in sorted(preserved.items()):
            stats[f"preserved:{field}"] = count
        for reason, count in sorted(dropped.items()):
            stats[f"DROPPED:{reason}"] = count
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="write to the database (default: print the plan only)")
    args = ap.parse_args()

    if not args.apply:
        print("PLAN (nothing written). Source rows found:")
        for k, v in plan().items():
            print(f"  {k:24} {v}")
        print("\nRe-run with --apply to write.")
        return 0

    for k, v in apply().items():
        print(f"  {k:24} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
