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

Write-time reconciliation
-------------------------
A write is not an append. Every candidate is resolved against what the scope
already holds, four ways (mem0's shape, no embeddings — the lexical token-set
machinery below is enough for a corpus this size):

- **NOOP** — exact canonical hash, or token-set Jaccard >= `_NEAR_DUP_JACCARD`.
  The signal is not dropped: the new source + timestamp join the existing
  entry's provenance.
- **UPDATE** — the candidate says the same thing at more length (it contains
  >= `_UPDATE_CONTAINMENT` of the existing entry's tokens and is longer). The
  existing row is rewritten in place, keeping its id and history; the previous
  copy goes to the archive.
- **SUPERSEDE** — a revision of an existing rule (`_SUPERSEDE_JACCARD` <= sim <
  `_NEAR_DUP_JACCARD`, both entries substantial). The old row is ARCHIVED with a
  pointer to the winner instead of sitting beside it. This is the specific fix
  for the restart guardrail that existed twice, created a month apart, because
  dedup was a hash of text that had been edited.
- **ADD** — nothing similar enough. A new row.

Quality gate
------------
A learning is instructional guidance. `Ralph rejected: ts_typecheck_fail` is a
log line: 33 characters, no instruction, and it rode in every agent prompt for a
month because nothing at the write path asked what it was. `quality_refusal`
refuses on observable SHAPE (too short, no imperative, identifier-shaped status
token), never on a blocklist of strings, so the next emitter of machine noise is
caught without having to be named. A refusal is returned, not raised: callers
keep working.

Counters
--------
`render_count` counts emissions of a block. `retrieval_count` counts selections
UNDER PRESSURE — a cap actually bound and this entry beat a competitor. They were
one field, which is why every entry read "retrieved 75 times" and why the age-out
pass could never fire: the injector falsified its own precondition.
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
# 900, not 700. The `global` block measured 795 tokens on 2026-08-12, so the
# old budget was dropping one entry every turn on a store this size, and the
# one it dropped was the longest, which here meant the most important (see
# `_entry_value`). Ordering alone fixes WHICH entry is dropped; this is what
# makes today's store fit whole. Ranking still matters, because the store only
# grows.
_BLOCK_TOKEN_BUDGET = int(os.environ.get("LEARNINGS_BLOCK_TOKEN_BUDGET", "900"))

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
_NEAR_DUP_JACCARD = float(os.environ.get("LEARNINGS_NEAR_DUP_JACCARD", "0.85"))

# Revision band: at or above this, and below _NEAR_DUP_JACCARD, a candidate is
# treated as a REVISION of the entry it resembles rather than a new rule, and
# supersedes it. Measured on the live `global` store 2026-08-16: the duplicated
# restart guardrail scores 0.684, and the highest-scoring pair of genuinely
# distinct substantial entries scores 0.125. The gap is enormous, so 0.60 sits
# in open space rather than on a knife edge.
_SUPERSEDE_JACCARD = float(os.environ.get("LEARNINGS_SUPERSEDE_JACCARD", "0.60"))

# ...but ONLY between substantial entries. The one close pair in the whole store
# that must NOT merge is `Ralph rejected: py_typecheck_fail` vs
# `..._ts_typecheck_fail` at 0.667: two different failure modes distinguished by
# one token. Short texts have too few tokens for Jaccard to mean anything, so
# supersede requires both sides to carry at least this many content tokens.
# (Those two are refused by the quality gate now, but the guard is what makes the
# threshold safe in general, not the gate.)
_SUPERSEDE_MIN_TOKENS = int(os.environ.get("LEARNINGS_SUPERSEDE_MIN_TOKENS", "12"))

# A candidate that CONTAINS this fraction of an existing entry's tokens and is
# longer is an elaboration of it, not a replacement: UPDATE in place.
_UPDATE_CONTAINMENT = float(os.environ.get("LEARNINGS_UPDATE_CONTAINMENT", "0.90"))

# Provenance cap. Every re-derivation appends, so an entry that keeps recurring
# grows without bound — one live row carried 46 entries on a one-sentence note,
# in a store whose whole point is to stay small. Keep the FIRST (where it came
# from) and the last N (whether it is still recurring); `provenance_total`
# carries the count that was there, so reinforcement scoring is unaffected.
_PROVENANCE_KEEP = int(os.environ.get("LEARNINGS_PROVENANCE_KEEP", "10"))

# Salience floor for age-out: an entry the operator authored or pinned, or one
# reinforced this many times, never ages out on time alone (D5).
_SALIENCE_MIN_PROVENANCE = int(os.environ.get("LEARNINGS_SALIENCE_MIN_PROVENANCE", "5"))

# --- quality gate thresholds -------------------------------------------------
# Length rules apply to LOOP-WRITTEN entries. An operator typing "prefers
# brevity" into a chat fence means it; a background loop emitting two words does
# not, and telling them apart is exactly what `source` is for (D3). The
# machine-status rule applies to everybody, because nobody wants a status token
# in a prompt.
_QUALITY_MIN_CHARS = int(os.environ.get("LEARNINGS_QUALITY_MIN_CHARS", "20"))
_QUALITY_MIN_TOKENS = int(os.environ.get("LEARNINGS_QUALITY_MIN_TOKENS", "3"))
# Prose with no imperative in it has to be substantial to count as guidance.
_QUALITY_MIN_PROSE_TOKENS = int(os.environ.get("LEARNINGS_QUALITY_MIN_PROSE_TOKENS", "9"))
# A status line is short. Beyond this many words, shape stops being evidence.
_QUALITY_STATUS_MAX_WORDS = int(os.environ.get("LEARNINGS_QUALITY_STATUS_MAX_WORDS", "8"))

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
    """Per-owner. Learnings are durable operator feedback — "prefers brevity",
    "always rebuild dist" — injected into every turn's prompt by
    `learnings_context`. One directory meant one person's corrections steering
    everybody's agents, so this follows `shared/knowledge/` (see
    `knowledge._shared_knowledge_root`) rather than being gated off for
    non-operators, which is what it was before and which left a second user's
    Workshop unable to learn anything at all."""
    from services import ownership
    d = ownership.workspace_path(knowledge._runtime() / "learnings", "learnings")
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


def _content_tokens(text: str) -> list[str]:
    """Content words in order, duplicates kept. `_token_set` is for comparison;
    this is for measuring how much a candidate actually says."""
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _containment(inner: set[str], outer: set[str]) -> float:
    """How much of `inner` `outer` already says. 1.0 means outer covers it whole."""
    if not inner:
        return 0.0
    return len(inner & outer) / len(inner)


# --- quality gate ------------------------------------------------------------
# Words that make a sentence an instruction. Presence of any one is what
# separates "Prefer rows to cards." (three words, real guidance) from
# "Ralph rejected: py_typecheck_fail" (five tokens, a status line). Deliberately
# generic: no product, tool or agent name appears here, so this does not become a
# blocklist for one emitter.
_INSTRUCTION_WORDS = {
    "do", "dont", "don", "not", "no", "never", "always", "must", "should",
    "shall", "need", "needs", "needed", "require", "requires", "required",
    "use", "using", "avoid", "ensure", "verify", "check", "confirm", "prefer",
    "prefers", "keep", "make", "write", "run", "add", "remove", "delete",
    "treat", "stop", "start", "call", "read", "put", "set", "only", "before",
    "after", "instead", "rather", "unless", "without", "every", "each", "first",
    "then", "when", "if", "because", "so", "cannot", "can", "may", "let", "give",
    "go", "fix", "build", "commit", "report", "ask", "tell", "default", "leave",
    "claim", "assume", "prove", "wait", "pick", "mark", "follow", "obey",
}

# snake_case: the shape of a machine identifier, and not the shape of prose.
# Hyphens are deliberately NOT included — "em-dash", "end-to-end" and "read-only"
# are ordinary English and appear in real guidance.
_SNAKE_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")

_STATUS_WORDS = {
    "fail", "failed", "failing", "failure", "error", "errored", "ok", "pass",
    "passed", "success", "succeeded", "timeout", "timedout", "rejected",
    "reject", "blocked", "denied", "aborted", "skipped", "exit", "exited",
    "nonzero", "status", "done", "unknown", "null", "none", "true", "false",
}

_TRIM_CHARS = "`'\"“”().,:;!?[]{}<>*_-"

# Sources whose text the operator typed. Exempt from the length rules (not from
# the machine-status rule). Kept deliberately small; see `quality_refusal`.
_OPERATOR_SOURCES = ("operator", "legacy-md")


def _has_instruction(text: str) -> bool:
    return any(t in _INSTRUCTION_WORDS for t in _TOKEN_RE.findall((text or "").lower()))


def _is_machine_status(text: str) -> bool:
    """A short line whose payload is an identifier or a status word, with no
    instruction in it: `<label>: <token>`, `build failed`, `exit 1`."""
    words = [w for w in (text or "").split() if w]
    if not words or len(words) > _QUALITY_STATUS_MAX_WORDS:
        return False
    if _has_instruction(text):
        return False
    low = [w.strip(_TRIM_CHARS).lower() for w in words]
    low = [w for w in low if w]
    if not low:
        return False
    if any(_SNAKE_RE.fullmatch(w) for w in low):
        return True
    return low[-1] in _STATUS_WORDS


def quality_refusal(text: str, *, source: str = "anon") -> str | None:
    """Why this text is not a learning, or None when it is one.

    Refusals are shape-based and stated plainly so a caller can show the reason.
    Never raises: a bad candidate is a refusal, not an exception, because the
    paths that write learnings (a review answer, a chat fence, a loop's failure
    handler) all have real work to finish afterwards.
    """
    t = (text or "").strip()
    if not t:
        return "empty"
    if _is_machine_status(t):
        return "machine status token, not instructional guidance"
    if str(source or "").strip().lower() in _OPERATOR_SOURCES:
        # The operator typed it, so it means what it says however short it is.
        # Note this is NARROWER than `_is_authored`: `chat` is authored for
        # RANKING (it came from a conversation with the operator) but it is
        # MODEL-written text, and a two-word chat capture is the model deciding
        # what mattered. That is precisely the writer D3 says to hold to a
        # standard.
        return None
    toks = _content_tokens(t)
    if len(t) < _QUALITY_MIN_CHARS or len(toks) < _QUALITY_MIN_TOKENS:
        return f"too short to be guidance ({len(t)} chars, {len(toks)} content words)"
    if not _has_instruction(t) and len(toks) < _QUALITY_MIN_PROSE_TOKENS:
        return "no instruction and too little content to be guidance"
    return None


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


# Sources that are STANDING RULES rather than incident reports. A curated
# guardrail and a note that one worker forgot a sentinel are not the same kind
# of thing, and ranking them on one scale is what produced the failure below.
_AUTHORED_SOURCES = ("legacy-md", "operator", "chat")
_AUTHORED_PREFIXES = ("review:",)


def _is_authored(e: dict) -> bool:
    src = str(e.get("source") or "").strip().lower()
    return src in _AUTHORED_SOURCES or src.startswith(_AUTHORED_PREFIXES)


def _provenance_count(e: dict) -> int:
    """How many times this was derived, INCLUDING what the cap trimmed. Reading
    `len(provenance)` after capping would silently demote the entries that earned
    the most reinforcement, which is backwards."""
    return max(len(e.get("provenance") or []), int(e.get("provenance_total", 0) or 0))


def _cap_provenance(e: dict) -> dict:
    """Hold `provenance` to first + last `_PROVENANCE_KEEP`, recording the true
    total. Mutates and returns the entry."""
    prov = e.get("provenance") or []
    total = max(len(prov), int(e.get("provenance_total", 0) or 0))
    if total:
        e["provenance_total"] = total
    if len(prov) > _PROVENANCE_KEEP + 1:
        e["provenance"] = [prov[0]] + prov[-_PROVENANCE_KEEP:]
    return e


def _is_salient(e: dict) -> bool:
    """The age-out floor (D5). Pinned, operator-authored, promoted or heavily
    reinforced entries are standing rules: they do not age out on time alone, no
    matter how the thresholds are tuned."""
    return bool(
        e.get("pinned")
        or e.get("promoted")
        or _is_authored(e)
        or _provenance_count(e) >= _SALIENCE_MIN_PROVENANCE
    )


def _entry_value(e: dict, newest_ts: float, oldest_ts: float) -> float:
    """Rank score: authorship + recency + reinforcement + severity + retrieval.
    Highest scorers survive the token budget; the rest collapse into the
    "+N more" remainder.

    AUTHORSHIP DOMINATES, and it has to. Measured 2026-08-12: the `global`
    block held 795 tokens against a 700 budget, and the single entry the budget
    dropped was the longest one, the Workshop Design System rule at 168 tokens.
    Three things ganged up on it: it is old, so recency scored it low; it has
    one provenance entry, so reinforcement scored it low; and being dropped
    meant it was never retrieved, which lowered the retrieval term that decides
    whether it is dropped. It had been silently absent for roughly 25 days,
    while thirteen mechanical `[ralph]` incident lines rendered every turn.

    A rule the operator wrote must not have to out-compete worker noise on
    recency, so authorship is a separate TIER rather than a bonus term: see
    `_entry_rank`. A bonus does not work, and trying it is instructive. The
    first attempt added 2.0 for authorship, described as "above the maximum any
    other term can reach". It is not: the other four terms SUM to 3.5, and a
    fresh, five-times-reinforced, recently-retrieved incident line scores 2.45
    against an old unreinforced rule's 2.2. The rule still lost.
    """
    span = (newest_ts - oldest_ts) or 1.0
    recency = (_recency_ts(e) - oldest_ts) / span  # 0..1, newer is higher
    prov = _provenance_count(e)
    reinforce = min(prov, 5) / 5.0                  # 0..1, saturates at 5
    severity = _severity_num(e.get("severity"))     # 0..1
    retr = min(int(e.get("retrieval_count", 0) or 0), 10) / 10.0  # 0..1
    return recency + reinforce + severity + 0.5 * retr


def _entry_rank(e: dict, newest_ts: float, oldest_ts: float) -> tuple[int, float]:
    """Sort key: authored rules first, then value within each tier."""
    return (1 if _is_authored(e) else 0, _entry_value(e, newest_ts, oldest_ts))


# --- IO ----------------------------------------------------------------------
def _normalize(e: dict) -> dict:
    """Bring a stored row up to the current shape, in memory. Persisted the next
    time anything writes the scope, so no migration script is needed and no read
    causes a write.

    The one real migration: every `retrieval_count` written before the counters
    were split is a RENDER count. Every entry in the `global` store carried 75 of
    them, one per turn, which is what made the age-out pass unable to fire. They
    move to `render_count` and the retrieval counter starts honest at zero;
    `last_retrieved` becomes `last_rendered` for the same reason, so recency
    falls back to creation time until something is genuinely retrieved.
    """
    if "render_count" not in e:
        e["render_count"] = int(e.get("retrieval_count", 0) or 0)
        e["retrieval_count"] = 0
        if e.get("last_retrieved"):
            e["last_rendered"] = e.pop("last_retrieved")
    if e.get("provenance") and "provenance_total" not in e:
        e["provenance_total"] = len(e["provenance"])
    return e


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
                out.append(_normalize(obj))
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
    stays inspectable (age-out demotes, it does not destroy).

    For `global` this ALSO drops the entry's line from `shared/learnings.md`.
    That mirror is folded back into the store on every read, so archiving a row
    without unmirroring it resurrects the row on the very next turn — which is
    not a hypothetical: it is how the superseded restart guardrail came back as a
    second live entry a month after it was revised."""
    if not entries:
        return
    p = _archive_path(scope_key)
    with p.open("a") as f:
        for e in entries:
            f.write(json.dumps(e, sort_keys=True) + "\n")
    if _valid_scope(scope_key) == GLOBAL:
        _unmirror_from_md([e.get("text", "") for e in entries])


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
        # `_md_line_text` also strips the trailing " - <timestamp>" the legacy
        # shell writer appended (hyphen or the legacy em-dash separator).
        text = _md_line_text(raw) if m else None
        if not text:
            continue
        h = _canon_hash(text)
        if h in have:
            continue
        src = m.group("src") or "legacy-md"
        # The md is hook-injected and hand-edited, so it is a write path like any
        # other and gets the same gate. Without this, archiving a machine-status
        # row from the JSONL resurrects it from the md on the very next read.
        if quality_refusal(text, source=src):
            continue
        have.add(h)
        entries.append({
            "id": h,
            "hash": h,
            "text": text,
            "source": src,
            "tags": [],
            "scope": GLOBAL,
            "created_at": _now(),
            "provenance": [{"source": src, "at": _now()}],
        })
        added = True
    if added:
        _write_all(GLOBAL, entries)


def _md_line_text(raw: str) -> str | None:
    """The learning text carried by a `- ...` bullet in the legacy md, or None."""
    m = _LEGACY_LINE_RE.match(raw.rstrip())
    if not m:
        return None
    text = re.sub("\\s+[-\\u2014]\\s+\\d{4}-\\d{2}-\\d{2}T[0-9:]+Z?$", "",
                  m.group("text").strip()).strip()
    return text or None


def _unmirror_from_md(texts: list[str]) -> None:
    """Drop bullets from `shared/learnings.md` whose text matches one of these,
    by canonical hash. Best-effort: the md is a mirror, not the source of truth."""
    md = _legacy_md()
    if not md.exists() or not texts:
        return
    drop = {_canon_hash(t) for t in texts if t}
    try:
        lines = md.read_text().splitlines()
        keep = []
        removed = False
        for raw in lines:
            t = _md_line_text(raw) if raw.startswith("- ") else None
            if t and _canon_hash(t) in drop:
                removed = True
                continue
            keep.append(raw)
        if removed:
            md.write_text("\n".join(keep) + "\n")
    except OSError:
        pass


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
def _resolve(text: str, entries: list[dict]) -> tuple[str, dict | None, float]:
    """Which of ADD / UPDATE / SUPERSEDE / NOOP this candidate is, against the
    scope's existing entries, and which entry it acts on.

    Conservative by construction: the best match wins at most one action, the
    revision band requires both texts to be substantial, and anything ambiguous
    falls through to ADD. A wrong merge destroys a rule; a wrong ADD costs one
    line that the at-rest merge pass can still collapse later.
    """
    h = _canon_hash(text)
    cand = _token_set(text)
    cand_len = len(_content_tokens(text))

    best: dict | None = None
    best_sim = 0.0
    for e in entries:
        if e.get("hash") == h:
            return "noop", e, 1.0
        sim = _jaccard(cand, _token_set(e.get("text", "")))
        if sim > best_sim:
            best, best_sim = e, sim

    if best is not None and best_sim >= _NEAR_DUP_JACCARD:
        return "noop", best, best_sim

    # UPDATE is tested on CONTAINMENT, not on similarity: a candidate that says
    # everything an existing entry says and then keeps going is an elaboration of
    # it however much it added, and adding a lot is exactly what drags Jaccard
    # below the revision band. Only substantial entries can be elaborated, so a
    # long candidate cannot swallow a short unrelated one.
    upd, upd_cont = None, 0.0
    for e in entries:
        old_len = len(_content_tokens(e.get("text", "")))
        if old_len < _SUPERSEDE_MIN_TOKENS or cand_len <= old_len:
            continue
        cont = _containment(_token_set(e.get("text", "")), cand)
        if cont >= _UPDATE_CONTAINMENT and cont > upd_cont:
            upd, upd_cont = e, cont
    if upd is not None:
        return "update", upd, _jaccard(cand, _token_set(upd.get("text", "")))

    if best is None or best_sim < _SUPERSEDE_JACCARD:
        return "add", None, best_sim
    if min(cand_len, len(_content_tokens(best.get("text", "")))) < _SUPERSEDE_MIN_TOKENS:
        # Too little text for lexical overlap to mean "same rule". Two status
        # lines differing by one token score 0.667 and are not the same rule.
        return "add", None, best_sim
    return "supersede", best, best_sim


def _absorb(target: dict, *, source: str | None = None, tags: list[str] = (),
            from_entry: dict | None = None) -> dict:
    """Fold a new derivation (and optionally a whole superseded entry) into an
    entry: provenance, tags, counters, severity. Nothing a duplicate carried is
    dropped, which is what makes archiving the loser safe."""
    total = _provenance_count(target)
    prov = target.setdefault("provenance", [])
    if from_entry is not None:
        seen = {(p.get("source"), p.get("at")) for p in prov}
        for pv in (from_entry.get("provenance") or []):
            key = (pv.get("source"), pv.get("at"))
            if key not in seen:
                seen.add(key)
                prov.append(pv)
        total += _provenance_count(from_entry)
        for t in (from_entry.get("tags") or []):
            if t not in target.setdefault("tags", []):
                target["tags"].append(t)
        for counter in ("render_count", "retrieval_count"):
            target[counter] = int(target.get(counter, 0) or 0) + int(from_entry.get(counter, 0) or 0)
        if _severity_num(from_entry.get("severity")) > _severity_num(target.get("severity")):
            target["severity"] = from_entry.get("severity")
        if from_entry.get("pinned"):
            target["pinned"] = True
        first = from_entry.get("created_at")
        if first and first < str(target.get("created_at") or ""):
            target["first_recorded_at"] = first
    if source is not None:
        prov.append({"source": source, "at": _now()})
        total += 1
    target["provenance_total"] = total
    for t in tags:
        if t not in target.setdefault("tags", []):
            target["tags"].append(t)
    return _cap_provenance(target)


def record(
    text: str,
    *,
    source: str = "anon",
    scope: str = GLOBAL,
    tags: list[str] | None = None,
) -> dict:
    """Record a learning at a scope, reconciled against what is already there.

    Returns {id, scope, recorded, deduped, resolution, ...}. `resolution` is one
    of `add` / `update` / `supersede` / `noop` / `refused`:

    - `noop`   — a duplicate. The existing entry gains the new source/timestamp
                 in its provenance; the signal is not dropped.
    - `update` — the candidate elaborates an existing entry: rewritten in place,
                 same id, previous copy archived.
    - `supersede` — the candidate is a revision: the old entry is ARCHIVED with a
                 pointer to the winner rather than left beside it. `superseded`
                 carries the archived id.
    - `refused` — not instructional guidance (`reason` says why). Nothing is
                 written and nothing is raised: callers keep working.

    `recorded` / `deduped` keep their old meanings for existing callers:
    `recorded` is true when a NEW row landed, `deduped` when an existing one
    absorbed the write.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("learning text is required")
    scope = _valid_scope(scope)
    tags = [t.strip() for t in (tags or []) if t and t.strip()]

    reason = quality_refusal(text, source=source)
    if reason:
        return {"id": None, "scope": scope, "recorded": False, "deduped": False,
                "resolution": "refused", "refused": True, "reason": reason}

    entries = _read(scope)
    action, target, sim = _resolve(text, entries)

    if action == "noop" and target is not None:
        _absorb(target, source=source, tags=tags)
        _write_all(scope, entries)
        return {"id": target["id"], "scope": scope, "recorded": False, "deduped": True,
                "resolution": "noop", "similarity": round(sim, 3)}

    if action == "update" and target is not None:
        _archive_append(scope, [{**target, "archived_at": _now(),
                                 "archived_reason": f"updated in place by a longer statement of the same rule (sim {sim:.2f})"}])
        target.setdefault("first_recorded_at", target.get("created_at"))
        target["text"] = text
        target["hash"] = _canon_hash(text)
        target["revised_at"] = _now()
        _absorb(target, source=source, tags=tags)
        _write_all(scope, entries)
        if scope == GLOBAL:
            _mirror_to_md(text, source, target["hash"])
        return {"id": target["id"], "scope": scope, "recorded": False, "deduped": False,
                "resolution": "update", "similarity": round(sim, 3)}

    h = _canon_hash(text)
    entry = {
        "id": h,
        "hash": h,
        "text": text,
        "source": source,
        "tags": tags,
        "scope": scope,
        "created_at": _now(),
        "provenance": [],
        "render_count": 0,
        "retrieval_count": 0,
    }

    if action == "supersede" and target is not None:
        _absorb(entry, source=source, tags=tags, from_entry=target)
        entry["supersedes"] = target.get("id")
        entries = [e for e in entries if e is not target]
        entries.append(entry)
        _archive_append(scope, [{**target, "archived_at": _now(),
                                 "archived_reason": f"superseded by a revision (sim {sim:.2f})",
                                 "superseded_by": entry["id"]}])
        _write_all(scope, entries)
        if scope == GLOBAL:
            _mirror_to_md(text, source, h)
        return {"id": entry["id"], "scope": scope, "recorded": True, "deduped": False,
                "resolution": "supersede", "superseded": target.get("id"),
                "similarity": round(sim, 3)}

    _absorb(entry, source=source, tags=tags)
    _append_line(scope, entry)
    if scope == GLOBAL:
        _mirror_to_md(text, source, h)
    return {"id": entry["id"], "scope": scope, "recorded": True, "deduped": False,
            "resolution": "add", "similarity": round(sim, 3)}


# --- promotion: has a rule proved itself? ------------------------------------
# Textual rules rot: they accumulate, they are advisory, and every job pays
# attention cost for all of them. Some are not judgments at all — they are grants
# the operator typed as prose because prose was the easy box. Detecting the
# stable ones costs nothing new, because reconciliation ALREADY attaches each
# re-derivation's source to the existing entry's provenance. So provenance IS the
# stability counter: a rule re-derived from N independent blocks has been
# independently confirmed N times.
#
# This is now a PREDICATE and nothing more. The candidate/promoted lobe that used
# to sit here (`promotion_candidates`, `mark_promoted`, `_write_promoted`) wrote
# `learnings/promoted.jsonl`, which never existed on disk in the year the code
# was live, and read it back for a Review-tab list that no client ever requested.
# Zero callers, zero rows, so it is deleted rather than left to be rediscovered
# (memory-consolidation 2026-08-16, D9/M4). The one live caller
# (`routes/tasks_all.py`, the mint-rule path) uses only the boolean, which still
# means exactly what it always meant.

# Provenance sources written by the review path are `review:<scope>:<task_id>`, so
# the ORIGIN is `review` and the TAIL makes two entries from two different blocks
# distinct. Three entries from re-answering the same block is one confirmation, not
# three, which is exactly what "independent" has to mean for the counter to be
# worth anything.
_BLOCK_ORIGIN = "review"
_PROMOTE_MIN_SOURCES = int(os.environ.get("LEARNINGS_PROMOTE_MIN_SOURCES", "3"))


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
    """Has this learning proved itself enough to be worth enforcing?

    True when the entry carries >= `_PROMOTE_MIN_SOURCES` independent block-origin
    provenance entries — the same rule re-derived from three different blocks and
    answered the same way three times. False below the threshold or for an unknown
    entry.

    Writes nothing, and mints nothing. The operator owns rules; a system that
    granted itself authority off its own counter is the one thing this loop
    exists to prevent.
    """
    entry = _find(_valid_scope(scope), hash)
    if entry is None:
        return False
    return len(block_origin_sources(entry)) >= _PROMOTE_MIN_SOURCES


def set_pinned(scope: str, hash: str, pinned: bool = True) -> dict | None:  # noqa: A002
    """Pin (or unpin) a learning. A pinned entry is salient: it never ages out on
    time alone, whatever the thresholds are set to. Returns the updated entry, or
    None when there is no such entry."""
    scope = _valid_scope(scope)
    entries = _read(scope)
    hit = None
    for e in entries:
        if e.get("hash") == hash or e.get("id") == hash:
            hit = e
            break
    if hit is None:
        return None
    if pinned:
        hit["pinned"] = True
    else:
        hit.pop("pinned", None)
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


def _persist_render(scope: str, entries: list[dict], selected_ids: set[str],
                    *, pressured: bool) -> None:
    """Throttled render bookkeeping, and the counter split that makes the
    garbage collector able to fire.

    Every selected entry gets `render_count` / `last_rendered`. Only a render
    where a CAP ACTUALLY BOUND — some entry was pushed out, so the selected ones
    beat a competitor — also gets `retrieval_count` / `last_retrieved`.

    The old code bumped one merged counter on every render. With 15 entries under
    a 60-entry / 900-token cap nothing was ever excluded, so "retrieved" meant
    "exists", every entry read 75 retrievals, and `prune_stale`'s
    `recent OR reinforced OR retrieved` precondition was permanently true for
    everything. The store could never shed anything, and two 33-character status
    lines rode into every agent prompt for a month.

    Persists at most once per `_RETRIEVAL_THROTTLE_SEC` per scope (via the store
    file's mtime) so a hot prompt path does not churn the disk. Best-effort;
    never breaks a render.
    """
    try:
        p = _path(scope)
        now = time.time()
        if p.exists() and (now - p.stat().st_mtime) < _RETRIEVAL_THROTTLE_SEC:
            return
        stamp = _now()
        touched = False
        for e in entries:
            if e.get("id") in selected_ids:
                e["render_count"] = int(e.get("render_count", 0) or 0) + 1
                e["last_rendered"] = stamp
                if pressured:
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
    from services import origin
    src = (entry.get("source") or "").strip()
    # Escaped here rather than at the injection site: this block carries its own
    # `=== LEARNINGS ===` heading, so escaping it whole would indent that too.
    text = origin.escape_markers(entry.get("text", ""))
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
    `render_count` bumped, throttled to stay disk-light — and `retrieval_count`
    only when a cap actually bound, because being emitted alongside everything
    else in a small store is not retrieval. Pass `track=False` for a pure read
    (e.g. previews) that must not touch the store.
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
        key=lambda e: (*_entry_rank(e, newest_ts, oldest_ts), order[id(e)]),
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
        _persist_render(scope, entries, {e.get("id") for e in selected},
                        pressured=remainder > 0)

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
    rend = sum(int(e.get("render_count", 0) or 0) for e in members)
    total_prov = sum(_provenance_count(e) for e in members)
    sev = max((e.get("severity") for e in members if e.get("severity") is not None),
              key=_severity_num, default=None)

    h = _canon_hash(text)
    merged = {**best, "text": text, "id": best.get("id") or h, "hash": h,
              "tags": tags, "provenance": prov, "created_at": created,
              "retrieval_count": retr, "render_count": rend,
              "provenance_total": total_prov}
    if last_ret:
        merged["last_retrieved"] = last_ret
    if sev is not None:
        merged["severity"] = sev
    if any(e.get("pinned") for e in members):
        merged["pinned"] = True
    return _cap_provenance(merged)


def merge_duplicates(scope: str = GLOBAL, *, threshold: float | None = None,
                     apply: bool = True) -> dict:
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
    losers: list[dict] = []
    for c in clusters:
        if len(c["members"]) == 1:
            out.append(c["members"][0])
        else:
            merged_count += len(c["members"]) - 1
            winner = _merge_cluster(c["members"])
            out.append(winner)
            losers += [{**e, "archived_at": _now(),
                        "archived_reason": "merged into a near-duplicate",
                        "superseded_by": winner.get("id")}
                       for e in c["members"] if e.get("text") != winner.get("text")]

    if merged_count and apply:
        # Archive first: for `global` that is also what clears the losers' lines
        # out of the md mirror, which would otherwise re-migrate them back in.
        _archive_append(scope, losers)
        _write_all(scope, out)
    return {"scope": scope, "before": len(entries), "after": len(out), "merged": merged_count}


def prune_stale(
    scope: str = GLOBAL,
    *,
    max_age_days: float | None = None,
    min_provenance: int | None = None,
    min_retrieval: int | None = None,
    apply: bool = True,
) -> dict:
    """Maintenance pass: demote learnings that have stopped earning their place.
    An entry is aged out only when it is stale (not retrieved, or never retrieved
    then not created, within `max_age_days`) AND weakly reinforced (provenance
    count below `min_provenance`) AND seldom retrieved (retrieval_count below
    `min_retrieval`). Anything recently retrieved OR well-reinforced OR
    frequently retrieved is kept, so a durable guardrail never ages out.

    A SALIENCE FLOOR sits above all three thresholds (D5): an entry the operator
    authored or pinned, one that was promoted, or one reinforced
    `_SALIENCE_MIN_PROVENANCE` times is a standing rule and never ages out on time
    alone — however the thresholds are tuned by a caller. Decay re-ranks; it does
    not delete.

    Demoted entries are appended to the scope's archive JSONL (with archived_at /
    archived_reason) rather than deleted, so history stays inspectable and a
    revived pattern can be found. Thresholds are env-overridable (_AGEOUT_*) and
    param-overridable. Returns {scope, before, kept, archived, salient}."""
    scope = _valid_scope(scope)
    max_age_days = _AGEOUT_DAYS if max_age_days is None else max_age_days
    min_provenance = _AGEOUT_MIN_PROVENANCE if min_provenance is None else min_provenance
    min_retrieval = _AGEOUT_MIN_RETRIEVAL if min_retrieval is None else min_retrieval

    entries = _read(scope)
    cutoff = time.time() - max_age_days * 86400.0
    keep: list[dict] = []
    demoted: list[dict] = []
    salient = 0
    for e in entries:
        recent = _recency_ts(e) >= cutoff
        reinforced = _provenance_count(e) >= min_provenance
        retrieved = int(e.get("retrieval_count", 0) or 0) >= min_retrieval
        if _is_salient(e):
            salient += 1
            keep.append(e)
        elif recent or reinforced or retrieved:
            keep.append(e)
        else:
            arch = dict(e)
            arch["archived_at"] = _now()
            arch["archived_reason"] = "aged-out: stale and low-reinforcement"
            demoted.append(arch)

    if demoted and apply:
        _archive_append(scope, demoted)
        _write_all(scope, keep)
    return {"scope": scope, "before": len(entries), "kept": len(keep),
            "archived": len(demoted), "salient": salient,
            "archived_texts": [_short(e) for e in demoted]}


def _short(e: dict, n: int = 70) -> str:
    t = " ".join((e.get("text") or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def sweep_quality(scope: str = GLOBAL, *, apply: bool = True) -> dict:
    """At-rest quality gate: archive entries already in the store that the write
    gate would refuse today. This is how machine noise written before the gate
    existed leaves the prompt, and it names the shape rather than the string, so
    it does not need to be told what to look for. A pinned entry is exempt — a
    pin is the operator saying "I know, keep it".

    Archived, not deleted. Returns {scope, before, after, archived, refused}."""
    scope = _valid_scope(scope)
    entries = _read(scope)
    keep: list[dict] = []
    demoted: list[dict] = []
    for e in entries:
        reason = quality_refusal(e.get("text", ""), source=e.get("source") or "anon")
        if reason and not e.get("pinned"):
            demoted.append({**e, "archived_at": _now(),
                            "archived_reason": f"refused by the quality gate: {reason}"})
        else:
            keep.append(e)
    if demoted and apply:
        _archive_append(scope, demoted)
        _write_all(scope, keep)
    return {"scope": scope, "before": len(entries), "after": len(keep),
            "archived": len(demoted),
            "refused": [{"id": e.get("id"), "text": _short(e),
                         "reason": e["archived_reason"]} for e in demoted]}


def reconcile_store(scope: str = GLOBAL, *, apply: bool = True,
                    threshold: float | None = None) -> dict:
    """At-rest write-time reconciliation: collapse REVISIONS that already landed
    beside each other. `merge_duplicates` handles near-identical text (>= 0.85);
    this handles the band below it, where a rule was re-recorded after being
    edited and the hash no longer matched — the restart guardrail that existed
    twice, a month apart, both copies injected into every prompt.

    The newest text wins; the older row is archived with a `superseded_by`
    pointer and the winner takes the union of provenance, tags and counters.
    Restricted to substantial entries (`_SUPERSEDE_MIN_TOKENS`) for the same
    reason the write path is. Returns {scope, before, after, superseded}."""
    scope = _valid_scope(scope)
    thr = _SUPERSEDE_JACCARD if threshold is None else threshold
    entries = _read(scope)
    live = list(entries)
    losers: list[dict] = []
    actions: list[dict] = []

    changed = True
    while changed:
        changed = False
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                ta, tb = _token_set(a.get("text", "")), _token_set(b.get("text", ""))
                if min(len(_content_tokens(a.get("text", ""))),
                       len(_content_tokens(b.get("text", "")))) < _SUPERSEDE_MIN_TOKENS:
                    continue
                sim = _jaccard(ta, tb)
                elaboration = max(_containment(ta, tb), _containment(tb, ta)) >= _UPDATE_CONTAINMENT
                if not (thr <= sim < _NEAR_DUP_JACCARD or
                        (elaboration and sim < _NEAR_DUP_JACCARD)):
                    continue
                newer, older = (b, a) if _parse_ts(str(b.get("created_at") or "")) >= \
                    _parse_ts(str(a.get("created_at") or "")) else (a, b)
                _absorb(newer, from_entry=older)
                newer["supersedes"] = older.get("id")
                losers.append({**older, "archived_at": _now(),
                               "archived_reason": f"superseded at rest by a later revision (sim {sim:.2f})",
                               "superseded_by": newer.get("id")})
                actions.append({"kept": newer.get("id"), "kept_text": _short(newer),
                                "archived": older.get("id"), "archived_text": _short(older),
                                "similarity": round(sim, 3)})
                live = [e for e in live if e is not older]
                changed = True
                break
            if changed:
                break

    if losers and apply:
        _archive_append(scope, losers)
        _write_all(scope, live)
    return {"scope": scope, "before": len(entries), "after": len(live),
            "superseded": actions}


def cap_provenance(scope: str = GLOBAL, *, apply: bool = True) -> dict:
    """Hold every entry's provenance list to first + last `_PROVENANCE_KEEP`,
    recording the true total. One live row had accrued 46 entries on a
    one-sentence note. Returns {scope, capped}."""
    scope = _valid_scope(scope)
    entries = _read(scope)
    capped = []
    for e in entries:
        before = len(e.get("provenance") or [])
        _cap_provenance(e)
        after = len(e.get("provenance") or [])
        if after < before:
            capped.append({"id": e.get("id"), "from": before, "to": after,
                           "total": _provenance_count(e)})
    if capped and apply:
        _write_all(scope, entries)
    return {"scope": scope, "capped": capped}


def maintain(scope: str = GLOBAL, *, apply: bool = True, prune: bool = True) -> dict:
    """The whole at-rest maintenance pass for one scope, in the order that makes
    each step cheaper: refuse what should never have been admitted, collapse
    duplicates, collapse revisions, cap provenance, then age out what is left and
    unloved. Every step archives rather than deletes.

    This is the single call a scheduler (the keeper, cron) makes. `apply=False`
    reports exactly what would change and writes nothing."""
    scope = _valid_scope(scope)
    before = len(_read(scope))
    out: dict = {"scope": scope, "apply": apply, "before": before}
    out["quality"] = sweep_quality(scope, apply=apply)
    out["duplicates"] = merge_duplicates(scope, apply=apply)
    out["revisions"] = reconcile_store(scope, apply=apply)
    out["provenance"] = cap_provenance(scope, apply=apply)
    if prune:
        out["aged_out"] = prune_stale(scope, apply=apply)
    out["after"] = len(_read(scope)) if apply else None
    return out


_STRUCTURED_PREFIXES = ("project", "agent", "app", "role")


def _scope_from_filename(stem: str) -> str:
    """Undo `_path`'s ':' -> '-'. Only the known structured prefixes are turned
    back into a colon, since a slug may itself contain hyphens."""
    head, _, tail = stem.partition("-")
    if tail and head in _STRUCTURED_PREFIXES:
        return f"{head}:{tail}"
    return stem


def store_scopes() -> list[str]:
    """Every scope key with a store file on disk, from the filenames."""
    return [_scope_from_filename(p.stem) for p in sorted(_dir().glob("*.jsonl"))]


def tidy_store_dir(*, apply: bool = False) -> dict:
    """Store-directory hygiene: the JSONL files are read by scope name, so
    anything else in the directory is either invisible or a scope nobody meant to
    have. Moves (never deletes) hand backups, empty scope files and smoke-test
    scopes to `scratch/learnings-evicted/`.

    Returns {moved: [{from, to, reason}], apply}."""
    d = _dir()
    dest = knowledge._root() / "scratch" / "learnings-evicted"
    moved: list[dict] = []
    for p in sorted(d.iterdir()):
        if p.is_dir() or p.name.endswith(".tmp"):
            continue
        reason = None
        if ".jsonl" in p.name and not p.name.endswith(".jsonl"):
            reason = "hand backup, not a scope file"       # global.jsonl.bak-2026-08-13
        elif p.suffix == ".jsonl":
            stem = p.stem
            rows = [ln for ln in p.read_text().splitlines() if ln.strip()]
            if not rows:
                reason = "empty scope file"
            elif re.search(r"(^|[-_])smoke([-_]|$)", stem):
                reason = "smoke-test scope"
        if not reason:
            continue
        target = dest / p.name
        if apply:
            dest.mkdir(parents=True, exist_ok=True)
            os.replace(p, target)
        moved.append({"from": str(p), "to": str(target), "reason": reason})
    return {"apply": apply, "moved": moved}


def cleanup(*, apply: bool = False, scopes: list[str] | None = None) -> dict:
    """Everything `maintain` does, across every scope on disk, plus store-dir
    hygiene. Dry-run by default: it prints what it would do and touches nothing,
    which is the only responsible way to run a destructive-looking pass over the
    store that is injected into every prompt."""
    # Hygiene first, so a scope that is about to leave the directory is not
    # maintained on the way out.
    files = tidy_store_dir(apply=apply)
    # Only a whole scope FILE takes its scope out of the pass. A hand backup
    # (`global.jsonl.bak-...`) is not the `global` scope leaving, and treating it
    # as one silently skipped the store this whole exercise is about.
    evicted = {_scope_from_filename(Path(m["from"]).stem)
               for m in files["moved"] if m["from"].endswith(".jsonl")}
    names = scopes if scopes is not None else [s for s in store_scopes() if s not in evicted]
    out: dict = {"apply": apply, "scopes": []}
    for s in names:
        try:
            out["scopes"].append(maintain(s, apply=apply))
        except ValueError as e:
            out["scopes"].append({"scope": s, "error": str(e)})
    out["files"] = files
    return out


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

    p_gate = sub.add_parser("gate", help="ask the quality gate about a candidate")
    p_gate.add_argument("text")
    p_gate.add_argument("--source", default="anon")

    p_pin = sub.add_parser("pin", help="pin a learning so it never ages out")
    p_pin.add_argument("id")
    p_pin.add_argument("--scope", default=GLOBAL)
    p_pin.add_argument("--off", action="store_true", help="unpin instead")

    p_mt = sub.add_parser("maintain", help="one scope: refuse, merge, supersede, cap, age out")
    p_mt.add_argument("--scope", default=GLOBAL)
    p_mt.add_argument("--apply", action="store_true", help="write (default is a dry run)")

    p_cl = sub.add_parser("cleanup", help="maintain every scope + tidy the store dir")
    p_cl.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    p_cl.add_argument("--scope", action="append", dest="scopes", default=None)

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
    elif args.verb == "gate":
        reason = quality_refusal(args.text, source=args.source)
        print(json.dumps({"accepted": reason is None, "reason": reason}))
    elif args.verb == "pin":
        hit = set_pinned(args.scope, args.id, not args.off)
        print(json.dumps({"id": args.id, "pinned": bool(hit and hit.get("pinned"))}
                         if hit else {"error": "no such learning"}))
    elif args.verb == "maintain":
        print(json.dumps(maintain(args.scope, apply=args.apply), indent=2))
    elif args.verb == "cleanup":
        print(json.dumps(cleanup(apply=args.apply, scopes=args.scopes), indent=2))
    elif args.verb == "distill":
        print(json.dumps(distill_skill(
            args.scope, args.name, args.description,
            from_tag=args.from_tag, texts=args.texts,
        )))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
