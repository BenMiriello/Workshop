"""Store abstraction — the platform data primitive (Phase 4).

A Store is a named, typed handle onto data an app owns. It is a REGISTRATION over
existing on-disk data, never a relocation: the underlying `.runtime/` files stay
exactly where they are, with their modes intact (e.g. plaid-items.json stays
0600 — nothing here moves or chmods a file). Wrapping data as a Store gives it a
stable URI (`workshop://store/<app>/<name>`), an optional `type` (so a broker can
answer "give me a Store of type X"), and a mandatory portable `export`.

The registry is populated from two sources:
  * `register_builtin_stores()` — the concrete data stores the built-in apps own
    (budget plan, plaid items, commute data, secrets), each pointing at its real
    existing path.
  * `register_from_manifests()` — app-manifest `provides` entries that carry a
    store `type` (the declarative half; see services/manifests.py).
  * `knowledge.register_knowledge_stores()` — knowledge scopes register
    themselves as platform-owned Stores of type "Note" (called from knowledge.py).

Grants (services/permissions.py) reference a Store by URI; a provider checks a
minted grant token per invocation via `permissions.check(token, uri, verb)`.

READING A STORE (P3). `read()` below is the one door, and it is the PROVIDER half
of the resource-access protocol (services/broker.py is the deciding half):

    operator            -> served, always, ungated, verbatim. A design where the
                           human asks a model for permission to read their own
                           notes is simply wrong.
    agent with a token  -> `permissions.check()` decides, mechanically, for free.
                           No broker call. This is the path a dispatched sweep is
                           on for its whole life, which is why an API outage costs
                           in-flight work nothing.
    agent, no token,    -> ONE broker adjudication, which mints or denies.
    mediated store
    agent, no token,    -> denied. Nothing decides at request time in these tiers.
    open/gated store

Adapters are the only app-specific part of the protocol (one per Store `type`).
The broker never imports one: it works on URIs, verbs, selectors, and policy, and
has no idea the notes app exists.
"""
from __future__ import annotations

import builtins
import json
import logging
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("workshop.stores")
from typing import Any

# workshop://store/<app>/<name>[/<path>] — app and name are slug-ish; the optional
# path suffix names a folder or item INSIDE the store, which is what makes
# per-folder policy expressible at all (a policy record keyed by URI prefix can
# only be as granular as the URIs are). The two-segment form is unchanged.
_URI_RE = re.compile(
    r"^workshop://store/(?P<app>[a-z0-9][a-z0-9._-]*)/(?P<name>[a-z0-9][a-z0-9._-]*)"
    r"(?:/(?P<path>[^\s?#]+))?$"
)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class AccessDenied(PermissionError):
    """A store read/write the caller does not hold authority for.

    `reason_class` is the enum the caller branches on and is deliberately separate
    from the prose: `adjudicator_unavailable` (retryable) must never be confusable
    with a policy denial, because an outage rendered as "denied" teaches an
    operator to loosen policy to fix an outage."""

    def __init__(self, reason_class: str, reason: str, *, retryable: bool = False):
        super().__init__(reason)
        self.reason_class = reason_class
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True)
class Store:
    """A registered handle onto an app's or project's data.

    uri          — workshop://store/<owner_app>/<name>, the stable identifier.
    owner_app    — the app that owns the store (its data lives inside that
                   app). Also serves as `host_app` in the entity-model
                   vocabulary (2026-07-18 §8.3).
    name         — the store name within the app or project.
    type         — an optional broker type ("Note", "Budget", "Finance", ...).
    path         — the resolved filesystem path (file or directory) the store
                   wraps. May not exist yet; export/consumers tolerate absence.
    host_project — the project slug that owns this store, when the store is
                   project-scoped rather than app-scoped. Mostly None for the
                   builtin stores; populated by the per-project knowledge
                   register path. Enables B11's "Storage and access" panel
                   inside ProjectDetail to fetch by host without parsing URIs.
    """
    uri: str
    owner_app: str
    name: str
    type: str | None
    path: str
    host_project: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# In-memory registry keyed by uri. Rebuilt on each register_* pass; declare is
# idempotent (re-declaring the same uri replaces the record).
_REGISTRY: dict[str, Store] = {}


