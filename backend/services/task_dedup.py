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

Embedding backend: the local birds `/embed` model is AUDIO only, so it cannot
embed task text. We use a dependency-light local text embedder (a hashing
term-frequency vectorizer over canonical tokens, numpy for the cosine) that needs
no model download and works on the tiny task corpus. A real text-embedding
service can be wired later through WORKSHOP_TEXT_EMBED_URL (an HTTP endpoint that
takes {"texts": [...]} and returns {"vectors": [[...], ...]}); if that endpoint is
set but unreachable, or embedding is force-disabled (WORKSHOP_DEDUP_EMBED=0), we
fall back to hash-only dedup (exact dupes still merge) and never crash.

Single-operator system: scope here means correctness (a chat dedups within its
own task scope), NOT a permission wall.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from pathlib import Path

from services import tasks

# Cosine decision thresholds (design 2e).
MERGE_AT = 0.90
RELATED_AT = 0.80

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
    """The canonical form for exact-dupe hashing: sorted unique-preserving token
    multiset joined by spaces (sorted so word-order variants hash the same)."""
    return " ".join(sorted(canonical_tokens(text)))


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


def _http_embed(texts: list[str], url: str) -> list[list[float]] | None:
    """POST texts to an external text-embed endpoint. Returns L2-normalized
    vectors, or None on any failure (caller falls back)."""
    try:
        body = json.dumps({"texts": texts}).encode()
        req = urllib.request.Request(
            url, data=body, headers={"content-type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            vecs = json.loads(r.read()).get("vectors")
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            return None
        out: list[list[float]] = []
        for v in vecs:
            v = [float(x) for x in v]
            norm = math.sqrt(sum(x * x for x in v))
            out.append([x / norm for x in v] if norm > 0 else v)
        return out
    except Exception:
        return None


def embed_available() -> bool:
    """Whether Stage-2 cosine embedding is active (vs hash-only fallback).
    False only when force-disabled via WORKSHOP_DEDUP_EMBED=0; the local lexical
    embedder is otherwise always available (no external dependency)."""
    return os.environ.get("WORKSHOP_DEDUP_EMBED", "1").strip() not in ("0", "false", "no", "")


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed each text to an L2-normalized vector, or None if embedding is
    force-disabled (hash-only fallback). Prefers an external text-embed service
    (WORKSHOP_TEXT_EMBED_URL) when set and reachable; otherwise uses the local
    lexical embedder. Never raises."""
    if not texts:
        return []
    if not embed_available():
        return None
    url = os.environ.get("WORKSHOP_TEXT_EMBED_URL", "").strip()
    if url:
        got = _http_embed(texts, url)
        if got is not None:
            return got
        # endpoint configured but unreachable -> fall through to local embedder
    try:
        return [_lexical_vector(t) for t in texts]
    except Exception:
        return None


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

def _open_tasks(root: Path) -> list[dict]:
    """Open (non-terminal) tasks in a scope, the dedup comparison set."""
    return [t for t in tasks.list_tasks(root, include_done=False)
            if t.get("status") not in tasks.TERMINAL and (t.get("text") or "").strip()]


def decide(text: str, open_tasks: list[dict]) -> dict:
    """Classify a new task text against the scope's open tasks WITHOUT mutating
    anything. Returns {decision, matched_id, cosine, stage} where decision is one
    of exact | merge | related | distinct. `exact` and `merge` both mean "attach
    to matched_id, do not create"; `related` means "create + link to matched_id";
    `distinct` means "create fresh"."""
    new_hash = canonical_hash(text)
    for t in open_tasks:
        if canonical_hash(t.get("text") or "") == new_hash:
            return {"decision": "exact", "matched_id": t["id"], "cosine": 1.0, "stage": "hash"}

    vectors = embed_texts([text] + [t.get("text") or "" for t in open_tasks])
    if vectors is None or len(vectors) != len(open_tasks) + 1:
        # hash-only fallback: no exact match above, so this is a new distinct task.
        return {"decision": "distinct", "matched_id": None, "cosine": None, "stage": "hash-only"}

    new_vec = vectors[0]
    best_id = None
    best_cos = 0.0
    for t, vec in zip(open_tasks, vectors[1:]):
        c = _cosine(new_vec, vec)
        if c > best_cos:
            best_cos, best_id = c, t["id"]

    if best_id is not None and best_cos >= MERGE_AT:
        return {"decision": "merge", "matched_id": best_id, "cosine": best_cos, "stage": "cosine"}
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
) -> dict:
    """Create a task from a chat, deduped against open tasks in this scope.

    Returns {decision, task, matched_id, cosine, message} where `task` is the
    canonical existing task (on merge) or the newly created one (related/distinct),
    and `message` is a short operator-facing line the chat can surface.

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

    d = decide(text, _open_tasks(root))
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

    # distinct
    task = tasks.add(
        root, text=text, priority=priority, depends_on=depends_on or [],
        acceptance=acceptance, touches=touches or [], source=src,
    )
    return {
        "decision": "distinct",
        "task": task,
        "matched_id": None,
        "cosine": cosine,
        "message": f"created task {task['id']}",
    }
