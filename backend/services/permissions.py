"""Grants + permissions + allowed-actions framework (the authz source of truth).

Implements the model in `docs/app-platform-plan.md` section 3 and
`docs/workshop-v1/01-agents-and-chat.md` section 2.

DESIGN POSTURE — default-permissive for the operator (operator hard rule, "no
defensive cruft for single-user"). This module is a CONSULTED source of truth,
NOT a wall in front of existing routes. There is no middleware here and nothing
in this file gates the operator's own/private app access:

  * `authorize(operator, ...)` is ALWAYS "allow" (never "deny"). The single
    exception is purely a UX nicety: if the operator has explicitly opted an
    action into a "confirm" policy in apps.json, authorize returns "confirm"
    for that action — a propose-then-apply prompt, not a block.
  * Gating ("deny"/"confirm" for safety) exists ONLY for the external/shared
    boundary: guests, and apps whose sensitivity is `shareable`/`public`.

The guest identity seam exists but guest *auth* (resolving a token to a guest)
is intentionally not built here — `resolve_identity` defaults to the operator
and leaves a clean place to plug guest-token resolution in later.

Stores:
  * App manifests (sensitivity, provides/needs, the optional per-action policy)
    live in `apps.json` via `services.manifests` — this module reads them.
  * Grants + guest capabilities live in `backend/grants.json`, owned by the
    operator. Auto-created empty, tolerant of corruption (serves empty).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from services import manifests
from wsplatform import paths

Decision = Literal["allow", "confirm", "deny"]
ActionPolicy = Literal["read", "confirm", "allow"]

# grants.json is per-instance state, now under the untracked runtime root rather
# than beside the backend package (Layer 0 eviction). Resolved per-call through
# `wsplatform.paths` so the startup relocation and the WORKSHOP_GRANTS_PATH /
# WORKSHOP_RESOURCE_GRANTS_PATH overrides are honored at read time, not frozen at
# import (a module constant computed before the startup move would point at the
# old, now-absent, location). Resource-scoped grants (Phase 4) live in a SEPARATE
# file so the app-level loader never sees the new shape; both derive from paths
# (imported above).

# The verbs a resource grant may carry.
#
#   list    enumerate ids, names, titles, tags, mtimes in the subtree. No bodies.
#   search  run a query against bodies and return which items match.
#   read    read an item's body.
#   append / write  unchanged.
#
# `list` without `read` and `read` without `list` are BOTH coherent states (an
# index agent needs names and not contents; a worker handed one note id needs that
# note and no enumeration power), which is why both verbs are real. There is
# deliberately no `exists` verb: `exists(x)` is `list` filtered to a singleton, so
# it adds no expressive power but does add a cheap oracle — an agent denied `list`
# could probe it in a loop and reconstruct the index one guess at a time.
#
# THE SEARCH LEAK RULE: `search` without `read` returns ids/titles/match counts and
# no snippets, because a snippet IS a body and would leak exactly what `read` was
# withheld for. `search` with `read` may return snippets. Mechanical, enforced by
# the provider (services/stores.py), never a broker judgment.
_VERBS = ("read", "list", "search", "append", "write")

# Public alias — services/policy.py validates an operator's verbs_max against it.
VERBS = _VERBS

# The OPERATOR's notes vault. Durable user data OUTSIDE the repo working tree, so
# it is not reachable by "it's in the tree, therefore it's fair game" reasoning —
# it has to be addressed as a Store to be reachable at all (services/stores.py
# registers it; services/notes_vault.py owns the filesystem access). Same env
# override `notes_vault.operator_vault_root()` honors.
#
# The operator's, specifically: the Store registry is instance-level (declared
# once at startup, before any request pins an owner), whereas the vault is now
# per-owner — `notes_vault.vault_root()` returns `.runtime/users/<id>/notes/` for
# anyone else. So this constant names one real directory for the addressing
# One app's vault root USED TO BE DECLARED HERE, and it is now declared by that
# app (`notes_vault.NOTES_VAULT_ROOT`). This is platform code and nothing in
# this file ever read it; an app's storage path is the app's to state.

OPERATOR = "operator"
AGENT = "agent"

# Sensitivities at which a guest may even be considered for read access. `private`
# apps are operator-only and never readable by a guest, regardless of grants.
_GUEST_READABLE_SENSITIVITIES = {"shareable", "public"}

_EMPTY_DOC: dict[str, Any] = {"grants": [], "guests": []}


# ---------------------------------------------------------------- identity model


class Identity:
    """A resolved caller identity.

    kind == "operator": the fully-trusted operator (the default). Never gated.
    kind == "guest":    an external/shared-boundary caller, identified by `id`.
    kind == "agent":    an UNATTENDED background process (`id` is the agent slug),
                        e.g. a dispatched sweep. It holds zero ambient authority:
                        it reaches exactly what the dispatcher minted it a grant
                        for, and nothing else.

    Guests and agents are the callers authz actually gates. `operator` is not the
    human's name for themselves — it is an authority assignment on a running
    process, and the human's own process is always assigned it. Bounding a
    program that ingests hostile input (an inbound Discord message, a scraped
    page, a note body) is not a gate on the operator.
    """

    __slots__ = ("kind", "id")

    def __init__(self, kind: str, id: str | None = None):
        self.kind = kind
        self.id = id

    @property
    def is_operator(self) -> bool:
        return self.kind == OPERATOR

    @property
    def is_agent(self) -> bool:
        return self.kind == AGENT

    @property
    def grant_subject(self) -> str:
        """How this identity is named on a grant's `to` side ("agent:<slug>")."""
        return f"{self.kind}:{self.id}" if self.id else self.kind

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Identity(kind={self.kind!r}, id={self.id!r})"


