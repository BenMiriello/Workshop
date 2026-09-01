"""The same thing, said more than once: near-duplicate message clusters.

WHAT THIS IS, AND THE LINE IT DOES NOT CROSS
--------------------------------------------
This module reports a fact about the transcript: this text was said, by this
author, in these threads, on these dates, this many times. That is all it
reports. It does not decide what a repetition MEANS -- it does not classify a
message, infer a priority, score importance, model intent, represent an "ask"
as an entity, or file anything. Counting and citing is indexing. Concluding is
not, and concluding is a system nobody has designed, so it stays out of here.

Ranking is by counts alone (occasions, then total, then recency), which are
properties of the log rather than judgments about it.

THE GAP THIS CLOSES
-------------------
`semantic_index` already indexes chat transcripts alongside notes, docs and
tickets, so "have we discussed X" has an answer. It has no answer for "was this
said once or six times, and when last", and similarity ranking structurally
cannot produce one: six restatements of one thing are six ordinary passages
competing for the same result slot, and the per-source cap (`_MAX_PER_REF`)
suppresses the repetition by design. Counting is a different operation from
ranking, so it lives in a different module -- and, per the retrieval design's
own rule, in the surfacing layer rather than the store. Nothing here adds a
ranking term to `semantic_index.search`, moves an admission floor, or writes
into the retrieval index.

It reads the chat log directly because it needs one message at a time
attributable to one author, and the indexed chat passage deliberately groups
consecutive messages of BOTH roles up to ~1200 characters -- the right shape
for reading a conversation back, the wrong shape for counting.

It reads exactly the corpus the chat collector already reads and nothing else:
no configuration files, no environment files, no credential store.

OCCASIONS, NOT COUNT
--------------------
Clusters are ordered by how many DISTINCT DAYS they appear on, not by how many
messages they contain. Measured on this installation's log: raw count puts a
same-day burst of eleven near-identical test messages above something restated
on sixteen separate days over seven weeks. The burst is one occasion. "Said
five times on five occasions" is the phrasing the memory model already uses for
a note's provenance, and it is the right unit here for the same reason.

THE THRESHOLD, AND HOW IT WAS CHOSEN
------------------------------------
`SAME_TEXT` is cosine 0.88 between two messages' embeddings. Measured over
1,221 candidate messages on 2026-09-01, sweeping the threshold:

    0.80 -> 42% of messages in a multi-member cluster, largest cluster 88
    0.85 -> 42%, largest cluster 73
    0.88 -> 20%, largest cluster 25
    0.90 -> a third as many clusters; obvious restatements split apart

At 0.85 and below the largest clusters stop being one statement and become a
subject area: seventy-three messages whose only commonality is mentioning the
chat surface. At 0.88 the largest cluster is one sentence restated across
fifteen threads. It is also the value that agrees with the only independent
measurement available -- a hand count of repeat-flavoured messages in this log
put them at 18%, against this module's 20%.

0.88 sits just under the 0.90 that `task_dedup` calls "the same work". A
restatement weeks later is worded more loosely than a duplicate filed twice in
an afternoon, so the bar is slightly lower, and being wrong is far cheaper: a
bad merge here shows a reader two provenance entries that do not belong
together, where a bad merge there silently destroys a unit of work.

STORAGE
-------
Two files beside the retrieval index, per owner, rebuilt when the chat log's
stat fingerprint changes. About 1,200 vectors at 384 dimensions is 1.8MB and a
brute-force similarity matrix over it is milliseconds, so there is nothing to
tune and no service to run -- the same reasoning that kept the retrieval index
out of a vector database.
"""
from __future__ import annotations

import json
import logging
import re
import threading

import numpy as np

log = logging.getLogger("workshop.repetition")

# Two messages this close in embedding space are treated as the same text. See
# the module docstring for the sweep this came from.
SAME_TEXT = 0.88

# Shorter than this is not clustered. This is a measurement about embeddings,
# not a judgment about content: very short texts sit close together in
# embedding space almost regardless of what they say, so including them
# produces one enormous cluster of unrelated one-liners. It drops 195 of 1,416
# messages in this log.
MIN_CHARS = 30

# Only the head of a long message is embedded. The model's window is 512
# tokens, so text past roughly this point contributes nothing to the vector.
EMBED_CHARS = 1200

# A cluster of one is not a repetition. This is the whole filter; there is no
# other score, because the count IS the finding.
MIN_OCCASIONS = 2

