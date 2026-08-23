"""Presence: a TTL'd row any process can write, that nobody reconciles on read.

Step B4 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
section 6. Each test here pins one clause of that section:

  * a presence written by ONE PROCESS is visible to another -- the requirement
    that killed revision 1's in-memory map, so it is tested with a real
    subprocess running the shell workers' actual CLI, not with a second import.
  * an EXPIRED presence is absent from the live list AND NOTHING REWROTE IT.
    Asserted on the bytes on disk, because "reading never reconciles" is a claim
    about writes, and only the file can testify to that.
  * the ACCELERATOR expires a dead pid's presence EARLY.
  * the ACCELERATOR CANNOT EXTEND a lifetime. This is the invariant, so it is
    asserted against a deliberately misbehaving accelerator rather than against
    the well-behaved one: the guarantee has to hold for an accelerator someone
    writes later and gets wrong.
  * the DIRECTORY join returns a session against its subject, and stores nothing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from services import presence, tasks

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "presence.py"


@pytest.fixture()
def channel(tmp_path, monkeypatch):
    """Point the channel at a tmp directory for the duration of one test."""
    d = tmp_path / "heartbeats"
    d.mkdir()
    monkeypatch.setattr(presence, "PRESENCE_DIR", d)
    return d


def _now():
    return datetime.now(timezone.utc)


# --- one process writes, another reads --------------------------------------

def test_a_presence_written_by_another_process_is_visible(channel):
    """The whole reason presence is a store and not a map in the backend.

    `project_ralph.sh`, `ralph.sh` and `queue_runner.sh` are separate OS
    processes; if this does not hold, none of them can be seen. So the write
    goes through the same CLI they call, in a real subprocess, with the channel
    named the same way the shell names it.
    """
    env = {**os.environ, "WORKSHOP_HEARTBEATS_DIR": str(channel)}
    out = subprocess.run(
        [sys.executable, str(CLI), "heartbeat", "sweep-1",
         "--kind", "run", "--activity", "working ticket t-1",
         "--subject-kind", "ticket", "--subject-id", "t-1"],
        capture_output=True, text=True, env=env, check=True)
    written = json.loads(out.stdout)
    assert written["session_id"] == "sweep-1"

    # Read from THIS process, which shares nothing with that one but the file.
    live = presence.list_live()
    assert [r["session_id"] for r in live] == ["sweep-1"]
    assert live[0]["activity"] == "working ticket t-1"
    assert live[0]["subject_id"] == "t-1"


def test_the_cli_can_release_and_the_other_process_sees_it_gone(channel):
    presence.beat("chat-9", session_kind="chat", activity="reading")
    env = {**os.environ, "WORKSHOP_HEARTBEATS_DIR": str(channel)}
    subprocess.run([sys.executable, str(CLI), "release", "chat-9"],
                   capture_output=True, text=True, env=env, check=True)
    assert presence.list_live() == []


# --- expiry is a comparison, not an event -----------------------------------

def test_expired_presence_is_absent_from_live_and_nothing_rewrote_it(channel):
    presence.beat("run-old", ttl_sec=1)
    path = channel / "run-old.json"
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    later = _now() + timedelta(seconds=5)
    assert presence.list_live(now=later) == []

    # THE POINT OF THE TEST. The record is still there, byte for byte, with the
    # same mtime: reading filtered it out and did not reconcile, delete, mark or
    # repair it. Section 6's lesson from the 753-row inbox was "do not keep a
    # derived duplicate of a fact another record owns", and an expiry FLAG
    # written by a reader would be exactly that duplicate.
    assert path.exists()
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    # And it is still readable as a record -- absent from live, not vanished.
    assert presence.get("run-old")["session_id"] == "run-old"


def test_expires_at_is_server_computed_not_the_callers(channel):
    """A caller supplies a duration; it never supplies an instant (section 5)."""
    rec = presence.beat("run-1", ttl_sec=90)
    computed = presence._parse(rec["expires_at"]) - presence._parse(rec["ts"])
    assert computed == timedelta(seconds=90)
    # There is no way to pass one in: the keyword does not exist.
    with pytest.raises(TypeError):
        presence.beat("run-2", expires_at="2099-01-01T00:00:00Z")


def test_a_ttl_beyond_the_bound_is_refused(channel):
    with pytest.raises(presence.PresenceError):
        presence.beat("run-3", ttl_sec=presence.MAX_TTL_SEC + 1)


# --- the accelerator --------------------------------------------------------

def test_accelerator_expires_a_dead_pid_early(channel):
    """A crashed sweep leaves the directory in seconds, not in a TTL.

    The pid is a real one that has really exited, so `port_registry.process_alive`
    -- the probe section 6 names, over `tasks._pid_alive`, because it reads
    /proc/<pid>/stat and calls a ZOMBIE dead -- is doing the work.
    """
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()

    presence.beat("run-crashed", pid=dead.pid, liveness_profile="pid", ttl_sec=900)
    rec = presence.get("run-crashed")

    # The stored expiry is a quarter of an hour away...
    assert presence._parse(rec["expires_at"]) > _now() + timedelta(seconds=600)
    # ...and the session is nonetheless already gone.
    assert presence.is_live(rec) is False
    assert presence.list_live() == []

    # A record that did NOT opt in is untouched by the same dead pid: the
    # accelerator is per-profile, which is what keeps `heartbeat.sh` (a shell
    # that exits immediately after writing) from expiring itself instantly.
    presence.beat("run-shell", pid=dead.pid, ttl_sec=900)
    assert presence.is_live(presence.get("run-shell")) is True


def test_the_accelerator_uses_the_zombie_safe_probe(channel):
    """Section 6 names the implementation, not just the behaviour: the probe
    must be `port_registry.process_alive` and not `tasks._pid_alive`.

    A ZOMBIE is where the two disagree, and it is the case that matters -- a
    sweep the backend spawned and never wait()ed becomes one the moment it
    crashes. `_pid_alive` is `os.kill(pid, 0)`, which SUCCEEDS for a zombie, so
    a run that had already died would keep looking present until its TTL. This
    test builds a real zombie and insists presence calls it gone.
    """
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    # Deliberately NOT reaped: wait for the kernel to report state Z.
    stat = Path(f"/proc/{child.pid}/stat")
    for _ in range(200):
        try:
            raw = stat.read_bytes()
        except FileNotFoundError:
            pytest.skip("the child was reaped before it could be observed")
        if raw[raw.rfind(b")") + 2: raw.rfind(b")") + 3] == b"Z":
            break
        time.sleep(0.01)
    else:
        pytest.skip("could not get the child into a zombie state")

    # os.kill(pid, 0) -- what tasks._pid_alive does -- says this is alive.
    os.kill(child.pid, 0)

    presence.beat("run-zombie", pid=child.pid, liveness_profile="pid", ttl_sec=900)
    assert presence.is_live(presence.get("run-zombie")) is False

    try:
        child.wait(timeout=2)
    except Exception:                       # process_alive may have reaped it
        pass


def test_accelerator_cannot_extend_a_lifetime(channel):
    """THE INVARIANT. An accelerator that can extend a lifetime is a bug, and it
    is prevented structurally (`min`), not by asking accelerators to behave."""
    presence.beat("run-short", pid=os.getpid(), liveness_profile="pid", ttl_sec=1)
    rec = presence.get("run-short")
    stored = presence._parse(rec["expires_at"])

    def greedy(record):
        return _now() + timedelta(days=1)      # a deliberately wrong accelerator

    assert presence.effective_expiry(rec, accelerator=greedy) == stored

    later = stored + timedelta(seconds=1)
    assert presence.is_live(rec, now=later, accelerator=greedy) is False
    assert presence.list_live(now=later, accelerator=greedy) == []

    # Installed as the PROFILE's accelerator it changes nothing either -- the
    # clamp is in `effective_expiry`, not in the caller and not in the table.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(presence, "ACCELERATORS",
                        MappingProxyType({"pid": greedy}))
    try:
        assert presence.effective_expiry(presence.get("run-short")) == stored
        assert presence.list_live(now=later) == []
    finally:
        monkeypatch.undo()

    # And the table cannot be mutated at runtime at all: it is process-local
    # state, which is what `scripts/check_no_process_globals.py` exists to keep
    # out of a backend meant to run more than one worker.
    with pytest.raises(TypeError):
        presence.ACCELERATORS["pid"] = greedy


def test_accelerator_never_lengthens_a_live_record_either(channel):
    """The other direction of the same rule: a live record's expiry is its own
    beat's, and an accelerator that says 'alive' does not push it out."""
    presence.beat("run-live", pid=os.getpid(), liveness_profile="pid", ttl_sec=60)
    rec = presence.get("run-live")
    assert presence.effective_expiry(rec) == presence._parse(rec["expires_at"])