OPERATOR_IDENTITY = Identity(OPERATOR)


def resolve_identity(value: Any = None) -> Identity:
    """Resolve a caller into an Identity. DEFAULT-PERMISSIVE: with no signal we
    treat the caller as the operator (this matches the loopback-trusted backend
    posture — the operator's own contexts must never be gated).

    Accepted inputs (all optional; the seam for guest auth is here, not built):
      * None / "" / "operator"      -> the operator
      * an existing Identity        -> returned as-is
      * "guest:<id>" / a dict with  -> a guest identity (guests come later from a
        {"kind": "guest", "id": ...}   token; we only need the shape now)
      * "agent:<slug>" / a dict with-> an unattended background process
        {"kind": "agent", "id": ...}   (the dispatcher names its sweeps this way)

    This never raises — an unparseable value falls back to the operator, because
    failing closed on the operator's own access is exactly the cruft forbidden.
    An agent identity is never inferred: a caller has to SAY it is one, which the
    dispatcher does explicitly. Nothing accidentally demotes itself.
    """
    if isinstance(value, Identity):
        return value
    if value is None:
        return OPERATOR_IDENTITY
    if isinstance(value, dict):
        kind = value.get("kind")
        if kind == "guest":
            gid = value.get("id")
            return Identity("guest", str(gid) if gid is not None else None)
        if kind == AGENT:
            aid = value.get("id")
            return Identity(AGENT, str(aid) if aid is not None else None)
        return OPERATOR_IDENTITY
    if isinstance(value, str):
        v = value.strip()
        if not v or v == OPERATOR:
            return OPERATOR_IDENTITY
        if v.startswith("guest:"):
            gid = v.split(":", 1)[1].strip()
            return Identity("guest", gid or None)
        if v.startswith("agent:"):
            aid = v.split(":", 1)[1].strip()
            return Identity(AGENT, aid or None)
        # Bare unknown string: be permissive, treat as operator.
        return OPERATOR_IDENTITY
    return OPERATOR_IDENTITY


# ---------------------------------------------------------------- grants store


