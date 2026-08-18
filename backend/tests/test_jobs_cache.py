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
# `ollama_failed:` prefix protocol. Both were folded to `{"summary": ...}` at
# this boundary on 2026-08-18 so that no reader above the store carries a branch
# for the old shape. These pin that, because the log still holds 1,956 records
# in the old shape and nothing else in the codebase can read them.

def test_a_freeform_string_evidence_reads_as_a_summary(tmp_path, monkeypatch):
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1", evidence="Monitoring ongoing tasks.")])
    assert jobs.get("j1")["evidence"] == {"summary": "Monitoring ongoing tasks."}


def test_the_ollama_prefix_protocol_reads_as_a_sentence(tmp_path, monkeypatch):
    """The prefix was parsed by a display helper in routes/jobs.py, which is
    deleted. Its one job now happens here, where every reader gets it."""
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1", evidence="ollama_failed: connection refused")])
    assert jobs.get("j1")["evidence"] == {
        "summary": "Local model server (Ollama) was unreachable. connection refused"}


def test_a_receipt_written_as_a_json_string_is_parsed(tmp_path, monkeypatch):
    """Shell workers close runs through the stdlib-only jobs CLI shim and some
    passed the receipt as a JSON string. It is a receipt, not a summary."""
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1", evidence=json.dumps(
        {"kind": "receipt", "files": ["one.py"], "commits": []}))])
    assert jobs.get("j1")["evidence"] == {
        "kind": "receipt", "files": ["one.py"], "commits": []}


def test_a_structured_record_is_untouched(tmp_path, monkeypatch):
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    _write(log, [_rec("j1", evidence={"halted": "tree busy"})])
    assert jobs.get("j1")["evidence"] == {"halted": "tree busy"}


def test_the_write_path_cannot_produce_a_string(tmp_path, monkeypatch):
    """Coercing only on read would let a new producer reintroduce the shape."""
    log = tmp_path / "jobs.jsonl"
    monkeypatch.setattr(jobs, "JOBS_LOG", log)
    jobs.start(trigger="manual", job_id="j1")
    jobs.finish("j1", status="failed", evidence="worker died on the gate")
    raw = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    written = [r["evidence"] for r in raw if "evidence" in r]
    assert written == [{"summary": "worker died on the gate"}]
