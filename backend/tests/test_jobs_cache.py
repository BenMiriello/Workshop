"""The jobs log is cached on the file's identity. These pin what that means.

`_read_all` parses the whole append-only `jobs.jsonl` on a miss, and the file
only grows, so the cache is what keeps `/api/jobs` off an O(total jobs ever)
read on every poll. It is only safe because it invalidates on any write by any
process, and because callers cannot mutate what it holds. Both properties are
invisible when broken: the app just serves a stale or corrupted answer.
"""
import json

from services import jobs


def _write(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _rec(jid, **over):
    base = {"id": jid, "status": "running", "started_at": f"2026-01-01T00:00:0{jid[-1]}"}
    base.update(over)
    return base


def test_a_new_line_is_seen_immediately(tmp_path, monkeypatch):
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1")])
    assert [r["id"] for r in jobs.read(limit=10)] == ["j1"]

    # Another process appends. The next read must reflect it.
    with log.open("a") as f:
        f.write(json.dumps(_rec("j2")) + "\n")
    assert {r["id"] for r in jobs.read(limit=10)} == {"j1", "j2"}


def test_a_later_line_updates_the_record(tmp_path, monkeypatch):
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1")])
    assert jobs.read(limit=10)[0]["status"] == "running"
    with log.open("a") as f:
        f.write(json.dumps({"id": "j1", "status": "done"}) + "\n")
    assert jobs.read(limit=10)[0]["status"] == "done"


def test_a_caller_cannot_poison_the_cache(tmp_path, monkeypatch):
    """`read` and `get` hand out copies, so an edit by one caller is not every
    later reader's truth."""
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1")])

    got = jobs.read(limit=10)
    got[0]["status"] = "MUTATED"
    got[0]["injected"] = True
    assert jobs.read(limit=10)[0]["status"] == "running"
    assert "injected" not in jobs.read(limit=10)[0]

    one = jobs.get("j1")
    assert one is not None
    one["status"] = "MUTATED"
    assert jobs.get("j1")["status"] == "running"


def test_two_logs_with_the_same_size_and_mtime_do_not_share_an_entry(tmp_path, monkeypatch):
    """The cache key carries the path.

    Timestamp granularity is coarser than a nanosecond on several filesystems,
    so two small files written in the same instant can agree on both size and
    mtime. Keyed on those alone, the second log served the first log's records.
    """
    a = tmp_path / "a" / "jobs.jsonl"
    b = tmp_path / "b" / "jobs.jsonl"
    a.parent.mkdir()
    b.parent.mkdir()
    # Identical shape, different content, and forced to identical stat fields.
    _write(a, [_rec("aaa")])
    _write(b, [_rec("bbb")])
    assert a.stat().st_size == b.stat().st_size, "test needs equal sizes to be meaningful"
    st = a.stat()
    import os
    os.utime(b, ns=(st.st_atime_ns, st.st_mtime_ns))

    monkeypatch.setattr(jobs, "JOBS_LOG", a)
    assert [r["id"] for r in jobs.read(limit=10)] == ["aaa"]
    monkeypatch.setattr(jobs, "JOBS_LOG", b)
    assert [r["id"] for r in jobs.read(limit=10)] == ["bbb"]


def test_a_missing_log_reads_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_LOG", tmp_path / "nope.jsonl")
    assert jobs.read(limit=10) == []
    assert jobs.get("j1") is None
