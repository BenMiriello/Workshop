"""Standing notes: the notes that are in every prompt, and the rules for writing one.

A standing note is a note with `reach: always`. Its body is injected verbatim
into every prompt in its scope, which is the one thing that ever made the
separate `.runtime/learnings` store a separate system.

WHAT THIS MODULE IS. The fold (ticket 81198145, the-plan-2026-08-23.md section
2) deleted `services/learnings.py`, 1,467 lines of machinery for 53 rows the
operator could not see. Most of it went: four counters, a garbage collector
that was never scheduled, an archive tier that a note's own `reach: search`
already expresses, a CLI, and a second scope grammar. What is here is what the
plan said must survive, and nothing else:

  - the QUALITY GATE that refuses a bare status token, narrowed to `always`
    writes. `Ralph rejected: ts_typecheck_fail` is not guidance.
  - write-time DEDUP and SUPERSEDE, so recording the same rule twice does not
    put it in the prompt twice.
  - a PROVENANCE COUNT: how many independent times this rule has been recorded.

THE TEXT MACHINERY BELOW IS A MOVE, NOT A REWRITE. The thresholds, the stopword
list, the instruction vocabulary and the resolution ladder are byte-identical to
what they were in `learnings.py`, because they were tuned against real rows and
retyping them is a chance to change a number nobody meant to change. Their
comments came with them.

WHAT DELIBERATELY DID NOT COME. `last_used`. The plan asked for one timestamp in
place of the four counters, and the four counters existed to drive a collector
that the same plan deletes, so nothing reads it. Writing it would mean writing
to every standing note's FILE on every prompt, and a note's mtime is what
`enforce_index_cap` demotes on and what the Memory page shows as "updated": a
read would look like an edit, on every turn, forever. Recorded on the ticket
rather than dropped silently.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import unicodedata

log = logging.getLogger("workshop.standing")

# The reach value this module is about.
ALWAYS = "always"

# NOTHING HERE SETS `note_type` OR `pinned`, deliberately. Both are deleted with
# their data by memory-is-files-2026-08-28.md section 5, and that document names
# `reach: always` as what answers "what gets read into a prompt first" once the
# type is gone. Writing a type here would be writing data for a field whose
# removal is the next ticket on this file.

# --- similarity thresholds ---------------------------------------------------

# Near-duplicate threshold on canonical token-set Jaccard.
_NEAR_DUP_JACCARD = float(os.environ.get("STANDING_NOTE_NEAR_DUP_JACCARD", "0.85"))

# Revision band: at or above this, and below _NEAR_DUP_JACCARD, a candidate is
# treated as a REVISION of the entry it resembles rather than a new rule, and
# supersedes it. Measured on the live `global` store 2026-08-16: the duplicated
# restart guardrail scores 0.684, and the highest-scoring pair of genuinely
# distinct substantial entries scores 0.125. The gap is enormous, so 0.60 sits
# in open space rather than on a knife edge.
_SUPERSEDE_JACCARD = float(os.environ.get("STANDING_NOTE_SUPERSEDE_JACCARD", "0.60"))

# ...but ONLY between substantial entries. The one close pair in the whole store
# that must NOT merge is `Ralph rejected: py_typecheck_fail` vs
# `..._ts_typecheck_fail` at 0.667: two different failure modes distinguished by
# one token. Short texts have too few tokens for Jaccard to mean anything, so
# supersede requires both sides to carry at least this many content tokens.
# (Those two are refused by the quality gate now, but the guard is what makes the
# threshold safe in general, not the gate.)
_SUPERSEDE_MIN_TOKENS = int(os.environ.get("STANDING_NOTE_SUPERSEDE_MIN_TOKENS", "12"))

# A candidate that CONTAINS this fraction of an existing entry's tokens and is
# longer is an elaboration of it, not a replacement: UPDATE in place.
_UPDATE_CONTAINMENT = float(os.environ.get("STANDING_NOTE_UPDATE_CONTAINMENT", "0.90"))


# --- quality gate thresholds -------------------------------------------------
# Length rules apply to LOOP-WRITTEN entries. An operator typing "prefers
# brevity" into a chat fence means it; a background loop emitting two words does
# not, and telling them apart is exactly what `source` is for (D3). The
# machine-status rule applies to everybody, because nobody wants a status token
# in a prompt.
_QUALITY_MIN_CHARS = int(os.environ.get("STANDING_NOTE_QUALITY_MIN_CHARS", "20"))
_QUALITY_MIN_TOKENS = int(os.environ.get("STANDING_NOTE_QUALITY_MIN_TOKENS", "3"))
# Prose with no imperative in it has to be substantial to count as guidance.
_QUALITY_MIN_PROSE_TOKENS = int(os.environ.get("STANDING_NOTE_QUALITY_MIN_PROSE_TOKENS", "9"))
# A status line is short. Beyond this many words, shape stops being evidence.
_QUALITY_STATUS_MAX_WORDS = int(os.environ.get("STANDING_NOTE_QUALITY_STATUS_MAX_WORDS", "8"))

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

# --- resolution: add / update / supersede / noop ------------------------------

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


# --- the store, which is the note store --------------------------------------
# A standing note has no store of its own. It is a note with `reach: always`,
# in the scope's own knowledge directory, with a title and a hook, editable and
# deletable on /memory through routes that already exist. That is the fold's
# whole return: 53 invisible rows became 53 notes the operator can see.

_PROVENANCE_RE = re.compile(r"^<!--\s*seen:\s*(?P<n>\d+)\s*-->\s*\n?", re.M)


def provenance_of(body: str) -> int:
    """How many independent times this rule has been recorded. 1 for a note
    written once, which is most of them, and the count is what tells a rule the
    system keeps rediscovering from one somebody typed on a whim."""
    m = _PROVENANCE_RE.search(body or "")
    return int(m.group("n")) if m else 1


def _with_provenance(body: str, n: int) -> str:
    """Stamp the count on, replacing any count already there. It is its own
    line rather than a field on the note marker because the note marker is
    knowledge.py's format and a standing note must not need a private dialect
    of it: strip this line and what is left is an ordinary note."""
    clean = _PROVENANCE_RE.sub("", body or "", count=1).lstrip("\n")
    return f"<!-- seen: {max(1, int(n))} -->\n{clean}" if n > 1 else clean


def _title_for(text: str) -> str:
    """A standing note's title is its first sentence, capped.

    A learning had no title, which is most of why it could not be shown: a list
    of 53 untitled paragraphs is not a list anybody reads. The first sentence is
    the right guess because every row is an imperative, so the first sentence IS
    the rule and the rest is the reason.
    """
    t = " ".join((text or "").split())
    if not t:
        return "Standing note"
    cut = t
    for stop in (". ", "; ", ": "):
        i = t.find(stop)
        if 0 < i < 90:
            cut = t[:i]
            break
    cut = cut.rstrip(" .;:")
    if len(cut) > 90:
        cut = cut[:87].rsplit(" ", 1)[0] + "..."
    return cut or "Standing note"


def _standing_notes(scope: str) -> list[dict]:
    """Every standing note in this scope, as `{id, text, hash, seen}` rows,
    which is the shape `_resolve` reads."""
    from services import knowledge
    out: list[dict] = []
    for n in knowledge.list_notes(scope):
        try:
            raw = (knowledge.scope_dir(scope) / f"{n['id']}.md").read_text()
        except OSError:
            continue
        if knowledge.note_reach(raw) != ALWAYS:
            continue
        text = knowledge.split_author(raw)[1]
        text = _PROVENANCE_RE.sub("", text, count=1).strip()
        out.append({"id": n["id"], "text": text, "hash": _canon_hash(text),
                    "seen": provenance_of(raw), "title": n.get("title") or n["id"]})
    return out


def record(text: str, *, source: str = "anon", scope: str = "workshop") -> dict:
    """Record a standing note, reconciling it against the ones already in scope.

    Returns `{resolution, scope, note_id, reason}` where resolution is one of
    add / update / supersede / noop / refused. Never raises: every path that
    writes one of these (a review answer, a chat fence, a loop's failure
    handler) has real work to finish afterwards, so a bad candidate is a
    refusal and not an exception.
    """
    from services import knowledge

    t = " ".join((text or "").split())
    reason = quality_refusal(t, source=source)
    if reason:
        return {"resolution": "refused", "refused": True, "reason": reason,
                "scope": scope, "note_id": ""}

    existing = _standing_notes(scope)
    action, target, _sim = _resolve(t, existing)

    if action == "noop":
        # Already said. Bump the provenance count: the same rule arriving twice
        # from two places is the signal that it is real, and it is the only
        # counter worth keeping.
        if target:
            raw = (knowledge.scope_dir(scope) / f"{target['id']}.md").read_text()
            seen = provenance_of(raw) + 1
            knowledge._put_note_sync(
                scope, target["id"], target["title"],
                _with_provenance(target["text"], seen),
                author="", reach=ALWAYS)
        return {"resolution": "noop", "scope": scope,
                "note_id": target["id"] if target else "", "reason": ""}

    if action in ("update", "supersede") and target:
        # The new text replaces the old IN PLACE, keeping the note's id so every
        # ref and backlink that named it still resolves. Superseding used to
        # mint a new row and archive the old one, which is the same information
        # and one more thing to garbage collect.
        knowledge._put_note_sync(
            scope, target["id"], _title_for(t),
            _with_provenance(t, provenance_of(target["text"]) + 1),
            hook="", author=source, reach=ALWAYS)
        return {"resolution": action, "scope": scope, "note_id": target["id"], "reason": ""}

    # The id rule lives in task_scope and is already tested there, including the
    # truncation bug that once produced `a--b` and made the store reject the
    # note with a message about an invalid SCOPE. A third copy of it would be a
    # third chance to reintroduce that.
    from services.task_scope import _note_id_from_title
    note_id = _note_id_from_title(_title_for(t))
    knowledge._put_note_sync(
        scope, note_id, _title_for(t), t,
        hook="", author=source, reach=ALWAYS)
    return {"resolution": "add", "scope": scope, "note_id": note_id, "reason": ""}


# --- the prompt block ---------------------------------------------------------
# The budget is the one the learnings block had, kept deliberately: the fold's
# ticket says not to change the injection budget totals, and this is the block
# that replaces it one for one.
_MAX_BLOCK_ENTRIES = int(os.environ.get("STANDING_NOTE_BLOCK_MAX_ENTRIES", "60"))

# 1800, and that is NOT a budget increase. `learnings_context` called
# `memory_block` TWICE -- once for the `global` scope and once for the chat's
# own -- and passed no overrides either time, so the 900 default applied to each
# and the ceiling was always 1800. This block replaces both calls with one that
# reads the whole read-union, so it carries both allowances. The fold's ticket
# says not to change the injection budget totals, and this is what not changing
# them looks like once two blocks become one.
_BLOCK_TOKEN_BUDGET = int(os.environ.get("STANDING_NOTE_BLOCK_TOKEN_BUDGET", "1800"))


def _approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token). Good enough to hold a prompt block
    to a budget; nothing here needs a real tokenizer."""
    return max(1, len(text or "") // 4)


def block(scope: str = "workshop", *, max_entries: int = _MAX_BLOCK_ENTRIES,
          max_tokens: int = _BLOCK_TOKEN_BUDGET, role: str | None = None,
          private: str | None = None) -> str:
    """The standing notes this scope READS, as prompt text, inside the budget.

    THE READ-UNION, not one scope. `knowledge.read_layers` already answers "what
    does a reader in this scope read": `workshop` (widest, and therefore what
    reaches everywhere), then the role, then the place, then the agent's own.
    That is what replaces the old store's separate `global` scope -- a scope
    whose entire definition was "injected everywhere", which is what the widest
    layer already means. One mechanism instead of two.

    Ranked by provenance (a rule rediscovered three times outranks one recorded
    once) and then by how recently the note was written, which is the file's own
    mtime and needs no second timestamp. Reading does NOT write: see the module
    docstring on why `last_used` is not here.
    """
    from services import knowledge
    rows: list[dict] = []
    seen_ids: set[tuple[str, str]] = set()
    try:
        layers = knowledge.read_layers(scope, role, private)
    except Exception:
        layers = [scope]
    for layer in layers:
        try:
            for r in _standing_notes(layer):
                key = (layer, r["id"])
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                r = {**r, "scope": layer}
                rows.append(r)
        except Exception:
            continue
    if not rows:
        return ""
    mtimes: dict[str, float] = {}
    for r in rows:
        try:
            mtimes[r["id"]] = (knowledge.scope_dir(r.get("scope", scope)) / f"{r['id']}.md").stat().st_mtime
        except OSError:
            mtimes[r["id"]] = 0.0
    rows.sort(key=lambda r: (-r["seen"], -mtimes[r["id"]], r["id"]))

    out: list[str] = []
    used = 0
    for r in rows[:max_entries]:
        line = f"- {r['text']}"
        cost = _approx_tokens(line)
        if used + cost > max_tokens:
            break
        out.append(line)
        used += cost
    return "\n".join(out)


def count(scope: str = "workshop") -> int:
    """How many standing notes this scope has. For the operator-visible number
    the plan asks for, and for the migration's conservation check."""
    return len(_standing_notes(scope))


# How many independent confirmations make a rule worth enforcing rather than
# merely recording. Ported with its threshold from the store this replaced.
_PROMOTE_MIN_SOURCES = int(os.environ.get("STANDING_NOTE_PROMOTE_MIN_SOURCES", "3"))


def is_stable(scope: str, note_id: str) -> bool:
    """Has this rule proved itself enough to be worth enforcing?

    True when it has been recorded independently at least `_PROMOTE_MIN_SOURCES`
    times: the same rule re-derived from three different places and answered the
    same way three times.

    Writes nothing and mints nothing. The operator owns rules; a system that
    granted itself authority off its own counter is the thing the loop this
    serves exists to prevent.
    """
    from services import knowledge
    try:
        raw = (knowledge.scope_dir(scope) / f"{note_id}.md").read_text()
    except OSError:
        return False
    if knowledge.note_reach(raw) != ALWAYS:
        return False
    return provenance_of(raw) >= _PROMOTE_MIN_SOURCES


# One filename component: a scope key with `:` folded to `-` and nothing else in
# it. `/` and `..` are what this exists to keep out, because the callers write
# to whatever comes back.
_SAFE_SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9:_-]*", re.IGNORECASE)