def _coerce_doc(data: Any) -> dict[str, Any]:
    """Coerce a loaded grants document into the canonical shape, dropping any
    malformed rows. Tolerant by design — a hand-edited grants.json never breaks
    reads; bad entries are simply skipped."""
    if not isinstance(data, dict):
        return {"grants": [], "guests": []}

    grants_out: list[dict[str, Any]] = []
    for row in data.get("grants") or []:
        if not isinstance(row, dict):
            continue
        frm = row.get("from")
        to = row.get("to")
        cap = row.get("capability")
        if not (isinstance(frm, str) and isinstance(to, str) and isinstance(cap, str)):
            continue
        mode = row.get("mode")
        if mode not in ("read", "rw"):
            mode = "read"
        grants_out.append({"from": frm, "to": to, "capability": cap, "mode": mode})

    guests_out: list[dict[str, Any]] = []
    for g in data.get("guests") or []:
        if not isinstance(g, dict):
            continue
        gid = g.get("id")
        app = g.get("app")
        if not (isinstance(gid, str) and isinstance(app, str)):
            continue
        caps = [c for c in (g.get("capabilities") or []) if isinstance(c, str)]
        guests_out.append({"id": gid, "app": app, "capabilities": caps})

    return {"grants": grants_out, "guests": guests_out}


def _write_doc(doc: dict[str, Any]) -> None:
    """Atomically write the grants doc (temp file + rename)."""
    path = paths.grants_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def load_grants() -> dict[str, Any]:
    """Return the grants document {"grants": [...], "guests": [...]}.

    Auto-creates an empty doc on disk if missing. If the file exists but is
    unreadable/corrupt, returns an in-memory empty doc (never raises) so reads
    stay available."""
    path = paths.grants_path()
    if not path.exists():
        try:
            _write_doc({"grants": [], "guests": []})
        except OSError:
            pass
        return {"grants": [], "guests": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"grants": [], "guests": []}
    return _coerce_doc(data)


def validate_grants(doc: Any) -> dict[str, Any]:
    """Validate + normalize a grants doc for saving. Raises ValueError if the
    top-level shape is wrong (so the PUT endpoint can 400); otherwise returns
    the coerced canonical doc (malformed individual rows are dropped, not
    fatal)."""
    if not isinstance(doc, dict):
        raise ValueError("grants document must be an object")
    if "grants" in doc and not isinstance(doc["grants"], list):
        raise ValueError("'grants' must be a list")
    if "guests" in doc and not isinstance(doc["guests"], list):
        raise ValueError("'guests' must be a list")
    return _coerce_doc(doc)


def save_grants(doc: Any) -> dict[str, Any]:
    """Validate + atomically persist a full grants doc. Returns the saved
    canonical doc."""
    canonical = validate_grants(doc)
    _write_doc(canonical)
    return canonical


# ---------------------------------------------------------------- capabilities


def app_capabilities(app_id: str) -> dict[str, list[str]]:
    """Return {provides, needs} for an app from its manifest (empty if unknown)."""
    m = manifests.get_manifest(app_id)
    if m is None:
        return {"provides": [], "needs": []}
    return {"provides": list(m.get("provides") or []), "needs": list(m.get("needs") or [])}


def app_sensitivity(app_id: str) -> str:
    """Return an app's sensitivity (defaults to 'private' for unknown apps —
    the safest default for an unrecognized id on the guest path)."""
    m = manifests.get_manifest(app_id)
    if m is None:
        return "private"
    return m.get("sensitivity") or "private"


def app_actions(app_id: str) -> dict[str, str]:
    """Return the per-action policy map for an app, {<action>: policy}.
    Absent => {} (every action takes its default). Policies are coerced to the
    valid set; an unrecognized policy value is treated as the default ('read')."""
    m = manifests.get_manifest(app_id)
    if m is None:
        return {}
    raw = m.get("actions") or {}
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for name, pol in raw.items():
            out[str(name)] = pol if pol in ("read", "confirm", "allow") else "read"
    return out


