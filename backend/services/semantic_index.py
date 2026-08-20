"""Semantic search over the workshop's own writing: notes, docs and past chats.

THE POINT OF THIS MODULE IS THAT NOTHING IT PRODUCES IS INJECTED. An agent asks
a question and gets ranked passages back, with the body and where it came from.
That is the whole contract, and it replaces a mechanism that did the opposite.

WHAT IT REPLACES, so nobody rebuilds it
---------------------------------------
Auto-recall used to run `knowledge.search` on every message and paste the top 3
notes into the prompt. That search scored a note as `sum(hay.count(t))`: raw term
frequency, no stopword list, no IDF, no length normalisation. Longer notes
contain more of every common word, so the ranking was really a length sort.
Probed live on 2026-08-12, a query of pure stopwords ("the a of to and is in")
returned the same three notes as a real question, and those three were the three
largest files in the scope. Meanwhile no agent could READ a note at all: the one
agent-facing tool listed titles and discarded its query parameter.

So the system pushed what it guessed and refused what was asked for. This
inverts that, and it is the only retrieval path that should exist.

WHY OLLAMA AND NOT AN API
-------------------------
It is already running on this box, `llm_router` already speaks to it, and
`nomic-embed-text` measured 39ms for a query and 6ms/doc batched. Local means no
per-query cost and no egress for the operator's own notes, which matters more
here than model quality does at this corpus size.

WHY NO VECTOR DATABASE
----------------------
The whole corpus is about 8,000 chunks. A float32 matrix of that shape is 24MB,
a brute-force dot product over it is sub-millisecond, and numpy is already in the
venv. A vector database would be a service to run, monitor and back up, in
exchange for nothing measurable until this is 100x bigger. Storage is two files.

PROVENANCE IS RECORDED EVEN WHERE NOTHING READS IT YET
------------------------------------------------------
Every chunk carries `kind`, `ref`, `title`, `scope` and `ts`. The operator asked
for this specifically: the write side of memory is being redesigned (ticket
31b0e762) and the point is that what exists now stays reconstructable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from wsplatform import paths

log = logging.getLogger("workshop.semantic_index")

EMBED_MODEL = os.environ.get("WORKSHOP_EMBED_MODEL", "nomic-embed-text")
EMBED_BATCH = 32
EMBED_TIMEOUT_S = 120.0

# Measured 2026-08-14 on this box: CPU does 121 ms/chunk batched, a query embed
# in about 0.1s warm, a full 3,800-chunk cold rebuild in about 8 minutes. GPU
# buys nothing at this corpus size and costs the whole feature whenever another
# workload holds the card, which is the state this box was actually found in:
# `/api/embed` 500ing with a CUDA OOM. Default is CPU (`num_gpu: 0`); a box
# with a genuinely free GPU can opt back in with the env var.
EMBED_NUM_GPU = int(os.environ.get("WORKSHOP_EMBED_NUM_GPU", "0"))

# ~1200 characters is about 300 tokens: long enough to carry an argument, short
# enough that a hit points at a paragraph rather than a document. The overlap
# stops a fact that straddles a boundary from being unfindable from either side.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150

# A chat log is mostly short messages, so they are grouped up to the same size
# rather than embedded one per message: 8,000 one-line chunks would rank noise
# above substance and cost more to build.
#
# `ticket` joins the corpus so "have we decided X" or "is there already a
# ticket for Y" can actually find the work, not just the discussion and the
# design doc (design doc sec 8: "kinds: [ticket] is how documentation and
# tickets integrate without merging the stores: separate stores, separate
# lifecycles, one query surface, one ref format").
KINDS = ("note", "doc", "chat", "ticket", "vault")

# The vault is the operator's own Obsidian folder. It is indexed only when asked
# for by name: a search about routing code must never surface a personal note.
# See the decision note in docs/memory-and-transcript-simplification-2026-08-12.md.
DEFAULT_KINDS = ("note", "doc", "chat", "ticket")

# Below this cosine score, a hit is not a hit and the search returns nothing.
#
# MEASURED, not guessed, against the real 3,407-chunk index on 2026-08-12. Seven
# genuine questions about this codebase scored 0.637 to 0.726 at the top. Seven
# nonsense or off-corpus queries ("banana helicopter tuesday", "recipe for
# sourdough starter", a pure-stopword string) scored 0.443 to 0.516. 0.58 sits in
# the middle of a 0.120 gap with room on both sides.
#
# THE FLOOR IS THE POINT. Cosine similarity from this model has a high,
# uninformative baseline: nonsense still scores ~0.5, so a search with no
# threshold ALWAYS returns five confident-looking results. That is the failure
# mode of the thing this replaces, rebuilt with better maths, and it is worse
# than saying nothing because the passages read as relevant.
#
# Two alternatives were tried and rejected on measurement. A z-score over the
# corpus is actively wrong: "banana helicopter tuesday" scored z=5.49, higher
# than two real questions, because a nonsense query drags the mean down. And
# nomic's `search_query:` / `search_document:` task prefixes raise every score
# but move the margin by +0.001, so they buy nothing here and would cost a full
# re-embed.
MIN_SCORE = 0.58


@dataclass
class Chunk:
    kind: str     # note | doc | chat | ticket | vault
    ref: str      # repo-relative path, or a thread id
    title: str
    scope: str    # knowledge scope or chat context_key; "" where there is none
    ts: str       # ISO8601, best effort
    text: str

    def digest(self) -> str:
        """Identity of the CONTENT, which is what lets a rebuild skip work.

        Keyed on the text plus its source, so the same sentence in two files is
        embedded once per file rather than collapsing into one hit."""
        h = hashlib.sha1()
        h.update(f"{self.kind}\0{self.ref}\0".encode())
        h.update(self.text.encode())
        return h.hexdigest()


# ---------------------------------------------------------------- embedding

def _ollama_url() -> str:
    try:
        from services import llm_router
        return llm_router.OLLAMA_URL
    except Exception:
        return os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def embed(texts: list[str]) -> np.ndarray:
    """L2-normalised embeddings, one row per text. Raises on failure.

    Normalised here rather than at query time so the ranking is a plain dot
    product and the stored matrix is directly usable."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    import httpx
    out: list[list[float]] = []
    with httpx.Client(timeout=EMBED_TIMEOUT_S) as cl:
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i:i + EMBED_BATCH]
            r = cl.post(f"{_ollama_url()}/api/embed",
                        json={"model": EMBED_MODEL, "input": batch,
                              "options": {"num_gpu": EMBED_NUM_GPU}})
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
    m = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    # A zero vector would divide to NaN and poison every later comparison.
    norms[norms == 0] = 1.0
    return m / norms


