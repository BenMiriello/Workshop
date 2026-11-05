"""The draft's disk write happens off the event loop, and what that must not change.

The rename that replaces live.json waits for the disk under pressure (ext4 flushes
a file renamed over another), and it used to wait on the event loop: 82 of 108
dumped backend stalls on 2026-10-03/04 were on that one line. With a loop
running, the loop now hands the serialised draft to a writer thread. These pin
the guarantees that hand-off has to keep, each of which is silent when broken.
"""
import asyncio
import shutil
import threading
import time
from pathlib import Path

import pytest

from services import chat_log, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        tid = chat_store.create_thread("general")["id"]
        chat_store.begin_turn(tid, "r1")
        yield tid
    finally:
        assert chat_log.flush_live(5.0)
        ownership.clear_current_owner()


def _on_loop(fn):
    async def run():
        return fn()
    return asyncio.run(run())


def _draft(text):
    return {"turn_id": "r1", "content": text, "meta": {"turn_id": "r1"}}


def test_a_draft_is_readable_the_moment_it_is_written(thread):
    def go():
        chat_log.write_live_sync(thread, _draft("so far"))
        return chat_log.read_live(thread)
    assert _on_loop(go)["content"] == "so far"
    assert chat_log.flush_live(5.0)
    assert chat_log.read_live(thread)["content"] == "so far"
    assert chat_log.live_path(thread).exists()


def test_only_the_newest_draft_reaches_the_disk(thread):
    def go():
        for i in range(50):
            chat_log.write_live_sync(thread, _draft(f"draft {i}"))
    _on_loop(go)
    assert chat_log.flush_live(5.0)
    assert '"draft 49"' in chat_log.live_path(thread).read_text()


def test_a_clear_is_not_undone_by_a_write_still_in_flight(thread, monkeypatch):
    release = threading.Event()
    real = chat_log._apply_live

    def held(path, text):
        release.wait(5)
        real(path, text)
    monkeypatch.setattr(chat_log, "_apply_live", held)

    def go():
        chat_log.write_live_sync(thread, _draft("stale"))
        chat_log.clear_live(thread)
        return chat_log.read_live(thread)
    assert _on_loop(go) is None
    release.set()
    assert chat_log.flush_live(5.0)
    assert not chat_log.live_path(thread).exists()
    assert chat_log.read_live(thread) is None


def test_a_slow_rename_does_not_hold_the_loop(thread, monkeypatch):
    real = Path.replace

    def slow(self, target):
        if Path(target).name == chat_log.LIVE_NAME:
            time.sleep(1.0)
        return real(self, target)
    monkeypatch.setattr(Path, "replace", slow)

    def go():
        t = time.monotonic()
        for i in range(5):
            chat_log.write_live_sync(thread, _draft(f"draft {i}"))
        return time.monotonic() - t
    assert _on_loop(go) < 0.2
    assert chat_log.flush_live(5.0)
    assert '"draft 4"' in chat_log.live_path(thread).read_text()


def test_a_deleted_thread_is_not_recreated_by_its_last_draft(thread, monkeypatch):
    release = threading.Event()
    real = chat_log._apply_live

    def held(path, text):
        release.wait(5)
        real(path, text)
    monkeypatch.setattr(chat_log, "_apply_live", held)
    _on_loop(lambda: chat_log.write_live_sync(thread, _draft("last words")))
    shutil.rmtree(chat_log.thread_dir(thread))
    release.set()
    assert chat_log.flush_live(5.0)
    assert not chat_log.thread_dir(thread).exists()


def test_without_a_loop_the_write_is_on_disk_at_once(thread):
    chat_log.write_live_sync(thread, _draft("scripted"))
    assert '"scripted"' in chat_log.live_path(thread).read_text()
