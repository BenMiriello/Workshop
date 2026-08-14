"""Reads of the work model, in the record shape the JSON store returns.

WHY A COMPATIBILITY SHAPE. 18 Python modules and every shell worker consume
`services/tasks.py` records: a flat dict with `id`, `title`, `body`, `status`,
`text` and the rest. P3 replaces that module's internals without changing its
signatures, so this layer returns the same dict rather than an ORM object. When
the callers have migrated, this shape goes and the ORM objects surface directly.

WHAT DELIBERATELY DIFFERS from the JSON record, and why:

  `priority`  is now one of urgent/high/medium/low/none, not a signed integer.
              The integer held 38 distinct values across 112 tickets and none of
              the distinctions were meant. `shadow.py` knows to compare bands
              rather than values.

  `id`        is the LEGACY uuid, not the new primary key. Every caller, every
              shell script and every row in TODO.md refers to tickets by the old
              id, so the compatibility shape keeps it. `pk` carries the new one
              for anything that needs to join.

  dropped     `latitude`, `max_attempts`, `sync`, `external_refs`,
              `discovered_from`, `owner`, `assignee`, `exec`, `blocked_request`.
              All were 0% occupancy or a mirror of another field. Nothing reads
              them; `shadow.py` asserts that by comparing every field that is
              kept and listing what is not.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from db.engine import tenant_session
from db.legacy_map import STATE_TO_STATUS

__all__ = ["list_tickets", "get_ticket", "next_ready", "LEGACY_STATUS"]

# The state names the backfill seeded, mapped back to the token the JSON store
# used. Only the compatibility layer needs this: a tenant that renames "Todo" to
# "Up next" keeps working everywhere else, and this map is deleted with the rest
# of the shape once callers speak in states. Shared with the backfill and the
# mirror via `legacy_map`, so the three cannot disagree.
LEGACY_STATUS = STATE_TO_STATUS

# One statement, because the legacy record is a join: the state's name, the
# type's name, and both link kinds aggregated. Doing this per row would be the
# N+1 the JSON store had no way to avoid.
_SELECT = """
SELECT
  t.id                       AS pk,
  t.external_id              AS legacy_id,
  p.slug                     AS scope,
  p.key                      AS project_key,
  t.number                   AS number,
  t.title                    AS title,
  t.body                     AS body,
  ws.name                    AS state_name,
  ws.category                AS state_category,
  t.priority::text           AS priority,
  t.effort                   AS effort,
  ty.name                    AS type_name,
  ty.agent_executable        AS agent_executable,
  t.created_at               AS created,
  t.updated_at               AS updated,
  t.closed_at                AS closed,
  t.source_kind              AS source_kind,
  t.source_ref               AS source_ref,
  t.spec_hash                AS spec_hash,
  t.touches                  AS touches,
  t.blocked_reason           AS blocked_reason,
  t.acceptance               AS acceptance,
  t.claim_token              AS claim_token,
  parent.external_id         AS parent_legacy,
  COALESCE((SELECT array_agg(d.external_id ORDER BY d.external_id)
            FROM ticket_link l JOIN ticket d ON d.id = l.to_ticket_id
            WHERE l.from_ticket_id = t.id AND l.kind = 'blocked_by'
              AND l.deleted_at IS NULL), '{}') AS depends_on,
  COALESCE((SELECT array_agg(d.external_id ORDER BY d.external_id)
            FROM ticket_link l JOIN ticket d ON d.id = l.to_ticket_id
            WHERE l.from_ticket_id = t.id AND l.kind = 'relates_to'
              AND l.deleted_at IS NULL), '{}') AS relates_to,
  -- Comments come back with the ticket because the JSON record carried them
  -- inline and every caller expects them there. The read-shadow caught this:
  -- five live comments existed in both stores and the compatibility shape
  -- returned none of them, so a cutover would have dropped them from the UI
  -- while the rows sat in the database looking healthy.
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
                     'id', c.external_id, 'author', c.author_type,
                     'text', c.body, 'created', c.created_at)
                   ORDER BY c.created_at)
            FROM comment c
            WHERE c.ticket_id = t.id AND c.deleted_at IS NULL), '[]') AS comments,
  COALESCE((SELECT array_agg(l.name ORDER BY l.name)
            FROM ticket_label tl JOIN label l ON l.id = tl.label_id
            WHERE tl.ticket_id = t.id AND l.deleted_at IS NULL), '{}') AS labels,
  -- `evidence` and `scoped_by` are history rows, reassembled into the shape the
  -- JSON record carried. They are read far more often than written, so the cost
  -- sits here rather than in a second round trip per ticket.
  COALESCE((SELECT jsonb_agg(jsonb_build_object('kind', e.field, 'ref', e.new_value)
                             ORDER BY e.created_at)
            FROM ticket_event e
            WHERE e.ticket_id = t.id AND e.verb = 'noted'), '[]') AS evidence,
  (SELECT e.new_value FROM ticket_event e
    WHERE e.ticket_id = t.id AND e.verb = 'scoped'
    ORDER BY e.created_at LIMIT 1) AS scoped_by,
  -- `attempts` was a hand-maintained counter on the ticket, and it had drifted:
  -- 11 of 98 live tickets stored a value LOWER than their real run count, one
  -- claiming 2 attempts against 5 runs that all started and all finished
  -- done-or-failed. Deriving it is a correction, not a translation.
  --
  -- Restricted to runs that STARTED, which is what "an attempt was made" means.
  -- Identical to a bare count(*) on today's data (every run with a ticket has a
  -- started_at), so nothing changes now, and it stays right the first time a
  -- run is queued and never picked up.
  (SELECT count(*) FROM run r
    WHERE r.ticket_id = t.id AND r.started_at IS NOT NULL) AS attempts,
  COALESCE((SELECT array_agg(r.legacy_job_id ORDER BY r.started_at)
            FROM run r
            WHERE r.ticket_id = t.id AND r.legacy_job_id IS NOT NULL), '{}')
    AS job_ids
