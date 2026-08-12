"""LearningsStore (Stage 8, 2f): durable learnings at three scopes.

A learning is a short, pattern-focused lesson the system accrues from work and
from operator feedback: a recurring failure pattern, an operator correction, a
verified approach. This is the structured successor to the legacy
`scripts/log_learning.sh` + `shared/learnings.md` append log: entries now carry
a stable id, source, timestamp, tags, and a provenance list, and are deduped so
the same lesson is not recorded twice.

Scopes
------
- `global`: the workspace-wide guardrails store. Injected into every agent's
  context. This is where cross-project corrections and the seed rules live. The
  legacy `shared/learnings.md` is mirrored here (readable, hook-injected) and
  migrated in on first read.
- `project:<slug>` / `agent:<slug>` / `app:<name>` / `workshop`: a scope's own
  accumulated memory, mirroring the knowledge scope keys. Injected only into
  that scope's chats and sweeps (see chat_context.learnings_context).

Storage
-------
Structured JSONL under `.runtime/learnings/<safe-scope>.jsonl` is the source of
truth (one object per line, append-ordered so a stable prefix stays byte-stable
for prompt caching). The `global` scope additionally mirrors human-readable
entries into `shared/learnings.md` so the existing SessionStart hook keeps
seeing them.

Dedup
-----
Single-operator corpus is tiny, so no LSH: canonicalize (lowercase, collapse
whitespace, drop trailing punctuation) + exact hash catches trivial dupes, and a
token-set Jaccard >= 0.85 vs existing in-scope entries catches near-dupes. A
duplicate does not drop the signal: the new source + timestamp are attached to
the existing entry's provenance.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import time
from pathlib import Path

# Reuse the knowledge module's root/runtime resolution and scope validation so
# the two stores agree on where things live and what a scope key looks like.
from services import knowledge

# Global guardrails scope key.
GLOBAL = "global"

# How many entries a memory block renders so an injected block stays bounded by
# count. The corpus is small; this is a ceiling, not an expected size. The token
# budget below is the harder cap: whichever binds first wins.
_MAX_BLOCK_ENTRIES = int(os.environ.get("LEARNINGS_BLOCK_MAX_ENTRIES", "60"))

# Fixed prompt-token budget for one injected block, approximated at ~4 chars per
# token (_approx_tokens). This is the anti-rot cap: no matter how many long
# learnings a scope accumulates, the injected block cannot exceed this, so it
# cannot bloat and rot the prompt. Env-overridable.
_BLOCK_TOKEN_BUDGET = int(os.environ.get("LEARNINGS_BLOCK_TOKEN_BUDGET", "700"))

# Retrieval tracking is throttled so per-render bookkeeping stays disk-light: an
# entry's retrieval_count is persisted at most once per this window per scope
# (renders inside the window bump nothing on disk). The signal is "retrieved in
# roughly this many recent windows", which is all age-out needs.
_RETRIEVAL_THROTTLE_SEC = int(os.environ.get("LEARNINGS_RETRIEVAL_THROTTLE_SEC", "3600"))

# Age-out: an entry is a candidate for demotion when it has not been retrieved
# (or, never retrieved, not been created) within this many days AND has low
# reinforcement (provenance count below _AGEOUT_MIN_PROVENANCE) AND a low
# retrieval count (below _AGEOUT_MIN_RETRIEVAL). Recently-retrieved OR
# well-reinforced entries are always kept. Archived, never hard-deleted.
_AGEOUT_DAYS = float(os.environ.get("LEARNINGS_AGEOUT_DAYS", "90"))
_AGEOUT_MIN_PROVENANCE = int(os.environ.get("LEARNINGS_AGEOUT_MIN_PROVENANCE", "2"))
_AGEOUT_MIN_RETRIEVAL = int(os.environ.get("LEARNINGS_AGEOUT_MIN_RETRIEVAL", "1"))

# Near-duplicate threshold on canonical token-set Jaccard.
_NEAR_DUP_JACCARD = 0.85

# Trivial stopwords dropped before the token-set comparison (keeps short lessons
# comparable without a real NLP dependency).
_STOPWORDS = {
    "the", "a", "an", "to", "of", "and", "or", "is", "are", "was", "were", "be",
    "in", "on", "at", "for", "with", "as", "by", "it", "this", "that", "not",
    "do", "dont", "when", "if", "you", "your", "its", "into", "than", "then",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# scope key -> file. `global` and `workshop` are plain names; the rest use the
# knowledge scope shape (`project:slug` etc). ':' -> '-' for a safe filename.
_SAFE_SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9:_-]*", re.IGNORECASE)


# --- roots -------------------------------------------------------------------
def _dir() -> Path:
    d = knowledge._runtime() / "learnings"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _legacy_md() -> Path:
    return knowledge._root() / "shared" / "learnings.md"


def _valid_scope(scope_key: str) -> str:
    scope_key = (scope_key or "").strip()
    if scope_key in (GLOBAL, "workshop"):
        return scope_key
    if _SAFE_SCOPE_RE.fullmatch(scope_key):
        # Defer to knowledge for the structured shapes so we reject junk.
        knowledge._parse_scope(scope_key)
        return scope_key
    raise ValueError(f"invalid learnings scope: {scope_key!r}")


def _path(scope_key: str) -> Path:
    return _dir() / (_valid_scope(scope_key).replace(":", "-") + ".jsonl")


# --- canonicalization + dedup ------------------------------------------------
def _canonical(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .,:;!?-")


def _canon_hash(text: str) -> str:
    return hashlib.sha1(_canonical(text).encode("utf-8")).hexdigest()[:12]


def _token_set(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


# --- ranking + token budget --------------------------------------------------
def _approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token). Good enough to hold a prompt block
    under a fixed budget without pulling in a tokenizer dependency."""
    return (len(text or "") + 3) // 4


