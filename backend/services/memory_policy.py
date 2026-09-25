"""memory_policy -- the `share` field (memory-and-documentation-model-2026-08-14.md
sec 6, sec 12 Phase 5): who a note, doc or chat is quiet toward.

CURATION, NOT CONFINEMENT. Per SEC-A2 (the 2026-07-18 agents security audit
A2, and `scope_tree`'s own module docstring), this box runs as one uid: any
process here can already read any file directly. `share` never restricts what a
process may read. It restricts one thing only -- what a SEARCH surfaces -- so
that one app's chat search does not fill up with another app's transcripts and a
project's search does not fill up with a sibling project's working notes. Do not
describe this module, in a docstring, a log line or the UI, as security,
permission or access control.

THE FIELD. `share` takes exactly three values, resolved narrowest wins (an
explicit value on the item beats one on its scope, which beats the kind's global
default):

  - `inherit` (the default) -- follow `scope_tree.reach` exactly as before this
    phase existed. An item in a scope a searcher can reach is found; one in a
    scope it cannot is not.
  - `open` -- found from ANY search, as if the item sat at the root of the tree.
    Overrides `reach` outright.
  - `private` -- found only by a search running AS the item's own scope. An
    ancestor's search -- which would normally reach downward into it -- does
    not. On a kind that HAS no scope (a doc lives in `docs/`, not in the
    tree) there is no own-scope to be private to, so `private` falls back to
    `inherit` there rather than matching nothing; see `reachable()` for the
    measured reason that is spelled out instead of left to emerge.
    `reachable()` is the one place this rule is expressed; every caller
    (`semantic_index._keep_mask`, `knowledge._plaintext_search`) calls through
    it rather than re-deriving the rule.

STORAGE. One file, `.runtime/memory-policy.json`, the same shape as
`scope_tree`'s `.runtime/scope-tree.json`: normally absent (meaning every kind's
global default applies, unmodified), loaded once and cached against
(size, mtime_ns) so a search or a recall never touches disk more than once per
change. Nothing writes it until the operator changes a setting; reading a
missing file is not an error and never creates one.

    {
      "version": 1,
      "defaults": {"note": "inherit", "doc": "inherit", "chat": "private"},
      "scopes":   {"project:foo": "private"},
      "items":    {"workshop:chat/abc123": "open"}
    }

ITEM KEYS ARE PHASE-3 REFS (`services/refs.py`) -- one addressing scheme, not a
second one. A note's item key is `refs.note_ref(scope, note_id)`; a doc's is
`refs.doc_ref(path)`. A CHAT's item key is `workshop:chat/<thread-id>` -- the
whole transcript, no `#<message-id>` suffix -- because sharing is a property of
the transcript, not of one message in it. This is a deliberate, narrow
divergence from `refs.parse_ref`'s chat branch, which requires `#<message-id>`
because IT addresses a citation anchor into one message, a different job. A
whole-thread key does not round-trip through `refs.parse_ref`/`resolve_ref`
for that reason; it is only ever looked up in this module's own maps, never
resolved as a citation chip. See the module's write-up for why this is called
out rather than silently patched over.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from wsplatform import paths

STORE = Path(os.environ.get(
    "WORKSHOP_MEMORY_POLICY", str(paths.workshop_root() / ".runtime" / "memory-policy.json")))

ShareValue = Literal["inherit", "open", "private"]
SHARE_VALUES: tuple[str, ...] = ("inherit", "open", "private")

# The operator's 2026-08-14 direction (sec 6, sec C): notes and docs are
# written to be read and default to following the tree; chat transcripts are
# the largest corpus by volume and the least intentional one, so they default
# private. This dict is itself the global setting the operator can change --
# `set_defaults` rewrites it, `resolve` always reads the current value.
DEFAULT_DEFAULTS: dict[str, str] = {"note": "inherit", "doc": "inherit", "chat": "private"}


def _empty() -> dict:
    return {"version": 1, "defaults": dict(DEFAULT_DEFAULTS), "scopes": {}, "items": {}}


# --- load, cached against (size, mtime_ns) -- same pattern as
# `scope_tree._load_overrides` -----------------------------------------------
_cache: tuple[tuple, dict] | None = None


def _stat_key() -> tuple:
    try:
        st = STORE.stat()
        return (str(STORE), st.st_size, st.st_mtime_ns)
    except OSError:
        return (str(STORE), None, None)


def _load() -> dict:
    """The current policy, absent-file-is-defaults, corrupt-file-is-defaults.

    A policy file is advisory configuration, not a lock: a bad edit (hand or
    agent) degrades to "nothing overridden" rather than breaking every search
    in the system."""
    global _cache
    key = _stat_key()
    cached = _cache
    if cached is not None and cached[0] == key:
        return cached[1]
    data = _empty()
    try:
        raw = json.loads(STORE.read_text())
        if isinstance(raw, dict):
            defaults = raw.get("defaults")
            if isinstance(defaults, dict):
                for k, v in defaults.items():
                    if v in SHARE_VALUES:
                        data["defaults"][str(k)] = v
            scopes = raw.get("scopes")
            if isinstance(scopes, dict):
                data["scopes"] = {str(k): v for k, v in scopes.items() if v in SHARE_VALUES}
            items = raw.get("items")
            if isinstance(items, dict):
                data["items"] = {str(k): v for k, v in items.items() if v in SHARE_VALUES}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = _empty()
    _cache = (key, data)
    return data


def _save(data: dict) -> None:
    global _cache
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, STORE)
    # Drop the cache rather than pre-seed it: the next `_load()` re-stats the
    # file we just wrote, which is one extra syscall and a guarantee the cache
    # key always matches what is actually on disk.
    _cache = None


def read() -> dict:
    """The whole policy document, for the Settings page and the Memory page's
    per-item control. Callers must not mutate the returned dict; treat it as a
    snapshot."""
    return _load()


# --- resolution --------------------------------------------------------------

def resolve(kind: str, scope_key: str | None = None, item_ref: str | None = None) -> str:
    """Narrowest wins: an explicit value on the ITEM beats one on its SCOPE,
    which beats the global default for `kind`. `kind` not in `defaults` (a
    kind this phase does not cover, e.g. `vault`) resolves to `"inherit"`."""
    data = _load()
    if item_ref:
        v = data["items"].get(item_ref)
        if v:
            return v
    if scope_key:
        v = data["scopes"].get(scope_key)
        if v:
            return v
    return data["defaults"].get(kind, "inherit")


def reachable(*, share: str, item_scope: str, from_scope: str | None, in_reach: bool) -> bool:
    """Whether an item is visible to a search/recall running AS `from_scope`,
    given its resolved `share` and whether the tree's plain `reach()` rule
    (computed by the caller, passed in as `in_reach`) already allows it.

    This is the one place the three-value rule is expressed; `semantic_index`
    and `knowledge` both call through it rather than re-deriving it. See the
    module docstring: this is a relevance filter for search, not a read guard.
    """
    if share == "open":
        return True
    if share == "private":
        # PRIVATE NEEDS A SCOPE TO BE PRIVATE TO. `private` means "found only
        # from inside its OWN scope". A doc has no scope -- it lives in
        # `docs/`, not in the tree, and `semantic_index` calls that "placeless,
        # not unreachable" -- so `item_scope` is "" and the equality below can
        # never be true from anywhere. Left to emerge from the string compare,
        # `doc: private` did not restrict documents, it ERASED them: on
        # 2026-08-23 the whole 215-document corpus, including the model of
        # record, returned zero hits from every real (scoped) search while
        # scoring 0.787 from an unscoped one.
        #
        # So a scopeless kind resolves `private` to the tree's own rule
        # instead, which for a placeless item means "found". No value of this
        # setting -- and no future kind that turns out to have no scope --
        # can express "findable from nowhere", because that is not a
        # restriction anyone would choose; it is a corpus deletion with no
        # undo and no error message. A kind that HAS scopes is unaffected.
        if not item_scope:
            return in_reach
        return bool(from_scope) and from_scope == item_scope
    return in_reach  # "inherit": the tree's own rule, unchanged


# --- writes -- operator-driven only, nothing here is called automatically ----

def _validate(share: str | None) -> None:
    if share is not None and share not in SHARE_VALUES:
        raise ValueError(f"invalid share value: {share!r} (must be one of {SHARE_VALUES})")


def set_defaults(defaults: dict[str, str]) -> dict:
    """Replace one or more of the global per-kind defaults. Unlisted kinds keep
    their current value; this is a merge, not a full replace, so a caller
    changing only `chat` cannot accidentally reset `note`/`doc`."""
    for v in defaults.values():
        _validate(v)
    data = _load()
    new_defaults = dict(data["defaults"])
    for k, v in defaults.items():
        new_defaults[str(k)] = v
    data = {**data, "defaults": new_defaults}
    _save(data)
    return data


def set_scopes(scopes: dict[str, str | None]) -> dict:
    """Set or clear scope-level overrides. A value of `None` or `"inherit"`
    clears that scope's override (falls back to the global default); any other
    value must be a valid share value."""
    data = _load()
    new_scopes = dict(data["scopes"])
    for scope_key, v in scopes.items():
        if v in (None, "inherit"):
            new_scopes.pop(scope_key, None)
            continue
        _validate(v)
        new_scopes[scope_key] = v
    data = {**data, "scopes": new_scopes}
    _save(data)
    return data


def set_item(item_ref: str, share: str | None) -> dict:
    """Set (or, for `None`/`"inherit"`, clear) one item's explicit override."""
    data = _load()
    new_items = dict(data["items"])
    if share in (None, "inherit"):
        new_items.pop(item_ref, None)
    else:
        _validate(share)
        new_items[item_ref] = share
    data = {**data, "items": new_items}
    _save(data)
    return data


def chat_item_ref(thread_id: str) -> str:
    """The whole-transcript item key for a chat thread. See the module
    docstring's "ITEM KEYS ARE PHASE-3 REFS" section for why this is
    `workshop:chat/<thread-id>` rather than `refs.chat_ref`, which requires a
    message id and addresses a citation, not a transcript."""
    return f"workshop:chat/{thread_id}"
