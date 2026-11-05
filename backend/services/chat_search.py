"""Find chats by what they are called and by what was said in them.

A plain word search, not an index. Every word of the query has to appear
(case-insensitive, anywhere, so a word prefix matches the longer word), and a
chat whose TITLE has them all ranks above one where they only turn up in the
messages. Inside each of those, the whole query as typed ranks above the same
words apart, and anything still tied keeps the list's own newest-first order.

The message text is read from the thread's own log files and cached per thread
against their stat signature (`chat_store._fold_fingerprint`, the same check the
summary cache uses), so only a chat that changed since the last search is read
again. Measured 2026-10-28 over this installation: ~400 threads, ~16 MB of
message text, a cold read of all of it in about a third of a second.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from services import chat_log, chat_store, ownership

# Chats whose text is kept between searches; the least recently searched go
# first, so a deleted chat's entry ages out instead of lingering.
CACHED_THREADS = 600

SNIPPET_BEFORE = 40
SNIPPET_AFTER = 120


def _read_messages(thread_id: str) -> str:
    parts: list[str] = []
    for path in (chat_log.archive_path(thread_id), chat_log.log_path(thread_id)):
        try:
            f = path.open(encoding="utf-8")
        except FileNotFoundError:
            continue
        with f:
            for line in f:
                # Most of a log is turn and step events; skip them unparsed.
                if '"message"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                content = ev.get("content") if ev.get("kind") == "message" else None
                if isinstance(content, str) and content.strip():
                    parts.append(content)
    return "\n".join(parts)


@lru_cache(maxsize=CACHED_THREADS)
def _cached_text(owner_id: int, thread_id: str, fingerprint: tuple) -> tuple[str, str]:
    """Keyed by the thread's log fingerprint, so a chat that changed is a new key
    and is read again; the old entry is never served."""
    text = _read_messages(thread_id)
    return text, text.lower()


def _thread_text(thread_id: str) -> tuple[str, str]:
    fp = chat_store._fold_fingerprint(thread_id)
    if fp is None:
        text = _read_messages(thread_id)
        return text, text.lower()
    return _cached_text(ownership.current_owner(), thread_id, fp)


def query_words(query: str) -> list[str]:
    return [w for w in re.split(r"\W+", query.lower()) if w]


def _snippet(text: str, lowered: str, phrase: str, words: list[str]) -> str:
    at = lowered.find(phrase)
    if at < 0:
        at = min((i for i in (lowered.find(w) for w in words) if i >= 0), default=0)
    start = max(0, at - SNIPPET_BEFORE)
    end = min(len(text), at + SNIPPET_AFTER)
    body = " ".join(text[start:end].split())
    return ("..." if start > 0 else "") + body + ("..." if end < len(text) else "")


def search(rows: list[dict], query: str) -> list[dict]:
    """Rank `rows` (thread summaries, newest first) against `query`.

    Returns `{id, where, snippet}` per match, best first. `where` is "title" or
    "content"; a content match carries the passage it was found in."""
    words = query_words(query)
    if not words:
        return []
    phrase = " ".join(query.lower().split())
    ranked: list[tuple[int, int, dict]] = []
    for i, row in enumerate(rows):
        title = (row.get("title") or "").lower()
        if all(w in title for w in words):
            tier = 0 if phrase in title else 1
            ranked.append((tier, i, {"id": row["id"], "where": "title", "snippet": None}))
            continue
        text, lowered = _thread_text(row["id"])
        if all(w in lowered for w in words):
            tier = 2 if phrase in lowered else 3
            ranked.append((tier, i, {
                "id": row["id"], "where": "content",
                "snippet": _snippet(text, lowered, phrase, words),
            }))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [hit for _, _, hit in ranked]