def _parse_ts(s: str) -> float:
    """Epoch seconds for a `_now()`-shaped UTC stamp. timegm keeps it UTC so the
    absolute age-out comparison against time.time() is correct."""
    try:
        return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0


_SEVERITY_NUM = {"high": 1.0, "critical": 1.0, "med": 0.5, "medium": 0.5, "low": 0.2}


def _severity_num(sev) -> float:
    if isinstance(sev, (int, float)):
        try:
            return max(0.0, min(1.0, float(sev)))
        except (ValueError, TypeError):
            return 0.0
    return _SEVERITY_NUM.get(str(sev or "").strip().lower(), 0.0)


def _recency_ts(e: dict) -> float:
    """When the entry last mattered: last retrieval if any, else creation."""
    return _parse_ts(e.get("last_retrieved") or e.get("created_at") or "")


def _entry_value(e: dict, newest_ts: float, oldest_ts: float) -> float:
    """Rank score in ~[0, 3.5]: recency + reinforcement + severity + retrieval.
    Most-recent, most-reinforced, highest-severity entries score highest and so
    survive the token budget; the rest are dropped into the "+N more" remainder."""
    span = (newest_ts - oldest_ts) or 1.0
    recency = (_recency_ts(e) - oldest_ts) / span  # 0..1, newer is higher
    prov = len(e.get("provenance") or [])
    reinforce = min(prov, 5) / 5.0                  # 0..1, saturates at 5
    severity = _severity_num(e.get("severity"))     # 0..1
    retr = min(int(e.get("retrieval_count", 0) or 0), 10) / 10.0  # 0..1
    return recency + reinforce + severity + 0.5 * retr


# --- IO ----------------------------------------------------------------------
def _read(scope_key: str) -> list[dict]:
    p = _path(scope_key)
    if not p.exists():
        out = []
    else:
        out = []
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("text"):
                out.append(obj)
    if scope_key == GLOBAL:
        _migrate_legacy_md(out, p)
    return out


def _write_all(scope_key: str, entries: list[dict]) -> None:
    p = _path(scope_key)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + ("\n" if entries else ""))
    os.replace(tmp, p)