def _grant_permits(
    doc: dict[str, Any], *, frm: str, to: str, capability: str, need_write: bool
) -> bool:
    """True if a grant row authorizes `to` consuming `capability` from `frm`.
    A write needs mode 'rw'; a read accepts 'read' or 'rw'."""
    for row in doc.get("grants", []):
        if row.get("from") == frm and row.get("to") == to and row.get("capability") == capability:
            if need_write:
                if row.get("mode") == "rw":
                    return True
            else:
                return True
    return False


def capability_permitted(
    identity: Any,
    *,
    from_app: str,
    to_app: str,
    capability: str,
    need_write: bool = False,
    doc: dict[str, Any] | None = None,
) -> bool:
    """Is a (from_app -> to_app, capability) cross-app use permitted?

    Operator: ALWAYS permitted (default-permissive). Guest: requires a matching
    grant row (and 'rw' for a write). Same-app use (from_app == to_app) for the
    operator is trivially permitted; for a guest it still flows through the
    guest's capability grant on that app via `authorize` — this helper is the
    cross-app grant check specifically.

    Agent: no ambient authority. A grant row addressed TO the agent
    ("agent:<slug>") is the only thing that permits it."""
    ident = resolve_identity(identity)
    if ident.is_operator:
        return True
    if doc is None:
        doc = load_grants()
    if ident.is_agent:
        return _grant_permits(
            doc, frm=from_app, to=ident.grant_subject, capability=capability,
            need_write=need_write,
        )
    return _grant_permits(
        doc, frm=from_app, to=to_app, capability=capability, need_write=need_write
    )


# ---------------------------------------------------------------- authorize


def _guest_has_capability(doc: dict[str, Any], guest_id: str | None, app_id: str, capability: str) -> bool:
    """True if the guest record for (guest_id, app_id) lists `capability`."""
    if guest_id is None:
        return False
    for g in doc.get("guests", []):
        if g.get("id") == guest_id and g.get("app") == app_id:
            if capability in (g.get("capabilities") or []):
                return True
    return False


def _is_read_action(app_id: str, action: str) -> bool:
    """Whether an action is a read. An action is a read if its policy is 'read'
    (the default) OR the action name reads like a read ('read'/'get'/'list'/
    'view'/'search'). Anything else is treated as a write."""
    policy = app_actions(app_id).get(action)
    if policy == "read":
        return True
    if policy in ("confirm", "allow"):
        return False
    name = action.lower()
    return name in ("read", "get", "list", "view", "search") or name.endswith(".read")