def test_a_broken_accelerator_falls_back_to_the_timestamp(channel):
    presence.beat("run-x", ttl_sec=60, liveness_profile="pid", pid=os.getpid())
    rec = presence.get("run-x")

    def explodes(record):
        raise RuntimeError("probe is down")

    assert presence.effective_expiry(rec, accelerator=explodes) == presence._parse(rec["expires_at"])


# --- the directory ----------------------------------------------------------

def test_directory_returns_a_session_against_its_subject(channel, tmp_path):
    """The read-time join: presence + the ticket it names, resolved on read."""
    scope_dir = tmp_path / "scope"
    scope_dir.mkdir()
    ticket = tasks.add(scope_dir, title="wire the presence route", execution="code")

    presence.beat("sweep-7", session_kind="run", activity="working the ticket",
                  subject_kind="ticket", subject_id=ticket["id"],
                  scope=str(scope_dir), principal="operator")

    entries = presence.directory()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["session_id"] == "sweep-7"
    assert entry["subject_kind"] == "ticket"
    assert entry["subject_id"] == ticket["id"]
    assert entry["subject_label"] == "wire the presence route"
    assert entry["subject_status"] == "todo"
    assert entry["activity"] == "working the ticket"
    assert entry["principal"] == "operator"
    # The lease is step B3: the seam is declared and honestly empty rather than
    # filled with something a caller could mistake for a lease.
    assert entry["lease"] is None