def _append_line(scope_key: str, entry: dict) -> None:
    p = _path(scope_key)
    with p.open("a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def _archive_path(scope_key: str) -> Path:
    d = _dir() / "archive"
    d.mkdir(parents=True, exist_ok=True)
    return d / (_valid_scope(scope_key).replace(":", "-") + ".jsonl")


def _archive_append(scope_key: str, entries: list[dict]) -> None:
    """Append demoted/merged-away entries to the scope's archive JSONL so history
    stays inspectable (age-out demotes, it does not destroy)."""
    if not entries:
        return
    p = _archive_path(scope_key)
    with p.open("a") as f:
        for e in entries:
            f.write(json.dumps(e, sort_keys=True) + "\n")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- legacy shared/learnings.md bridge ---------------------------------------
_LEGACY_LINE_RE = re.compile(r"^-\s+(?P<text>.*?)(?:\s+\[src:(?P<src>[^\s\]]+)(?:\s+sha:(?P<sha>[a-f0-9]+))?\])?\s*$")


def _migrate_legacy_md(entries: list[dict], global_path: Path) -> None:
    """One-time fold of `shared/learnings.md` bullet lines into the global JSONL
    store, so nothing already learned is lost. Runs when the md holds lines the
    JSONL does not yet have (matched by canonical hash). Idempotent."""
    md = _legacy_md()
    if not md.exists():
        return
    have = {e.get("hash") for e in entries}
    added = False
    for raw in md.read_text().splitlines():
        raw = raw.rstrip()
        if not raw.startswith("- "):
            continue
        m = _LEGACY_LINE_RE.match(raw)
        if not m:
            continue
        text = m.group("text").strip()
        # Strip a trailing " - <timestamp>" the legacy shell writer appended. The
        # class covers both a hyphen and the legacy em-dash separator (u2014).
        text = re.sub("\\s+[-\\u2014]\\s+\\d{4}-\\d{2}-\\d{2}T[0-9:]+Z?$", "", text).strip()
        if not text:
            continue
        h = _canon_hash(text)
        if h in have:
            continue
        have.add(h)
        entries.append({
            "id": h,
            "hash": h,
            "text": text,
            "source": m.group("src") or "legacy-md",
            "tags": [],
            "scope": GLOBAL,
            "created_at": _now(),
            "provenance": [{"source": m.group("src") or "legacy-md", "at": _now()}],
        })
        added = True
    if added:
        _write_all(GLOBAL, entries)


def _mirror_to_md(text: str, source: str, h: str) -> None:
    """Append a global learning to shared/learnings.md so the SessionStart hook
    (which injects that file) keeps seeing new lessons. Dedup by the sha the
    legacy writer used. Best-effort."""
    md = _legacy_md()
    try:
        md.parent.mkdir(parents=True, exist_ok=True)
        existing = md.read_text() if md.exists() else ""
        if h in existing:
            return
        section = "## Failure patterns the worker has hit before"
        if section not in existing:
            existing += f"\n{section}\n\n"
        # Keep the exact `- <text> [src:<source> sha:<hash>]` shape the legacy
        # writer + _LEGACY_LINE_RE agree on, so a migration round-trip extracts
        # the original text and dedups on hash instead of re-ingesting the line.
        line = f"- {text} [src:{source} sha:{h}]\n"
        md.write_text(existing + line)
    except OSError:
        pass


# --- public API --------------------------------------------------------------
def record(
    text: str,
    *,
    source: str = "anon",
    scope: str = GLOBAL,
    tags: list[str] | None = None,
) -> dict:
    """Record a learning at a scope, deduped.

    Returns {id, scope, recorded, deduped}. On an exact or near-duplicate the
    existing entry gains the new source/timestamp in its provenance (the signal
    is not dropped) and `recorded` is False, `deduped` is True.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("learning text is required")
    scope = _valid_scope(scope)
    tags = [t.strip() for t in (tags or []) if t and t.strip()]

    entries = _read(scope)
    h = _canon_hash(text)
    new_tokens = _token_set(text)

    for e in entries:
        dup = e.get("hash") == h
        if not dup:
            sim = _jaccard(new_tokens, _token_set(e.get("text", "")))
            dup = sim >= _NEAR_DUP_JACCARD
        if dup:
            prov = e.setdefault("provenance", [])
            prov.append({"source": source, "at": _now()})
            for t in tags:
                if t not in e.setdefault("tags", []):
                    e["tags"].append(t)
            _write_all(scope, entries)
            return {"id": e["id"], "scope": scope, "recorded": False, "deduped": True}

    entry = {
        "id": h,
        "hash": h,
        "text": text,
        "source": source,
        "tags": tags,
        "scope": scope,
        "created_at": _now(),
        "provenance": [{"source": source, "at": _now()}],
    }
    _append_line(scope, entry)
    if scope == GLOBAL:
        _mirror_to_md(text, source, h)
    return {"id": entry["id"], "scope": scope, "recorded": True, "deduped": False}


# --- promotion: prose that proved itself becomes an enforced predicate --------
# Textual rules rot: they accumulate, they are advisory, and every job pays
# attention cost for all of them. Some of them are not judgments at all — they are
# grants the operator typed as prose because prose was the easy box. Promotion is
# the path out, and it costs nothing new to detect, because dedup ALREADY attaches
# each re-derivation's source to the existing entry's provenance (see Dedup, above).
# So provenance IS the stability counter: a rule re-derived from N independent
# blocks has been independently confirmed N times.
#
# Nothing here mints anything. A candidate is a PROMPT for the operator in the
# Review tab; the operator owns rules, and an auto-minted grant would be the system
# granting itself authority — the one thing this loop exists to prevent.

# Provenance sources written by the review path are `review:<scope>:<task_id>`, so
# the ORIGIN is `review` and the TAIL makes two entries from two different blocks
# distinct. Three entries from re-answering the same block is one confirmation, not
# three, which is exactly what "independent" has to mean for the counter to be
# worth anything.
_BLOCK_ORIGIN = "review"
_PROMOTE_MIN_SOURCES = int(os.environ.get("LEARNINGS_PROMOTE_MIN_SOURCES", "3"))


def _promoted_path() -> Path:
    return _dir() / "promoted.jsonl"


def _read_promoted() -> list[dict]:
    p = _promoted_path()
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("hash"):
            out.append(obj)
    return out


def _write_promoted(rows: list[dict]) -> None:
    p = _promoted_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + ("\n" if rows else ""))
    os.replace(tmp, p)


def _find(scope: str, hash_: str) -> dict | None:
    for e in _read(_valid_scope(scope)):
        if e.get("hash") == hash_ or e.get("id") == hash_:
            return e
    return None


def block_origin_sources(entry: dict) -> list[str]:
    """The DISTINCT block-origin provenance sources on an entry, oldest first.
    Each one is a different block that independently produced this same rule."""
    seen: list[str] = []
    for p in entry.get("provenance") or []:
        src = str((p or {}).get("source") or "")
        if src.split(":", 1)[0] != _BLOCK_ORIGIN:
            continue
        if src not in seen:
            seen.append(src)
    return seen


def promote_when_stable(scope: str, hash: str) -> bool:  # noqa: A002 (plan names it `hash`)
    """Has this learning proved itself enough to PROPOSE making it enforced?

    True when the entry carries >= `_PROMOTE_MIN_SOURCES` independent block-origin
    provenance entries — i.e. the same rule was re-derived from three different
    blocks and answered the same way three times. Recording a candidate is
    idempotent: it is a row in `learnings/promoted.jsonl`, which the Review tab
    reads as an operator prompt.

    Returns False (and writes nothing) below the threshold or for an unknown
    entry. It NEVER mints a grant: promotion is proposed here and performed only
    by the operator, from the Review tab.
    """
    scope = _valid_scope(scope)
    entry = _find(scope, hash)
    if entry is None:
        return False
    sources = block_origin_sources(entry)
    if len(sources) < _PROMOTE_MIN_SOURCES:
        return False
    rows = _read_promoted()
    for r in rows:
        if r.get("scope") == scope and r.get("hash") == entry["hash"]:
            # Already a candidate (or already promoted): refresh the evidence
            # count so the card shows how many times it has now held.
            if r.get("state") == "candidate" and r.get("sources") != sources:
                r["sources"] = sources
                r["proposed_at"] = _now()
                _write_promoted(rows)
            return r.get("state") == "candidate"
    rows.append({
        "hash": entry["hash"],
        "scope": scope,
        "text": entry.get("text", ""),
        "sources": sources,
        "state": "candidate",
        "proposed_at": _now(),
        "grant_id": None,
    })
    _write_promoted(rows)
    return True


def promotion_candidates() -> list[dict]:
    """Every rule currently proposed for promotion, across scopes (newest first).
    The Review tab's second list."""
    rows = [r for r in _read_promoted() if r.get("state") == "candidate"]
    rows.sort(key=lambda r: str(r.get("proposed_at") or ""), reverse=True)
    return rows


def mark_promoted(scope: str, hash: str, grant_id: str) -> dict | None:  # noqa: A002
    """Record that the operator promoted a rule to an enforced grant.

    The prose STAYS (marked `promoted`), because an enforced rule without its
    rationale in context produces jobs that fail against a wall they do not
    understand. The floor and the explanation are different artifacts.

    Returns the updated candidate row, or None when there is no such candidate."""
    scope = _valid_scope(scope)
    entry = _find(scope, hash)
    rows = _read_promoted()
    hit = None
    for r in rows:
        if r.get("scope") == scope and r.get("hash") == hash:
            hit = r
            break
    if hit is None or entry is None:
        return None
    hit["state"] = "promoted"
    hit["grant_id"] = str(grant_id)
    hit["promoted_at"] = _now()
    _write_promoted(rows)
    entries = _read(scope)
    for e in entries:
        if e.get("hash") == hash:
            e["promoted"] = str(grant_id)
            break
    _write_all(scope, entries)
    return hit


def record_feedback(
    text: str,
    *,
    source: str = "operator",
    scope: str = GLOBAL,
    tags: list[str] | None = None,
) -> dict:
    """The feedback -> learning loop. An operator correction, a rejected reviewer
    reason, or a failed-experiment note becomes a durable learning so it is not
    repeated. A thin, well-named wrapper over record() that stamps a `feedback`
    tag and defaults the source to `operator`, so callers on the feedback path
    read clearly and their entries are filterable."""
    tags = list(tags or [])
    if "feedback" not in tags:
        tags.append("feedback")
    return record(text, source=source, scope=scope, tags=tags)


def list_learnings(scope: str = GLOBAL) -> list[dict]:
    """All learnings in a scope, insertion order (oldest first)."""
    return _read(_valid_scope(scope))


def _persist_retrieval(scope: str, entries: list[dict], selected_ids: set[str]) -> None:
    """Throttled retrieval bookkeeping: bump retrieval_count / last_retrieved for
    the entries that made it into the rendered block. Persists at most once per
    _RETRIEVAL_THROTTLE_SEC per scope (checked via the store file's mtime) so a
    hot prompt path does not churn the disk. Best-effort; never breaks a render."""
    try:
        p = _path(scope)
        now = time.time()
        if p.exists() and (now - p.stat().st_mtime) < _RETRIEVAL_THROTTLE_SEC:
            return
        stamp = _now()
        touched = False
        for e in entries:
            if e.get("id") in selected_ids:
                e["retrieval_count"] = int(e.get("retrieval_count", 0) or 0) + 1
                e["last_retrieved"] = stamp
                touched = True
        if touched:
            _write_all(scope, entries)
    except Exception:
        pass


def _block_line(entry: dict) -> str:
    """One rendered learning, CARRYING ITS SOURCE.

    The store has recorded `source` and a `provenance[]` list since it was
    written, and this render threw both away: a preference the operator stated
    in chat, a lesson a reviewer filed after a failed run, and a fact the
    memory-keeper consolidated all arrived as identical bullets, so nothing
    downstream could weigh "I was told this" against "I inferred this".

    `anon` and empty sources render bare. They say nothing, and a label that
    says nothing is worse than no label at a per-turn token cost.
    See docs/decisions/0015-transcript-structure-and-content-provenance.md §8.
    """
    src = (entry.get("source") or "").strip()
    text = entry.get("text", "")
    return f"- {text} [{src}]" if src and src != "anon" else f"- {text}"


def memory_block(
    scope: str = GLOBAL,
    *,
    max_entries: int = _MAX_BLOCK_ENTRIES,
    max_tokens: int = _BLOCK_TOKEN_BUDGET,
    track: bool = True,
) -> str:
    """A compact block of a scope's learnings for prompt injection, or "" when
    empty.

    Two hard caps keep the block from bloating and rotting the prompt: at most
    `max_entries` bullets AND at most `max_tokens` approximate tokens
    (`_approx_tokens`, ~4 chars/token). Entries are ranked by value (recency +
    reinforcement/provenance count + severity + recent retrieval) and the highest
    scorers are kept until either cap binds; the rest collapse into a trailing
    `- +N more` remainder line. Kept bullets render in append (oldest-first)
    order so a stable prefix survives for prompt caching.

    When `track` is set (default), the entries included in the render have their
    retrieval_count / last_retrieved bumped, throttled to stay disk-light. Pass
    `track=False` for a pure read (e.g. previews) that must not touch the store.
    """
    try:
        entries = _read(_valid_scope(scope))
        scope = _valid_scope(scope)
    except Exception:
        return ""
    if not entries:
        return ""

    total = len(entries)
    tss = [_recency_ts(e) for e in entries]
    newest_ts, oldest_ts = max(tss), min(tss)
    order = {id(e): i for i, e in enumerate(entries)}  # append position

    ranked = sorted(
        entries,
        key=lambda e: (_entry_value(e, newest_ts, oldest_ts), order[id(e)]),
        reverse=True,
    )

    selected: list[dict] = []
    used_tokens = 0
    for e in ranked:
        if len(selected) >= max_entries:
            break
        cost = _approx_tokens(_block_line(e))
        if used_tokens + cost > max_tokens:
            continue  # try smaller lower-ranked entries rather than overflow
        selected.append(e)
        used_tokens += cost

    if not selected:
        return ""

    remainder = total - len(selected)
    selected.sort(key=lambda e: order[id(e)])  # render oldest-first, cache-stable
    lines = [_block_line(e) for e in selected]
    if remainder > 0:
        lines.append(f"- +{remainder} more")

    if track:
        _persist_retrieval(scope, entries, {e.get("id") for e in selected})

    return "\n".join(lines)


def distill_skill(
    scope: str,
    name: str,
    description: str,
    *,
    from_tag: str | None = None,
    texts: list[str] | None = None,
    extra_body: str = "",
) -> dict:
    """Skill distillation: turn repeated successful patterns into a reusable
    SKILL.md (or update one) via services.skills.put_skill.

    The skill body is assembled deterministically (no model call, so it runs in
    a sweep or offline): a short preamble + a bullet list of the source lessons.
    Source lessons come from `texts` (explicit) and/or every in-scope learning
    tagged `from_tag`. The written skill lives in the knowledge/skills scope
    (`scope` must be a knowledge scope, not `global`, since skills mirror the
    knowledge scopes). Returns the stored skill descriptor.
    """
    from services import skills

    scope = _valid_scope(scope)
    if scope == GLOBAL:
        # Skills mirror the knowledge scopes; the workspace-wide skill pool is
        # `workshop`, not the `global` learnings store.
        scope = "workshop"
    knowledge._parse_scope(scope)

    lessons: list[str] = list(texts or [])
    if from_tag:
        for e in _read(scope if scope != "workshop" else GLOBAL):
            if from_tag in (e.get("tags") or []):
                lessons.append(e["text"])
    # De-dupe lessons by canonical form, preserve order.
    seen: set[str] = set()
    ordered: list[str] = []
    for t in lessons:
        h = _canon_hash(t)
        if h in seen:
            continue
        seen.add(h)
        ordered.append(t.strip())

    body_parts = []
    if extra_body.strip():
        body_parts.append(extra_body.strip())
    if ordered:
        body_parts.append("Learned patterns to apply:\n" + "\n".join(f"- {t}" for t in ordered))
    if not body_parts:
        body_parts.append("(no distilled patterns yet)")
    body = "\n\n".join(body_parts)

    return skills.put_skill(scope, name, description, body)


# --- at-rest maintenance -----------------------------------------------------
def _merge_cluster(members: list[dict]) -> dict:
    """Collapse near-duplicate entries into one. Keeps the best-phrased text (the
    member with the most provenance, tie-break longest), the UNION of provenance
    and tags, the earliest creation, the latest retrieval, summed retrieval
    count, and the highest severity. No signal is lost: the merged entry carries
    every source that any duplicate carried."""
    best = max(members, key=lambda e: (len(e.get("provenance") or []), len(e.get("text", ""))))
    text = best.get("text", "")

    prov: list[dict] = []
    seen: set = set()
    for e in members:
        for pv in (e.get("provenance") or []):
            key = (pv.get("source"), pv.get("at"))
            if key in seen:
                continue
            seen.add(key)
            prov.append(pv)

    tags: list[str] = []
    for e in members:
        for t in (e.get("tags") or []):
            if t not in tags:
                tags.append(t)

    created = min((e.get("created_at") for e in members if e.get("created_at")),
                  default=best.get("created_at"))
    last_ret = max((e.get("last_retrieved") for e in members if e.get("last_retrieved")),
                   default=None)
    retr = sum(int(e.get("retrieval_count", 0) or 0) for e in members)
    sev = max((e.get("severity") for e in members if e.get("severity") is not None),
              key=_severity_num, default=None)

    h = _canon_hash(text)
    merged = {**best, "text": text, "id": best.get("id") or h, "hash": h,
              "tags": tags, "provenance": prov, "created_at": created,
              "retrieval_count": retr}
    if last_ret:
        merged["last_retrieved"] = last_ret
    if sev is not None:
        merged["severity"] = sev
    return merged


def merge_duplicates(scope: str = GLOBAL, *, threshold: float | None = None) -> dict:
    """Maintenance pass: collapse near-duplicate learnings already sitting in a
    scope's store (append-time dedup only catches dups present when a learning
    lands; this catches historical dups that accreted around it). Reuses the
    exact canonical-hash + `_jaccard` >= `_NEAR_DUP_JACCARD` test and the
    provenance-merge rule. Non-destructive: distinct learnings are left alone and
    every merged entry keeps the union of its duplicates' provenance.

    Returns {scope, before, after, merged}. Rewrites the store only if something
    actually merged."""
    scope = _valid_scope(scope)
    thr = _NEAR_DUP_JACCARD if threshold is None else threshold
    entries = _read(scope)

    clusters: list[dict] = []
    for e in entries:
        ts = _token_set(e.get("text", ""))
        h = e.get("hash") or _canon_hash(e.get("text", ""))
        placed = False
        for c in clusters:
            if c["hash"] == h or _jaccard(ts, c["tokens"]) >= thr:
                c["members"].append(e)
                placed = True
                break
        if not placed:
            clusters.append({"hash": h, "tokens": ts, "members": [e]})

    merged_count = 0
    out: list[dict] = []
    for c in clusters:
        if len(c["members"]) == 1:
            out.append(c["members"][0])
        else:
            merged_count += len(c["members"]) - 1
            out.append(_merge_cluster(c["members"]))

    if merged_count:
        _write_all(scope, out)
    return {"scope": scope, "before": len(entries), "after": len(out), "merged": merged_count}


def prune_stale(
    scope: str = GLOBAL,
    *,
    max_age_days: float | None = None,
    min_provenance: int | None = None,
    min_retrieval: int | None = None,
) -> dict:
    """Maintenance pass: demote learnings that have stopped earning their place.
    An entry is aged out only when it is stale (not retrieved, or never retrieved
    then not created, within `max_age_days`) AND weakly reinforced (provenance
    count below `min_provenance`) AND seldom retrieved (retrieval_count below
    `min_retrieval`). Anything recently retrieved OR well-reinforced OR
    frequently retrieved is kept, so a durable guardrail never ages out.

    Demoted entries are appended to the scope's archive JSONL (with archived_at /
    archived_reason) rather than deleted, so history stays inspectable and a
    revived pattern can be found. Thresholds are env-overridable (_AGEOUT_*) and
    param-overridable. Returns {scope, before, kept, archived}."""
    scope = _valid_scope(scope)
    max_age_days = _AGEOUT_DAYS if max_age_days is None else max_age_days
    min_provenance = _AGEOUT_MIN_PROVENANCE if min_provenance is None else min_provenance
    min_retrieval = _AGEOUT_MIN_RETRIEVAL if min_retrieval is None else min_retrieval

    entries = _read(scope)
    cutoff = time.time() - max_age_days * 86400.0
    keep: list[dict] = []
    demoted: list[dict] = []
    for e in entries:
        recent = _recency_ts(e) >= cutoff
        reinforced = len(e.get("provenance") or []) >= min_provenance
        retrieved = int(e.get("retrieval_count", 0) or 0) >= min_retrieval
        if recent or reinforced or retrieved:
            keep.append(e)
        else:
            arch = dict(e)
            arch["archived_at"] = _now()
            arch["archived_reason"] = "aged-out: stale and low-reinforcement"
            demoted.append(arch)

    if demoted:
        _archive_append(scope, demoted)
        _write_all(scope, keep)
    return {"scope": scope, "before": len(entries), "kept": len(keep), "archived": len(demoted)}


# --- CLI ---------------------------------------------------------------------
# `python -m services.learnings <verb> ...` (run with PYTHONPATH=backend). Lets a
# later autonomous pass, a cron hook, or the operator record/list/distill without
# the HTTP surface. This supersedes scripts/log_learning.sh (which now delegates
# here).
def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="services.learnings")
    sub = ap.add_subparsers(dest="verb", required=True)

    p_rec = sub.add_parser("record", help="record a learning (deduped)")
    p_rec.add_argument("text")
    p_rec.add_argument("--source", default="anon")
    p_rec.add_argument("--scope", default=GLOBAL)
    p_rec.add_argument("--tags", default="")

    p_fb = sub.add_parser("feedback", help="record operator feedback as a learning")
    p_fb.add_argument("text")
    p_fb.add_argument("--source", default="operator")
    p_fb.add_argument("--scope", default=GLOBAL)
    p_fb.add_argument("--tags", default="")

    p_ls = sub.add_parser("list", help="list a scope's learnings")
    p_ls.add_argument("--scope", default=GLOBAL)

    p_blk = sub.add_parser("block", help="print the injection block for a scope")
    p_blk.add_argument("--scope", default=GLOBAL)

    p_mrg = sub.add_parser("merge", help="collapse near-duplicate stored learnings")
    p_mrg.add_argument("--scope", default=GLOBAL)

    p_prune = sub.add_parser("prune", help="archive stale, low-reinforcement learnings")
    p_prune.add_argument("--scope", default=GLOBAL)
    p_prune.add_argument("--max-age-days", dest="max_age_days", type=float, default=None)
    p_prune.add_argument("--min-provenance", dest="min_provenance", type=int, default=None)
    p_prune.add_argument("--min-retrieval", dest="min_retrieval", type=int, default=None)

    p_di = sub.add_parser("distill", help="distill in-scope patterns into a SKILL.md")
    p_di.add_argument("--scope", required=True)
    p_di.add_argument("--name", required=True)
    p_di.add_argument("--description", default="")
    p_di.add_argument("--from-tag", dest="from_tag", default=None)
    p_di.add_argument("--text", dest="texts", action="append", default=[])

    args = ap.parse_args(argv)
    tags = lambda s: [t.strip() for t in (s or "").split(",") if t.strip()]

    if args.verb == "record":
        print(json.dumps(record(args.text, source=args.source, scope=args.scope, tags=tags(args.tags))))
    elif args.verb == "feedback":
        print(json.dumps(record_feedback(args.text, source=args.source, scope=args.scope, tags=tags(args.tags))))
    elif args.verb == "list":
        for e in list_learnings(args.scope):
            print(f"{e['id']}  {e['text']}  [src:{e.get('source')} tags:{','.join(e.get('tags') or [])}]")
    elif args.verb == "block":
        print(memory_block(args.scope))
    elif args.verb == "merge":
        print(json.dumps(merge_duplicates(args.scope)))
    elif args.verb == "prune":
        print(json.dumps(prune_stale(
            args.scope, max_age_days=args.max_age_days,
            min_provenance=args.min_provenance, min_retrieval=args.min_retrieval,
        )))
    elif args.verb == "distill":
        print(json.dumps(distill_skill(
            args.scope, args.name, args.description,
            from_tag=args.from_tag, texts=args.texts,
        )))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
