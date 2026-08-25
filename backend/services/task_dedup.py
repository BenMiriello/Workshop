"""Task creation with cross-chat dedup (Stage 7 / design 2e).

When a task is filed from a chat (the `spawn-task` endpoint and the `task-patch`
chat protocol), two different chats can independently file the SAME work and the
queue silently fills with duplicates. This module is the single creation path
that dedups against existing OPEN tasks in the SAME scope before adding:

  Stage 1 (canonical hash): lowercase, tokenize, drop stopwords, sort -> a
    canonical string + sha256. An exact canonical-hash match is a trivial dupe
    and merges with certainty (no embedding needed).

  Stage 2 (embedding cosine): embed the new text + every open task's text and
    brute-force cosine (the corpus is tiny, so no LSH). Auto-decide, no prompts:
      cosine >= 0.90  -> merge:   attach the new chat's provenance to the existing
                                  task (evidence), do NOT create a duplicate.
      0.80 <= c < 0.90 -> related: create the task AND link it to the near-match
                                  (discovered_from + an evidence back-reference).
      cosine < 0.80   -> distinct: a plain new task.

Embedding backend (preference order): an explicit HTTP text-embed service
(WORKSHOP_TEXT_EMBED_URL, {"texts": [...]} -> {"vectors": [[...], ...]}); else the
local Ollama service (kept alive for exactly this) with a real semantic model
(WORKSHOP_EMBED_OLLAMA_MODEL, default bge-m3) via /api/embeddings; else a
dependency-light lexical hashing term-frequency vectorizer. The lexical fallback
undershoots on paraphrase (two duplicate lines can score ~0.83 and be filed
"related" instead of merged), which is exactly why the Ollama semantic path is
preferred. The birds `/embed` model is AUDIO only and cannot embed task text. If
every backend is unreachable, or embedding is force-disabled
(WORKSHOP_DEDUP_EMBED=0), we fall back to hash-only dedup (exact dupes still
merge) and never crash.

Merge safety (non-destructive): a cosine >= MERGE_AT match is CONFIRMED by a
cheap local LLM same-work check before merging, because a false merge silently
drops a distinct unit of work (the worst failure of the ticket pipeline). If the
LLM says "not the same work" (or is unreachable and we cannot confirm), the
decision is downgraded to "related" (create + link) rather than merged. The merge
itself is already non-destructive: it folds the new chat's provenance and exact
wording into the surviving task's evidence, so no signal is lost.

Single-operator system: scope here means correctness (a chat dedups within its
own task scope), NOT a permission wall.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path

from services import tasks

log = logging.getLogger(__name__)

# Cosine decision thresholds (design 2e). Calibrated for a semantic embedder.
# A cosine >= MERGE_AT is a near-duplicate, but nothing auto-merges on cosine
# alone: a false merge silently drops a unit of work, so the high tier LINKS
# (creates a `related` task pointing at the match) rather than merging. Merging
# was previously gated by a local Ollama LLM confirmation; that local-model
# dependency was removed on 2026-08-20, and without a confirmation the safe,
# non-destructive default is to link. A future hosted confirmation could
# re-enable auto-merge behind this same threshold.
MERGE_AT = 0.90
RELATED_AT = 0.80

# Embeddings run through services/embeddings.py, an in-process local ONNX model
# (bge-small-en-v1.5, 384-d) with no daemon and no network. This replaced the
# local Ollama /api/embeddings backend on 2026-08-20. On any failure the lexical
# hashing vectorizer below is the fallback so filing never breaks.

# Hashing-vectorizer width. Small is fine: the task corpus per scope is tiny and
# collisions only slightly blur cosine, never break the hash-exact merge path.
_EMBED_DIM = 512

# Minimal stopword set: articles, prepositions, conjunctions, and copulas only.
# We deliberately KEEP verbs like "add"/"fix"/"remove" because they carry the
# actual intent of a task line.
_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "at", "with",
    "by", "from", "into", "onto", "is", "are", "be", "this", "that", "it", "as",
    "so", "then", "than", "but", "if", "we", "you", "i",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def canonical_tokens(text: str) -> list[str]:
    """Lowercase, tokenize to alnum runs, drop stopwords. Order preserved (the
    canonical STRING sorts; the vector uses raw term frequency)."""
    toks = _TOKEN_RE.findall((text or "").lower())
    return [t for t in toks if t not in _STOPWORDS]


def canonical(text: str) -> str:
    """The canonical form for exact-dupe hashing: tokens in ORDER, joined by
    spaces. Order is kept on purpose: "move header above footer" and "move footer
    above header" are DIFFERENT work and must not hash the same. Word-order
    variants that really are the same task are caught downstream by the semantic
    cosine + LLM same-work gate, not by the exact-hash path."""
    return " ".join(canonical_tokens(text))


def canonical_hash(text: str) -> str:
    return hashlib.sha256(canonical(text).encode("utf-8")).hexdigest()


# ---- embedding ------------------------------------------------------------

def _lexical_vector(text: str) -> list[float]:
    """A hashing term-frequency vector over canonical tokens, L2-normalized.
    Dependency-light (no model): cosine of two of these is the normalized token
    overlap, which is a sound similarity signal for short task lines on a tiny
    corpus. Empty text -> zero vector."""
    vec = [0.0] * _EMBED_DIM
    toks = canonical_tokens(text)
    for tok in toks:
        idx = int(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).hexdigest(), 16) % _EMBED_DIM
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


# ---- vector cache ---------------------------------------------------------
# Keyed by (backend/model, text). The MODEL is in the key rather than checked
# separately, so switching embedders cannot serve a vector from the old one: a
# cosine between two models' vectors is a number with no meaning, and a silent
# wrong merge is the expensive failure here. An edited ticket changes its text,
# so it changes its key, so it is re-embedded; there is nothing to invalidate.
_VEC_CACHE_MAX = 600          # ~2 scopes' backlogs with room, at ~9KB each
_VEC_CACHE_DIGITS = 5         # cosine is unaffected well before this precision
_vec_cache_mem: dict[str, list[float]] | None = None


def _vec_cache_path() -> Path:
    from wsplatform import paths
    return paths.runtime("dedup-vectors.json")


def _active_backend() -> str:
    """The backend identity to key cached vectors by. The provider name changes
    with the model, so a model swap changes every key and old vectors are never
    served. `provider_name()` does not load the model, so this is cheap; it only
    fails if the provider is misconfigured, in which case the lexical fallback
    owns the key."""
    from services import embeddings
    try:
        return embeddings.provider_name()
    except Exception:
        return "lexical"


def _vec_key(text: str) -> str:
    return hashlib.sha256(f"{_active_backend()}\x00{text or ''}".encode()).hexdigest()[:32]


def _vec_cache() -> dict[str, list[float]]:
    global _vec_cache_mem
    if _vec_cache_mem is not None:
        return _vec_cache_mem
    try:
        raw = json.loads(_vec_cache_path().read_text())
        _vec_cache_mem = {k: v for k, v in raw.items()
                          if isinstance(v, list) and v}
    except Exception:
        _vec_cache_mem = {}
    return _vec_cache_mem


def _save_vec_cache(cache: dict[str, list[float]]) -> None:
    """Best effort: a cache that cannot be written costs speed, never
    correctness, so a failure here must not reach a filing."""
    try:
        if len(cache) > _VEC_CACHE_MAX:
            # Insertion-ordered, so the oldest keys go first.
            for k in list(cache)[: len(cache) - _VEC_CACHE_MAX]:
                cache.pop(k, None)
        path = _vec_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        trimmed = {k: [round(x, _VEC_CACHE_DIGITS) for x in v]
                   for k, v in cache.items()}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(trimmed))
        tmp.replace(path)
    except Exception:
        log.debug("could not write the dedup vector cache", exc_info=True)


def warm_vectors(roots: list[Path] | None = None) -> dict:
    """Embed the open backlog into the vector cache, off the request path.

    The cache makes a filing fast (0.16s measured, from 17.5s) but only once it
    holds the backlog. A cold cache puts the whole cost back on whoever files
    first, which is the operator pressing a button. So the backend warms it at
    startup instead. It is a no-op costing one dict lookup per ticket when
    everything is already cached, which is the normal case after the first run,
    because the cache is a file and outlives the process.
    """
    from services import task_scope
    if not embed_available():
        return {"skipped": "embedding disabled"}
    if roots is None:
        try:
            roots = [Path(task_scope.root_for_context(k))
                     for k in ("development", "workshop")]
        except Exception:
            return {"skipped": "no scopes"}
    cache = _vec_cache()
    wanted: list[str] = []
    for root in roots:
        try:
            wanted += [t.get("text") or "" for t in _open_tasks(Path(root))]
        except Exception:
            continue
    missing = [t for t in wanted if t and _vec_key(t) not in cache]
    if missing:
        embed_texts(missing)
    return {"tickets": len(wanted), "embedded": len(missing)}


def embed_available() -> bool:
    """Whether Stage-2 cosine embedding is active (vs hash-only fallback).
    False only when force-disabled via WORKSHOP_DEDUP_EMBED=0; a local embedder
    (Ollama semantic, else lexical) is otherwise always available."""
    return os.environ.get("WORKSHOP_DEDUP_EMBED", "1").strip() not in ("0", "false", "no", "")


def embed_backend() -> str:
    """Name of the embedding backend embed_texts() would use right now, for
    observability + model-change tracking. Cheap: the provider name is a string,
    it does not load the model or hit the network."""
    if not embed_available():
        return "disabled"
    return _active_backend()


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed each text to an L2-normalized vector, or None if embedding is
    force-disabled (hash-only fallback). Uses the in-process local provider
    (services/embeddings), falling back to the lexical hashing vectorizer if the
    provider cannot run. Never raises.

    CACHED BY TEXT, which is what makes filing a ticket fast. `decide` compares a
    new ticket against every OPEN ticket in the scope, so it asks for the new
    text plus the whole backlog. Measured 2026-08-16 on the live store (against
    the older per-text Ollama call): 28 texts, 17.5 seconds, paid on every single
    filing, which is the delay the operator reported. A ticket's vector depends
    only on its words, and the backlog's words almost never change between two
    filings, so caching means all but one of those embeds is skipped. The local
    ONNX model is far faster than the old network call, but the cache still earns
    its keep on a large backlog.
    """
    if not texts:
        return []
    if not embed_available():
        return None
    cache = _vec_cache()
    keys = [_vec_key(t) for t in texts]
    missing = [t for t, k in zip(texts, keys) if k not in cache]
    if missing:
        # Embed ONLY what is not cached, preserving order for the zip below.
        fresh = _embed_uncached(list(dict.fromkeys(missing)))
        if fresh is None:
            return None
        for text, vec in fresh.items():
            cache[_vec_key(text)] = vec
        _save_vec_cache(cache)
    out = [cache.get(k) for k in keys]
    if any(v is None for v in out):
        return None
    return out