# How long a health check is trusted before it is repeated. Cheap enough to
# pay per search (one tiny embed, ~0.1s warm) but there is no reason to pay it
# on every one when nothing about the embedder changes between two searches a
# second apart.
_AVAILABILITY_TTL_S = float(os.environ.get("WORKSHOP_EMBED_PROBE_TTL_S", "30"))
_availability_cache: tuple[float, tuple[bool, str]] | None = None


def available() -> tuple[bool, str]:
    """Whether embedding can actually run right now, and why not when it can't.

    Cached for `_AVAILABILITY_TTL_S` so a burst of calls pays for one probe.
    Callers surface the reason instead of failing opaquely: an index that
    cannot build because a model was never pulled is a fixable, nameable
    condition, and so is one that cannot build because the runtime holding the
    model has no memory to run it in."""
    global _availability_cache
    now = time.monotonic()
    cached = _availability_cache
    if cached is not None and now - cached[0] < _AVAILABILITY_TTL_S:
        return cached[1]
    result = _probe_embedder()
    _availability_cache = (now, result)
    return result


def _probe_embedder() -> tuple[bool, str]:
    """The uncached check. Split out so `available()` owns the caching and this
    owns the actual test, which is a real (tiny) embed call and not just "is
    the model pulled".

    That distinction is the whole point: `/api/tags` reporting the model as
    pulled says nothing about whether the runtime backing it can currently run
    it. Measured on this box: the model was pulled, `/api/tags` was healthy,
    and every embed still 500'd with a CUDA out-of-memory because another
    workload held the GPU. Only an actual embed call catches that."""
    try:
        import httpx
        with httpx.Client(timeout=5.0) as cl:
            r = cl.get(f"{_ollama_url()}/api/tags")
            r.raise_for_status()
            names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
        if EMBED_MODEL.split(":")[0] not in names:
            return False, f"ollama is up but the model {EMBED_MODEL} is not pulled"
    except Exception as e:
        return False, f"ollama is unreachable at {_ollama_url()}: {e}"
    try:
        embed(["ok"])
    except Exception as e:
        return False, f"the model {EMBED_MODEL} is pulled but cannot embed right now: {e}"
    return True, ""


# ---------------------------------------------------------------- chunking

def _split(text: str) -> list[str]:
    """Paragraph-aware split to about CHUNK_CHARS.

    Splitting on blank lines first keeps a markdown list or a code block whole
    where it fits, which matters because the retrieved passage is what the model
    reads. An oversized single paragraph is hard-cut rather than dropped."""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        while len(p) > CHUNK_CHARS:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS - CHUNK_OVERLAP:]
        if not cur:
            cur = p
        elif len(cur) + len(p) + 2 <= CHUNK_CHARS:
            cur = f"{cur}\n\n{p}"
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- collection

def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(paths.workshop_root()))
    except Exception:
        return str(p)


def _iso(p: Path) -> str:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        return ""


