"""Mirror the JSON ticket store into the relational one on every write.

P3, first half. `.tasks.json` is still authoritative and every read still comes
from it; this keeps Postgres current so a read-shadow (`db/shadow.py`) can prove
the relational side agrees before anything is switched over. It is the same
staging the chat tree used, and for the same reason: a store nobody reads cannot
break the operator, and a store nobody WRITES cannot be trusted when it is time
to read it.

BEST-EFFORT, ALWAYS. Filing a ticket must not fail because Postgres is down or
absent, so every path here swallows its exception and logs. The JSON write has
already happened by the time this runs.

WHY A DIFF AND NOT A REPLACE. Rewriting all 98 rows on every save would be
cheap enough, but it would destroy `ticket_event`: the field-level history is
the one thing the relational store offers that the JSON file structurally could
not, and "who changed the priority" is currently unrecoverable. So this compares
field by field and records what actually changed. The history starts being real
from the first mirrored write.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from db.legacy_map import STATUS_TO_STATE, priority_band
from db.models import SOURCE_KINDS

log = logging.getLogger(__name__)


def _json(value):
    """A dict for a jsonb bind. psycopg will not adapt a plain dict to jsonb, so
    the value is serialised here and cast in the statement."""
    import json as _j
    return None if value is None else _j.dumps(value)

__all__ = ["mirror", "scope_for_root"]

# Fields worth a history row. Deliberately not every field: `updated` changes on
# every write by definition, and recording that it changed says nothing.
_TRACKED = ("title", "body", "status", "priority", "effort", "type", "parent",
            "source_kind", "source_ref", "spec_hash", "touches",
            "blocked_reason", "acceptance", "claim_token")

# Of those, the ones worth a history row when they change. A `spec_hash` moving
# is a restatement of a title or body change already recorded, and a
# `claim_token` bump is an execution event that `run` records properly, so
# writing history for either would be noise in the feed that history is for.
_HISTORIC = ("title", "body", "status", "priority", "effort", "type",
             "blocked_reason")

# The legacy `execution` values, as type names. `None` means no type, which is
# how the JSON store spelled "not classified".
_EXECUTION_TO_TYPE = {"code": "Code", "judgement": "Judgement",
                      "external": "External"}

# A chat thread id is a `uuid4().hex`. See `_legacy_view`.
_THREAD_ID = re.compile(r"[0-9a-f]{32}")


def scope_for_root(root: Path) -> str | None:
    """Which project a working tree's `.tasks.json` belongs to.

    Mirrors `backfill.SCOPES` rather than guessing: the repo root is the
    `development` scope and `shared/queue` is `workshop`. An unrecognised root
    returns None and is not mirrored, which is the honest answer for a checkout
    this installation does not know about.
    """
    from db.backfill import SCOPES
    try:
        resolved = root.resolve()
    except OSError:
        return None
    for slug, _name, _key, store_dir in SCOPES:
        try:
            if resolved == Path(store_dir).resolve():
                return slug
        except OSError:
            continue
    return None


def _lookup(sess, sql: str, **params) -> Any:
    # The session parameter is `sess`, not `s`, because `:s` is a bind name in
    # the callers below and the two collided silently as a kwarg clash.
    row = sess.execute(text(sql), params).one_or_none()
    return row[0] if row is not None else None


def _project(s, org_id, slug: str):
    return _lookup(s, "SELECT id FROM project WHERE org_id = :o AND slug = :s "
                      "AND deleted_at IS NULL", o=org_id, s=slug)


def _state_id(s, org_id, project_id, status: str):
    """The workflow state for a legacy status token, falling back to the
    project's default. A status this installation does not know about must not
    drop the ticket on the floor."""
    name = STATUS_TO_STATE.get(status or "todo")
    sid = None
    if name:
        sid = _lookup(s, "SELECT id FROM workflow_state WHERE org_id = :o "
                         "AND project_id = :p AND name = :n AND deleted_at IS NULL",
                      o=org_id, p=project_id, n=name)
    if sid is None:
        sid = _lookup(s, "SELECT id FROM workflow_state WHERE org_id = :o "
                         "AND project_id = :p AND is_default AND deleted_at IS NULL",
                      o=org_id, p=project_id)
    return sid


def _type_id(s, org_id, project_id, execution):
    name = _EXECUTION_TO_TYPE.get(execution or "")
    if not name:
        return None
    return _lookup(s, "SELECT id FROM ticket_type WHERE org_id = :o "
                      "AND project_id = :p AND name = :n AND deleted_at IS NULL",
                   o=org_id, p=project_id, n=name)


def _event(s, org_id, ticket_id, field, old, new) -> None:
    from db.ids import uuid7
    s.execute(text(
        "INSERT INTO ticket_event (id, org_id, ticket_id, actor_type, verb, "
        "field, old_value, new_value) "
        "VALUES (:i, :o, :t, 'system', 'changed', :f, :ov, :nv)"),
        {"i": uuid7(), "o": org_id, "t": ticket_id, "f": field,
         "ov": None if old is None else str(old)[:8000],
         "nv": None if new is None else str(new)[:8000]})


def _legacy_view(task: dict) -> dict:
    """The tracked fields of a JSON record, in the schema's vocabulary, so the
    comparison is like for like. Comparing a raw integer priority against an
    enum band would report a change on every single write."""
    source = task.get("source") if isinstance(task.get("source"), dict) else {}
    kind = (source.get("kind") or "").strip() or None
    ref = str(source.get("ref")).strip() if source.get("ref") else None
    # A CHAT REF IS A THREAD ID OR IT IS NOTHING. Before the convention settled,
    # `source.ref` held the SCOPE key for chat-filed tickets: 19 of the live 112
    # say `"general"`, which names a scope and not a conversation. Storing that
    # as a thread reference produces a link that resolves to whatever chat the
    # scope currently shows, which is worse than no link because it looks right.
    # A thread id is a `uuid4().hex`, so the shape is the test; anything else
    # keeps `kind='chat'` (it did come from a conversation) with no ref, which
    # is the true statement. The partial index skips those rows.
    if kind == "chat" and not _THREAD_ID.fullmatch(ref or ""):
        ref = None
    return {
        "title": (task.get("title") or "").strip(),
        "body": task.get("body") or "",
        "status": task.get("status") or "todo",
        "priority": priority_band(task.get("priority")),
        "effort": task.get("effort") or None,
        "type": _EXECUTION_TO_TYPE.get(task.get("execution") or "") or None,
        "parent": task.get("parent") or None,
        # An unrecognised source kind is dropped rather than stored, because the
        # CHECK would refuse it and losing a whole ticket write over a
        # provenance label would be the wrong trade.
        "source_kind": kind if kind in SOURCE_KINDS else None,
        "source_ref": ref,
        "spec_hash": task.get("provenance") or None,
        "touches": list(task.get("touches") or []) or None,
        "blocked_reason": task.get("blocked_reason") or None,
        "acceptance": task.get("acceptance") or None,
        "claim_token": int(task.get("claim_token") or 0),
    }


def _row_view(row) -> dict:
    m = row._mapping
    return {
        "title": m["title"] or "",
        "body": m["body"] or "",
        # The state's NAME; `_mirror` maps it back to a legacy token.
        "status": m["state_name"],
        "priority": m["priority"],
        "effort": m["effort"],
        "type": m["type_name"],
        "parent": m["parent_legacy"],
        "source_kind": m["source_kind"],
        "source_ref": m["source_ref"],
        "spec_hash": m["spec_hash"],
        "touches": list(m["touches"]) if m["touches"] else None,
        "blocked_reason": m["blocked_reason"],
        "acceptance": m["acceptance"],
        "claim_token": m["claim_token"],
    }


_CURRENT = """
SELECT t.id, t.external_id, t.title, t.body, t.priority::text AS priority,
       t.effort, t.closed_at, ws.name AS state_name, ty.name AS type_name,
       parent.external_id AS parent_legacy,
       t.source_kind, t.source_ref, t.spec_hash, t.touches, t.blocked_reason,
       t.acceptance, t.claim_token
  FROM ticket t
  JOIN workflow_state ws ON ws.id = t.state_id
  LEFT JOIN ticket_type ty ON ty.id = t.type_id
  LEFT JOIN ticket parent ON parent.id = t.parent_id
 WHERE t.org_id = :o AND t.project_id = :p AND t.deleted_at IS NULL