def _embed_uncached(texts: list[str]) -> dict[str, list[float]] | None:
    """The actual embedding call, for texts with no cached vector. The in-process
    local provider first; the lexical hashing vectorizer if it cannot run, so
    filing never breaks on an embedding failure."""
    from services import embeddings
    got: list[list[float]] | None = None
    try:
        raw = embeddings.embed_documents(texts)
        got = []
        for v in raw:
            v = [float(x) for x in v]
            norm = math.sqrt(sum(x * x for x in v))
            got.append([x / norm for x in v] if norm > 0 else v)
    except Exception as e:  # noqa: BLE001
        log.warning("embedding provider failed, using lexical fallback: %s", e)
        got = None
    if got is None:
        try:
            got = [_lexical_vector(t) for t in texts]
        except Exception:
            return None
    if got is None or len(got) != len(texts):
        return None
    return dict(zip(texts, got))


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine of two already-L2-normalized vectors (dot product). Guards length
    mismatch and unnormalized input defensively."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    # a,b are normalized upstream; clamp for float drift.
    return max(-1.0, min(1.0, dot))


# ---- decision -------------------------------------------------------------

# parked = dead-letter (a poison-pill task next_ready never picks). A fresh
# legitimate request must NOT be folded into a parked task: the work would
# silently never happen while the chat reports "merged". Exclude parked from the
# dedup candidate set so such a request becomes its own live task.
_DEDUP_EXCLUDE = {"parked"}