def _first_heading(body: str, fallback: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or fallback
        if s and not s.startswith("<!--"):
            return s[:80]
    return fallback


def collect_notes() -> list[Chunk]:
    """Every knowledge note, across every scope directory that exists.

    The docstring said this already and the code did not. It walked the shared
    root plus that directory's IMMEDIATE subdirectories, non-recursively, so
    `projects/*/knowledge`, `apps/*/knowledge`, `.runtime/agents/*/knowledge` and
    `roles/<role>/` were never reached: measured 2026-08-13, all 21 indexed note
    refs came from one directory. `knowledge.all_scope_dirs()` is now the single
    enumeration, and it is the inverse of `knowledge.scope_dir()` rather than a
    second guess at the layout.
    """
    from services import knowledge
    out: list[Chunk] = []
    try:
        roots = knowledge.all_scope_dirs()
    except Exception as e:
        log.warning("knowledge scopes unavailable: %s", e)
        roots = []
    for scope, d in roots:
        for f in sorted(d.glob("*.md")):
            try:
                raw = f.read_text()
            except Exception:
                continue
            _, body = knowledge.split_author(raw)
            title = _first_heading(body, f.stem)
            for c in _split(body):
                out.append(Chunk("note", _rel(f), title, scope, _iso(f), c))
    return out


def collect_docs() -> list[Chunk]:
    root = paths.workshop_root() / "docs"
    out: list[Chunk] = []
    for f in sorted(root.rglob("*.md")):
        try:
            body = f.read_text()
        except Exception:
            continue
        title = _first_heading(body, f.stem)
        for c in _split(body):
            out.append(Chunk("doc", _rel(f), title, "", _iso(f), c))
    return out


def collect_chats() -> list[Chunk]:
    """Past conversations, grouped into passages in log order.

    This is the corpus that makes distillation unnecessary: the thing a summary
    was standing in for is right here, verbatim and attributable."""
    from services import chat_log
    out: list[Chunk] = []
    try:
        ids = chat_log.list_thread_ids()
    except Exception as e:
        log.warning("chat logs unavailable: %s", e)
        return out
    for tid in ids:
        meta = {}
        try:
            meta = chat_log.read_meta(tid) or {}
        except Exception:
            pass
        title = meta.get("title") or "Untitled chat"
        scope = meta.get("context_key") or meta.get("scope") or ""
        try:
            events = chat_log.read_events(tid)
        except Exception:
            continue
        buf, buf_ts = "", ""
        for ev in events:
            if ev.get("kind") != "message":
                continue
            content = (ev.get("content") or "").strip()
            if not content:
                continue
            who = "Operator" if ev.get("role") == "user" else "Assistant"
            line = f"{who}: {content}"
            if not buf_ts:
                buf_ts = ev.get("ts") or ""
            if len(buf) + len(line) + 2 > CHUNK_CHARS and buf:
                out.append(Chunk("chat", tid, title, scope, buf_ts, buf))
                buf, buf_ts = line[:CHUNK_CHARS], ev.get("ts") or ""
            else:
                buf = f"{buf}\n\n{line}" if buf else line
        if buf:
            out.append(Chunk("chat", tid, title, scope, buf_ts, buf))
    return out


def collect_vault() -> list[Chunk]:
    """The operator's own Obsidian vault. Opt-in only: see DEFAULT_KINDS."""
    from services import notes_vault
    out: list[Chunk] = []
    try:
        root = notes_vault.vault_root()
    except Exception:
        return out
    for f in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in f.parts):
            continue
        try:
            body = f.read_text()
        except Exception:
            continue
        try:
            ref = str(f.relative_to(root))
        except Exception:
            ref = str(f)
        for c in _split(body):
            out.append(Chunk("vault", ref, _first_heading(body, f.stem), "", _iso(f), c))
    return out


def collect_tickets() -> list[Chunk]:
    """Every ticket, across every scope that has a task store.

    Enumeration mirrors `routes.tasks_all.scope_roots()`: the sweep that
    already covers development, workshop, every project, every registry
    assistant scope and every build agent, deduped by resolved tree so a build
    agent sharing a project's tree is not double-counted. It is the closest
    thing to `knowledge.all_scope_dirs()` for tickets (no bulk enumerator
    existed before it; see the design doc sec 8 and Phase 2's own note about
    the gap). Imported lazily, matching `services/refs.py`'s existing
    `from routes import tasks_all` (routes -> services is the normal
    direction; this one call goes the other way on purpose, same as refs.py's
    ticket resolver, because the sweep genuinely lives with the route).

    Every scope `scope_roots()` returns (`development`, `workshop`,
    `project:<slug>`, `agent:<slug>`) is a real scope_tree key and needs no
    translation. It used to also emit `build:<slug>`, which `reach()` and
    `memory_policy` had never heard of and which had to be translated here; that
    key is deleted, so the translation is too.

    A ticket's `ref` is its bare id (matching `refs.ticket_ref` / the
    `workshop:ticket/<id>` format `services/refs.py` already resolves), and its
    text is title + body, split like every other collector."""
    from routes import tasks_all
    from services import tasks
    out: list[Chunk] = []
    try:
        roots = tasks_all.scope_roots()
    except Exception as e:
        log.warning("ticket scopes unavailable: %s", e)
        return out
    for scope, root in roots:
        try:
            items = tasks.list_tasks(root)
        except Exception:
            continue
        for t in items:
            tid = t.get("id")
            if not tid:
                continue
            title = t.get("title") or tid
            text = f"{title}\n\n{t.get('body') or ''}".strip()
            ts = t.get("updated") or t.get("created") or ""
            for c in _split(text):
                out.append(Chunk("ticket", tid, title, scope, ts, c))
    return out


_COLLECTORS = {
    "note": collect_notes,
    "doc": collect_docs,
    "chat": collect_chats,
    "ticket": collect_tickets,
    "vault": collect_vault,
}


# ---------------------------------------------------------------- storage

def index_dir() -> Path:
    """Per owner, exactly like the chat log, because it indexes the chat log."""
    from services import ownership
    d = ownership.user_runtime_dir() / "semantic"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path() -> Path:
    return index_dir() / "index.meta.jsonl"


def _stale_path() -> Path:
    return index_dir() / "index.stale"


def _fingerprints_path() -> Path:
    return index_dir() / "index.fingerprints.json"


def _vecs_path() -> Path:
    return index_dir() / "index.vecs.npy"


def load() -> tuple[list[dict], np.ndarray]:
    """The index as it stands, or empty. Never raises: a corrupt or half-written
    index must degrade to "no results", not break the tool that reads it."""
    mp, vp = _meta_path(), _vecs_path()
    if not mp.exists() or not vp.exists():
        return [], np.zeros((0, 0), dtype=np.float32)
    try:
        # `split("\n")`, NEVER `splitlines()`. Python also splits on U+2028,
        # U+2029, U+0085, \v, \f and \x1c-\x1e, and JSON escapes none of them.
        # One chunk carrying a U+2028 therefore cut a valid record in half and
        # the whole index read as corrupt. JSONL is newline-delimited, exactly.
        meta = [json.loads(l) for l in mp.read_text().split("\n") if l.strip()]
        vecs = np.load(vp)
    except Exception as e:
        log.warning("semantic index unreadable, treating as empty: %s", e)
        return [], np.zeros((0, 0), dtype=np.float32)
    if len(meta) != vecs.shape[0]:
        log.warning("semantic index rows disagree (%d meta, %d vecs); treating as empty",
                    len(meta), vecs.shape[0])
        return [], np.zeros((0, 0), dtype=np.float32)
    return meta, vecs