def make_uri(owner_app: str, name: str, path: str | None = None) -> str:
    """The URI for a store, or for a path inside one (`.../vault/journal/2026`)."""
    if not _SLUG_RE.match(owner_app or "") or not _SLUG_RE.match(name or ""):
        raise ValueError(f"invalid store id parts: {owner_app!r}/{name!r}")
    base = f"workshop://store/{owner_app}/{name}"
    suffix = (path or "").strip("/")
    return f"{base}/{suffix}" if suffix else base


def parse_uri(uri: str) -> tuple[str, str, str]:
    """Return (owner_app, name, path) for a valid store uri; raise otherwise.

    `path` is "" for the two-segment form, so a caller that only wants the store
    is unaffected by the suffix existing."""
    m = _URI_RE.match(uri or "")
    if not m:
        raise ValueError(f"not a store uri: {uri!r}")
    return m.group("app"), m.group("name"), (m.group("path") or "")


def resolve(uri: str) -> "tuple[Store, str]":
    """The registered Store a URI names, plus the path inside it. KeyError if the
    store is not declared."""
    app, name, path = parse_uri(uri)
    store = _REGISTRY.get(make_uri(app, name))
    if store is None:
        raise KeyError(f"store not declared: {make_uri(app, name)}")
    return store, path


def declare(owner_app: str, name: str, path: str | Path, type: str | None = None,
            host_project: str | None = None) -> Store:
    """Register a Store over an existing path. REGISTRATION, not relocation — the
    file/dir is left exactly as it is (mode, location, content). Idempotent.

    `host_project` tags the store as project-scoped (per entity-model
    2026-07-18 §8.3). Optional; the URI still keys uniqueness."""
    uri = make_uri(owner_app, name)
    store = Store(uri=uri, owner_app=owner_app, name=name, type=type, path=str(path),
                  host_project=host_project)
    _REGISTRY[uri] = store
    return store


def by_host(*, app: str | None = None, project: str | None = None) -> "list[Store]":
    """Stores hosted by a given app or project (per entity-model §8.3).

    Enables B11's Storage-and-access panel inside App and Project detail:
    call by_host(app="notes") or by_host(project="acme") to fetch the stores
    that surface's "Storage and access" section should display."""
    matches = []
    for s in _REGISTRY.values():
        if app is not None and s.owner_app == app:
            matches.append(s)
        elif project is not None and s.host_project == project:
            matches.append(s)
    return sorted(matches, key=lambda s: s.uri)


def get(uri: str) -> Store | None:
    """The registered Store for a uri, or None."""
    return _REGISTRY.get(uri)


def list(type: str | None = None) -> "list[Store]":
    """All declared stores, optionally filtered by `type`, sorted by uri.

    Annotations are lazy strings under `from __future__ import annotations`, so
    the `list` name shadow is cosmetic in a signature — but it is REAL in a body:
    inside this module `list(x)` calls THIS function and `isinstance(x, list)`
    raises. Use `[*x]` and `builtins.list`."""
    stores = sorted(_REGISTRY.values(), key=lambda s: s.uri)
    if type is not None:
        stores = [s for s in stores if s.type == type]
    return stores


def types() -> "list[str]":
    """The distinct set of declared store types (for the broker's type menu)."""
    return sorted({s.type for s in _REGISTRY.values() if s.type})


# --------------------------------------------------------------------------- #
# Export — the mandatory portable dump. Copies the store's underlying file(s) to
# a fresh, self-contained artifact directory and returns its path. Read-only over
# the source: it never moves, deletes, or chmods the original.
# --------------------------------------------------------------------------- #
def _runtime_root() -> Path:
    import os
    return Path(os.environ.get(
        "WORKSHOP_RUNTIME",
        str(Path(__file__).resolve().parents[2] / ".runtime"),
    ))