def _open_tasks(root: Path) -> list[dict]:
    """The dedup comparison set: open (non-terminal, non-parked) tasks in a scope
    that a new task could legitimately merge into or link against."""
    return [t for t in tasks.list_tasks(root, include_done=False)
            if t.get("status") not in tasks.TERMINAL
            and t.get("status") not in _DEDUP_EXCLUDE
            and (t.get("text") or "").strip()]


def decide(text: str, open_tasks: list[dict], *,
           semantic: bool = True) -> dict:
    """Classify a new task text against the scope's open tasks WITHOUT mutating
    anything. Returns {decision, matched_id, cosine, stage} where decision is one
    of exact | merge | related | distinct. `exact` and `merge` both mean "attach
    to matched_id, do not create"; `related` means "create + link to matched_id";
    `distinct` means "create fresh". Cosine alone never returns `merge` (see
    MERGE_AT): only the hash-exact tier attaches, everything else links."""
    new_hash = canonical_hash(text)
    for t in open_tasks:
        if canonical_hash(t.get("text") or "") == new_hash:
            return {"decision": "exact", "matched_id": t["id"], "cosine": 1.0, "stage": "hash"}

    if not semantic:
        # THE INTERACTIVE ANSWER. The hash tier above is free; everything below
        # costs a network round trip per uncached text and, when the local model
        # has been evicted, seconds of model load. Nobody watching a button
        # should pay that: the caller files now on the cheap answer and runs
        # `link_near_matches` afterwards, which does this work off the request
        # and records a LINK rather than a merge.
        return {"decision": "distinct", "matched_id": None, "cosine": None,
                "stage": "hash-only-interactive"}

    vectors = embed_texts([text] + [t.get("text") or "" for t in open_tasks])
    if vectors is None or len(vectors) != len(open_tasks) + 1:
        # hash-only fallback: no exact match above, so this is a new distinct task.
        return {"decision": "distinct", "matched_id": None, "cosine": None, "stage": "hash-only"}

    new_vec = vectors[0]
    best_id = None
    best_text = ""
    best_cos = 0.0
    for t, vec in zip(open_tasks, vectors[1:]):
        c = _cosine(new_vec, vec)
        if c > best_cos:
            best_cos, best_id, best_text = c, t["id"], (t.get("text") or "")

    if best_id is not None and best_cos >= MERGE_AT:
        # A near-duplicate, but nothing auto-merges on cosine alone: a false
        # merge silently drops a distinct unit of work. Merging used to be gated
        # by a local Ollama same-work confirmation, which was removed with the
        # rest of the local-model dependency (2026-08-20). Without a confirmation
        # the safe result is a LINK: the ticket is created and points at the
        # match, nothing is taken away. A future hosted confirmation could
        # re-enable a `merge` here behind this same threshold.
        return {"decision": "related", "matched_id": best_id, "cosine": best_cos,
                "stage": "cosine-high-no-confirm"}
    if best_id is not None and best_cos >= RELATED_AT:
        return {"decision": "related", "matched_id": best_id, "cosine": best_cos, "stage": "cosine"}
    return {"decision": "distinct", "matched_id": best_id, "cosine": best_cos, "stage": "cosine"}