def _save(meta: list[dict], vecs: np.ndarray) -> None:
    """Written to a temp file and renamed, so a reader never sees a half-index."""
    d = index_dir()
    mp, vp = _meta_path(), _vecs_path()
    tmp_m, tmp_v = d / "index.meta.tmp", d / "index.vecs.tmp.npy"
    tmp_m.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in meta))
    np.save(tmp_v, vecs)
    os.replace(tmp_m, mp)
    os.replace(tmp_v, vp)


# -------------------------------------------------------------- staleness
#
# THE INDEX HEALS ITSELF, AND IT IS NOT ON A SCHEDULE. A note written today was
# unfindable until somebody remembered to run the build by hand, which is the
# same as unfindable. The fix is two small functions and no new process: a write
# leaves a marker, and the next search that sees the marker rebuilds first.
#
# NOT A CRON ENTRY. The operator turned cron off deliberately
# (infra/cron/workshop.crontab), and a scheduler is the wrong shape anyway: it
# rebuilds when nothing changed and lags when something did. This costs nothing
# when nothing was written and is exact when something was.
#
# The rebuild is INCREMENTAL by content digest (see `build`), so the common case
# after one note is a handful of new chunks embedded locally, not a full re-embed
# of the corpus.
#
# A MARKER FILE, not a module global. The writer is the backend process and the
# reader is very often the MCP server, which is a separate stdio subprocess
# spawned per turn. An in-memory flag would be invisible across that boundary,
# which is the boundary that matters. It lives in the owner's index dir, so it is
# per-owner exactly as the index is.
#
# THE MARKER IS ONE OF TWO SIGNALS. It covers every kind with a store that can
# actually stamp it on every write. `doc` cannot be reached that way -- see the
# "ON-DISK FINGERPRINTING" block further down, by `_fs_is_stale` -- so
# `is_stale()` is the OR of this marker and that on-disk check.

def mark_stale(reason: str = "") -> None:
    """Record that the corpus changed under the index. Never raises.

    Best-effort on purpose: this is called from the tail of a write that has
    already succeeded, and failing a note write because a marker could not be
    touched would be a strictly worse outcome than a stale index.

    DEBOUNCED: skips the write entirely when the marker is already down. A
    chat message, a ticket heartbeat, and a note edit all funnel through this
    on their hot write paths, so between one rebuild and the next this can be
    called many times for one meaningful staleness -- the first caller's write
    is the one that matters, and every caller after it until the marker is
    cleared is a no-op `exists()` check rather than a second file write. Costs
    nothing when nothing was stale (the common case, one `Path.exists()`), and
    the reason recorded on disk is therefore the FIRST cause since the last
    rebuild rather than the most recent one -- nothing reads it back, it is
    debugging text for a human running the CLI.
    """
    try:
        if _stale_path().exists():
            return
        _stale_path().write_text((reason or "changed").strip() + "\n")
    except Exception:
        log.debug("could not mark the semantic index stale", exc_info=True)


def is_stale() -> bool:
    """True when EITHER the explicit marker above says so, OR the on-disk
    fingerprint check below does. See that check's own comment for why a
    second, independent signal exists at all."""
    try:
        if _stale_path().exists():
            return True
    except Exception:
        pass
    return _fs_is_stale()


def _clear_stale() -> None:
    try:
        _stale_path().unlink(missing_ok=True)
    except Exception:
        log.debug("could not clear the semantic index stale marker", exc_info=True)


# ---------------------------------------------------- on-disk fingerprinting
#
# WHY THIS EXISTS ALONGSIDE THE MARKER ABOVE. `mark_stale` is a write-path
# signal: whoever accepts a write leaves a marker so the next search rebuilds
# first. That is complete for note (services/knowledge.py), chat
# (services/chat_log.py) and ticket (services/tasks.py) -- every note, chat
# message and ticket is written through exactly the one store that stamps the
# marker, with no other way in. `doc` has no such choke point:
# routes/docs_index.py marks the index when the in-app editor writes one, but
# the overwhelming majority of docs are written straight to `docs/*.md` by an
# editor or an agent's filesystem tools, none of which import this module or
# know the marker exists. That path leaves nothing behind, so the doc corpus
# in the index could drift from what is actually on disk indefinitely, healed
# only by accident the next time an unrelated note/chat/ticket write happens
# to flip the shared marker.
#
# The fix is a second, independent staleness signal that does not depend on
# being told: a cheap fingerprint of the doc TREE (which files exist, and
# each one's size and mtime -- never a file's body, see `_stat_fingerprint`),
# compared against the fingerprint the last successful build actually
# observed. A mismatch means the disk moved since the index was built, full
# stop, regardless of which tool moved it.
#
# ONLY `doc` GETS THIS. `note`, `chat` and `ticket` are also read from
# storage on every build, but each already has a reliable write-path signal
# (above) covering every way into that store, so a second signal would only
# add a stat walk with nothing new to catch. `vault` has the same filesystem
# gap `doc` does (no writer marks anything), but is deliberately left out: it
# is opt-in only (see DEFAULT_KINDS) and is never part of a self-heal rebuild
# unless a caller already asked for it by name, so the failure mode this
# closes -- the DEFAULT corpus silently drifting -- does not apply to it. If
# vault ever needs this too, add `"vault": _vault_fingerprint` to
# `_FS_FINGERPRINTERS` below; nothing else changes.
#
# NO BASELINE MEANS SILENT, ON PURPOSE. `is_stale()` runs on every search, so
# a kind with no prior fingerprint (never built, or built before this
# existed) must not manufacture staleness out of nothing -- that is what the
# explicit marker and the ordinary first-build path are for. Once one
# successful build records a baseline for a kind, drift from THAT baseline is
# what gets caught, forever, with no further write-path wiring required.