def test_directory_stores_nothing(channel, tmp_path):
    scope_dir = tmp_path / "scope"
    scope_dir.mkdir()
    ticket = tasks.add(scope_dir, title="a ticket")
    presence.beat("sweep-8", subject_kind="ticket", subject_id=ticket["id"],
                  scope=str(scope_dir))

    before = {p.name: p.read_bytes() for p in sorted(channel.iterdir())}
    for _ in range(3):
        presence.directory()
    after = {p.name: p.read_bytes() for p in sorted(channel.iterdir())}
    assert before == after


def test_directory_takes_a_lease_source_without_restructuring(channel):
    """B3's seam, exercised so the join is known to accept a lease source the
    day one exists. This is NOT a lease: it is a stub the caller supplies,
    proving the parameter reaches the entry."""
    presence.beat("chat-3", session_kind="chat", subject_kind="worktree",
                  subject_id="feature-x", scope="project:demo")

    def lease_source(subject_kind, subject_id):
        return {"subject_kind": subject_kind, "subject_id": subject_id}

    entry = presence.directory(lease_source=lease_source)[0]
    assert entry["lease"] == {"subject_kind": "worktree", "subject_id": "feature-x"}


def test_directory_shows_only_live_sessions(channel):
    presence.beat("gone", ttl_sec=1)
    presence.beat("here", ttl_sec=300)
    later = _now() + timedelta(seconds=5)
    assert [e["session_id"] for e in presence.directory(now=later)] == ["here"]


def test_an_unresolvable_subject_is_null_not_an_error(channel):
    presence.beat("sweep-9", subject_kind="ticket", subject_id="t-nope",
                  scope="project:does-not-exist")
    entry = presence.directory()[0]
    assert entry["subject_id"] == "t-nope"
    assert entry["subject_label"] is None


# --- the generalized heartbeat channel --------------------------------------

def test_a_pre_b4_heartbeat_file_still_reads(channel):
    """The channel was generalized, not replaced, so records written by the old
    `heartbeat.sh` -- `ts`/`agent_id`/`pid`/`hostname` and nothing else -- must
    still read, and must NOT be rewritten to be readable."""
    legacy = {"ts": presence._iso(_now()), "agent_id": "agent-legacy",
              "hostname": "box", "pid": 1, "status": "stopped"}
    (channel / "agent-legacy.json").write_text(json.dumps(legacy))
    before = (channel / "agent-legacy.json").read_bytes()

    rec = presence.get("agent-legacy")
    assert rec["session_id"] == "agent-legacy"
    assert rec["subject_kind"] == "none"
    assert presence.is_live(rec) is True          # ts + default TTL
    assert (channel / "agent-legacy.json").read_bytes() == before


