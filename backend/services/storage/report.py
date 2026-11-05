"""The latest usage scan, and running a new one off the request path.

A scan walks every managed tree and takes tens of seconds, so it runs on its own
thread and its result is saved; readers get the last saved scan and whether a
newer one is in progress.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from services.storage import expire, measure, policy
from wsplatform import paths

log = logging.getLogger("workshop.storage")


class Busy(RuntimeError):
    """Another scan holds the lock."""


def path() -> Path:
    return paths.runtime_dir() / "storage" / "usage.json"


def latest() -> dict | None:
    try:
        return json.loads(path().read_text())
    except (OSError, json.JSONDecodeError):
        return None


@contextmanager
def _held() -> Iterator[None]:
    """The scan lock, a file lock so every backend process sees the same one."""
    lock = path().with_name("scan.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy("a storage scan is already running") from None
        yield


def scanning() -> bool:
    try:
        with _held():
            return False
    except Busy:
        return True


def run() -> dict:
    """Scan now on this thread and save the result. Raises Busy when another
    scan is running."""
    with _held():
        result = measure.scan()
        target = path()
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        os.replace(tmp, target)
    return result


def start() -> bool:
    """Begin a scan on a background thread; False when one is already running."""
    if scanning():
        return False

    def go() -> None:
        try:
            run()
        except Busy:
            pass
        except Exception:
            log.exception("storage scan failed")

    threading.Thread(target=go, name="storage-scan", daemon=True).start()
    return True


def summary() -> dict:
    """Settings, the limits they resolve to, the last scan and the last applied
    retention pass."""
    doc = policy.read()
    usage = latest()
    floors = {d["mount"]: policy.keep_free_bytes(doc, d["total"])
              for d in (usage or {}).get("disks", [])}
    return {
        "policy": doc,
        "limit_bytes": policy.limit_bytes(doc),
        "keep_free_bytes": floors,
        "scanning": scanning(),
        "usage": usage,
        "last_pass": expire.last(),
    }