def _stat_fingerprint(root: Path, pattern: str) -> str:
    """A cheap identity for a directory tree of files: which files exist,
    and each one's size and mtime. NEVER a file's body -- a glob plus
    `stat()` per match is O(number of files) and runs on the `is_stale()`
    path (so on every search); reading every file to confirm its body
    actually changed would be O(total bytes) and defeat the point of a check
    meant to be cheap enough to pay unconditionally.

    Sorted by relative path first so the result does not depend on directory
    iteration order, which the filesystem gives no guarantee about."""
    h = hashlib.sha1()
    try:
        entries = sorted(root.rglob(pattern))
    except OSError:
        return ""
    for f in entries:
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(_rel(f).encode())
        h.update(f"\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def _doc_fingerprint() -> str:
    return _stat_fingerprint(paths.workshop_root() / "docs", "*.md")


_FS_FINGERPRINTERS = {"doc": _doc_fingerprint}


def _load_fingerprints() -> dict:
    try:
        return json.loads(_fingerprints_path().read_text())
    except Exception:
        return {}


def _save_fingerprints(fps: dict) -> None:
    """Written to a temp file and renamed, same as `_save`, so a reader never
    sees a half-written file."""
    try:
        d = index_dir()
        tmp = d / "index.fingerprints.tmp.json"
        tmp.write_text(json.dumps(fps))
        os.replace(tmp, _fingerprints_path())
    except Exception:
        log.debug("could not persist semantic index fingerprints", exc_info=True)


def _refresh_fingerprints(kinds) -> None:
    """Record the on-disk state this build actually just collected, per
    fingerprinted kind. Only for kinds THIS call covered: a build restricted
    to e.g. `("chat",)` never re-ran `collect_docs`, so it must not overwrite
    the doc baseline as if nothing had changed since -- that baseline is only
    trustworthy right after a build that actually looked at the doc tree."""
    to_update = [k for k in kinds if k in _FS_FINGERPRINTERS]
    if not to_update:
        return
    fps = _load_fingerprints()
    for k in to_update:
        fps[k] = _FS_FINGERPRINTERS[k]()
    _save_fingerprints(fps)


def _fs_is_stale() -> bool:
    """The on-disk half of `is_stale()`. Best-effort like the marker: a
    fingerprint that cannot be computed must not make every search look stale
    forever, so a failure here reads as "no drift detected", not "stale"."""
    try:
        saved = _load_fingerprints()
    except Exception:
        return False
    for kind, fp_fn in _FS_FINGERPRINTERS.items():
        baseline = saved.get(kind)
        if baseline is None:
            continue  # never built; the marker and the first build own this
        try:
            if fp_fn() != baseline:
                return True
        except Exception:
            log.debug("could not compute the %s fingerprint; skipping", kind,
                      exc_info=True)
    return False


def rebuild_kinds() -> tuple[str, ...]:
    """Which kinds a self-healing rebuild must cover.

    NOT the caller's `kinds` filter, and this is the trap. `build` REPLACES the
    index with exactly what it collected, so rebuilding with the kinds of the
    query in hand (`kinds=("note",)` from a note-only search) would delete every
    doc and chat from the index as a side effect of one search. It is the
    defaults, plus anything already indexed — so an opted-in `vault` survives a
    rebuild it did not ask for.
    """
    present = {m.get("kind") for m in load()[0] if m.get("kind") in KINDS}
    return tuple(k for k in KINDS if k in present or k in DEFAULT_KINDS)


def rebuild_if_stale() -> dict | None:
    """Rebuild when a write has marked the index stale. Returns the build result,
    or None when there was nothing to do.

    The marker is cleared by `build` on success only. A rebuild that fails
    because the embedder is down leaves the marker in place, so the next search
    tries again instead of silently accepting a stale index forever.
    """
    if not is_stale():
        return None
    log.info("semantic index is stale; rebuilding before this search")
    res = build(rebuild_kinds())
    if not res.get("ok"):
        log.warning("stale semantic index could not be rebuilt: %s", res.get("error"))
    return res


# ------------------------------------------------------ background rebuild
#
# `rebuild_if_stale` above runs INSIDE the caller (still used directly by the
# CLI and a couple of tests that want a synchronous rebuild). `search()` must
# NOT do that: on CPU a cold build is about 8 minutes, and a search is a
# user-facing request. This is the same self-healing trigger, moved off the
# request path into a single background thread per owner's index, so a search
# that arrives mid-rebuild answers from what is on disk right now instead of
# blocking on it or returning nothing.
#
# Keyed by index_dir() rather than one global thread, so two owners rebuilding
# their own (separate) indexes never contend on each other's lock; single-
# flight is scoped to "the index this call would rebuild," which is the thing
# that actually cannot run twice at once.
_rebuild_state_lock = threading.Lock()
_rebuild_threads: dict[str, threading.Thread] = {}


def _background_rebuild_if_stale() -> bool:
    """Kick off a background rebuild if the index is stale and none is already
    running for this owner. Returns whether a rebuild is in flight (just
    started, or already running) so the caller can say so. Never blocks."""
    if not is_stale():
        return False
    key = str(index_dir())
    with _rebuild_state_lock:
        existing = _rebuild_threads.get(key)
        if existing is not None and existing.is_alive():
            return True

        from services import ownership
        owner_id = ownership.owner_or_none()

        def _run(oid=owner_id) -> None:
            # A background thread does not inherit the calling thread's
            # ownership contextvar, so the owner captured above (in the
            # thread that already had it pinned) is set explicitly here.
            # `pin_owner()` is the same operator fallback every other
            # background pass in this module uses when there was no owner to
            # capture (see its own docstring).
            if oid is not None:
                ownership.set_current_owner(oid)
            else:
                pin_owner()
            try:
                res = build(rebuild_kinds())
                if not res.get("ok"):
                    log.warning("background semantic index rebuild failed: %s",
                                res.get("error"))
            except Exception:
                log.warning("background semantic index rebuild crashed", exc_info=True)

        t = threading.Thread(target=_run, name="semantic-index-rebuild", daemon=True)
        _rebuild_threads[key] = t
        t.start()
        return True


# ---------------------------------------------------------------- build

def build(kinds: tuple[str, ...] | list[str] = DEFAULT_KINDS,
          *, log_fn=None) -> dict:
    """Rebuild the index, re-embedding only what is new.

    Identity is the content digest, so this is incremental without tracking
    mtimes: an unchanged note keeps its vector, an edited one gets a new digest
    and is re-embedded, and a deleted one simply stops being collected. That
    also makes the operation idempotent, which a mtime cache is not."""
    say = log_fn or (lambda s: None)
    ok, why = available()
    if not ok:
        return {"ok": False, "error": why}

    chunks: list[Chunk] = []
    for k in kinds:
        c = _COLLECTORS[k]()
        say(f"collected {len(c)} chunks from {k}")
        chunks.extend(c)

    old_meta, old_vecs = load()
    cache = {m["digest"]: i for i, m in enumerate(old_meta) if "digest" in m}

    meta: list[dict] = []
    rows: list[np.ndarray | None] = []
    todo: list[int] = []
    for ch in chunks:
        dg = ch.digest()
        row = {**asdict(ch), "digest": dg}
        meta.append(row)
        hit = cache.get(dg)
        if hit is not None and hit < old_vecs.shape[0]:
            rows.append(old_vecs[hit])
        else:
            rows.append(None)
            todo.append(len(rows) - 1)

    say(f"{len(chunks)} chunks, {len(todo)} to embed, {len(chunks) - len(todo)} reused")
    if todo:
        vecs = embed([meta[i]["text"] for i in todo])
        for n, i in enumerate(todo):
            rows[i] = vecs[n]

    # Cleared only once the index is actually on disk, and only by a build that
    # covered every default kind: `build(("chat",))` re-collects no notes, so
    # letting it clear a marker a note write left would lose that note until the
    # next unrelated write. An `ok: False` return above (embedder down) never
    # reaches here at all, so the marker survives and the next search retries.
    covered = set(DEFAULT_KINDS).issubset(set(kinds))

    if not rows:
        _save([], np.zeros((0, 0), dtype=np.float32))
        if covered:
            _clear_stale()
        _refresh_fingerprints(kinds)
        return {"ok": True, "chunks": 0, "embedded": 0, "reused": 0}

    matrix = np.vstack([r for r in rows if r is not None]).astype(np.float32)
    _save(meta, matrix)
    if covered:
        _clear_stale()
    _refresh_fingerprints(kinds)
    return {"ok": True, "chunks": len(meta), "embedded": len(todo),
            "reused": len(meta) - len(todo), "dim": int(matrix.shape[1])}


# ------------------------------------------------------------ lexical fallback
#
# BM25 over the SAME chunk corpus the vector index embeds, not a second store.
# `index.meta.jsonl` already carries every chunk's `text`, so lexical search
# needs no embedder at all: even a stale index still answers. Chosen over the
# simpler term-count scorer in `knowledge._plaintext_search` (raw
# `sum(hay.count(t))`, no IDF, no length normalisation) because that scorer's
# actual failure was measured, not theoretical: a query of pure stopwords
# returned the three largest files in the scope, because longer documents
# contain more of every common word and the "ranking" was really a length
# sort. BM25's IDF term downweights words that are common across the corpus
# and its length normalisation stops a long chunk from winning on bulk alone.
#
# Cached against the meta file's (size, mtime), same pattern as
# `scope_tree._load_overrides`: a cheap `stat()` per call, a full retokenise
# only when the index actually changed. Tokenising is only paid on the
# fallback path (the embedder is down), never on a healthy search.

_BM25_K1 = 1.5
_BM25_B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


_lexical_cache: tuple[tuple, dict] | None = None


def _bm25_state(meta: list[dict]) -> dict:
    """Postings, document lengths and the corpus average, built once per index
    generation. `postings[term]` is `{doc_index: term_frequency}`, so scoring a
    query only ever touches documents that actually contain one of its terms
    rather than looping the whole corpus per term."""
    global _lexical_cache
    mp = _meta_path()
    try:
        st = mp.stat()
        key = (str(mp), st.st_size, st.st_mtime_ns)
    except OSError:
        key = (str(mp), None, None)
    cached = _lexical_cache
    if cached is not None and cached[0] == key:
        return cached[1]

    postings: dict[str, dict[int, int]] = {}
    doc_len = [0] * len(meta)
    for i, m in enumerate(meta):
        toks = _tokenize(m.get("text", ""))
        doc_len[i] = len(toks)
        for t in toks:
            bucket = postings.setdefault(t, {})
            bucket[i] = bucket.get(i, 0) + 1
    avgdl = (sum(doc_len) / len(doc_len)) if doc_len else 0.0
    state = {"postings": postings, "doc_len": doc_len, "avgdl": avgdl, "n": len(meta)}
    _lexical_cache = (key, state)
    return state


def _bm25_scores(query: str, meta: list[dict]) -> np.ndarray:
    """BM25 score for `query` against every chunk in `meta`, one float per row
    (0.0 where nothing matched). Full-corpus, like the vector path: the caller
    masks it, rather than this function taking a candidate subset."""
    state = _bm25_state(meta)
    n = state["n"]
    scores = np.zeros(n, dtype=np.float32)
    if n == 0:
        return scores
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return scores
    avgdl = state["avgdl"] or 1.0
    doc_len = state["doc_len"]
    postings = state["postings"]
    for t in q_tokens:
        docs = postings.get(t)
        if not docs:
            continue
        df = len(docs)
        # log(1 + ...), never the bare Robertson-Sparck-Jones form: that
        # variant goes negative for a term present in most of the corpus,
        # which would let a document's score fall by matching MORE of the
        # query. This variant is always >= 0.
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for i, tf in docs.items():
            denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len[i] / avgdl)
            scores[i] += idf * (tf * (_BM25_K1 + 1)) / denom
    return scores