"""


# (legacy field, link kind). `discovered_from` was a scalar on the JSON record
# and is a list here, because an edge table has no reason to special-case one.
_LINK_KINDS = (("depends_on", "blocked_by"), ("relates_to", "relates_to"),
               ("discovered_from", "discovered_from"))


def _sync_links(s, org_id, from_pk, ids: dict, task: dict) -> int:
    """Bring one ticket's typed edges in line with its JSON lists.

    Soft-deletes an edge that went away rather than removing the row, matching
    how tickets themselves are retired: `ticket_event` and anything else that
    referenced the link stays readable. An edge naming a ticket this project
    does not have is skipped, not invented, because the JSON store allowed a
    dangling id and the schema does not.
    """
    from db.ids import uuid7

    changed = 0
    for legacy_field, kind in _LINK_KINDS:
        raw = task.get(legacy_field) or []
        # `discovered_from` is a bare id, the others are lists.
        if isinstance(raw, str):
            raw = [raw]
        want = {ids[t] for t in raw
                if isinstance(t, str) and t in ids and ids[t] != from_pk}
        rows = s.execute(text(
            "SELECT id, to_ticket_id FROM ticket_link WHERE org_id = :o "
            "AND from_ticket_id = :f AND kind = :k AND deleted_at IS NULL"),
            {"o": org_id, "f": from_pk, "k": kind}).all()
        have = {r._mapping["to_ticket_id"]: r._mapping["id"] for r in rows}

        for target in want - set(have):
            s.execute(text(
                "INSERT INTO ticket_link (id, org_id, from_ticket_id, "
                "to_ticket_id, kind) VALUES (:i, :o, :f, :t, :k)"),
                {"i": uuid7(), "o": org_id, "f": from_pk, "t": target, "k": kind})
            changed += 1
        stale = [lid for target, lid in have.items() if target not in want]
        if stale:
            s.execute(text("UPDATE ticket_link SET deleted_at = now() "
                           "WHERE id = ANY(:ids)"), {"ids": stale})
            changed += len(stale)
    return changed


def _sync_labels(s, org_id, project_id, ticket_pk, task: dict) -> int:
    """Bring a ticket's labels in line, creating the `label` row on first use.

    Labels are per-project and shared, so the name is looked up (or created)
    once and the join row is what changes per ticket. Two names are live across
    the store, `chat-reported` (18) and `design` (3).
    """
    from db.ids import uuid7

    want_names = {str(x).strip() for x in (task.get("labels") or [])
                  if str(x).strip()}
    have = {r._mapping["name"]: r._mapping["label_id"] for r in s.execute(text(
        "SELECT l.name, l.id AS label_id FROM ticket_label tl "
        "  JOIN label l ON l.id = tl.label_id "
        " WHERE tl.ticket_id = :t AND l.deleted_at IS NULL"),
        {"t": ticket_pk}).all()}
    changed = 0

    for name in want_names - set(have):
        label_id = _lookup(s, "SELECT id FROM label WHERE org_id = :o "
                              "AND project_id = :p AND name = :n "
                              "AND deleted_at IS NULL",
                           o=org_id, p=project_id, n=name)
        if label_id is None:
            label_id = uuid7()
            s.execute(text("INSERT INTO label (id, org_id, project_id, name) "
                           "VALUES (:i, :o, :p, :n)"),
                      {"i": label_id, "o": org_id, "p": project_id, "n": name})
        s.execute(text("INSERT INTO ticket_label (ticket_id, label_id, org_id) "
                       "VALUES (:t, :l, :o) ON CONFLICT DO NOTHING"),
                  {"t": ticket_pk, "l": label_id, "o": org_id})
        changed += 1

    stale = [lid for name, lid in have.items() if name not in want_names]
    if stale:
        s.execute(text("DELETE FROM ticket_label WHERE ticket_id = :t "
                       "AND label_id = ANY(:ids)"),
                  {"t": ticket_pk, "ids": stale})
        changed += len(stale)
    return changed


def _sync_history(s, org_id, ticket_pk, task: dict) -> int:
    """Write the two JSON fields that are history rather than state.

    `evidence` (136 items over 85 tickets) is an append-only worklog of
    `{kind, ref}` notes, which is exactly "an actor did a thing at a time".
    `scoped_by` (112/112) is who scoped the ticket, which is an event at
    creation, not a property that keeps being true.

    Matched on (verb, field, new_value) so a re-run inserts nothing. Evidence is
    append-only in the source, so nothing is ever retired here; an entry that
    vanished from the JSON list was a rewrite of history and the row stays.
    """
    from db.ids import uuid7

    have = {(r._mapping["verb"], r._mapping["field"], r._mapping["new_value"])
            for r in s.execute(text(
                "SELECT verb, field, new_value FROM ticket_event "
                " WHERE ticket_id = :t AND verb IN ('noted', 'scoped')"),
                {"t": ticket_pk}).all()}
    changed = 0
    wanted = []
    for e in (task.get("evidence") or []):
        if isinstance(e, dict) and e.get("ref"):
            wanted.append(("noted", str(e.get("kind") or "note")[:64],
                           str(e["ref"])[:8000]))
    if task.get("scoped_by"):
        wanted.append(("scoped", "scoped_by", str(task["scoped_by"])[:8000]))

    for verb, field, value in wanted:
        if (verb, field, value) in have:
            continue
        s.execute(text(
            "INSERT INTO ticket_event (id, org_id, ticket_id, actor_type, verb, "
            "field, new_value) VALUES (:i, :o, :t, 'system', :v, :f, :nv)"),
            {"i": uuid7(), "o": org_id, "t": ticket_pk, "v": verb, "f": field,
             "nv": value})
        have.add((verb, field, value))
        changed += 1
    return changed


def _author(value: str | None, user_id) -> tuple[str, Any]:
    """A legacy comment author string as an actor type, plus its actor id.

    "assistant" maps to `system`, NOT to `agent`, and a constraint is what
    settled it. `ck_comment_actor_is_consistent` requires an `agent` author to
    name WHICH agent, which is the whole reason that constraint exists: 117 rows
    once claimed an actor with no actor id. The JSON store recorded the literal
    word "assistant" and no identity, so there is no agent to name and `system`
    (a machine, unattributed) is the honest slot.

    `operator` is the human, and it must carry a user id for the same reason.
    `user` with a null id -- what the first backfill produced for these rows --
    was the one genuinely wrong answer: it claims a person wrote it and then
    declines to say which, which is exactly what the constraint forbids.
    """
    a = (value or "").strip().lower()
    if a in ("operator", "user", "human") and user_id is not None:
        return "user", user_id
    return "system", None


def _sync_comments(s, org_id, user_id, ticket_pk, task: dict) -> int:
    """Mirror a ticket's inline comments, matched on their legacy id.

    Insert-and-retire only: a comment's body is not updated in place because the
    JSON store has no edit path for one, so an apparent body change would mean
    the id was reused, and overwriting would be the wrong repair.
    """
    from db.ids import uuid7

    wanted = {str(c.get("id")): c for c in (task.get("comments") or [])
              if isinstance(c, dict) and c.get("id")}
    rows = s.execute(text(
        "SELECT id, external_id, body, author_type FROM comment "
        "WHERE org_id = :o AND ticket_id = :t AND deleted_at IS NULL"),
        {"o": org_id, "t": ticket_pk}).all()
    have = {r._mapping["external_id"]: r._mapping["id"] for r in rows
            if r._mapping["external_id"]}
    author_now = {r._mapping["external_id"]: r._mapping["author_type"]
                  for r in rows if r._mapping["external_id"]}
    changed = 0

    # The JSON store is authoritative, so a labelled comment whose actor
    # disagrees with it is corrected rather than left. This is a standing
    # invariant, not a one-off repair: it is also what heals the rows the first
    # backfill mis-attributed by deriving the author from the org's member
    # count instead of from the comment.
    for ext, c in wanted.items():
        if ext not in have:
            continue
        atype, auser = _author(c.get("author"), user_id)
        if author_now.get(ext) != atype:
            s.execute(text("UPDATE comment SET author_type = :at, "
                           "author_user_id = :au WHERE id = :i"),
                      {"at": atype, "au": auser, "i": have[ext]})
            changed += 1

    # ADOPTION, for the comments the backfill wrote before `external_id`
    # existed. They are real and they are the same comments; matching on body
    # and labelling them is a repair, whereas inserting alongside them would
    # duplicate every one and deleting them would lose content the JSON store
    # is the only other copy of. Self-healing: once labelled this never fires
    # again, and it cannot fire for a comment that already has an id.
    unlabelled = {(r._mapping["body"] or ""): r._mapping["id"] for r in rows
                  if not r._mapping["external_id"]}
    if unlabelled:
        for ext, c in wanted.items():
            if ext in have:
                continue
            pk = unlabelled.pop(c.get("text") or "", None)
            if pk is None:
                continue
            atype, auser = _author(c.get("author"), user_id)
            # The author is corrected here too. The first backfill derived it
            # from the org's member count rather than from the comment, so an
            # "assistant" comment was recorded as a human's or as `system`.
            s.execute(text("UPDATE comment SET external_source = 'workshop-json', "
                           "external_id = :ex, author_type = :at, "
                           "author_user_id = :au WHERE id = :i"),
                      {"ex": ext, "at": atype, "au": auser, "i": pk})
            have[ext] = pk
            changed += 1

    for ext, c in wanted.items():
        if ext in have:
            continue
        atype, auser = _author(c.get("author"), user_id)
        s.execute(text(
            "INSERT INTO comment (id, org_id, ticket_id, author_type, "
            "author_user_id, body, external_source, external_id) "
            "VALUES (:i, :o, :t, :at, :au, :b, 'workshop-json', :ex)"),
            {"i": uuid7(), "o": org_id, "t": ticket_pk, "at": atype,
             "au": auser, "b": c.get("text") or "", "ex": ext})
        changed += 1

    stale = [pk for ext, pk in have.items() if ext not in wanted]
    if stale:
        s.execute(text("UPDATE comment SET deleted_at = now() "
                       "WHERE id = ANY(:ids)"), {"ids": stale})
        changed += len(stale)
    return changed


def mirror(root: Path, tasks: list[dict]) -> dict:
    """Bring this project's rows in line with the JSON list it was just given.

    Returns a counts dict for the caller to log or a test to assert on. Never
    raises: a mirror failure is logged and the JSON store, which is still
    authoritative, carries on alone.
    """
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "events": 0,
             "skipped": 0}
    try:
        from services import tenancy
        if not tenancy.available():
            stats["skipped"] = len(tasks)
            return stats
        slug = scope_for_root(root)
        if slug is None:
            stats["skipped"] = len(tasks)
            return stats
        org_id, user_id = tenancy.org_and_user()
        return _mirror(org_id, user_id, slug, tasks, stats)
    except Exception:
        log.warning("ticket mirror failed for %s", root, exc_info=True)
        stats["skipped"] = len(tasks)
        return stats


def _mirror(org_id, user_id, slug: str, tasks: list[dict], stats: dict) -> dict:
    from db.engine import tenant_session
    from db.ids import uuid7
    from db.legacy_map import STATE_TO_STATUS

    now = datetime.now(timezone.utc)
    with tenant_session(org_id) as s:
        project_id = _project(s, org_id, slug)
        if project_id is None:
            stats["skipped"] = len(tasks)
            return stats

        rows = s.execute(text(_CURRENT), {"o": org_id, "p": project_id}).all()
        existing = {r._mapping["external_id"]: r for r in rows
                    if r._mapping["external_id"]}
        seen: set[str] = set()

        # PASS ONE: insert and update, without parents. A ticket's parent may
        # appear later in the same list, so the edge is set once every row
        # exists. Same reason the backfill needs two passes.
        for task in tasks:
            legacy_id = str(task.get("id") or "")
            if not legacy_id:
                continue
            seen.add(legacy_id)
            want = _legacy_view(task)
            row = existing.get(legacy_id)
            state_id = _state_id(s, org_id, project_id, want["status"])
            type_id = _type_id(s, org_id, project_id, task.get("execution"))
            closed = want["status"] in ("done", "cancelled")

            if row is None:
                number = s.execute(text(
                    "UPDATE project SET next_ticket_number = next_ticket_number + 1 "
                    "WHERE id = :p RETURNING next_ticket_number - 1"),
                    {"p": project_id}).scalar()
                s.execute(text(
                    "INSERT INTO ticket (id, org_id, project_id, number, title, "
                    "body, state_id, type_id, priority, effort, closed_at, "
                    "source_kind, source_ref, spec_hash, touches, "
                    "blocked_reason, acceptance, claim_token, "
                    "external_source, external_id) VALUES (:i, :o, :p, :n, :ti, "
                    ":bo, :st, :ty, CAST(:pr AS ticket_priority), :ef, :cl, "
                    ":sk, :sr, :sh, :tc, :br, CAST(:ac AS jsonb), :ct, "
                    "'workshop-json', :ex)"),
                    {"i": uuid7(), "o": org_id, "p": project_id, "n": number,
                     "ti": want["title"][:255] or "(untitled)", "bo": want["body"],
                     "st": state_id, "ty": type_id, "pr": want["priority"],
                     "ef": want["effort"], "cl": now if closed else None,
                     "sk": want["source_kind"], "sr": want["source_ref"],
                     "sh": want["spec_hash"], "tc": want["touches"],
                     "br": want["blocked_reason"],
                     "ac": _json(want["acceptance"]),
                     "ct": want["claim_token"],
                     "ex": legacy_id})
                stats["inserted"] += 1
                continue

            have = _row_view(row)
            have["status"] = STATE_TO_STATUS.get(have["status"], have["status"])
            changed = {k: (have.get(k), want[k]) for k in _TRACKED
                       if k != "parent" and have.get(k) != want[k]}
            if not changed:
                continue
            ticket_id = row._mapping["id"]
            s.execute(text(
                "UPDATE ticket SET title = :ti, body = :bo, state_id = :st, "
                "type_id = :ty, priority = CAST(:pr AS ticket_priority), "
                "effort = :ef, closed_at = :cl, source_kind = :sk, "
                "source_ref = :sr, spec_hash = :sh, touches = :tc, "
                "blocked_reason = :br, acceptance = CAST(:ac AS jsonb), "
                "claim_token = :ct WHERE id = :i"),
                {"ti": want["title"][:255] or "(untitled)", "bo": want["body"],
                 "st": state_id, "ty": type_id, "pr": want["priority"],
                 "ef": want["effort"],
                 "cl": (row._mapping["closed_at"] or now) if closed else None,
                 "sk": want["source_kind"], "sr": want["source_ref"],
                 "sh": want["spec_hash"], "tc": want["touches"],
                 "br": want["blocked_reason"],
                 "ac": _json(want["acceptance"]),
                 "ct": want["claim_token"],
                 "i": ticket_id})
            stats["updated"] += 1
            for field, (old, new) in changed.items():
                if field not in _HISTORIC:
                    continue
                _event(s, org_id, ticket_id, field, old, new)
                stats["events"] += 1

        # PASS TWO: parents, now that every row exists.
        ids = {r._mapping["external_id"]: r._mapping["id"]
               for r in s.execute(text(_CURRENT),
                                  {"o": org_id, "p": project_id}).all()}
        for task in tasks:
            legacy_id = str(task.get("id") or "")
            parent = task.get("parent") or None
            child_pk, parent_pk = ids.get(legacy_id), ids.get(parent or "")
            if child_pk is None or child_pk == parent_pk:
                continue
            s.execute(text("UPDATE ticket SET parent_id = :pp WHERE id = :i "
                           "AND parent_id IS DISTINCT FROM :pp"),
                      {"pp": parent_pk, "i": child_pk})

        # Typed edges, for EVERY ticket rather than only the ones with a parent.
        stats["links"] = 0
        stats["comments"] = 0
        stats["labels"] = 0
        stats["history"] = 0
        for task in tasks:
            pk = ids.get(str(task.get("id") or ""))
            if pk is not None:
                stats["links"] += _sync_links(s, org_id, pk, ids, task)
                stats["comments"] += _sync_comments(s, org_id, user_id, pk, task)
                stats["labels"] += _sync_labels(s, org_id, project_id, pk, task)
                stats["history"] += _sync_history(s, org_id, pk, task)

        # A ticket that left the JSON list is soft-deleted, never hard. The
        # events, comments and runs that reference it stay readable, which is
        # the whole reason the column exists.
        gone = [r._mapping["id"] for ext, r in existing.items() if ext not in seen]
        if gone:
            s.execute(text("UPDATE ticket SET deleted_at = :n WHERE id = ANY(:ids)"),
                      {"n": now, "ids": gone})
            stats["deleted"] = len(gone)

        s.commit()
    return stats