# How close a cluster must be to a topic query to be shown for it.
#
# NOT the 0.65 that `semantic_index` admits a passage on, and copying that
# number was a real bug: this corpus is one person writing about one system, so
# 0.65 admitted 26-43% of it and three completely different topic queries
# returned an identical top three. The topic had become decorative.
#
# Measured against this log on 2026-09-01. A query about a subject the corpus
# genuinely does not cover peaks at 0.656 and has a 99th percentile of 0.560;
# an on-topic query reaches 0.80+ with a 90th percentile around 0.69. 0.70
# separates them with room on both sides.
TOPIC_FLOOR = 0.70

_lock = threading.Lock()
_rebuild_thread: threading.Thread | None = None


# --------------------------------------------------------------- collection

def _index_dir():
    from services import semantic_index
    return semantic_index.index_dir()


def _vecs_path():
    return _index_dir() / "repetition.npy"


def _meta_path():
    return _index_dir() / "repetition.json"


def _fingerprint() -> str:
    """Identity of the chat log as it is on disk right now.

    Reuses the retrieval index's own stat fingerprint rather than inventing a
    second staleness rule, so both stores agree on what "the chat log changed"
    means."""
    from services import chat_log, semantic_index
    try:
        return semantic_index.stat_fingerprint(chat_log.chat_dir(), "*.jsonl")
    except Exception:  # noqa: BLE001
        return ""


def collect(role: str = "user") -> list[dict]:
    """Every message by one author, one row each, with its ref and timestamp.

    `role` is the transcript's own author field, not an interpretation of it.
    """
    from services import chat_log, refs
    out: list[dict] = []
    try:
        ids = chat_log.list_thread_ids()
    except Exception as e:  # noqa: BLE001
        log.warning("chat logs unavailable: %s", e)
        return out
    for tid in ids:
        meta = {}
        try:
            meta = chat_log.read_meta(tid) or {}
        except Exception:  # noqa: BLE001
            pass
        title = meta.get("title") or "Untitled chat"
        scope = meta.get("context_key") or meta.get("scope") or ""
        try:
            events = chat_log.read_events(tid)
        except Exception:  # noqa: BLE001
            continue
        for ev in events:
            if ev.get("kind") != "message" or ev.get("role") != role:
                continue
            text = (ev.get("content") or "").strip()
            if len(text) < MIN_CHARS:
                continue
            mid = str(ev.get("id") or "")
            try:
                ref = refs.chat_ref(tid, mid) if mid else f"workshop:chat/{tid}"
            except Exception:  # noqa: BLE001
                ref = f"workshop:chat/{tid}"
            out.append({
                "ref": ref,
                "thread": tid,
                "title": title,
                "scope": scope,
                "ts": ev.get("ts") or "",
                "text": text,
            })
    return out


# ------------------------------------------------------------------ storage

def _load() -> tuple[list[dict], np.ndarray, str]:
    mp, vp = _meta_path(), _vecs_path()
    if not (mp.exists() and vp.exists()):
        return [], np.zeros((0, 0), dtype=np.float32), ""
    try:
        blob = json.loads(mp.read_text("utf-8"))
        vecs = np.load(vp)
    except Exception as e:  # noqa: BLE001
        log.warning("could not load the repetition store: %s", e)
        return [], np.zeros((0, 0), dtype=np.float32), ""
    rows = blob.get("rows") or []
    if len(rows) != len(vecs):
        # A half-written pair is not partially usable; rebuild.
        return [], np.zeros((0, 0), dtype=np.float32), ""
    return rows, vecs, blob.get("fingerprint") or ""


def _save(rows: list[dict], vecs: np.ndarray, fingerprint: str) -> None:
    d = _index_dir()
    tmp_v, tmp_m = d / "repetition.npy.tmp", d / "repetition.json.tmp"
    # Written through an open handle, not by path: `np.save` appends `.npy` to
    # any path that does not already end in it, so saving to a `.tmp` name
    # silently produces `<name>.tmp.npy` and the rename below finds nothing.
    with open(tmp_v, "wb") as fh:
        np.save(fh, vecs)
    tmp_m.write_text(json.dumps({"fingerprint": fingerprint, "rows": rows}),
                     "utf-8")
    # Vectors first: a reader that sees the new meta must never find old
    # vectors under it, and `_load` rejects a length mismatch either way.
    tmp_v.replace(_vecs_path())
    tmp_m.replace(_meta_path())