# ------------------------------------------------------------- scope masking
#
# A chat chunk's `scope` field is really a chat `context_key` ("general",
# "budget", "development", ...), not a `scope_tree` scope_key: chats and
# knowledge notes are two different namespaces (design doc sec 7.1), and
# unifying them is a further migration this phase does not own. Only chats
# need the translation HERE; a note's `scope` is already a real scope_key (it
# came straight off `knowledge.all_scope_dirs()`) and a doc's is always "". A
# ticket's `scope` is a real scope_key too: every key `scope_roots()` returns is
# one, so nothing ticket-shaped needs a case here.
#
# A chunk with no scope at all (every doc) is never excluded by a scope
# filter: docs are cross-cutting reference material with no place in the
# tree, not something a narrower scope has failed to reach.

def _resolve_chunk_scope(kind: str, raw_scope: str) -> str:
    if kind != "chat" or not raw_scope:
        return raw_scope
    try:
        from services import knowledge
        return knowledge.scope_for_context(raw_scope)
    except Exception:
        return raw_scope


def _scope_set(scope: str | tuple | list | set | frozenset | None) -> set[str] | None:
    """Normalise the `scope=` argument to a set, or None for "no filter".
    `search()` takes a set of allowed scopes; a bare string is still accepted
    so single-scope callers (existing tests, direct callers) keep working."""
    if scope is None:
        return None
    if isinstance(scope, str):
        return {scope}
    return set(scope)