FROM ticket t
JOIN project p         ON p.id  = t.project_id
JOIN workflow_state ws ON ws.id = t.state_id
LEFT JOIN ticket_type ty ON ty.id = t.type_id
LEFT JOIN ticket parent  ON parent.id = t.parent_id
WHERE t.deleted_at IS NULL
"""


def _row_to_legacy(row: Any) -> dict:
    m = row._mapping
    title = m["title"] or ""
    body = m["body"] or ""
    return {
        "id": m["legacy_id"] or str(m["pk"]),
        "pk": str(m["pk"]),
        "ref": f"{m['project_key']}-{m['number']}",
        "scope": m["scope"],
        "title": title,
        "body": body,
        # `text` stayed a projection of the pair, exactly as it was.
        "text": f"{title}\n{body}".rstrip("\n") if body else title,
        "status": LEGACY_STATUS.get(m["state_name"], m["state_name"]),
        "state": m["state_name"],
        "state_type": "canceled" if m["state_category"] == "canceled" else m["state_category"],
        "priority": m["priority"],
        "effort": m["effort"],
        "type": m["type_name"],
        "agent_executable": bool(m["agent_executable"]),
        "parent": m["parent_legacy"],
        "depends_on": list(m["depends_on"] or []),
        "relates_to": list(m["relates_to"] or []),
        "comments": list(m["comments"] or []),
        # `source` is rebuilt as the pair the JSON record carried, because every
        # caller reads `task["source"]["kind"]`. The columns are what make it
        # queryable; this is the compatibility shape on top of them.
        "source": {"kind": m["source_kind"], "ref": m["source_ref"]},
        "provenance": m["spec_hash"],
        "touches": list(m["touches"] or []),
        "blocked_reason": m["blocked_reason"],
        "acceptance": m["acceptance"],
        "claim_token": m["claim_token"],
        "labels": list(m["labels"] or []),
        "evidence": list(m["evidence"] or []),
        "scoped_by": m["scoped_by"],
        "attempts": int(m["attempts"] or 0),
        "job_ids": list(m["job_ids"] or []),
        # Both were the literal string "operator" on all 112 tickets: two
        # fields, one concept, no information (ADR 0019 R4). They are
        # SYNTHESISED here so the 18 callers that read `task["assignee"]` keep
        # working, and they go when those callers do. `ticket.assignee_id` is
        # the real column for when assignment starts meaning something.
        "assignee": "operator",
        "owner": "operator",
        "created": m["created"].isoformat() if m["created"] else None,
        "updated": m["updated"].isoformat() if m["updated"] else None,
        "closed": m["closed"].isoformat() if m["closed"] else None,
    }


def list_tickets(org_id: UUID | str, *, scope: str | None = None,
                 include_done: bool = True) -> list[dict]:
    """Every ticket in the organization, newest first, in the legacy shape."""
    sql = _SELECT
    params: dict[str, Any] = {}
    if scope:
        sql += " AND p.slug = :scope"
        params["scope"] = scope
    if not include_done:
        sql += " AND ws.category NOT IN ('completed','canceled')"
    sql += " ORDER BY t.updated_at DESC"

    with tenant_session(org_id) as s:
        return [_row_to_legacy(r) for r in s.execute(text(sql), params).all()]


def get_ticket(org_id: UUID | str, legacy_id: str) -> dict | None:
    with tenant_session(org_id) as s:
        row = s.execute(text(_SELECT + " AND t.external_id = :lid"),
                        {"lid": legacy_id}).one_or_none()
    return _row_to_legacy(row) if row is not None else None


# The scheduler, as one statement.
#
# `unattended` adds the two predicates the JSON scheduler had to compute in
# Python over every record: the type must be agent-executable, and no blocked_by
# edge may point at a ticket that is not finished.
#
# `t.closed_at IS NULL` is redundant with the category filter and is here
# anyway, because `ix_ticket_queue` is a PARTIAL index predicated on
# `closed_at IS NULL AND deleted_at IS NULL`. Postgres will only use a partial
# index when it can PROVE the query implies the predicate, and it cannot derive
# "not closed" from a join to `workflow_state.category`. Without this line the
# plan is a sequential scan, which a reviewer confirmed with EXPLAIN: the index
# built for this query could not serve it.
#
# The ORDER BY is TABLE-QUALIFIED on purpose: an unqualified `priority` would
# bind to the `priority::text` output column above and sort alphabetically. See
# db/models.PRIORITIES.
_READY = _SELECT + """
  AND t.closed_at IS NULL
  AND ws.category IN ('backlog','unstarted')
  AND ws.name <> 'Draft'
  AND NOT EXISTS (
        SELECT 1 FROM ticket_link l
        JOIN ticket d          ON d.id  = l.to_ticket_id
        JOIN workflow_state ds ON ds.id = d.state_id
        WHERE l.from_ticket_id = t.id AND l.kind = 'blocked_by'
          AND l.deleted_at IS NULL
          AND ds.category NOT IN ('completed','canceled'))
"""


def next_ready(org_id: UUID | str, *, scope: str | None = None,
               unattended: bool = False, limit: int = 1) -> list[dict]:
    sql = _READY
    params: dict[str, Any] = {"lim": limit}
    if scope:
        sql += " AND p.slug = :scope"
        params["scope"] = scope
    if unattended:
        sql += " AND COALESCE(ty.agent_executable, false)"
    sql += " ORDER BY t.priority, t.created_at LIMIT :lim"

    with tenant_session(org_id) as s:
        return [_row_to_legacy(r) for r in s.execute(text(sql), params).all()]