def authorize(
    identity: Any,
    app_id: str,
    action: str,
    *,
    capability: str | None = None,
    doc: dict[str, Any] | None = None,
) -> Decision:
    """Decide whether `identity` may perform `action` on `app_id`.

    Returns "allow" | "confirm" | "deny".

    OPERATOR (default-permissive):
      * ALWAYS "allow", with ONE exception: if the operator has explicitly set a
        "confirm" policy for this action in apps.json, return "confirm" (a UX
        propose-then-apply nicety, never a block). The operator NEVER gets "deny".

    GUEST (the external/shared boundary — the only gated path):
      * Reads are allowed only on `shareable`/`public` apps AND only when the
        guest holds the relevant capability (the app's first `provides`, or an
        explicitly passed `capability`). Reads on `private` apps -> "deny".
      * Writes default to "deny" unless BOTH an action policy permits it
        (confirm/allow) AND the guest holds the capability. A confirm policy
        yields "confirm"; an allow policy yields "allow".
      * Cross-app capability use (capability passed AND its providing app differs
        from `app_id`) additionally requires a grant row (from provider -> app_id).

    AGENT (an unattended background process — zero ambient authority):
      * Reads and writes both require a grant row addressed to "agent:<slug>"
        (writes additionally require mode 'rw'). Unmatched -> "deny". Sensitivity
        does not gate an agent (it is not an external caller); the grant does.
      * An agent NEVER gets "confirm": there is no human in its loop to confirm.
        A confirm-policy action resolves to allow-if-granted, deny otherwise.
    """
    ident = resolve_identity(identity)
    actions = app_actions(app_id)
    policy = actions.get(action)

    if ident.is_operator:
        # Honor an explicit per-app confirm opt-in; otherwise allow.
        return "confirm" if policy == "confirm" else "allow"

    if doc is None:
        doc = load_grants()

    if ident.is_agent:
        caps = app_capabilities(app_id)
        gate_cap = capability or (caps["provides"][0] if caps["provides"] else None)
        if gate_cap is None:
            return "deny"
        need_write = not _is_read_action(app_id, action)
        return "allow" if _grant_permits(
            doc, frm=app_id, to=ident.grant_subject, capability=gate_cap,
            need_write=need_write,
        ) else "deny"

    # ---- guest path (the boundary) ----

    sensitivity = app_sensitivity(app_id)
    is_read = _is_read_action(app_id, action)

    # Determine which capability gates this app for the guest. Prefer an explicit
    # capability; else the app's first declared `provides`.
    caps = app_capabilities(app_id)
    gate_cap = capability or (caps["provides"][0] if caps["provides"] else None)

    # Cross-app capability use needs a grant from the providing app to this app.
    if capability is not None:
        provider_app = _provider_of_capability(capability)
        if provider_app is not None and provider_app != app_id:
            if not _grant_permits(
                doc, frm=provider_app, to=app_id, capability=capability, need_write=not is_read
            ):
                return "deny"

    if is_read:
        if sensitivity not in _GUEST_READABLE_SENSITIVITIES:
            return "deny"
        if gate_cap is None:
            # Shareable app with no capability to gate on: deny by default — a
            # guest needs an explicit grant of *something* to read.
            return "deny"
        return "allow" if _guest_has_capability(doc, ident.id, app_id, gate_cap) else "deny"

    # Write path for a guest: needs an action policy that permits it AND the cap.
    if policy not in ("confirm", "allow"):
        return "deny"
    if gate_cap is None or not _guest_has_capability(doc, ident.id, app_id, gate_cap):
        return "deny"
    return "confirm" if policy == "confirm" else "allow"


def _provider_of_capability(capability: str) -> str | None:
    """Find which app `provides` a capability (first match by app id order)."""
    for app_id, m in sorted(manifests.load_manifests().items()):
        if capability in (m.get("provides") or []):
            return app_id
    return None


# ---------------------------------------------------------------- reachability


def reachable_apps(identity: Any, app_id: str, doc: dict[str, Any] | None = None) -> list[str]:
    """Apps an assistant bound to `app_id` may reach: always itself, plus any app
    whose provided capability `app_id` is granted to consume.

    Operator: itself + every app it `needs` whose provider exists (operator can
    reach anything it declares a need for; default-permissive). Guest: itself +
    apps reachable via an actual grant row to `app_id`. Agent: itself + the
    providers named on grant rows addressed to the agent itself (its minted
    reach, never the app manifest's declared needs)."""
    ident = resolve_identity(identity)
    result = {app_id}

    if doc is None:
        doc = load_grants()

    if ident.is_operator:
        # Operator: reach providers of every capability this app needs.
        needs = app_capabilities(app_id)["needs"]
        for cap in needs:
            prov = _provider_of_capability(cap)
            if prov:
                result.add(prov)
        # Also honor any explicit grant rows targeting this app (they only widen).
        for row in doc.get("grants", []):
            if row.get("to") == app_id:
                result.add(row.get("from"))
    else:
        subject = ident.grant_subject if ident.is_agent else app_id
        for row in doc.get("grants", []):
            if row.get("to") == subject:
                result.add(row.get("from"))

    return sorted(result)


# ---------------------------------------------------------------- assistant view


