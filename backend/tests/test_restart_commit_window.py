"""The restart script's commit-window wait reads the key the chat actually writes.

`restart_backend.sh` waits, and its synchronous guard refuses, while a thread
names a turn whose reply is not committed yet. It grepped for `active_run_id`
after the chat had renamed the field to `active_turn_id`, so both checks found
nothing and passed silently. These run the script's own `commit_holder` against
a fake runtime tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "restart_backend.sh"


def _holder(root: Path) -> str:
    """Run `commit_holder` from the script with REPO_ROOT pointed at `root`."""
    src = SCRIPT.read_text()
    start = src.index("STALE_HOLD_S=300\ncommit_holder()")
    end = src.index("\n}\n", start) + 3
    snippet = f'REPO_ROOT="{root}"\n' + src[start:end] + "commit_holder\n"
    out = subprocess.run(["bash", "-c", snippet], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _thread(root: Path, tid: str, meta: dict, age_s: float = 0) -> Path:
    d = root / ".runtime" / "users" / "1" / "chat" / tid
    d.mkdir(parents=True)
    p = d / "meta.json"
    p.write_text(json.dumps(meta, indent=2))
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


def test_the_key_is_the_one_chat_log_writes():
    chat_log = (REPO / "backend" / "services" / "chat_log.py").read_text()
    assert '"active_turn_id"' in chat_log
    assert "active_run_id\": *" not in SCRIPT.read_text()


def test_a_thread_naming_a_turn_holds_the_window(tmp_path):
    p = _thread(tmp_path, "a" * 32, {"id": "a" * 32, "active_turn_id": "08d8c97c-d223"})
    assert _holder(tmp_path) == str(p)


def test_an_idle_thread_does_not(tmp_path):
    _thread(tmp_path, "b" * 32, {"id": "b" * 32, "active_turn_id": None})
    assert _holder(tmp_path) == ""


def test_a_stale_holder_does_not_park_the_restart(tmp_path):
    _thread(tmp_path, "c" * 32, {"id": "c" * 32, "active_turn_id": "08d8c97c"}, age_s=3600)
    assert _holder(tmp_path) == ""
