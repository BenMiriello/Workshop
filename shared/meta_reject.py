"""Shared meta-task reject helper.

Single source of truth for the markers lives in the sibling ``meta_reject.txt``.
This module is the importable/CLI helper the non-sandboxed consumers use so the
Letta filter, ``work_generator.sh``, and ``graduate_proposals.sh`` all reject
the exact same list-management spam.

Usage:
    from meta_reject import is_meta_reject
    is_meta_reject("reduce backlog by clearing open todos")  -> True
    is_meta_reject("wire /api/notes route into backend/main.py") -> False

CLI (for bash callers):
    python3 shared/meta_reject.py --check "<task text>"
        prints REJECT (vacuous meta-task) or ALLOW; always exits 0.
    python3 shared/meta_reject.py            # print the loaded markers
"""
from __future__ import annotations

from pathlib import Path

_DEFAULT_TXT = Path(__file__).resolve().parent / "meta_reject.txt"


def load_markers(path=None):
    """Return the lowercased substring markers, dropping blanks and `#` comments."""
    p = Path(path) if path else _DEFAULT_TXT
    if not p.exists():
        return []
    markers = []
    for raw in p.read_text().splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        markers.append(s.lower())
    return markers


def _norm(text):
    return " ".join(str(text).lower().split())


def is_meta_reject(text, path=None):
    """True if ``text`` is vacuous list-management spam per the shared markers."""
    norm = _norm(text)
    if not norm:
        return False
    return any(m in norm for m in load_markers(path))


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    if argv and argv[0] == "--check":
        text = " ".join(argv[1:])
        print("REJECT" if is_meta_reject(text) else "ALLOW")
        sys.exit(0)
    for m in load_markers():
        print(m)