def assistant_capabilities(app_id: str, identity: Any = OPERATOR) -> dict[str, Any]:
    """What a data assistant bound to `app_id` may actually do — the thing the
    chat layer consults so an assistant's preamble can state its real allowed
    actions (doc 01 section 3).

    Returns:
      {
        "app": <app_id>,
        "identity": "operator" | "guest",
        "can_read": bool,
        "actions": {<action>: "allow"|"confirm"|"deny"},  # declared action policies
        "reachable_apps": [<app_id>, ...],
      }

    For the operator: can_read is True and every declared action resolves to
    "allow" (or "confirm" where the operator opted into a confirm policy) — never
    "deny". For a guest: resolved per `authorize`."""
    ident = resolve_identity(identity)
    doc = load_grants()

    # Read decision: model reads as a synthetic "read" action through authorize.
    can_read = authorize(ident, app_id, "read", doc=doc) in ("allow", "confirm")

    # Resolve each DECLARED action's policy into a concrete decision. If no
    # actions are declared, the operator still gets an empty map (nothing to
    # confirm); the can_read flag covers the read surface.
    resolved: dict[str, str] = {}
    for action in app_actions(app_id):
        resolved[action] = authorize(ident, app_id, action, doc=doc)

    return {
        "app": app_id,
        "identity": ident.kind,
        "can_read": can_read,
        "actions": resolved,
        "reachable_apps": reachable_apps(ident, app_id, doc=doc),
    }


# ---------------------------------------------------------------- knowledge scopes

def _scope_sensitivity(scope_key: str) -> str:
    """Sensitivity of a knowledge scope: private | shareable | public.

    project:<slug> reads the project's registry sensitivity; workshop and
    app:<name> default shareable; agent:<id> defaults private. Any parse/read
    failure falls back to the safe-but-usable `shareable`."""
    if scope_key == "workshop":
        return "shareable"
    kind, _, slug = scope_key.partition(":")
    if kind == "project" and slug:
        try:
            # The registry lives at .runtime/projects.json. This used to derive it
            # as <projects root>/../projects.json, i.e. a repo-root file that has
            # never existed, so the read ALWAYS raised and every project silently
            # fell through to `shareable` below. A project marked private was
            # therefore treated as shareable for knowledge recall.
            reg = paths.env_path("WORKSHOP_PROJECTS_REGISTRY", "projects.json")
            data = json.loads(reg.read_text())
            rec = (data.get("projects") or {}).get(slug) or {}
            return rec.get("sensitivity") or "shareable"
        except Exception:
            return "shareable"
    if kind == "app":
        return "shareable"
    if kind == "agent":
        return "private"
    return "private"


def can_recall(from_scope: str, target_scope: str) -> bool:
    """Whether a chat in `from_scope` may auto-pull knowledge from `target_scope`.

    This is context hygiene, NOT a security wall (the operator can always read any
    scope directly through the knowledge API). It limits what one scope's chat
    silently reaches into: same scope always; a public target always; a shareable
    target only with a grants.json row {from, to, capability: "knowledge.read"};
    a private target never (auto)."""
    if from_scope == target_scope:
        return True
    sens = _scope_sensitivity(target_scope)
    if sens == "public":
        return True
    if sens == "private":
        return False
    doc = load_grants()
    for row in doc.get("grants") or []:
        if (row.get("to") == target_scope and row.get("from") == from_scope
                and row.get("capability") == "knowledge.read"):
            return True
    return False


