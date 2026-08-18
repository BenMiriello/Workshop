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


# --- the retired freeform-string evidence shape ------------------------------
# `evidence` is a structured record. It was ALSO written as a bare string by two
# producers that no longer exist: the Letta overseer's per-tick narration and an
# `ollama_failed:` prefix protocol. The log's own history was rewritten on
# 2026-08-18, so the string shape cannot be READ any more and there is no
# read-side coercion to test. What remains is the guard on the way IN, at the
# one door every producer uses, which is what stops the shape coming back.

def _finish_with(tmp_path, monkeypatch, evidence):
    """Close a job through the real write path and return what hit the disk."""
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    jobs.start(trigger="manual", job_id="j1")
    jobs.finish("j1", status="failed", evidence=evidence)
    raw = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    return [r["evidence"] for r in raw if "evidence" in r]


def test_a_freeform_string_is_written_as_a_summary(tmp_path, monkeypatch):
    assert _finish_with(tmp_path, monkeypatch, "worker died on the gate") == [
        {"summary": "worker died on the gate"}]


def test_the_ollama_prefix_protocol_is_written_as_a_sentence(tmp_path, monkeypatch):
    """The prefix used to be expanded by a display helper in routes/jobs.py.
    That helper is deleted; the expansion happens once, here, on the way in."""
    assert _finish_with(tmp_path, monkeypatch, "ollama_failed: connection refused") == [
        {"summary": "Local model server (Ollama) was unreachable. connection refused"}]


def test_a_receipt_passed_as_a_json_string_is_stored_as_an_object(tmp_path, monkeypatch):
    """Shell workers close runs through the stdlib-only jobs CLI shim and some
    passed the receipt as a JSON string. It is a receipt, not a summary."""
    assert _finish_with(tmp_path, monkeypatch, json.dumps(
        {"kind": "receipt", "files": ["one.py"], "commits": []})) == [
        {"kind": "receipt", "files": ["one.py"], "commits": []}]


def test_a_structured_record_is_written_untouched(tmp_path, monkeypatch):
    assert _finish_with(tmp_path, monkeypatch, {"halted": "tree busy"}) == [
        {"halted": "tree busy"}]


def test_an_empty_string_is_not_stored_as_evidence(tmp_path, monkeypatch):
    """`_clean` drops a None, so a blank string leaves no key rather than an
    empty summary that reads as a run having said something."""
    assert _finish_with(tmp_path, monkeypatch, "   ") == []


def test_the_reader_no_longer_coerces(tmp_path, monkeypatch):
    """The counterpart to the guard above, and the reason it has to exist. The
    read path is deliberately literal now: history was migrated, so a string
    here would mean a NEW producer bypassed finish(), and that must surface
    rather than being quietly folded."""
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1", evidence="a string nothing should have written")])
    assert jobs.get("j1")["evidence"] == "a string nothing should have written"