def test_heartbeat_sh_writes_through_the_presence_channel(channel):
    """ONE mechanism, not two: the shell script that used to compose its own
    JSON now writes the same record every other writer does."""
    env = {**os.environ, "WORKSHOP_HEARTBEATS_DIR": str(channel)}
    out = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "heartbeat.sh"),
                          "agent-abc", "mode=event-driven", "source=cron"],
                         capture_output=True, text=True, env=env, check=True)
    assert out.stdout.strip() == str(channel / "agent-abc.json")

    rec = presence.get("agent-abc")
    assert rec["session_kind"] == "run"
    assert rec["expires_at"]                       # the record now expires
    assert rec["meta"] == {"mode": "event-driven", "source": "cron"}
    # The pid is recorded but NOT probed: that shell has already exited, and a
    # profile of `pid` here would expire every agent the instant it beat.
    assert rec["liveness_profile"] == "heartbeat"
    assert presence.is_live(rec) is True


# --- maintenance is not a read ----------------------------------------------

def test_prune_removes_only_long_expired_records(channel):
    presence.beat("fresh", ttl_sec=300)
    presence.beat("stale", ttl_sec=1)
    # Nothing prunes on its own; the caller says when.
    assert presence.prune(older_than_sec=0, now=_now() + timedelta(seconds=5)) == 1
    assert [r["session_id"] for r in presence.list_all()] == ["fresh"]


# --- the route surface ------------------------------------------------------

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import presence as presence_routes

    app = FastAPI()
    app.include_router(presence_routes.router)
    return TestClient(app)


def test_the_routes_answer_with_their_declared_models(channel, tmp_path):
    """`response_model` FILTERS and FastAPI 500s on a mismatch, so the only
    sound check is to call the routes (`scripts/check_routes_answer.py`'s own
    reasoning). Every one of them is exercised here against a real record."""
    scope_dir = tmp_path / "scope"
    scope_dir.mkdir()
    ticket = tasks.add(scope_dir, title="a real ticket")
    client = _client()

    written = client.post("/presence", json={
        "session_id": "chat-42", "session_kind": "chat",
        "activity": "asking about the ticket", "subject_kind": "ticket",
        "subject_id": ticket["id"], "scope": str(scope_dir),
        "meta": {"origin": "test"},
    })
    assert written.status_code == 200, written.text
    assert written.json()["expires_at"]                 # server-computed
    assert written.json()["meta"] == {"origin": "test"}

    listed = client.get("/presence")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["presences"][0]["session_id"] == "chat-42"

    joined = client.get("/presence/directory")
    assert joined.status_code == 200
    assert joined.json()["entries"][0]["subject_label"] == "a real ticket"

    gone = client.delete("/presence/chat-42")
    assert gone.status_code == 200
    assert gone.json() == {"released": True, "session_id": "chat-42"}
    assert client.get("/presence").json()["count"] == 0


def test_the_route_will_not_take_an_expiry_from_the_caller(channel):
    """Section 5's rule, on the wire: a caller supplies a duration, never an
    instant. An unknown key is dropped by the model, so a client that tries to
    pin its own expiry gets the server's."""
    client = _client()
    body = client.post("/presence", json={
        "session_id": "chat-43",
        "expires_at": "2099-01-01T00:00:00Z",
        "ttl_sec": 60,
    }).json()
    assert body["expires_at"].startswith("20")
    assert not body["expires_at"].startswith("2099")


def test_a_bad_beat_is_a_400_not_a_500(channel):
    client = _client()
    r = client.post("/presence", json={"session_id": "chat-44",
                                       "session_kind": "not-a-kind"})
    assert r.status_code == 400


def test_include_expired_serves_what_the_live_list_filters_out(channel):
    presence.beat("just-gone", ttl_sec=1)
    time.sleep(1.1)
    client = _client()
    assert client.get("/presence").json()["count"] == 0
    assert client.get("/presence", params={"include_expired": True}).json()["count"] == 1