# ============================================================================ #
# Resource-scoped grants (Phase 4): per-invocation capability tokens over Stores
# ============================================================================ #
# A resource grant authorizes `to` to perform specific verbs against a Store URI
# (or a subtree of URIs), optionally caveated (expiry, filter). Minting returns a
# grant whose `id` IS the token; a provider calls `check(token, uri, verb)` per
# invocation. This is the interop mechanism, not a per-action confirmation gate —
# the operator is never re-prompted; the broker mints once and the token is then
# checked mechanically. Stored separately from grants.json so the app-level
# loader (load_grants/validate_grants) never handles the new shape.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_resource_grant(row: Any) -> dict[str, Any] | None:
    """Coerce one raw resource-grant row into canonical shape, or None if it is
    unusable (missing id / store_uri)."""
    if not isinstance(row, dict):
        return None
    gid = row.get("id")
    store_uri = row.get("store_uri")
    if not (isinstance(gid, str) and gid and isinstance(store_uri, str) and store_uri):
        return None
    verbs = [v for v in (row.get("verbs") or []) if v in _VERBS]
    caveats = row.get("caveats") if isinstance(row.get("caveats"), dict) else {}
    return {
        "id": gid,
        "from": str(row.get("from") or ""),
        "to": str(row.get("to") or ""),
        "store_uri": store_uri,      # exact URI, or a URI prefix when subtree=True
        "subtree": bool(row.get("subtree")),
        "verbs": verbs,
        "caveats": caveats,
        "created_at": str(row.get("created_at") or ""),
    }


def load_resource_grants() -> list[dict[str, Any]]:
    """The resource-grant list. Auto-creates an empty file if missing; tolerant
    of a corrupt file (returns [])."""
    path = paths.resource_grants_path()
    if not path.exists():
        try:
            _write_resource_grants([])
        except OSError:
            pass
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("grants") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        c = _coerce_resource_grant(row)
        if c is not None:
            out.append(c)
    return out


def _write_resource_grants(rows: list[dict[str, Any]]) -> None:
    path = paths.resource_grants_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"grants": rows}, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _resolve_grant(token_or_grant: Any) -> dict[str, Any] | None:
    """Resolve a token (grant id string) or a grant dict to a canonical grant
    record, or None. A dict is validated but NOT required to be persisted (a
    caller may pass a freshly-minted grant object)."""
    if isinstance(token_or_grant, dict):
        return _coerce_resource_grant(token_or_grant)
    if isinstance(token_or_grant, str):
        for g in load_resource_grants():
            if g["id"] == token_or_grant:
                return g
    return None


def get_resource_grant(token_or_grant: Any) -> dict[str, Any] | None:
    """The canonical grant record behind a token, or None. A provider that has
    already passed `check()` uses this to read the caveats it must apply (the
    `filter` caveat and the response mode the broker recorded)."""
    return _resolve_grant(token_or_grant)


def mint_grant(
    *,
    from_app: str,
    to: str,
    store_uri: str,
    verbs: list[str],
    caveats: dict[str, Any] | None = None,
    subtree: bool = False,
) -> dict[str, Any]:
    """Mint a scoped grant over a Store (or subtree of Store URIs) and persist it.
    Returns the grant record; its `id` is the capability token. Verbs are filtered
    to the valid set; at least one valid verb is required."""
    valid = [v for v in verbs if v in _VERBS]
    if not valid:
        raise ValueError(f"no valid verbs (allowed: {', '.join(_VERBS)})")
    grant = {
        "id": uuid.uuid4().hex,
        "from": str(from_app or ""),
        "to": str(to or ""),
        "store_uri": str(store_uri),
        "subtree": bool(subtree),
        "verbs": valid,
        "caveats": dict(caveats or {}),
        "created_at": _now_iso(),
    }
    rows = load_resource_grants()
    rows.append(grant)
    _write_resource_grants(rows)
    return grant


def _within(child_uri: str, parent_uri: str, parent_subtree: bool) -> bool:
    """Is `child_uri` inside the parent grant's reach? Exact match always; a
    subtree parent additionally covers anything under its prefix. Same rule
    `check()` enforces per invocation, so attenuation and enforcement agree."""
    if child_uri == parent_uri:
        return True
    if not parent_subtree:
        return False
    return child_uri.startswith(parent_uri.rstrip("/") + "/")


