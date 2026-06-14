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
from pathlib import Path
from typing import Any, Literal

from services import manifests

Decision = Literal["allow", "confirm", "deny"]
ActionPolicy = Literal["read", "confirm", "allow"]

# grants.json lives next to the backend package (operator-owned, backend-managed).
GRANTS_PATH = Path(os.environ.get(
    "WORKSHOP_GRANTS_PATH",
    str(Path(__file__).resolve().parent.parent / "grants.json"),
))

OPERATOR = "operator"

# Sensitivities at which a guest may even be considered for read access. `private`
# apps are operator-only and never readable by a guest, regardless of grants.
_GUEST_READABLE_SENSITIVITIES = {"shareable", "public"}

_EMPTY_DOC: dict[str, Any] = {"grants": [], "guests": []}


# ---------------------------------------------------------------- identity model


class Identity:
    """A resolved caller identity.

    kind == "operator": the fully-trusted operator (the default). Never gated.
    kind == "guest":    an external/shared-boundary caller, identified by `id`.
                        Guests are the ONLY callers that authz actually gates.
    """

    __slots__ = ("kind", "id")

    def __init__(self, kind: str, id: str | None = None):
        self.kind = kind
        self.id = id

    @property
    def is_operator(self) -> bool:
        return self.kind == OPERATOR

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

    This never raises — an unparseable value falls back to the operator, because
    failing closed on the operator's own access is exactly the cruft forbidden.
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
        return OPERATOR_IDENTITY
    if isinstance(value, str):
        v = value.strip()
        if not v or v == OPERATOR:
            return OPERATOR_IDENTITY
        if v.startswith("guest:"):
            gid = v.split(":", 1)[1].strip()
            return Identity("guest", gid or None)
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
    GRANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(doc, indent=2) + "\n"
    tmp = GRANTS_PATH.with_suffix(GRANTS_PATH.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, GRANTS_PATH)


def load_grants() -> dict[str, Any]:
    """Return the grants document {"grants": [...], "guests": [...]}.

    Auto-creates an empty doc on disk if missing. If the file exists but is
    unreadable/corrupt, returns an in-memory empty doc (never raises) so reads
    stay available."""
    if not GRANTS_PATH.exists():
        try:
            _write_doc({"grants": [], "guests": []})
        except OSError:
            pass
        return {"grants": [], "guests": []}
    try:
        data = json.loads(GRANTS_PATH.read_text(encoding="utf-8"))
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
    cross-app grant check specifically."""
    ident = resolve_identity(identity)
    if ident.is_operator:
        return True
    if doc is None:
        doc = load_grants()
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
    """
    ident = resolve_identity(identity)
    actions = app_actions(app_id)
    policy = actions.get(action)

    if ident.is_operator:
        # Honor an explicit per-app confirm opt-in; otherwise allow.
        return "confirm" if policy == "confirm" else "allow"

    # ---- guest path (the boundary) ----
    if doc is None:
        doc = load_grants()

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
    apps reachable via an actual grant row to `app_id`."""
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
        for row in doc.get("grants", []):
            if row.get("to") == app_id:
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
