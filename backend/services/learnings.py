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

# How many entries a memory block renders (from the tail, oldest-first within
# that window) so an injected block stays bounded. The corpus is small; this is
# a ceiling, not an expected size.
_MAX_BLOCK_ENTRIES = 60

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


def memory_block(scope: str = GLOBAL, *, max_entries: int = _MAX_BLOCK_ENTRIES) -> str:
    """A compact block of a scope's learnings for prompt injection, or "" when
    empty. Oldest-first (append order) so adding a learning extends the tail and
    leaves the earlier bytes stable for prompt caching. Bounded to the most
    recent `max_entries`."""
    try:
        entries = _read(_valid_scope(scope))
    except Exception:
        return ""
    if not entries:
        return ""
    window = entries[-max_entries:]
    return "\n".join(f"- {e['text']}" for e in window)


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
    elif args.verb == "distill":
        print(json.dumps(distill_skill(
            args.scope, args.name, args.description,
            from_tag=args.from_tag, texts=args.texts,
        )))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
