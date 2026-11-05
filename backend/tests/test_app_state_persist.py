"""Concurrent writers of the app deploy state never lose an update.

On 2026-10-18 several apps came up together at boot and each persisted through
one shared `app-deploys.json.tmp`: one writer's rename moved another's temp file
away, which logged `could not persist app state: ... .tmp ... No such file` and
dropped that write.
"""
from __future__ import annotations

import json
import logging
import threading

from services import app_runner

WRITERS = 16
ROUNDS = 60


def test_concurrent_persists_keep_every_update(tmp_path, monkeypatch, caplog):
    store = tmp_path / "app-deploys.json"
    monkeypatch.setattr(app_runner, "STATE_STORE", store)
    state: dict = {}
    monkeypatch.setattr(app_runner, "_STATE", state)
    caplog.set_level(logging.WARNING, logger=app_runner.log.name)
    start = threading.Barrier(WRITERS)
    errors: list[BaseException] = []

    def writer(i: int) -> None:
        slug = f"app{i}"
        try:
            start.wait()
            for k in range(ROUNDS):
                app = state.setdefault(slug, {"slug": slug, "services": {}})
                app["round"] = k
                app["services"][f"svc{k}"] = {"status": "running", "port": 9000 + k}
                app_runner._persist_state()
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert [r.getMessage() for r in caplog.records] == []
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert sorted(on_disk) == sorted(f"app{i}" for i in range(WRITERS))
    for i in range(WRITERS):
        app = on_disk[f"app{i}"]
        assert app["round"] == ROUNDS - 1
        assert len(app["services"]) == ROUNDS
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app-deploys.json"]
