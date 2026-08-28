#!/usr/bin/env python3
"""Remove rows the TEST SUITE wrote into the operator's real database.

WHY THIS EXISTS. `backend/tests/conftest.py` guarded teardown (`_delete_test_orgs`
refuses to delete an organization without the `wstest-` prefix) and nothing
guarded creation, so a test that never minted an org at all simply inherited the
real one. Measured 2026-08-27, in the live database:

    worktree           289 rows, EVERY ONE under a fixture's project slug,
                       at /tmp/pytest-of-* paths, plus 577 timeline events
    lease              174 rows held by `job-1`..`job-4`, which is a fixture's
                       holder id and not the format a real sweep uses
    organization       17 leaked `wstest-*` tenants, one per interrupted fixture
    user_account       17 accounts in the reserved `example.test` domain, left
                       behind because nothing cascades to `user_account`

Run once on 2026-08-27, which took the tables to 1 organization, 1 account,
1 worktree (soft-deleted, from a verification script, and outside every
criterion here) and 17 leases, all of them real.

The cost was never disk. `/branches` reads the `worktree` table, so the page the
operator opens to see what is stuck was describing pytest's temporary
directories: `live=true` returned exactly one row, pointing at a /tmp path that
had not existed for hours, while eleven real abandoned worktrees sat on disk.

`conftest._no_test_may_resolve_the_real_tenant` is what stops this recurring.
This script is the one-time cleanup, kept because a guard that has just been
added has not yet been proven, and because "what would we delete" is a question
worth being able to ask again without writing the SQL from memory.

EVERY CRITERION IS A POSITIVE IDENTIFICATION, and that is the whole design. It
deletes a row because of something the row SAYS about itself, never because of
its age, its shape or the fact that nothing else references it:

    orgs        `organization.slug` starts with `wstest-`, the prefix
                conftest mints test tenants under and refuses to delete without
    worktrees   `project_slug = 'test-proj'` (a fixture's literal slug) or
                `path` under `/tmp/pytest-`, which is pytest's own tmp root
    leases      the lease of a worktree deleted above, or a holder id matching
                `job-<digits>`. A real sweep's holder id is
                `job_<epoch-ms>_<hex>`; `job-1` is only ever written by a test.
    users       email in `example.test`, which RFC 2606 reserves so no real
                address can end in it, AND with no membership remaining

DRY RUN BY DEFAULT. It prints counts before, what each criterion matches, and
counts after, and changes nothing without `--apply`.

    python3 scripts/prune_test_fixture_rows.py            # report only
    python3 scripts/prune_test_fixture_rows.py --apply

Needs the OWNER credential (`WORKSHOP_DATABASE_ADMIN_URL`, which lives beside
the other instance secrets and the backend service deliberately does not load):
deleting an organization is provisioning, and the application role cannot do it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text  # noqa: E402

# Each criterion, as the SQL that identifies it plus the sentence that justifies
# it. Kept together on purpose: a delete whose reason lives somewhere else is how
# a cleanup script grows a clause nobody can defend.
ORG_TEST_PREFIX = "wstest-"
TEST_EMAIL_DOMAIN = "@example.test"

WORKTREE_WHERE = (
    "project_slug = 'test-proj' OR path LIKE '/tmp/pytest-%'"
)
LEASE_HOLDER_WHERE = (
    # `job-1`, not `job_1787476680276_543f`. The separator is the tell.
    r"holder_id ~ '^job-[0-9]+$'"
)


def _counts(s) -> dict:
    return {
        "organization": s.execute(text("SELECT count(*) FROM organization")).scalar(),
        "user_account": s.execute(text("SELECT count(*) FROM user_account")).scalar(),
        "worktree": s.execute(text("SELECT count(*) FROM worktree")).scalar(),
        "worktree_event": s.execute(text("SELECT count(*) FROM worktree_event")).scalar(),
        "lease": s.execute(text("SELECT count(*) FROM lease")).scalar(),
    }


def _report(label: str, counts: dict) -> None:
    print(f"\n{label}")
    for k, v in counts.items():
        print(f"  {k:<16} {v}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it nothing is written")
    args = ap.parse_args()

    if not os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip():
        print("WORKSHOP_DATABASE_ADMIN_URL is not set. Deleting an organization "
              "is provisioning and the application role cannot do it; source "
              "the db-admin env file first.", file=sys.stderr)
        return 2

    from db.engine import owner_session

    with owner_session() as s:
        before = _counts(s)
        _report("BEFORE", before)

        orgs = s.execute(text(
            "SELECT id, slug FROM organization WHERE slug LIKE :p"),
            {"p": f"{ORG_TEST_PREFIX}%"}).all()
        wts = s.execute(text(
            f"SELECT id, project_slug, path FROM worktree WHERE {WORKTREE_WHERE}")).all()
        wt_ids = [str(r[0]) for r in wts]
        wt_events = s.execute(text(
            f"SELECT count(*) FROM worktree_event WHERE worktree_id IN "
            f"(SELECT id FROM worktree WHERE {WORKTREE_WHERE})")).scalar()
        leases_by_holder = s.execute(text(
            f"SELECT count(*) FROM lease WHERE {LEASE_HOLDER_WHERE}")).scalar()
        leases_by_subject = s.execute(text(
            "SELECT count(*) FROM lease WHERE subject_kind = 'worktree' "
            "AND subject_id = ANY(:ids)"), {"ids": wt_ids}).scalar() if wt_ids else 0
        # AFTER the orgs above are gone, which is the only time this is true:
        # `membership` cascades with its organization, so a fixture's user looks
        # perfectly well-attached right up until its throwaway org is deleted,
        # and a query run before the delete matches nothing. `user_account`
        # carries no `org_id` (it is the one tenant table that cannot, since a
        # user exists before any membership), so nothing cascades to it and this
        # is what stops 17 orphan accounts replacing 17 orphan organizations.
        org_ids = [str(o[0]) for o in orgs]
        users = s.execute(text(
            "SELECT id, email FROM user_account u WHERE u.email LIKE :d "
            "AND NOT EXISTS (SELECT 1 FROM membership m WHERE m.user_id = u.id "
            "AND NOT (m.org_id = ANY(CAST(:orgs AS uuid[]))))"),
            {"d": f"%{TEST_EMAIL_DOMAIN}", "orgs": org_ids}).all()

        print("\nMATCHED (every row below says what it is)")
        print(f"  organizations with the {ORG_TEST_PREFIX!r} prefix: {len(orgs)}")
        for _, slug in orgs[:40]:
            print(f"      {slug}")
        print(f"  worktrees under a fixture slug or a /tmp/pytest- path: {len(wts)}")
        for _, slug, path in wts[:5]:
            print(f"      {slug}  {path}")
        if len(wts) > 5:
            print(f"      ... and {len(wts) - 5} more")
        print(f"  worktree_events belonging to those trees: {wt_events}")
        print(f"  leases held by a `job-<n>` fixture id: {leases_by_holder}")
        print(f"  leases on those worktrees: {leases_by_subject}")
        print(f"  accounts in {TEST_EMAIL_DOMAIN} left with no org: {len(users)}")

        if not args.apply:
            print("\nDRY RUN. Nothing was written. Re-run with --apply.")
            return 0

        # Leases first: they name worktrees by id, and a lease whose subject has
        # already gone is a row nothing can identify any more.
        if wt_ids:
            s.execute(text("DELETE FROM lease WHERE subject_kind = 'worktree' "
                           "AND subject_id = ANY(:ids)"), {"ids": wt_ids})
        s.execute(text(f"DELETE FROM lease WHERE {LEASE_HOLDER_WHERE}"))
        # `worktree_event` cascades on the worktree's FK, so it goes with it.
        s.execute(text(f"DELETE FROM worktree WHERE {WORKTREE_WHERE}"))
        if orgs:
            s.execute(text("DELETE FROM organization WHERE id = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": org_ids})
        if users:
            s.execute(text("DELETE FROM user_account WHERE id = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": [str(u[0]) for u in users]})
        s.flush()

        after = _counts(s)
        _report("AFTER", after)
        print("\nDELTA")
        for k in before:
            print(f"  {k:<16} {before[k]} -> {after[k]}  ({after[k] - before[k]:+d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