def _item_ref(kind: str, ref: str, resolved_scope: str) -> str | None:
    """The `share` policy key for a chunk, in the Phase 3 ref vocabulary.

    A chunk's `ref` is whatever its collector recorded: a repo-relative path for
    notes and docs, a thread id for chats. This is the one place that is turned
    back into an addressable ref, so `memory_policy` and the Memory page are
    keyed the same way and a per-item override set in the UI is the one a search
    reads."""
    from services import memory_policy
    if kind == "chat":
        return memory_policy.chat_item_ref(ref)
    if kind == "note":
        note_id = Path(ref).stem
        return f"workshop:note/{resolved_scope}/{note_id}" if resolved_scope else None
    if kind == "doc":
        return f"workshop:doc/{ref}"
    if kind == "ticket":
        from services import refs
        return refs.ticket_ref(ref)
    return None


def _keep_mask(meta: list[dict], kinds, scopes: set[str] | None,
               from_scope: str | None = None) -> np.ndarray:
    """A boolean mask over the whole corpus, computed once and applied to the
    score vector rather than used to fancy-index `vecs` (design doc sec 7.2:
    `vecs[keep] @ q` allocates a submatrix per call; masking a precomputed
    `vecs @ q` does not, and the cost stops depending on how narrow the filter
    is)."""
    n = len(meta)
    mask = np.empty(n, dtype=bool)
    cache: dict[tuple[str, str], str] = {}
    # `from_scope` is the searcher's OWN scope, which `private` needs and plain
    # `reach` does not: "reachable only from inside its own scope" cannot be
    # answered by a set of allowed scopes alone. With no `from_scope` (a direct
    # or test caller that did not say who it is) the policy layer is skipped
    # entirely and this behaves exactly as it did before Phase 5.
    policy = None
    if from_scope:
        from services import memory_policy as policy  # noqa: N813
    for i, m in enumerate(meta):
        if kinds is not None and m.get("kind") not in kinds:
            mask[i] = False
            continue
        raw = m.get("scope") or ""
        kind = m.get("kind")
        if raw:
            key = (kind, raw)
            resolved = cache.get(key)
            if resolved is None:
                resolved = _resolve_chunk_scope(kind, raw)
                cache[key] = resolved
        else:
            resolved = ""
        # Unscoped chunks (docs) are placeless, not unreachable: they sit in no
        # scope, so the tree has nothing to say about them and they stay in.
        in_reach = True if (scopes is None or not resolved) else (resolved in scopes)
        if policy is None:
            mask[i] = in_reach
            continue
        share = policy.resolve(kind, resolved or None,
                               _item_ref(kind, m.get("ref") or "", resolved))
        mask[i] = policy.reachable(share=share, item_scope=resolved,
                                   from_scope=from_scope, in_reach=in_reach)
    return mask


