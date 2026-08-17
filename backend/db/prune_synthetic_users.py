"""Remove `user_account` rows that only tests ever created.

WHY THIS EXISTS. `user_account` holds 3,727 rows and exactly ONE of them is a
person. The other 3,726 have an `@example.test` address, no membership row, and
were minted a few hundred a day starting 2026-08-14 by test runs that
provisioned an owner against the real database. It is the same failure the
learning `tests-must-not-share-owner-storage` records for the file stores, and
the same one `db/prune_test_threads.py` cleans up on the chat side. They are
invisible to the application -- RLS keys on a SHARED MEMBERSHIP and these rows
have none -- which is exactly why nobody noticed for three days.

NOTE THE COUNT IS STILL RISING (1,937 of them dated 2026-08-17). This script
removes what leaked; it does not close the leak. Pruning without fixing the test
fixture buys a few days.

WHAT MAKES A ROW SAFE TO DELETE. Four conditions, ALL required:

  1. The address is in `example.test`. RFC 2606 reserves that TLD as
     permanently unresolvable, so no account a person could receive mail at can
     live there. This is a signature, not a heuristic.
  2. No `membership` row. A user with no membership cannot be seen through any
     tenant session, so it is not participating in anything.
  3. NOTHING REFERENCES IT. Not "nothing I remembered to check": the referring
     columns are read out of the live catalog (`_referring_columns`), so a table
     added later that points at `user_account` is covered without anyone
     remembering to edit this file. Most of those FKs are `ON DELETE SET NULL`,
     which means a careless delete here would not fail -- it would quietly
     erase the author of a comment or the actor on an audit row. That is
     unrecoverable, and it is the reason this condition is derived rather than
     listed.
  4. `legacy_user_id` IS NULL. That column is the bridge to the SQLite
     credential store in `.runtime/auth.db`; a row holding one is claiming to
     shadow a real login, and whether it does is a question about a different
     database. 100 of the synthetic rows carry one, and they are REPORTED AND
     KEPT rather than deleted.

Anything meeting 1 but failing 2, 3 or 4 is reported and kept, the same way
`prune_test_threads.py` keeps an orphaned thread that does not match a fixture
signature. Leaving junk behind is cheap; deleting the wrong identity row is not.

Dry run unless `--apply`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The reserved domain the test fixtures mint into. Matched on the whole domain,
# never as a substring: `%example.test` would also match `notexample.test`, and
# `%example%` would match a real address at `example-corp.com`.
_SYNTHETIC_DOMAIN = "example.test"

_TABLE = "user_account"


def _referring_columns(session) -> list[tuple[str, str]]:
    """Every (table, column) in the live schema with an FK into `user_account`.

    Derived, so condition 3 cannot go stale. A composite FK contributes each of
    its constrained columns; over-checking is free here and under-checking is
    the failure this guards against.
    """
    from sqlalchemy import text

    rows = session.execute(text("""
        SELECT child.relname AS tbl, att.attname AS col
          FROM pg_constraint con
          JOIN pg_class child  ON child.oid = con.conrelid
          JOIN pg_class parent ON parent.oid = con.confrelid
          JOIN unnest(con.conkey) AS k(attnum) ON true
          JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = k.attnum
         WHERE con.contype = 'f' AND parent.relname = :t
         ORDER BY 1, 2
    """), {"t": _TABLE}).all()
    # A composite FK into user_account would also list its `org_id` leg, which
    # is not a user reference. Only columns whose target is `user_account.id`
    # matter, and every such leg is the one named in `conkey` opposite `id`;
    # filtering by type keeps this simple and errs toward checking more.
    return [(r[0], r[1]) for r in rows if r[1] != "org_id"]


def _report(session, refs: list[tuple[str, str]]) -> dict:
    from sqlalchemy import text

    ref_counts = "".join(
        f", (SELECT count(*) FROM {t} x WHERE x.{c} = u.id) AS ref_{t}_{c}"
        for t, c in refs)
    ref_sum = " + ".join(
        f"(SELECT count(*) FROM {t} x WHERE x.{c} = u.id)" for t, c in refs
    ) or "0"

    rows = session.execute(text(f"""
        SELECT u.id, u.email, u.legacy_user_id, u.created_at,
               ({ref_sum}) AS refs{ref_counts}
          FROM {_TABLE} u
         WHERE lower(u.email) LIKE :pat
    """), {"pat": f"%@{_SYNTHETIC_DOMAIN}"}).mappings().all()

    total = session.scalar(text(f"SELECT count(*) FROM {_TABLE}"))
    deletable, kept = [], []
    for r in rows:
        if r["refs"] == 0 and r["legacy_user_id"] is None:
            deletable.append(r)
        else:
            kept.append(r)
    return {"total": int(total or 0), "synthetic": rows,
            "deletable": deletable, "kept": kept, "refs": refs}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="delete; without it nothing is written")
    args = ap.parse_args()

    # Same reasoning as prune_test_threads: the admin DSN lives in
    # ~/.config/workshop/db-admin.env and reaches the backend through
    # EnvironmentFile=, never an interactive shell.
    from db.script_env import load_config_env
    load_config_env()

    from sqlalchemy import text

    from db.engine import owner_session

    with owner_session() as s:
        refs = _referring_columns(s)
        if not refs:
            print(f"refusing to run: no foreign keys into {_TABLE} were found, "
                  "so every row would look unreferenced. Check the connection.")
            return 2
        r = _report(s, refs)

        print(f"referring columns checked ({len(refs)}): "
              + ", ".join(f"{t}.{c}" for t, c in refs))
        print(f"rows in {_TABLE}: {r['total']}")
        print(f"  real accounts (not @{_SYNTHETIC_DOMAIN}): "
              f"{r['total'] - len(r['synthetic'])}")
        print(f"  synthetic (@{_SYNTHETIC_DOMAIN}): {len(r['synthetic'])}")
        print(f"  ...unreferenced and unbridged (deletable): {len(r['deletable'])}")
        print(f"  ...referenced or bridged, kept for review: {len(r['kept'])}")

        by_reason: dict[str, int] = {}
        for m in r["kept"]:
            if m["legacy_user_id"] is not None:
                by_reason["legacy_user_id is set"] = \
                    by_reason.get("legacy_user_id is set", 0) + 1
            for t, c in refs:
                if m[f"ref_{t}_{c}"]:
                    key = f"referenced by {t}.{c}"
                    by_reason[key] = by_reason.get(key, 0) + 1
        for reason, n in sorted(by_reason.items()):
            print(f"      KEEP {n:5}  {reason}")

        if not r["deletable"]:
            print("nothing to do")
            return 0

        # By construction the deletable set cannot contain a real address, but
        # this is the assertion whose failure would be catastrophic and silent,
        # so it is checked rather than reasoned about.
        stray = [m["email"] for m in r["deletable"]
                 if not m["email"].lower().endswith(f"@{_SYNTHETIC_DOMAIN}")]
        if stray:
            print(f"refusing to run: {len(stray)} non-synthetic addresses in "
                  f"the deletable set, first {stray[0]!r}")
            return 2

        if not args.apply:
            print(f"\ndry run; pass --apply to delete {len(r['deletable'])} rows")
            return 0

        ids = [m["id"] for m in r["deletable"]]
        done = s.execute(text(f"DELETE FROM {_TABLE} WHERE id = ANY(:ids)"),
                         {"ids": ids})
        s.commit()
        print(f"deleted {done.rowcount:5} from {_TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