def attenuate(
    token_or_grant: Any,
    *,
    store_uri: str | None = None,
    verbs: list[str] | None = None,
    subtree: bool | None = None,
    caveats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive a NARROWER grant from an existing one and persist it.

    This is the one direction authority is allowed to move: every axis may only
    shrink.
      * `store_uri` must lie INSIDE the parent's reach (the parent's exact uri, or
        under it when the parent is a subtree grant). Widening raises.
      * `verbs` must be a subset of the parent's.
      * `subtree` may narrow True -> False, never widen False -> True.
      * `caveats` merge over the parent's, so a child can add an expiry but never
        drop one the parent carried.
    Returns the new grant (a new id/token). Raises if the parent is unknown or the
    request would widen it on any axis."""
    parent = _resolve_grant(token_or_grant)
    if parent is None:
        raise ValueError("cannot attenuate: unknown grant")
    if verbs is None:
        new_verbs = _filter_verbs(parent["verbs"])
    else:
        req = [v for v in verbs if v in _VERBS]
        if not set(req).issubset(set(parent["verbs"])):
            raise ValueError("attenuated verbs must be a subset of the parent grant")
        new_verbs = req
    if not new_verbs:
        raise ValueError("attenuated grant must retain at least one verb")

    new_uri = parent["store_uri"] if store_uri is None else str(store_uri)
    if not _within(new_uri, parent["store_uri"], bool(parent.get("subtree"))):
        raise ValueError(
            f"attenuated store_uri must be within the parent grant: "
            f"{new_uri!r} is not inside {parent['store_uri']!r}"
        )

    new_subtree = bool(parent.get("subtree")) if subtree is None else bool(subtree)
    if new_subtree and not parent.get("subtree"):
        raise ValueError("cannot attenuate an exact grant into a subtree grant")

    merged_caveats = {**parent.get("caveats", {}), **(caveats or {})}
    return mint_grant(
        from_app=parent["from"],
        to=parent["to"],
        store_uri=new_uri,
        verbs=new_verbs,
        caveats=merged_caveats,
        subtree=new_subtree,
    )


def revoke(token_or_grant: Any) -> bool:
    """Revoke a grant by token (id) or record. Returns True if a grant was
    removed. A revoked token fails every subsequent `check`."""
    if isinstance(token_or_grant, dict):
        gid = token_or_grant.get("id")
    else:
        gid = token_or_grant
    if not isinstance(gid, str) or not gid:
        return False
    rows = load_resource_grants()
    remaining = [g for g in rows if g["id"] != gid]
    if len(remaining) == len(rows):
        return False
    _write_resource_grants(remaining)
    return True


def _caveat_expired(caveats: dict[str, Any]) -> bool:
    exp = caveats.get("expiry")
    if not exp:
        return False
    try:
        when = datetime.fromisoformat(str(exp))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return False  # unparseable expiry does not expire the grant
    return datetime.now(timezone.utc) > when


def check(token_or_grant: Any, store_uri: str, verb: str) -> bool:
    """Per-invocation enforcement: does this token authorize `verb` on `store_uri`?

    A provider calls this on every store operation. Denies when the token is
    unknown/revoked, the verb is not granted, the URI does not match (exact, or a
    subtree prefix for a subtree grant), or an expiry caveat has passed. The
    `filter` caveat is carried on the grant for the provider to apply; it is not a
    gate here."""
    grant = _resolve_grant(token_or_grant)
    if grant is None:
        return False
    if verb not in grant.get("verbs", []):
        return False
    if _caveat_expired(grant.get("caveats", {})):
        return False
    guri = grant["store_uri"]
    if grant.get("subtree"):
        return store_uri == guri or store_uri.startswith(guri.rstrip("/") + "/")
    return store_uri == guri


def _filter_verbs(verbs: Any) -> list[str]:
    """Copy of a verb list, filtered to the valid set (helper for attenuate)."""
    return [v for v in (verbs or []) if v in _VERBS]