def build(force: bool = False) -> dict:
    """Embed every collected message and persist. Blocks; ~60s cold on this
    corpus, which is why nothing on a request path calls it directly."""
    with _lock:
        fp = _fingerprint()
        rows, vecs, have = _load()
        if not force and rows and have == fp:
            return {"messages": len(rows), "rebuilt": False}
        rows = collect()
        if not rows:
            _save([], np.zeros((0, 384), dtype=np.float32), fp)
            return {"messages": 0, "rebuilt": True}
        from services import embeddings
        vecs = np.asarray(
            embeddings.embed_documents([r["text"][:EMBED_CHARS] for r in rows]),
            dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / np.where(norms == 0, 1.0, norms)
        _save(rows, vecs, fp)
        return {"messages": len(rows), "rebuilt": True}


def is_stale() -> bool:
    rows, _, have = _load()
    return (not rows) or have != _fingerprint()


def _background_rebuild_if_stale() -> bool:
    """Start a rebuild off the request path if one is needed. Never blocks.

    Deferred to whoever owns index maintenance, exactly like the retrieval
    index: a chat turn's short-lived child process must not spend a minute of
    CPU embedding the whole log, and the long-lived process is already awake
    on a timer to do it."""
    global _rebuild_thread
    if not is_stale():
        return False
    from services import semantic_index
    if not semantic_index.owns_maintenance():
        return True
    with _lock:
        if _rebuild_thread is not None and _rebuild_thread.is_alive():
            return True

        from services import ownership
        owner_id = ownership.owner_or_none()

        def _run(oid=owner_id) -> None:
            # A thread does not inherit the caller's ownership contextvar.
            if oid is not None:
                ownership.set_current_owner(oid)
            else:
                semantic_index.pin_owner()
            try:
                build()
            except Exception:  # noqa: BLE001
                log.warning("background repetition rebuild crashed", exc_info=True)

        _rebuild_thread = threading.Thread(target=_run, name="repetition-rebuild",
                                           daemon=True)
        _rebuild_thread.start()
        return True


def maintenance_pass() -> None:
    """Called by the retrieval index's own maintenance pass, so this store is
    kept warm by the same timer rather than by a second loop."""
    res = build()
    if res.get("rebuilt"):
        log.info("repetition store rebuilt: %s", res)


# ----------------------------------------------------------------- clustering

def _cluster(vecs: np.ndarray, threshold: float) -> list[list[int]]:
    """Greedy single-pass clustering, seeded by connectedness.

    Seeding by descending degree is what makes the representative meaningful:
    an arbitrary seed order (index order, timestamp order) makes whichever
    message happened to be scanned first the face of the cluster -- the "one
    arbitrary winner" failure. The most-connected member is the one the others
    are all near. Every member is kept either way."""
    n = len(vecs)
    if n == 0:
        return []
    adjacency = (vecs @ vecs.T) >= threshold
    degree = adjacency.sum(axis=1)
    taken = np.zeros(n, dtype=bool)
    clusters: list[list[int]] = []
    for i in np.argsort(-degree):
        i = int(i)
        if taken[i]:
            continue
        members = np.where(adjacency[i] & ~taken)[0]
        taken[members] = True
        clusters.append([i] + [int(x) for x in members if int(x) != i])
    return clusters


def _day(ts: str) -> str:
    return (ts or "")[:10]


def _summarize(rows: list[dict], members: list[int], seed: int) -> dict:
    items = sorted((rows[i] for i in members), key=lambda r: r.get("ts") or "")
    days = sorted({_day(r["ts"]) for r in items if r["ts"]})
    return {
        # The settled unit: "said N times on M occasions".
        "occasions": len(days),
        "total": len(items),
        "threads": len({r["thread"] for r in items}),
        "first_ts": items[0].get("ts") or "",
        "last_ts": items[-1].get("ts") or "",
        "text": rows[seed]["text"],
        "ref": rows[seed]["ref"],
        "scope": rows[seed].get("scope") or "",
        # EVERY member travels with the cluster. A count with no way to go read
        # the occurrences is an assertion the reader cannot check, and being
        # able to cite rather than paraphrase is the point of provenance.
        "provenance": [{"ref": r["ref"], "thread": r["thread"],
                        "title": r["title"], "ts": r["ts"],
                        "text": r["text"][:400]} for r in items],
    }


# --------------------------------------------------------------------- query

def parse_since(value: str | None) -> str:
    """The retrieval index's own `since` parser. One definition, so a window
    means the same thing whether it narrows passages or repetitions."""
    from services import semantic_index
    return semantic_index.parse_since(value)


def repeats(*, query: str | None = None, since: str | None = None,
            limit: int = 10, min_occasions: int = MIN_OCCASIONS,
            scope: set[str] | None = None) -> dict:
    """Clusters of near-identical messages, most-repeated first.

    `query` narrows to a topic and is optional; with no query this is the whole
    list of things said more than once. `since` filters on when a cluster was
    LAST added to, not when it started, so something first said in July and
    said again yesterday is still in a one-week window.
    """
    rebuilding = _background_rebuild_if_stale()
    rows, vecs, _ = _load()
    if not rows:
        return {"repeats": [], "corpus": 0, "considered": 0, "since": "",
                "repeat_total": 0, "mode": "semantic", "rebuilding": rebuilding}

    keep = np.ones(len(rows), dtype=bool)
    if scope is not None:
        from services import semantic_index
        cache: dict[str, str] = {}
        for i, r in enumerate(rows):
            raw = r.get("scope") or ""
            if not raw:
                continue
            resolved = cache.get(raw)
            if resolved is None:
                resolved = semantic_index.resolve_chunk_scope("chat", raw)
                cache[raw] = resolved
            keep[i] = resolved in scope

    idx = np.where(keep)[0]
    floor = parse_since(since)
    if not len(idx):
        return {"repeats": [], "corpus": len(rows), "considered": 0,
                "since": floor, "repeat_total": 0, "mode": "semantic",
                "rebuilding": rebuilding}

    mode = "semantic"
    qv = None
    if (query or "").strip():
        try:
            from services import embeddings
            qv = np.asarray(embeddings.embed_query(query.strip()), dtype=np.float32)
            qv = qv / (float(np.linalg.norm(qv)) or 1.0)
        except Exception as e:  # noqa: BLE001
            # Counting still works without an embedder; only the topic filter
            # is lost. Say so rather than returning a silently unfiltered list.
            mode = f"unfiltered (embedder unavailable: {str(e)[:120]})"

    out: list[dict] = []
    for members in _cluster(vecs[idx], SAME_TEXT):
        real = [int(idx[m]) for m in members]
        summary = _summarize(rows, real, real[0])
        if summary["occasions"] < min_occasions:
            continue
        if floor and _day(summary["last_ts"]) < floor:
            continue
        if qv is not None:
            relevance = float(max(vecs[m] @ qv for m in real))
            if relevance < TOPIC_FLOOR:
                continue
            summary["relevance"] = round(relevance, 4)
        out.append(summary)

    # WHAT THE CALLER ASKED FOR DECIDES THE ORDER.
    #
    # With a topic, this is a search and relevance orders it; the counts are
    # what the answer ADDS, not what selects it. Ordering by occasions inside a
    # topic filter is what made the topic decorative -- the most-repeated
    # cluster in the corpus outranked everything for every query, so three
    # unrelated questions got the same three answers.
    #
    # With no topic, there is nothing to be relevant to and the counts are the
    # only ordering there is: occasions, then total, then most recent. Recency
    # breaks ties rather than driving the order, because sorting a transcript
    # by recency just reproduces the chat list.
    if qv is not None:
        out.sort(key=lambda s: (s["relevance"], s["occasions"]), reverse=True)
    else:
        out.sort(key=lambda s: (s["occasions"], s["total"], s["last_ts"]),
                 reverse=True)
    return {"repeats": out[:max(1, limit)], "corpus": len(rows),
            "considered": int(len(idx)), "repeat_total": len(out),
            "since": floor, "mode": mode, "rebuilding": rebuilding}


def echoes(text: str, *, limit: int = 3, scope: set[str] | None = None,
           from_scope: str | None = None) -> list[dict]:
    """Where else the same thing is written down: the docs, tickets and notes
    that restate a passage.

    The cross-source half of deduplication, and it deliberately reuses the
    retrieval index instead of building a second one. The same statement is
    routinely recorded three times -- said in a chat, written up in a doc,
    filed as a ticket -- and listing all of them beside each other is what
    stops three records of one thing reading as three things."""
    from services import semantic_index
    try:
        result = semantic_index.search(
            text[:EMBED_CHARS], limit=limit, kinds=("doc", "ticket", "note"),
            scope=scope, from_scope=from_scope)
    except Exception as e:  # noqa: BLE001
        log.warning("could not look up echoes: %s", e)
        return []
    # ONE ROW PER SOURCE. `search` admits up to `_MAX_PER_REF` passages from
    # the same file, which is right when the passages are what you are reading
    # and wrong here, where the answer is a list of WHERE something is written
    # down: the same document named twice reads as two records of it.
    seen: set[str] = set()
    out: list[dict] = []
    for h in result.get("hits", []):
        ref = h["ref"]
        if ref in seen:
            continue
        seen.add(ref)
        out.append({"kind": h["kind"], "ref": ref, "title": h["title"],
                    "ts": h.get("ts") or "", "score": h["score"],
                    "status": h.get("status") or ""})
    return out


def status() -> dict:
    rows, vecs, have = _load()
    return {"messages": len(rows),
            "dim": int(vecs.shape[1]) if len(vecs) else 0,
            "stale": (not rows) or have != _fingerprint(),
            "threshold": SAME_TEXT}