def valid_scope(scope_key: str) -> str:
    """`scope_key` if the note store accepts it, else ValueError.

    Shared with `memory_keeper` and `triager`, which borrow it purely to build a
    safe filename and had the identical defect before it existed: folding `:` to
    `-` leaves `/` and `..` intact, and both of them write to whatever comes
    back. See the 2026-08-11 scope-separation audit section 2.
    """
    from services import knowledge
    key = (scope_key or "").strip()
    if key == "workshop":
        return key
    if _SAFE_SCOPE_RE.fullmatch(key):
        # Defer to the store for the structured shapes so junk is rejected once.
        knowledge._parse_scope(key)
        return key
    raise ValueError(f"invalid memory scope: {scope_key!r}")


def list_standing(scope: str = "workshop") -> list[dict]:
    """Every standing note in exactly this scope, as `{id, title, text, seen}`.
    Exactly this scope and not the read-union: a caller consolidating INTO a
    scope must not dedup against notes it cannot write."""
    return _standing_notes(scope)


def canon_hash(text: str) -> str:
    """The canonical hash two texts share when they say the same thing."""
    return _canon_hash(text)


def token_set(text: str) -> set[str]:
    """Content tokens, for a caller doing its own similarity pass."""
    return _token_set(text)


def jaccard(a: set[str], b: set[str]) -> float:
    """Token-set overlap, on the same scale the dedup ladder uses."""
    return _jaccard(a, b)