# ---------------------------------------------------------------- search

def search(query: str, *, limit: int = 8,
           kinds: tuple[str, ...] | list[str] | None = None,
           scope: str | tuple | list | set | frozenset | None = None,
           from_scope: str | None = None,
           min_score: float = MIN_SCORE) -> dict:
    """Ranked passages for a query, hybrid: vector when the embedder answers,
    BM25 lexical always available so a search degrades instead of raising.

    Returns `{"hits": [...], "mode": str, "rebuilding": bool}`. `mode` is
    `"semantic"`, `"lexical"`, or `"lexical (embedder unavailable: <reason>)"`
    so a caller (and the model reading the MCP tool's output) knows how much
    to trust the result. `rebuilding` is true when a background rebuild is in
    flight; the hits are still from whatever is on disk right now, never
    withheld while it runs.

    `scope` is the set of scopes the caller may reach (Phase 1's
    `scope_tree.reach`). `from_scope` is the caller's OWN scope, and it is a
    separate argument because the `share` field (Phase 5) needs it: `private`
    means "found only from inside its own scope", which a set of allowed scopes
    cannot express. Omitting it skips the policy layer entirely.

    A hit's BODY is returned, not a title. Being unable to read what was found
    is the defect this module exists to remove."""
    query = (query or "").strip()
    if not query:
        return {"hits": [], "mode": "semantic", "rebuilding": False}

    # Self-healing, off the request path (design doc sec 7.2/7.4): a write
    # left a marker, so a background thread catches the index up while THIS
    # search still answers from what is on disk. On CPU a cold build is ~8
    # minutes; a search must never wait on it.
    try:
        rebuilding = _background_rebuild_if_stale()
    except Exception:
        log.warning("could not start a background rebuild; searching the "
                    "existing index", exc_info=True)
        rebuilding = False

    meta, vecs = load()
    if not meta:
        return {"hits": [], "mode": "semantic", "rebuilding": rebuilding}

    scopes = _scope_set(scope)
    mask = _keep_mask(meta, kinds, scopes, from_scope)
    if not mask.any():
        return {"hits": [], "mode": "semantic", "rebuilding": rebuilding}

    mode = "semantic"
    scores: np.ndarray | None = None
    try:
        q = embed([query])[0]
        scores = vecs @ q
    except Exception as e:
        reason = str(e).strip() or "unknown error"
        if len(reason) > 160:
            reason = reason[:157] + "..."
        log.warning("embedder unavailable, falling back to lexical search: %s",
                    reason, exc_info=True)
        mode = f"lexical (embedder unavailable: {reason})"

    if scores is None:
        scores = _bm25_scores(query, meta)
        floor = 0.0  # BM25 has no meaning below zero: nothing matched.
        is_semantic = False
    else:
        floor = min_score
        is_semantic = True

    masked = np.where(mask, scores, -np.inf)
    order = np.argsort(-masked)[:limit]
    hits = []
    for j in order:
        score = float(masked[int(j)])
        # Ordered descending, so the first miss (or the mask's -inf) ends it.
        if (score < floor) if is_semantic else (score <= floor):
            break
        i = int(j)
        m = meta[i]
        hits.append({
            "score": round(score, 4),
            "kind": m.get("kind"), "ref": m.get("ref"),
            "title": m.get("title"), "scope": m.get("scope"),
            "ts": m.get("ts"), "text": m.get("text"),
        })
    return {"hits": hits, "mode": mode, "rebuilding": rebuilding}


def status() -> dict:
    """What is indexed, for a caller that needs to say why a search was empty."""
    meta, vecs = load()
    by_kind: dict[str, int] = {}
    for m in meta:
        by_kind[m.get("kind", "?")] = by_kind.get(m.get("kind", "?"), 0) + 1
    ok, why = available()
    return {"chunks": len(meta), "dim": int(vecs.shape[1]) if vecs.size else 0,
            "by_kind": by_kind, "model": EMBED_MODEL,
            "embedder_ok": ok, "embedder_error": why}


# ---------------------------------------------------------------- CLI

def pin_owner(owner_id: int | None = None) -> None:
    """Pin the data owner for a call that has no HTTP request behind it.

    The index reads owner-scoped stores (chat logs, the knowledge root), so a CLI
    build or a background pass has to say whose data it is. Defaulting to the
    operator matches `ownership`'s own rule for background loops, and mirrors
    `workshop_query_server._pin_owner`."""
    from services import auth_store, ownership
    oid = owner_id if owner_id is not None else auth_store.operator_id()
    if oid is not None:
        ownership.set_current_owner(oid)


def _main(argv: list[str]) -> int:
    pin_owner()
    verb = argv[1] if len(argv) > 1 else "status"
    if verb == "build":
        kinds = tuple(argv[2].split(",")) if len(argv) > 2 else DEFAULT_KINDS
        print(json.dumps(build(kinds, log_fn=lambda s: print(f"  {s}")), indent=2))
    elif verb == "search":
        result = search(" ".join(argv[2:]), limit=5)
        print(f"mode: {result['mode']}"
              + ("  (rebuilding in background)" if result["rebuilding"] else ""))
        for h in result["hits"]:
            print(f"[{h['score']:.3f}] {h['kind']}: {h['title']}  ({h['ref']})")
            print("    " + h["text"][:180].replace("\n", " ") + "\n")
    else:
        print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
