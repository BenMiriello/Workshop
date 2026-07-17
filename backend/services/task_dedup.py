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
import math
import os
import re
import urllib.request
from pathlib import Path

from services import tasks

# Cosine decision thresholds (design 2e). Calibrated for a semantic embedder;
# the >= MERGE_AT tier is additionally gated by an LLM same-work check so a
# slightly generous threshold cannot cause a silent false merge.
MERGE_AT = 0.90
RELATED_AT = 0.80

# Local Ollama embedding + same-work check (the Ollama service is kept alive for
# this and the local relay). All best-effort: any failure falls back cleanly.
_OLLAMA_URL = os.environ.get("WORKSHOP_OLLAMA_URL", "http://localhost:11434").rstrip("/")
_EMBED_MODEL = os.environ.get("WORKSHOP_EMBED_OLLAMA_MODEL", "bge-m3").strip()
# The same-work confirmation model: a small, fast local instruct model is plenty
# for a binary judgement. Set WORKSHOP_DEDUP_LLM_CHECK=0 to disable the gate
# (pure-cosine merge, the pre-hardening behavior).
_DEDUP_LLM_MODEL = os.environ.get("WORKSHOP_DEDUP_LLM_MODEL", "llama3:latest").strip()

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


def _ollama_embed(texts: list[str]) -> list[list[float]] | None:
    """Embed each text via the local Ollama /api/embeddings (one call per text;
    the corpus is tiny). Returns L2-normalized vectors, or None on any failure so
    the caller falls back to the lexical embedder."""
    if not _EMBED_MODEL:
        return None
    out: list[list[float]] = []
    try:
        for t in texts:
            body = json.dumps({"model": _EMBED_MODEL, "prompt": t or ""}).encode()
            req = urllib.request.Request(
                _OLLAMA_URL + "/api/embeddings",
                data=body, headers={"content-type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                v = json.loads(r.read()).get("embedding")
            if not isinstance(v, list) or not v:
                return None
            v = [float(x) for x in v]
            norm = math.sqrt(sum(x * x for x in v))
            out.append([x / norm for x in v] if norm > 0 else v)
        return out
    except Exception:
        return None


def embed_available() -> bool:
    """Whether Stage-2 cosine embedding is active (vs hash-only fallback).
    False only when force-disabled via WORKSHOP_DEDUP_EMBED=0; a local embedder
    (Ollama semantic, else lexical) is otherwise always available."""
    return os.environ.get("WORKSHOP_DEDUP_EMBED", "1").strip() not in ("0", "false", "no", "")


def embed_backend() -> str:
    """Name of the embedding backend that embed_texts() would use right now, for
    observability + model-change tracking. Cheap probe (no network for the URL
    case; a tiny Ollama call to confirm reachability)."""
    if not embed_available():
        return "disabled"
    if os.environ.get("WORKSHOP_TEXT_EMBED_URL", "").strip():
        return "http-embed"
    if _ollama_embed(["ping"]) is not None:
        return f"ollama:{_EMBED_MODEL}"
    return "lexical"


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Embed each text to an L2-normalized vector, or None if embedding is
    force-disabled (hash-only fallback). Preference: an explicit HTTP text-embed
    service (WORKSHOP_TEXT_EMBED_URL); else local Ollama semantic embeddings; else
    the local lexical embedder. Never raises."""
    if not texts:
        return []
    if not embed_available():
        return None
    url = os.environ.get("WORKSHOP_TEXT_EMBED_URL", "").strip()
    if url:
        got = _http_embed(texts, url)
        if got is not None:
            return got
        # endpoint configured but unreachable -> fall through
    got = _ollama_embed(texts)
    if got is not None:
        return got
    try:
        return [_lexical_vector(t) for t in texts]
    except Exception:
        return None


def _dedup_llm_check_enabled() -> bool:
    return os.environ.get("WORKSHOP_DEDUP_LLM_CHECK", "1").strip() not in ("0", "false", "no", "")


def same_work_llm(a: str, b: str) -> bool | None:
    """Ask a small local model whether two task lines are the SAME unit of work
    (same change, same target). Returns True/False, or None when the check is
    disabled or the model is unreachable (caller treats None as "cannot confirm").
    Deterministic-ish (temperature 0) and cheap; only ever called on the rare
    >= MERGE_AT tier."""
    if not _dedup_llm_check_enabled() or not _DEDUP_LLM_MODEL:
        return None
    prompt = (
        "You are deduplicating a task queue. Two task descriptions are below.\n"
        "Answer strictly whether they are the SAME unit of work: the same change "
        "to the same target (same file/endpoint/feature). Different targets, or a "
        "broader-vs-narrower scope, are NOT the same work.\n\n"
        f"TASK A: {a}\n\nTASK B: {b}\n\n"
        "Answer with exactly one word on the first line: YES or NO."
    )
    try:
        body = json.dumps({
            "model": _DEDUP_LLM_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        }).encode()
        req = urllib.request.Request(
            _OLLAMA_URL + "/api/generate",
            data=body, headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            resp = json.loads(r.read()).get("response") or ""
        head = resp.strip().splitlines()[0].strip().upper() if resp.strip() else ""
        if head.startswith("YES"):
            return True
        if head.startswith("NO"):
            return False
        # Ambiguous answer -> cannot confirm.
        return None
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
    best_text = ""
    best_cos = 0.0
    for t, vec in zip(open_tasks, vectors[1:]):
        c = _cosine(new_vec, vec)
        if c > best_cos:
            best_cos, best_id, best_text = c, t["id"], (t.get("text") or "")

    if best_id is not None and best_cos >= MERGE_AT:
        # Non-destructive gate: a false merge silently drops a distinct unit of
        # work, so confirm same-work with a cheap local LLM before merging. With
        # the gate disabled we keep the pre-hardening pure-cosine merge; with it
        # enabled, only an explicit "yes" merges -- a "no" or an unreachable model
        # downgrades to a linked "related" task rather than risking a false merge.
        if not _dedup_llm_check_enabled():
            return {"decision": "merge", "matched_id": best_id, "cosine": best_cos,
                    "stage": "cosine"}
        confirm = same_work_llm(text, best_text)
        if confirm is True:
            return {"decision": "merge", "matched_id": best_id, "cosine": best_cos,
                    "stage": "cosine+llm"}
        return {"decision": "related", "matched_id": best_id, "cosine": best_cos,
                "stage": "cosine+llm-downgrade", "gate": confirm}
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
) -> dict:
    """Create a task from a chat, deduped against open tasks in this scope.

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
            provenance=prov,
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
        provenance=prov,
    )
    return {
        "decision": "distinct",
        "task": task,
        "matched_id": None,
        "cosine": cosine,
        "message": f"created task {task['id']}",
    }