NEAR_DUP_JACCARD = _NEAR_DUP_JACCARD


# --- CLI ----------------------------------------------------------------------
# `python -m services.standing block --scope workshop` and `... record "<text>"`.
# It exists because the SessionStart hook renders the guardrail block through it,
# the same call the backend uses to inject one, so a session and a chat cannot
# disagree about what the standing notes say.

def _main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="services.standing")
    sub = ap.add_subparsers(dest="verb", required=True)

    b = sub.add_parser("block", help="the standing notes of a scope, as prompt text")
    b.add_argument("--scope", default="workshop")

    r = sub.add_parser("record", help="record a standing note")
    r.add_argument("text")
    r.add_argument("--source", default="anon")
    r.add_argument("--scope", default="workshop")

    c = sub.add_parser("count", help="how many standing notes a scope has")
    c.add_argument("--scope", default="workshop")

    args = ap.parse_args(argv)
    if args.verb == "block":
        out = block(args.scope)
        if out:
            print(out)
        return 0
    if args.verb == "count":
        print(count(args.scope))
        return 0
    res = record(args.text, source=args.source, scope=args.scope)
    if res.get("resolution") == "refused":
        print(f"not recorded: {res.get('reason')}", file=sys.stderr)
        return 1
    print(f"{res['resolution']}: {res.get('note_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
