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
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# workshop://store/<app>/<name>  — app and name are slug-ish (no path separators).
_URI_RE = re.compile(r"^workshop://store/(?P<app>[a-z0-9][a-z0-9._-]*)/(?P<name>[a-z0-9][a-z0-9._-]*)$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class Store:
    """A registered handle onto an app's data.

    uri        — workshop://store/<owner_app>/<name>, the stable identifier.
    owner_app  — the app that owns the store (its data lives inside that app).
    name       — the store name within the app.
    type       — an optional broker type ("Note", "Budget", "Finance", ...).
    path       — the resolved filesystem path (file or directory) the store
                 wraps. May not exist yet; export/consumers tolerate absence.
    """
    uri: str
    owner_app: str
    name: str
    type: str | None
    path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# In-memory registry keyed by uri. Rebuilt on each register_* pass; declare is
# idempotent (re-declaring the same uri replaces the record).
_REGISTRY: dict[str, Store] = {}


def make_uri(owner_app: str, name: str) -> str:
    if not _SLUG_RE.match(owner_app or "") or not _SLUG_RE.match(name or ""):
        raise ValueError(f"invalid store id parts: {owner_app!r}/{name!r}")
    return f"workshop://store/{owner_app}/{name}"


def parse_uri(uri: str) -> tuple[str, str]:
    """Return (owner_app, name) for a valid store uri; raise ValueError otherwise."""
    m = _URI_RE.match(uri or "")
    if not m:
        raise ValueError(f"not a store uri: {uri!r}")
    return m.group("app"), m.group("name")


def declare(owner_app: str, name: str, path: str | Path, type: str | None = None) -> Store:
    """Register a Store over an existing path. REGISTRATION, not relocation — the
    file/dir is left exactly as it is (mode, location, content). Idempotent."""
    uri = make_uri(owner_app, name)
    store = Store(uri=uri, owner_app=owner_app, name=name, type=type, path=str(path))
    _REGISTRY[uri] = store
    return store


def get(uri: str) -> Store | None:
    """The registered Store for a uri, or None."""
    return _REGISTRY.get(uri)


def list(type: str | None = None) -> "list[Store]":
    """All declared stores, optionally filtered by `type`, sorted by uri.

    (Annotations are lazy strings under `from __future__ import annotations`, so
    the `list` name shadow here is cosmetic only.)"""
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
    runtime = _runtime_root()

    # App-shared (not per-owner) stores.
    declare("commute", "data", runtime / "commute", type="Commute")
    declare("secrets", "env", runtime / "secrets", type="Secret")

    # The notes vault (~/notes). Declared in place: the vault stays a plain folder
    # of Markdown that real Obsidian opens untouched. Registering it makes it
    # ADDRESSABLE — until now it was the one live data set with no URI, so no
    # grant could name it and no per-folder policy could be expressed about it.
    # The path comes from permissions.NOTES_VAULT_ROOT, which reads the same
    # WORKSHOP_NOTES_VAULT override notes_vault.vault_root() reads.
    from services import permissions
    declare("notes", "vault", permissions.NOTES_VAULT_ROOT, type="Note")

    # Per-owner stores (single-user: the operator's runtime dir).
    user_dir = _operator_runtime_dir()
    if user_dir is not None:
        declare("budget", "plan", user_dir / "budget.json", type="Budget")
        # plaid-items.json is 0600 — declared in place, never moved or chmodded.
        declare("finance", "plaid-items", user_dir / "plaid-items.json", type="Finance")


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