# ---- creation -------------------------------------------------------------

def dedup_add(
    root: Path,
    *,
    text: str,
    source: dict | None = None,
    priority: int = 0,
    depends_on: list[str] | None = None,
    acceptance: dict | None = None,
    touches: list[str] | None = None,
    provenance: str | None = None,
    effort: str | None = None,
    type: str | None = None,
    labels: list[str] | None = None,
    scoped_by: str | None = None,
    status: str = "todo",
    semantic: bool = True,
) -> dict:
    """Create a task from a chat, deduped against open tasks in this scope.

    ``status`` is what a NEWLY created task starts as. A ticket the system wrote
    for itself starts as ``draft``: a suggestion the operator has not agreed to
    yet. A merge never changes the matched task's status — folding a new request
    into live work must not quietly send it back for re-approval.

    Returns {decision, task, matched_id, cosine, message} where `task` is the
    canonical existing task (on merge) or the newly created one (related/distinct),
    and `message` is a short operator-facing line the chat can surface.

    `provenance` overrides the default dedupe key (the canonical text hash) when
    the caller has a STABLER identity for the work than its wording — a triager
    passes `note:<scope>:<id>`, so re-filing the same note is idempotent in
    `tasks.add` even if the model phrases the task differently on a later pass.
    Omitted (the chat path), the canonical hash is used exactly as before.

    - merge/exact: no new task; the matched task gets an evidence entry carrying
      the new chat's provenance (the signal is preserved, not dropped).
    - related: a new task is created with discovered_from + an evidence link to
      the near-match, and the near-match gets a back-reference evidence entry.
    - distinct: a plain new task.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("text required")
    src = source or {"kind": "chat", "ref": None}
    src_ref = src.get("ref") or "chat"
    prov = provenance or canonical_hash(text)

    d = decide(text, _open_tasks(root), semantic=semantic)
    decision = d["decision"]
    matched_id = d.get("matched_id")
    cosine = d.get("cosine")

    if decision in ("exact", "merge"):
        # Do not create a duplicate: fold the new provenance into the existing
        # task so the signal (who else asked for this, and their exact wording)
        # is not lost.
        note = f"also filed from {src_ref}: {text}"
        merged = tasks.update(
            root, matched_id,
            evidence={"kind": "dedup-merge", "ref": note},
        )
        target = merged or tasks.get_task(root, matched_id)
        cpart = f" (cosine {cosine:.2f})" if isinstance(cosine, float) else ""
        return {
            "decision": decision,
            "task": target,
            "matched_id": matched_id,
            "cosine": cosine,
            "message": f"merged into existing task {matched_id}{cpart}",
        }

    if decision == "related":
        task = tasks.add(
            root, text=text, priority=priority, depends_on=depends_on or [],
            acceptance=acceptance, touches=touches or [],
            discovered_from=matched_id, source=src,
            provenance=prov, effort=effort, type=type, labels=labels or [],
            scoped_by=scoped_by, status=status,
        )
        # Evidence on BOTH sides so the link is navigable either direction.
        tasks.update(root, task["id"],
                     evidence={"kind": "related-to", "ref": matched_id})
        tasks.update(root, matched_id,
                     evidence={"kind": "related-from", "ref": task["id"]})
        cpart = f" (cosine {cosine:.2f})" if isinstance(cosine, float) else ""
        return {
            "decision": "related",
            "task": task,
            "matched_id": matched_id,
            "cosine": cosine,
            "message": f"created task {task['id']}, linked related to {matched_id}{cpart}",
        }

    # distinct. provenance defaults to the canonical hash so two chats filing the
    # same text concurrently collapse atomically under the store lock (TOCTOU:
    # both decide "distinct" before either commits; add() returns the first under
    # the lock). A caller-supplied key (a note id) collapses the same way.
    task = tasks.add(
        root, text=text, priority=priority, depends_on=depends_on or [],
        acceptance=acceptance, touches=touches or [], source=src,
        provenance=prov, effort=effort, type=type, labels=labels or [],
        scoped_by=scoped_by, status=status,
    )
    return {
        "decision": "distinct",
        "task": task,
        "matched_id": None,
        "cosine": cosine,
        "message": f"created task {task['id']}",
    }


# ---- the semantic pass, off the request path -------------------------------

def link_near_matches(root: Path, task_id: str) -> dict:
    """Find near-duplicates of a just-filed ticket and LINK them.

    This is the half of dedup that costs seconds: an embedding call per uncached
    text, and a local model that may have to be loaded. `dedup_add(semantic=
    False)` files on the free hash tier and the caller runs this afterwards, so
    the operator's click returns immediately and the comparison still happens.

    IT LINKS, IT NEVER MERGES, and that is a deliberate difference from the
    synchronous path. A merge means "your ticket is not a new ticket", which is
    an answer the operator can only be given while he is still looking at the
    thing he filed. Deciding it a second later, behind his back, would make a
    ticket he watched being created disappear into another one. That happened on
    2026-08-15 and it read as the filing having failed. A link says the same
    thing about the work and takes nothing away.
    """
    task = tasks.get_task(root, task_id)
    if not task:
        return {"status": "gone"}
    text = (task.get("text") or "").strip()
    if not text:
        return {"status": "empty"}
    others = [t for t in _open_tasks(root) if t.get("id") != task_id]
    if not others:
        return {"status": "nothing to compare"}
    d = decide(text, others)
    matched_id, cosine = d.get("matched_id"), d.get("cosine")
    if d["decision"] == "distinct" or not matched_id:
        return {"status": "distinct", "cosine": cosine}
    # Both directions, the same shape the synchronous `related` branch writes.
    near = "a likely duplicate" if d["decision"] in ("exact", "merge") else "related work"
    cpart = f" (cosine {cosine:.2f})" if isinstance(cosine, float) else ""
    tasks.update(root, task_id,
                 evidence={"kind": "related-to",
                           "ref": f"{matched_id}: {near}{cpart}"})
    tasks.update(root, matched_id,
                 evidence={"kind": "related-from",
                           "ref": f"{task_id}: {near}{cpart}"})
    return {"status": "linked", "matched_id": matched_id, "cosine": cosine,
            "decision": d["decision"]}
