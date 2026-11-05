"""A grant on a network share that is not answering is left out of a turn's
sandbox within the probe budget, instead of holding the backend until the
kernel gives up on the server."""
from __future__ import annotations

import io
import os
import time

from services import scoped_exec


def _share(tmp_path, monkeypatch, hang: bool):
    share = tmp_path / "share"
    share.mkdir()
    real = os.path.exists

    def exists(path):
        if str(path) == str(share) and hang:
            time.sleep(3)
        return real(path)

    monkeypatch.setattr(scoped_exec.os.path, "exists", exists)
    monkeypatch.setattr(scoped_exec, "_network_mounts", lambda: [str(share)])
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC_BINDS", f"ro:{share}")
    return str(share)


def test_a_share_that_does_not_answer_is_left_out_quickly(tmp_path, monkeypatch):
    share = _share(tmp_path, monkeypatch, hang=True)
    start = time.monotonic()
    grants = scoped_exec._grants(str(tmp_path))
    assert time.monotonic() - start < 1.5
    assert share not in [s for _, s, _ in grants]


def test_a_share_that_answers_is_granted(tmp_path, monkeypatch):
    share = _share(tmp_path, monkeypatch, hang=False)
    assert ("ro", share, share) in scoped_exec._grants(str(tmp_path))


def test_network_mounts_come_from_the_mount_table(monkeypatch):
    table = (
        "36 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
        "90 36 0:50 / /srv/my\\040share rw,relatime - cifs //host/Share rw,soft\n"
        "91 36 0:51 / /srv/nfs rw - nfs4 host:/export rw\n"
    )
    monkeypatch.setattr(scoped_exec, "open", lambda *a, **k: io.StringIO(table), raising=False)
    assert scoped_exec._network_mounts() == ["/srv/my share", "/srv/nfs"]


def test_a_share_still_not_answering_costs_the_next_turn_nothing(tmp_path, monkeypatch):
    share = _share(tmp_path, monkeypatch, hang=True)
    scoped_exec._grants(str(tmp_path))
    start = time.monotonic()
    grants = scoped_exec._grants(str(tmp_path))
    assert time.monotonic() - start < 0.2
    assert share not in [s for _, s, _ in grants]
