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
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from services import paths

log = logging.getLogger("workshop.semantic_index")

EMBED_MODEL = os.environ.get("WORKSHOP_EMBED_MODEL", "nomic-embed-text")
EMBED_BATCH = 32
EMBED_TIMEOUT_S = 120.0

# ~1200 characters is about 300 tokens: long enough to carry an argument, short
# enough that a hit points at a paragraph rather than a document. The overlap
# stops a fact that straddles a boundary from being unfindable from either side.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150

# A chat log is mostly short messages, so they are grouped up to the same size
# rather than embedded one per message: 8,000 one-line chunks would rank noise
# above substance and cost more to build.
KINDS = ("note", "doc", "chat", "vault")

# The vault is the operator's own Obsidian folder. It is indexed only when asked
# for by name: a search about routing code must never surface a personal note.
# See the decision note in docs/memory-and-transcript-simplification-2026-08-12.md.
DEFAULT_KINDS = ("note", "doc", "chat")

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
    kind: str     # note | doc | chat | vault
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
                        json={"model": EMBED_MODEL, "input": batch})
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
    m = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    # A zero vector would divide to NaN and poison every later comparison.
    norms[norms == 0] = 1.0
    return m / norms


def available() -> tuple[bool, str]:
    """Whether embedding can run at all, and why not when it cannot.

    Callers surface the reason instead of failing opaquely: an index that cannot
    build because a model was never pulled is a fixable, nameable condition."""
    try:
        import httpx
        with httpx.Client(timeout=5.0) as cl:
            r = cl.get(f"{_ollama_url()}/api/tags")
            r.raise_for_status()
            names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
        if EMBED_MODEL.split(":")[0] not in names:
            return False, f"ollama is up but the model {EMBED_MODEL} is not pulled"
        return True, ""
    except Exception as e:
        return False, f"ollama is unreachable at {_ollama_url()}: {e}"


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
    """Every knowledge note, across every scope directory that exists."""
    from services import knowledge
    out: list[Chunk] = []
    roots: list[tuple[str, Path]] = []
    try:
        shared = knowledge._shared_knowledge_root()
        roots.append(("workshop", shared))
        for sub in sorted(p for p in shared.iterdir() if p.is_dir()):
            if sub.name != "archive":
                roots.append((sub.name, sub))
    except Exception as e:
        log.warning("knowledge root unavailable: %s", e)
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


_COLLECTORS = {
    "note": collect_notes,
    "doc": collect_docs,
    "chat": collect_chats,
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

    if not rows:
        _save([], np.zeros((0, 0), dtype=np.float32))
        return {"ok": True, "chunks": 0, "embedded": 0, "reused": 0}

    matrix = np.vstack([r for r in rows if r is not None]).astype(np.float32)
    _save(meta, matrix)
    return {"ok": True, "chunks": len(meta), "embedded": len(todo),
            "reused": len(meta) - len(todo), "dim": int(matrix.shape[1])}


# ---------------------------------------------------------------- search

def search(query: str, *, limit: int = 8,
           kinds: tuple[str, ...] | list[str] | None = None,
           scope: str | None = None,
           min_score: float = MIN_SCORE) -> list[dict]:
    """Ranked passages for a query. Empty list when there is nothing to say.

    Returns the BODY, not a title. Being unable to read what was found is the
    defect this module exists to remove."""
    query = (query or "").strip()
    if not query:
        return []
    meta, vecs = load()
    if not meta:
        return []
    keep = [i for i, m in enumerate(meta)
            if (kinds is None or m.get("kind") in kinds)
            and (scope is None or m.get("scope") == scope)]
    if not keep:
        return []
    q = embed([query])[0]
    sims = vecs[keep] @ q
    order = np.argsort(-sims)[:limit]
    out = []
    for j in order:
        score = float(sims[int(j)])
        # Ordered descending, so the first miss ends it.
        if score < min_score:
            break
        i = keep[int(j)]
        m = meta[i]
        out.append({
            "score": round(float(sims[int(j)]), 4),
            "kind": m.get("kind"), "ref": m.get("ref"),
            "title": m.get("title"), "scope": m.get("scope"),
            "ts": m.get("ts"), "text": m.get("text"),
        })
    return out


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
        for h in search(" ".join(argv[2:]), limit=5):
            print(f"[{h['score']:.3f}] {h['kind']}: {h['title']}  ({h['ref']})")
            print("    " + h["text"][:180].replace("\n", " ") + "\n")
    else:
        print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
