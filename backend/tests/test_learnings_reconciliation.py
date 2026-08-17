"""The learnings store admits nothing unexamined, and can shed what it admitted.

WHY THIS EXISTS. Measured on the live `global` store 2026-08-16, before this
file existed:

- Two entries read, in full, `Ralph rejected: py_typecheck_fail` and
  `Ralph rejected: ts_typecheck_fail`. Thirty-three characters, no instruction,
  written by an autonomous loop on 2026-07-15 and injected into every agent
  prompt for a month.
- Two OTHER entries were the same restart guardrail, created 2026-07-15 and
  2026-08-14, both live, both injected. A revision ADDED a row because dedup was
  a hash of text that had been edited.
- Every entry read `retrieval_count: 75`, because `memory_block` stamped a
  retrieval on everything it rendered and the store was small enough that it
  rendered everything. `prune_stale` keeps an entry that is
  `recent OR reinforced OR retrieved`, so it could never archive anything, and
  its only caller was its own CLI.

Each test below pins one of those three, plus the guards that keep the fix from
becoming a new failure: a near-miss pair must NOT merge, and a rule the operator
wrote must NOT age out.

Every test redirects the store dir, for the reason
`test_learnings_reach_the_chat._isolate` documents at length: a write that is not
redirected lands in the live store and rides into every prompt in the workspace.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from services import learnings  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "learnings"
    d.mkdir()
    monkeypatch.setattr(learnings, "_dir", lambda: d)
    # Render bookkeeping is throttled by the store file's mtime, which in a test
    # is always "just now". Zero the window so a render's counters are visible.
    monkeypatch.setattr(learnings, "_RETRIEVAL_THROTTLE_SEC", 0)
    return d


def _texts(scope="global"):
    return [e["text"] for e in learnings.list_learnings(scope)]


def _archive(store: Path, scope="global"):
    p = store / "archive" / f"{scope}.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


# --- write-time reconciliation ----------------------------------------------
# The two live rows, verbatim (`.runtime/learnings/global.jsonl` rows 10 and 15
# as of 2026-08-16). Synthetic stand-ins are not good enough here: what has to be
# proved is that THESE two, which a real revision produced a month apart, resolve
# to one entry. They score 0.684 Jaccard.
_RULE = (
    "DO NOT claim a service restart succeeded without checking it actually happened. "
    "Verify with `bash scripts/apply.sh --check` or the pid in "
    "`.runtime/apply-last.json`, and state the actual outcome. (Revised 2026-08-13: "
    "previously said to use `nohup` rather than systemctl because systemctl was "
    "classifier-blocked. Measured: `systemctl --user restart` runs fine for agents on "
    "this box, and every Workshop service is a systemd --user unit, so the old wording "
    "steered agents off the correct mechanism onto one that leaves processes outside "
    "the unit's cgroup.)"
)
_REVISED = (
    "DO NOT claim a service restart succeeded without checking it actually happened. "
    "Verify: `bash scripts/apply.sh --check`, or the pid in `.runtime/apply-last.json`. "
    "State the actual outcome. (Revised 2026-08-13: this used to say \"use `nohup`, not "
    "systemctl, because systemctl gets classifier-blocked\". Measured on this box, "
    "`systemctl --user restart` runs fine for agents, and every Workshop service IS a "
    "systemd --user unit — so the old advice steered agents away from the correct "
    "mechanism toward one that leaves processes outside the unit's cgroup. That exact "
    "mistake once left the frontend unit crash-looping on a port a stray nohup vite was "
    "holding.)"
)


def test_a_revision_supersedes_the_rule_it_revises(store):
    """The live defect: the restart guardrail existed twice, a month apart."""
    first = learnings.record(_RULE, source="operator")
    second = learnings.record(_REVISED, source="operator")

    assert second["resolution"] == "supersede", second
    assert second["superseded"] == first["id"]

    live = _texts()
    assert len(live) == 1, "the revision landed beside the rule instead of over it"
    assert "Revised 2026-08-13" in live[0]

    archived = _archive(store)
    assert len(archived) == 1
    assert archived[0]["id"] == first["id"]
    assert archived[0]["superseded_by"] == second["id"]
    assert "superseded" in archived[0]["archived_reason"]


def test_the_winner_keeps_the_losers_provenance(store):
    """Superseding is not forgetting: every source the old row carried moves."""
    learnings.record(_RULE, source="operator")
    learnings.record(_RULE, source="reviewer")          # noop, +1 provenance
    out = learnings.record(_REVISED, source="operator")

    entry = learnings.list_learnings()[0]
    assert entry["id"] == out["id"]
    sources = {p["source"] for p in entry["provenance"]}
    assert {"operator", "reviewer"} <= sources
    assert entry["provenance_total"] >= 3


def test_an_exact_and_a_near_duplicate_are_still_noops(store):
    a = learnings.record(_RULE, source="operator")
    same = learnings.record(_RULE, source="cron")
    assert same["resolution"] == "noop" and same["id"] == a["id"]
    assert same["deduped"] is True
    assert len(_texts()) == 1


def test_an_elaboration_updates_the_entry_in_place(store):
    """A candidate that says everything the old one did, and more, is the same
    rule at more length: same id, text replaced, previous copy archived."""
    base = learnings.record(
        "Always run npm run typecheck from the repo root before claiming a "
        "frontend task is done.", source="reviewer")
    longer = learnings.record(
        "Always run npm run typecheck from the repo root before claiming a "
        "frontend task is done. Never use npx tsc: npx resolves a deprecated "
        "stub package that prints a message and exits 0, so the gate passes "
        "having checked nothing.", source="reviewer")

    assert longer["resolution"] == "update"
    assert longer["id"] == base["id"], "an update must keep the entry's identity"
    live = _texts()
    assert len(live) == 1 and "exits 0" in live[0]
    assert _archive(store)[0]["text"].endswith("is done.")


def test_two_status_lines_differing_by_one_token_never_merge(store):
    """The guard that makes the 0.60 revision threshold safe.

    `Ralph rejected: py_typecheck_fail` and `..._ts_typecheck_fail` score 0.667
    Jaccard — above the revision band — and are two DIFFERENT failure modes.
    Short texts have too few tokens for overlap to mean anything, so the revision
    band requires substance on both sides. (The quality gate refuses both of
    these anyway; this pins the resolver itself, which is what has to be right in
    general.)
    """
    existing = [{"id": "a", "hash": "a", "text": "Ralph rejected: py_typecheck_fail",
                 "source": "ralph"}]
    action, target, sim = learnings._resolve("Ralph rejected: ts_typecheck_fail", existing)
    assert sim > learnings._SUPERSEDE_JACCARD, "the pair really is lexically close"
    assert action == "add" and target is None


def test_distinct_guardrails_are_left_alone(store):
    """Two real, unrelated rules must not reconcile into one."""
    learnings.record(
        "DO NOT leave a backend route added to a file without wiring it in "
        "backend/main.py with app.include_router. Untested routes that 404 "
        "because they are not wired are not done.", source="operator")
    second = learnings.record(
        "DO NOT add a frontend page without adding it to App.tsx routes and a "
        "nav link. Orphaned pages that never render are not done.",
        source="operator")
    assert second["resolution"] == "add"
    assert len(_texts()) == 2


# --- quality gate ------------------------------------------------------------
def test_a_machine_status_token_is_refused(store):
    out = learnings.record("Ralph rejected: ts_typecheck_fail", source="ralph")
    assert out["resolution"] == "refused"
    assert out["refused"] is True
    assert "machine status" in out["reason"]
    assert _texts() == [], "a status token was admitted into the prompt store"


def test_the_gate_reads_shape_not_a_name(store):
    """Nothing in the gate knows what wrote the two live rows. Any emitter of
    the same shape is refused, and a name that appears in real guidance is not."""
    for junk in ("Supervisor rejected: db_migrate_fail",
                 "verifier: exit 1",
                 "build failed",
                 "step_3_upload timed out"):
        assert learnings.quality_refusal(junk, source="loop"), junk
    for good in ("Never use an em-dash in copy.",
                 "Prefer rows to cards.",
                 "Run npm run typecheck, never npx tsc, before claiming done.",
                 "Ralph: worker finished cleanly but forgot the sentinel — always "
                 "end the final iteration with that exact string on its own line."):
        assert learnings.quality_refusal(good, source="ralph") is None, good


def test_a_refusal_does_not_raise_and_reports_why(store):
    """Callers (a review answer, a chat fence, a loop failure handler) all have
    work to finish after the write. A refusal is a return value."""
    out = learnings.record("probe pref", source="loop")
    assert out["recorded"] is False and out["id"] is None
    assert "too short" in out["reason"]


def test_the_operator_is_not_length_gated(store):
    """The operator typing four words means it. A loop emitting four does not."""
    assert learnings.quality_refusal("Prefer rows.", source="operator") is None
    assert learnings.quality_refusal("Prefer rows.", source="ralph")


def test_the_review_mint_rule_path_still_records(store):
    """`routes/tasks_all.py` mints a rule with a `review:<scope>:<task>` source
    and then asks whether it is stable. Both halves must keep working."""
    rule = ("When a ticket names a store URI, resolve it through the resource "
            "interface rather than reading the path directly.")
    ids = []
    for task in ("t1", "t2", "t3"):
        rec = learnings.record(rule, source=f"review:workshop:{task}",
                               scope="global", tags=["block"])
        ids.append(rec["id"])
    assert len(set(ids)) == 1, "three answers to three blocks made three rows"
    assert learnings.promote_when_stable("global", ids[0]) is True
    assert learnings.promote_when_stable("global", "nope") is False
    # A predicate, not a lobe: nothing is written to propose anything.
    assert not (store / "promoted.jsonl").exists()


# --- honest counters ---------------------------------------------------------
def _fixture_rows(store: Path, rows: list[dict], scope="global") -> None:
    (store / f"{scope}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")


def test_a_render_that_fits_is_not_a_retrieval(store):
    """The defect that killed the garbage collector. Everything fits, so nothing
    was chosen, so nothing may claim it was retrieved."""
    learnings.record("Always commit your own work before emitting the sentinel; "
                     "the completion gate reads the git log.", source="ralph")
    assert learnings.memory_block("global")
    e = learnings.list_learnings()[0]
    assert e["render_count"] == 1
    assert e["retrieval_count"] == 0
    assert "last_retrieved" not in e


def test_a_cap_that_binds_is_a_retrieval(store):
    """When a cap actually excludes something, the survivors beat a competitor."""
    for text in (
        "Always rebuild the bundle before reporting a UI change delivered, "
        "because the public origin serves the built files.",
        "Never mark a ticket complete while its description still says deferred; "
        "block it with a specific reason instead.",
        "Run the backend suite through the virtualenv python, because system "
        "python breaks collection on several modules.",
        "Do not modify a file you have not read this tick; a stale write usually "
        "means another writer is racing you.",
    ):
        learnings.record(text, source="ralph")
    assert len(learnings.list_learnings()) == 4
    block = learnings.memory_block("global", max_tokens=60)
    assert "more" in block, "the budget did not actually bind"
    chosen = [e for e in learnings.list_learnings() if e["retrieval_count"] > 0]
    assert chosen, "a cap bound and nothing was credited with being retrieved"
    assert all(e["render_count"] > 0 for e in chosen)


def test_an_old_retrieval_count_migrates_to_a_render_count(store):
    """Every row written before the split carries a render count mislabelled as a
    retrieval count. Reading one moves it, so the age-out precondition stops
    being permanently true."""
    _fixture_rows(store, [{
        "id": "x", "hash": "x", "scope": "global", "source": "ralph",
        "text": "Ralph: worker left its changes uncommitted; always commit first.",
        "created_at": "2026-07-15T12:53:01Z",
        "last_retrieved": "2026-08-17T06:09:20Z",
        "retrieval_count": 75,
        "provenance": [{"source": "ralph", "at": "2026-07-15T12:53:01Z"}],
    }])
    e = learnings.list_learnings()[0]
    assert e["render_count"] == 75
    assert e["retrieval_count"] == 0
    assert e["last_rendered"] == "2026-08-17T06:09:20Z"
    assert "last_retrieved" not in e


# --- age-out -----------------------------------------------------------------
def _old(days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))


def test_prune_archives_a_stale_low_provenance_entry(store):
    """What the store demonstrably could not do before: shed anything.

    The row is shaped exactly like the live ones — a loop-written line, one
    provenance entry, and 75 "retrievals" that were really renders. It ages out
    now because the counter tells the truth.
    """
    _fixture_rows(store, [{
        "id": "stale", "hash": "stale", "scope": "global", "source": "ralph",
        "text": "Ralph: worker hit a transient npm registry error on this task.",
        "created_at": _old(200),
        "last_retrieved": _old(0),      # migrates to last_rendered
        "retrieval_count": 75,          # migrates to render_count
        "provenance": [{"source": "ralph", "at": _old(200)}],
    }])
    out = learnings.prune_stale("global")
    assert out["archived"] == 1, out
    assert _texts() == []
    arch = _archive(store)
    assert arch[0]["archived_reason"].startswith("aged-out")
    assert arch[0]["text"].startswith("Ralph:"), "the entry is archived, not deleted"


def test_a_salient_entry_survives_the_same_pass(store):
    """The floor: operator-authored, pinned, and heavily reinforced entries do
    not age out on time alone, however hard the thresholds are turned up."""
    _fixture_rows(store, [
        {"id": "op", "hash": "op", "scope": "global", "source": "operator",
         "text": "DO NOT write UI without following the Workshop Design System.",
         "created_at": _old(400), "retrieval_count": 0, "render_count": 0,
         "provenance": [{"source": "operator", "at": _old(400)}]},
        {"id": "hot", "hash": "hot", "scope": "global", "source": "ralph",
         "text": "Ralph: worker forgot the completion sentinel again.",
         "created_at": _old(400), "retrieval_count": 0, "render_count": 0,
         "provenance": [{"source": "ralph", "at": _old(400)}],
         "provenance_total": 46},
        {"id": "pin", "hash": "pin", "scope": "global", "source": "ralph",
         "text": "Ralph: the yard bridge needs its sidecar restarted after a Pi reboot.",
         "created_at": _old(400), "retrieval_count": 0, "render_count": 0,
         "pinned": True,
         "provenance": [{"source": "ralph", "at": _old(400)}]},
        {"id": "junk", "hash": "junk", "scope": "global", "source": "ralph",
         "text": "Ralph: worker hit a transient npm registry error on this task.",
         "created_at": _old(400), "retrieval_count": 0, "render_count": 0,
         "provenance": [{"source": "ralph", "at": _old(400)}]},
    ])
    out = learnings.prune_stale("global", max_age_days=0, min_provenance=99,
                                min_retrieval=99)
    assert out["archived"] == 1
    assert {e["id"] for e in learnings.list_learnings()} == {"op", "hot", "pin"}
    assert out["salient"] == 3


def test_pin_and_unpin(store):
    rec = learnings.record(
        "Ralph: always rebuild the bundle before reporting a UI task complete.",
        source="ralph")
    assert learnings.set_pinned("global", rec["id"])["pinned"] is True
    assert learnings.set_pinned("global", rec["id"], False).get("pinned") is None
    assert learnings.set_pinned("global", "missing") is None


# --- provenance --------------------------------------------------------------
def test_provenance_is_capped_and_the_total_is_kept(store):
    """One live row had accrued 46 provenance entries on a one-sentence note."""
    rule = ("Ralph: worker finished cleanly but forgot to emit the completion "
            "sentinel; always end the final iteration with that exact string.")
    for i in range(40):
        learnings.record(rule, source=f"ralph-{i}")
    e = learnings.list_learnings()[0]
    assert len(e["provenance"]) <= learnings._PROVENANCE_KEEP + 1
    assert e["provenance_total"] == 40
    # The first source is kept (where it came from) along with the most recent.
    assert e["provenance"][0]["source"] == "ralph-0"
    assert e["provenance"][-1]["source"] == "ralph-39"
    # A capped list must not demote the entry that earned the most reinforcement.
    assert learnings._provenance_count(e) == 40


# --- the maintenance pass ----------------------------------------------------
def test_the_at_rest_pass_finds_what_the_live_store_held(store):
    """One pass over a copy of the shape the live `global` store was in."""
    _fixture_rows(store, [
        {"id": "keep", "hash": "keep", "scope": "global", "source": "legacy-md",
         "text": "DO NOT mark [x] when the task description still says deferred.",
         "created_at": "2026-07-15T12:53:01Z", "retrieval_count": 75,
         "provenance": [{"source": "legacy-md", "at": "2026-07-15T12:53:01Z"}]},
        {"id": "py", "hash": "py", "scope": "global", "source": "ralph",
         "text": "Ralph rejected: py_typecheck_fail",
         "created_at": "2026-07-15T12:53:01Z", "retrieval_count": 75,
         "provenance": [{"source": "ralph", "at": "2026-07-15T12:53:01Z"}]},
        {"id": "ts", "hash": "ts", "scope": "global", "source": "ralph",
         "text": "Ralph rejected: ts_typecheck_fail",
         "created_at": "2026-07-15T12:53:01Z", "retrieval_count": 75,
         "provenance": [{"source": "ralph", "at": "2026-07-15T12:53:01Z"}]},
        {"id": "old", "hash": "old", "scope": "global", "source": "legacy-md",
         "text": _RULE, "created_at": "2026-07-15T12:53:01Z",
         "provenance": [{"source": "legacy-md", "at": "2026-07-15T12:53:01Z"}]},
        {"id": "new", "hash": "new", "scope": "global", "source": "legacy-md",
         "text": _REVISED, "created_at": "2026-08-14T01:52:18Z",
         "provenance": [{"source": "legacy-md", "at": "2026-08-14T01:52:18Z"}]},
    ])

    dry = learnings.maintain("global", apply=False)
    assert dry["quality"]["archived"] == 2
    assert len(dry["revisions"]["superseded"]) == 1
    assert len(learnings.list_learnings()) == 5, "a dry run wrote to the store"

    learnings.maintain("global", apply=True)
    live = learnings.list_learnings()
    assert len(live) == 2
    assert not any("typecheck_fail" in e["text"] for e in live)
    restart = [e for e in live if "restart succeeded" in e["text"]]
    assert len(restart) == 1 and "Revised 2026-08-13" in restart[0]["text"]
    assert len(_archive(store)) == 3


def test_tidy_moves_store_dir_junk_without_deleting_it(store, tmp_path, monkeypatch):
    from services import knowledge
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path / "repo")
    (store / "global.jsonl").write_text('{"id":"a","hash":"a","text":"DO NOT ship without a rebuild of the bundle."}\n')
    (store / "global.jsonl.bak-2026-08-13").write_text("not even valid jsonl\n")
    (store / "project-demo-x.jsonl").write_text("")
    (store / "project-autonomy-smoke.jsonl").write_text('{"id":"s","hash":"s","text":"smoke"}\n')

    plan = learnings.tidy_store_dir(apply=False)
    assert {Path(m["from"]).name for m in plan["moved"]} == {
        "global.jsonl.bak-2026-08-13", "project-demo-x.jsonl",
        "project-autonomy-smoke.jsonl"}
    assert (store / "project-demo-x.jsonl").exists(), "a dry run moved a file"

    learnings.tidy_store_dir(apply=True)
    assert learnings.store_scopes() == ["global"]
    evicted = tmp_path / "repo" / "scratch" / "learnings-evicted"
    assert (evicted / "global.jsonl.bak-2026-08-13").exists()
    assert (evicted / "project-autonomy-smoke.jsonl").exists()


def test_an_archived_row_does_not_come_back_on_the_next_read(store):
    """Archiving is final, because the JSONL is the only store.

    This is the surviving half of the check that used to be written against
    `shared/learnings.md`. That file was a SECOND ORIGIN, not a mirror: writes
    appended to it and `_read(global)` folded it back in, so a status token the
    quality sweep had just archived reappeared live on the very next read. Both
    the markdown and the fold-back are deleted; a read is now a read of one
    file, and this pins that."""
    _fixture_rows(store, [
        {"id": "ts", "hash": "ts", "scope": "global", "source": "ralph",
         "text": "Ralph rejected: ts_typecheck_fail",
         "created_at": "2026-07-15T12:53:01Z",
         "provenance": [{"source": "ralph", "at": "2026-07-15T12:53:01Z"}]},
        {"id": "keep", "hash": "keep", "scope": "global", "source": "legacy-md",
         "text": "DO NOT modify a file you did not read first this tick.",
         "created_at": "2026-07-15T12:53:01Z",
         "provenance": [{"source": "legacy-md", "at": "2026-07-15T12:53:01Z"}]},
    ])

    learnings.sweep_quality("global", apply=True)
    assert [t for t in _texts()] == ["DO NOT modify a file you did not read first this tick."]
    assert [e["text"] for e in _archive(store)] == ["Ralph rejected: ts_typecheck_fail"]

    # The read that used to resurrect it.
    assert len(_texts()) == 1
    assert not any("typecheck_fail" in t for t in _texts())