def export(uri: str) -> str:
    """Serialize a store to a portable artifact and return the artifact path.

    The artifact is a directory under `.runtime/store-exports/` containing a
    `store.json` manifest plus a copy of the store's payload (a single file, or a
    tree copy for a directory-backed store). The source is never modified."""
    store = get(uri)
    if store is None:
        raise KeyError(f"store not declared: {uri}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = f"{store.owner_app}-{store.name}"
    out_dir = _runtime_root() / "store-exports" / f"{safe}-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    src = Path(store.path)
    payload_rel: str | None = None
    if src.exists():
        if src.is_dir():
            dest = out_dir / "payload"
            shutil.copytree(src, dest, dirs_exist_ok=True)
            payload_rel = "payload"
        else:
            dest = out_dir / src.name
            shutil.copy2(src, dest)  # copy2 preserves the source; original untouched
            payload_rel = src.name

    manifest = {
        "uri": store.uri,
        "owner_app": store.owner_app,
        "name": store.name,
        "type": store.type,
        "source_path": store.path,
        "payload": payload_rel,
        "exported_at": ts,
        "present": src.exists(),
    }
    (out_dir / "store.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return str(out_dir)


# --------------------------------------------------------------------------- #
# Population.
# --------------------------------------------------------------------------- #
def _operator_runtime_dir() -> Path | None:
    """`.runtime/users/<operator_id>/`, or the first existing per-user dir as a
    single-user fallback, or None. Used to resolve per-owner store paths at
    registration time (single-user system — one owner in practice)."""
    root = _runtime_root() / "users"
    try:
        from services import auth_store
        oid = auth_store.operator_id()
    except Exception:
        oid = None
    if oid is not None:
        d = root / str(int(oid))
        if d.exists():
            return d
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.name.isdigit():
                return child
    return None


def register_builtin_stores() -> None:
    """Declare the concrete data stores the built-in apps own, each pointing at
    its real existing path. Per-owner files resolve to the operator's runtime
    dir. Best-effort: a missing owner dir simply skips the per-owner stores."""
    # The platform's own stores. Everything app-specific is declared on that
    # app's AppSpec and registered below, so a store exists exactly while the
    # app that owns it is installed.
    runtime = _runtime_root()
    declare("secrets", "env", runtime / "secrets", type="Secret")

    # The notes vault used to be declared HERE, four lines below a comment
    # saying app-specific stores are not. It moved onto the notes app's own
    # AppSpec on 2026-08-27 (`store_owner="notes"` keeps the `notes:vault` URI
    # unchanged), so an installation without that app no longer declares a store
    # over a folder in the user's home directory. See
    # docs/platform-redesign/memory-page-and-file-viewer-2026-08-27.md 1.2.

    # Chat attachments. Content-addressed blobs live per-owner under
    # .runtime/users/<id>/files (chat_attachments.py). Declaring the store makes an
    # attached file addressable as workshop://store/attachments/chat, so a
    # `workshop:file/<id>` ref and a Store target name the same object. The store
    # path is the OWNER-AGNOSTIC users root (there is no pinned owner at startup);
    # per-owner resolution happens in the resolver, keyed by the attachment id.
    declare("attachments", "chat", _runtime_root() / "users", type="File")

    register_app_stores()


def register_app_stores() -> None:
    """Declare each installed app's own stores, from its AppSpec.

    Best-effort per store: a per-owner store resolves to None when there is no
    owner runtime dir yet, and is simply skipped, exactly as before.
    """
    from services import app_registry

    for app_id, name, factory, type_, per_owner in app_registry.installed_stores():
        try:
            path = factory()
        except Exception:
            log.exception("stores: %s/%s failed to resolve its path", app_id, name)
            continue
        if path is None:
            continue
        declare(app_id, name, path, type=type_)


def register_from_manifests() -> None:
    """Declare stores from app-manifest `provides` entries that carry a store
    `type`. The declarative half of the model: a manifest can name a store and
    its type without wiring a concrete path here (path defaults under the app's
    knowledge/data dir). Backward compatible — plain string `provides` entries
    (no type) declare nothing."""
    try:
        from services import manifests
    except Exception:
        return
    for app_id, m in manifests.load_manifests().items():
        provide_types = m.get("provide_types") or {}
        if not isinstance(provide_types, dict):
            continue
        for capability, stype in provide_types.items():
            # A capability like "finance.read" -> store name "finance".
            name = str(capability).split(".", 1)[0] or str(capability)
            if not _SLUG_RE.match(name):
                continue
            # Path defaults under the app's data area; concrete built-in paths are
            # set by register_builtin_stores() and win (same uri, later declare).
            path = _runtime_root() / "apps" / app_id / name
            with_type = str(stype) if stype else None
            declare(app_id, name, path, type=with_type)


def register_all() -> None:
    """Populate the registry from every source. Idempotent; safe to call at
    startup and after a manifest change. builtin stores are declared last so
    their concrete paths win over any manifest-declared placeholder of the same
    uri."""
    register_from_manifests()
    try:
        from services import knowledge
        knowledge.register_knowledge_stores()
    except Exception:
        pass
    register_builtin_stores()


# --------------------------------------------------------------------------- #
# Adapters — the ONE app-specific part of the protocol (report Part 9).
#
# "Search by name or tag" means something for a Note and nothing for a Plaid
# transaction; "filter to the relevant fields" means something for a transaction
# and little for a Markdown file. That is the whole of the per-type surface: one
# adapter per Store `type`, resolving a selector to items and shaping a response.
# Everything else (policy, grants, verbs, the broker, check()) is type-agnostic.
#
# Note what does NOT live here: a generative filter pass. Every adapter below
# selects and projects mechanically, so its output is a subset of its input BY
# CONSTRUCTION and needs no verifier. A future semantic-filter pass over untrusted
# bodies would need the same Sonnet floor as the broker and a subset check on its
# way out; nothing here does, because nothing here reasons.
# --------------------------------------------------------------------------- #
# Structural redaction: a regex problem, so it needs no model at any tier. Doing
# it deterministically is faster, free, testable, and cannot be talked out of its
# job by the document it is redacting.
_REDACT_PATTERNS = (
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[redacted:card]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[redacted:email]"),
    (re.compile(r"\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"), "[redacted:phone]"),
    (re.compile(r"\b(?:sk|pk|ghp|xox[bpas])[-_][A-Za-z0-9_-]{12,}\b"), "[redacted:key]"),
)


def redact_text(text: str) -> str:
    """Structural spans out of a body. No model, no judgment, no exceptions."""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _project(item: dict[str, Any], fields: "list[str] | None") -> dict[str, Any]:
    """An item narrowed to the policy's declared field ceiling (None = as-is)."""
    if not fields:
        return dict(item)
    return {k: v for k, v in item.items() if k in fields}


class StoreAdapter:
    """Per-type selector vocabulary, response shaping, and the write door.

    An adapter is contributed either by the platform (below) or by an app
    (`AppSpec.store_adapters`), and the two are looked up through one list. So an
    app's data can be reachable through the Store protocol without the protocol
    holding a line about that app.
    """

    type: str = ""

    def claims(self, store: Store) -> bool:
        """Whether this adapter serves `store`. Type alone by default.

        Overridable because a type is not always served by one adapter: "Note"
        is a knowledge scope (the platform's) and a Markdown vault (an app's),
        which is a fact `resolve` used to encode as `if store.owner_app ==
        "knowledge"` -- the platform branching on which app it was talking to.
        """
        return (store.type or "") == self.type

    def selectors(self) -> "list[str]":
        return []

    def resolve(self, store: Store, path: str, selector: dict[str, Any],
                verbs: "list[str]", verb: str) -> "list[dict[str, Any]]":
        raise NotImplementedError

    def write(self, store: Store, path: str, content: str, *,
              verb: str, selector: dict[str, Any]) -> "dict[str, Any]":
        """Create/update/append through this store. Default is refusal: most
        stores are read-only through this door, and a store that IS writable
        says so by implementing this rather than by the door knowing its name."""
        raise AccessDenied(
            "no_adapter",
            f"{store.uri} is type {store.type!r}; this door cannot write it.",
        )

    def to_response(self, items: "list[dict[str, Any]]", *, mode: str,
                    filter_caveat: dict[str, Any] | None) -> "list[dict[str, Any]]":
        fields = (filter_caveat or {}).get("fields")
        if mode == "verbatim":
            return [dict(i) for i in items]
        out = [_project(i, fields) for i in items]
        if mode == "redacted":
            out = [
                {k: (redact_text(v) if isinstance(v, str) else v) for k, v in i.items()}
                for i in out
            ]
        return out


class KnowledgeNoteAdapter(StoreAdapter):
    """Type "Note", knowledge-scope-backed: the platform's own note store, a flat
    per-scope directory.

    This was `NoteAdapter`, which served this AND the notes app's Markdown vault
    and picked between them with `if store.owner_app == "knowledge"`. The vault
    half now lives with the app that owns the folder
    (`services/notes_store_adapter.py`) and arrives through
    `AppSpec.store_adapters`; the platform keeps only its own substrate.

    THE SEARCH LEAK RULE: `search` WITHOUT `read` returns id/title/match counts
    and no snippet, since a hook or a body is exactly what `read` was withheld
    for. Mechanical; the broker is not consulted and cannot waive it.

    No `write`: a knowledge scope has its own write door
    (`PUT /api/memory/scopes/{scope}/notes/{id}`), which owns the MEMORY.md index
    write too, and a second writer would desynchronise that index.
    """

    type = "Note"

    def claims(self, store: Store) -> bool:
        return (store.type or "") == "Note" and store.owner_app == "knowledge"

    def selectors(self) -> "list[str]":
        return ["query", "name", "folder"]

    # -- knowledge scope ---------------------------------------------------
    def _scope_key(self, store: Store) -> str:
        """`knowledge/project-acme` -> the scope key `project:acme`. `workshop`
        has no kind prefix and stays as it is."""
        name = store.name
        for kind in ("project", "app", "agent"):
            if name.startswith(kind + "-"):
                return f"{kind}:{name[len(kind) + 1:]}"
        return name

    def _scope_items(self, store: Store, selector: dict[str, Any], verbs: "list[str]",
                     verb: str) -> "list[dict[str, Any]]":
        from services import knowledge
        scope = self._scope_key(store)

        if verb == "read":
            note_id = str(selector.get("name") or "")
            if not note_id:
                raise ValueError("read needs selector.name")
            body = knowledge.get_note(scope, note_id)
            if body is None:
                raise FileNotFoundError(note_id)
            return [{"id": note_id, "scope": scope, "content": body}]

        notes = knowledge.list_notes(scope)
        if verb == "list":
            return notes

        query = str(selector.get("query") or "").lower()
        if not query:
            raise ValueError("search needs selector.query")
        out: "list[dict[str, Any]]" = []
        for note in notes:
            body = knowledge.get_note(scope, note["id"]) or ""
            hay = f"{note['title']} {note['hook']} {body}".lower()
            count = hay.count(query)
            if not count:
                continue
            if "read" in verbs:
                out.append({**note, "content": body, "matches": count})
            else:
                # Same rule, same reason: hook and body are bodies.
                out.append({"id": note["id"], "title": note["title"], "matches": count})
        return out

    def resolve(self, store, path, selector, verbs, verb):
        return self._scope_items(store, selector, verbs, verb)


# The PLATFORM's adapters, for the platform's own substrates. An app's arrive
# through AppSpec.store_adapters -- see adapter_for().
_PLATFORM_ADAPTERS: "tuple[StoreAdapter, ...]" = (KnowledgeNoteAdapter(),)


def _app_adapters() -> "list[StoreAdapter]":
    try:
        from services import app_registry

        return app_registry.installed_store_adapters()
    except Exception:
        log.exception("stores: could not load app store adapters")
        return []


def adapter_for(store: Store) -> StoreAdapter:
    """The adapter serving `store`: the platform's, else an installed app's.

    First claim wins, platform first, because the platform's claims are the
    narrow ones (a knowledge scope is a Note store, but only one kind of Note
    store). Resolved per call, so an app's data becomes reachable the moment it
    is installed and stops being reachable when it is not -- which is the honest
    answer, not a hole: an uninstalled app's store is not declared either.
    """
    for a in (*_PLATFORM_ADAPTERS, *_app_adapters()):
        if a.claims(store):
            return a
    raise AccessDenied(
        "no_adapter",
        f"{store.uri} is type {store.type!r}; no adapter can shape a response for "
        "it, so it is reachable by export only.",
    )


# --------------------------------------------------------------------------- #
# The door.
# --------------------------------------------------------------------------- #
def _grant_for(uri: str, verb: str, token: Any) -> "dict[str, Any] | None":
    """The grant authorizing `verb` on `uri`, or None. Mechanical and free — this
    is the path every in-scope read is on, and the reason an adjudicator outage
    does not touch work already underway."""
    if not token:
        return None
    from services import permissions
    if not permissions.check(token, uri, verb):
        return None
    return permissions.get_resource_grant(token)


def read(
    uri: str,
    *,
    identity: Any = None,
    verb: str = "read",
    selector: dict[str, Any] | None = None,
    purpose: str = "",
    token: Any = None,
) -> dict[str, Any]:
    """Serve a read-shaped verb (`read` | `list` | `search`) against a Store.

    Raises AccessDenied (never a bare False) so a denial always carries the enum
    the caller has to branch on. KeyError for an unknown store; ValueError for a
    selector the adapter cannot use."""
    from services import permissions, policy as policy_mod

    if verb not in ("read", "list", "search"):
        raise ValueError(f"{verb!r} is not a read-shaped verb")
    store, path = resolve(uri)
    ident = permissions.resolve_identity(identity)
    selector = dict(selector or {})
    adapter = adapter_for(store)

    # The operator is never gated, never adjudicated, and never filtered. The gate
    # is on an agent JOB'S PERIMETER; it is not a permission system for the human.
    if ident.is_operator:
        items = adapter.resolve(store, path, selector, ["read", "list", "search"], verb)
        return {
            "uri": uri, "verb": verb, "mode": "verbatim", "tier": "operator",
            "items": items, "count": len(items), "withheld": {}, "reason": "",
        }

    pol = policy_mod.for_uri(uri)
    grant = _grant_for(uri, verb, token)
    adjudicated: dict[str, Any] | None = None

    if grant is None:
        if pol.tier != "mediated":
            raise AccessDenied(
                "no_grant",
                f"{ident.grant_subject} holds no grant for {verb} on {uri} "
                f"(tier {pol.tier}: nothing adjudicates at request time).",
            )
        from services.broker import broker
        verdict = broker.adjudicate(ident, uri, [verb], selector, purpose, pol)
        if not verdict.allowed:
            raise AccessDenied(verdict.reason_class, verdict.reason, retryable=verdict.retryable)
        grant = verdict.grant
        adjudicated = verdict.adjudicator

    caveats = grant.get("caveats") or {}
    mode = str(caveats.get("response_mode") or "verbatim")
    filter_caveat = caveats.get("filter") if isinstance(caveats.get("filter"), dict) else None
    verbs = [*(grant.get("verbs") or [])]

    items = adapter.resolve(store, path, selector, verbs, verb)
    shaped = adapter.to_response(items, mode=mode, filter_caveat=filter_caveat)
    withheld: dict[str, Any] = {}
    if mode != "verbatim":
        # Always tell a requester THAT withholding happened. A filtered answer
        # indistinguishable from a complete one makes an agent confidently act on a
        # partial picture and write a wrong ticket — a correctness bug, and the more
        # likely one to bite. The disclosure never exceeds the policy's own
        # declaration: the field names are the operator's.
        withheld = {"truncated": True}
        if filter_caveat and filter_caveat.get("fields"):
            withheld["fields_kept"] = [*filter_caveat["fields"]]
    return {
        "uri": uri, "verb": verb, "mode": mode, "tier": pol.tier,
        "items": shaped, "count": len(shaped), "withheld": withheld,
        "grant_token": grant.get("id"), "adjudicator": adjudicated,
        "reason": "",
    }


def write(
    uri: str,
    *,
    content: str,
    identity: Any = None,
    verb: str = "write",
    token: Any = None,
    selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write/append a note through a Store. Grant-checked, never adjudicated: the
    broker exists to scope what may be RELEASED, and there is no filtered view of
    a write. A write is either within a grant the operator's policy already
    permits, or it does not happen."""
    from services import permissions, policy as policy_mod

    if verb not in ("write", "append"):
        raise ValueError(f"{verb!r} is not a write-shaped verb")
    store, path = resolve(uri)
    ident = permissions.resolve_identity(identity)
    selector = dict(selector or {})
    name = str(selector.get("name") or path or "")
    if not name:
        raise ValueError("write needs selector.name (or a path-suffixed uri)")

    if not ident.is_operator:
        pol = policy_mod.for_uri(uri)
        if verb not in pol.verbs_max:
            raise AccessDenied(
                "verb_not_permitted",
                f"{verb} exceeds verbs_max {[*pol.verbs_max]} for {pol.store_uri}.",
            )
        if _grant_for(uri, verb, token) is None:
            raise AccessDenied(
                "no_grant", f"{ident.grant_subject} holds no grant for {verb} on {uri}.",
            )

    # Whether this store is writable, and how, is the adapter's to answer. This
    # used to be `if store.type != "Note" or store.owner_app == "knowledge"`
    # followed by a direct call into the notes app's module: the platform's
    # generic write door hardcoded which app it could write and reached into it.
    # The base adapter refuses by default, so a store is writable only if
    # something implemented it.
    return adapter_for(store).write(
        store, path, content, verb=verb, selector=selector
    )
