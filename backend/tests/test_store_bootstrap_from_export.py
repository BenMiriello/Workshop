"""One file, one parser.

A file-backed scope with a TODO.md but no `.tasks.json` builds its store from
the export. That path used to run `services/todos.py`, a second parser of the
same file left over from ADR-009 whose model was a checkbox: `done: bool`. The
export has written eight statuses since ADR 0019, so everything that was not
plainly open or closed came back wrong, and a multi-line detail block came back
as its first line only.

It now reads through `_parse_export_blocks`, which is what `reconcile` uses.
"""
from __future__ import annotations

from services import tasks

EXPORT = """# TODO

## operator

- [ ] Plain open work
- [!] Blocked on the firewall change
- [~] Being worked right now
- [p] Parked until the GPU is free
- [x] Finished work
- [ ] Work with a detail block
      Objective: the body survives the bootstrap.
      Boundaries: none.
"""


def test_a_bootstrapped_store_keeps_every_status(tmp_path):
    (tmp_path / "TODO.md").write_text(EXPORT)
    got = {t["title"]: t["status"] for t in tasks.list_tasks(tmp_path)}
    assert got["Plain open work"] == "todo"
    assert got["Blocked on the firewall change"] == "blocked"
    assert got["Being worked right now"] == "doing"
    assert got["Parked until the GPU is free"] == "parked"
    assert got["Finished work"] == "done"


def test_a_bootstrapped_ticket_keeps_its_detail_block(tmp_path):
    (tmp_path / "TODO.md").write_text(EXPORT)
    got = {t["title"]: t for t in tasks.list_tasks(tmp_path)}
    body = got["Work with a detail block"]["body"]
    assert "Objective: the body survives the bootstrap." in body
    assert "Boundaries: none." in body


def test_the_bootstrapped_store_is_written_without_the_text_duplicate(tmp_path):
    import json
    (tmp_path / "TODO.md").write_text(EXPORT)
    tasks.list_tasks(tmp_path)
    records = json.loads((tmp_path / ".tasks.json").read_text())["tasks"]
    assert records, "the bootstrap wrote no tickets"
    assert all("text" not in r for r in records)
