# Resource-Access Protocol: Interface Spec (2026-07-18)

> This doc is the **implementable contract**. Rationale and research live in
> `resource-access-protocol-2026-07-16.md`. ADR 0004 decides the agent identity
> model; ADR 0005 decides the rules engine. This doc tells you what to write.

---

## 1. What this spec covers

A Store provider (Notes, Finance, Budget, Commute, etc.) participates in the
resource-access protocol by implementing three things:

1. A **StoreAdapter** — the type-specific resolution and filtering logic.
2. A **provider-side integration** — calling `check()` before serving data and
   applying the `filter` caveat from the grant.
3. A **policy record** in `resource-policy.json` — declaring what agents may do
   on its Stores.

The broker (`services/broker.py`) and the policy engine (`services/resource_policy.py`)
are general-purpose and owned by the platform. Providers do NOT import or call the
broker directly. The flow is always:
requester → `POST /broker/request` → broker → `permissions.mint_grant` →
requester calls provider → provider calls `permissions.check()` → returns data.

---

## 2. Vocabulary

| Term | Definition |
|------|------------|
| **Store** | A registration over existing on-disk data. URI `workshop://store/<app>/<name>[/<path>]`. |
| **Provider** | The backend code (route + service) that owns a Store and serves its data. |
| **StoreAdapter** | A typed class the provider registers. Translates generic selectors and filter caveats into provider-specific queries. |
| **Policy record** | A JSON row in `resource-policy.json` that declares a Store's access tier and ceilings. |
| **Broker** | The platform component (`services/broker.py`) that adjudicates cross-scope access requests using the policy record and an LLM. |
| **Grant** | A capability token (`permissions.mint_grant`). The broker mints; `permissions.check()` validates on every call. |
| **Tier** | `open` (no gate), `gated` (programmatic only), `mediated` (broker + LLM required). |
| **I1** | The attenuation invariant: the broker may only narrow within ceilings declared in the policy record. The LLM never widens. |
| **I2** | The separation invariant: the component that decides (broker) never reads the data; the component that reads the data (quarantined filter) holds no grant. |

---

## 3. StoreAdapter contract

One adapter per Store **type** (not per Store instance). Register it with the
platform on startup. Adapters are imported by `services/store_adapters.py`.

```python
from __future__ import annotations
from typing import Any, Protocol
from dataclasses import dataclass


@dataclass
class Item:
    """A single resource item returned by a Store."""
    id: str                          # stable, opaque id (no path separators)
    title: str | None                # human-readable name; None if not applicable
    body: str | None                 # full text/content; None for list-only results
    tags: list[str]                  # empty list if not applicable
    metadata: dict[str, Any]         # type-specific fields (date, amount, merchant, …)


@dataclass
class FilterCaveat:
    """The filter caveat carried on a grant. Provider applies it; broker does not."""
    match: dict[str, Any]            # type-specific match predicates
    fields: list[str] | None         # if set, only these fields are returned in metadata
    max_items: int | None            # if set, limit the result count


class StoreAdapter(Protocol):
    """
    One implementation per Store type. Platform calls these methods; the adapter
    never calls the broker or touches grants.
    """

    def selectors(self) -> list[str]:
        """
        Returns the selector keys this type understands.
        Common values: "query" (substring), "name" (exact title), "id" (exact id),
        "tags" (tag list), "date_range" (ISO date pair).
        The broker's request envelope may use any selector key; unrecognized keys
        are ignored by the adapter.
        """
        ...

    def resolve(
        self,
        store_uri: str,
        selector: dict[str, Any],
        verb: str,
    ) -> list[Item]:
        """
        Resolves a selector against the Store at `store_uri`.
        `verb` is one of: read, list, search, append, write.
        For list/search without read: return items with body=None.
        For search without read: return items with body=None, id+title+tags only.
        Never raises on missing store; returns [].
        """
        ...

    def apply_filter(
        self,
        items: list[Item],
        caveat: FilterCaveat,
    ) -> list[Item]:
        """
        Applies the grant's filter caveat deterministically.
        Must be a pure function of (items, caveat): no LLM, no side effects.
        Returns a subset of items (never new items), with metadata narrowed to
        caveat.fields if specified.
        """
        ...

    def to_response(
        self,
        items: list[Item],
        mode: str,
        withheld_count: int,
    ) -> dict[str, Any]:
        """
        Serializes items to the wire format for response mode `mode`.
        `withheld_count` is the count of items excluded before this call.
        Must include a `withheld` key if withheld_count > 0 and the policy
        permits count-only disclosure (see §7 condition table).
        Modes: "verbatim", "filtered", "redacted", "existence-only", "count-only".
        For "existence-only": return {"matched": len(items) > 0}.
        For "count-only": return {"matched": len(items)}.
        """
        ...
```

### Structural redaction

Adapters implement structural redaction (account numbers, emails, phone numbers,
key-shaped strings) in `apply_filter` using regex, with no LLM. Example:

```python
_ACCOUNT_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_EMAIL_RE    = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

def _redact_field(text: str) -> str:
    text = _ACCOUNT_RE.sub("[redacted]", text)
    text = _EMAIL_RE.sub("[redacted]", text)
    return text
```

The **subset check** — verifying that every token in the filtered output appears
in some source item — is run by the platform after the adapter returns from
`to_response`. Adapters do not run it themselves, but their output must pass it.

### Adapter registration

```python
# services/store_adapters.py
_REGISTRY: dict[str, StoreAdapter] = {}

def register(store_type: str, adapter: StoreAdapter) -> None:
    _REGISTRY[store_type] = adapter

def get(store_type: str) -> StoreAdapter | None:
    return _REGISTRY.get(store_type)
```

Called once per process at startup, before the broker can serve requests.

---

## 4. Request envelope schema

Sent by a requester to `POST /broker/request`. All fields are required unless
marked optional.

```jsonc
{
  // Idempotency key. Same request_id → same verdict within the grant TTL.
  // Requester must generate a stable id (e.g. hash of job_id + uri + selector).
  "request_id": "req_9f2a",

  "requester": {
    "kind": "agent",                 // always "agent" for broker requests
    "id": "agent:notes-triage",      // "agent:<slug>" format
    "on_behalf_of": "operator",      // claim; for escalation provenance
    "job_id": "job_1183",
    "task_id": "t_402"               // optional; links to the blocked task
  },

  "target": {
    "store_uri": "workshop://store/knowledge/project-acme/finance",
    "verb": "search",                // one of: read, list, search, append, write
    "selector": {
      // Selector keys understood by the target Store's adapter (§3).
      // Common keys: query, name, id, tags, date_range.
      // Unrecognized keys are ignored by the adapter; the broker sees all.
      "query": "runway",
      "tags": ["planning"]
    }
  },

  // Untrusted. Delimited by the broker's prompt; treated as evidence of intent,
  // never as instruction. The broker reads this; the provider does NOT.
  "purpose": "<<<UNTRUSTED>>> need burn rate to draft the Q3 ticket <<<END>>>",

  // Preferred response mode. Broker may downgrade; cannot upgrade to a mode
  // not in the policy's response_modes list.
  "response_mode_requested": "filtered",

  // Optional. If attenuating an existing grant rather than minting a new one.
  "parent_token": null
}
```

**Validation rules applied by the platform before reaching the broker:**

- `requester.kind` must be `"agent"`.
- `target.verb` must be in `["read", "list", "search", "append", "write"]`.
- `target.store_uri` must match `_URI_RE` from `stores.py`.
- `purpose` length is capped at 4096 characters; longer values are truncated with
  a visible truncation marker before reaching the broker.

---

## 5. Verdict envelope schema

Returned by `POST /broker/request`.

```jsonc
{
  "request_id": "req_9f2a",

  // "allow": full access within the grant's verbs and caveats.
  // "partial": access granted, but some items were withheld.
  // "deny": no access. Grant is null.
  "decision": "allow" | "partial" | "deny",

  // Human-readable prose. Written by the broker LLM on allow/partial/deny;
  // written by platform code on adjudicator_unavailable.
  "reason": "Query matches topic 'burn rate' in the allowlist.",

  // Machine-readable class. Requester branches on this, not on `reason`.
  "reason_class":
    "policy_match"           // allow: matched topics_allow
    | "off_topic"            // deny: did not match topics_allow
    | "verb_not_permitted"   // deny: verb not in verbs_max
    | "requester_not_permitted"  // deny: identity not in requesters_allow
    | "no_policy"            // allow: store has no policy row (tier = open)
    | "adjudicator_unavailable", // deny: broker LLM unreachable; retryable

  // true only when reason_class == "adjudicator_unavailable"
  "retryable": false,

  // The response mode the broker selected. Always in policy.response_modes.
  // Null on deny.
  "response_mode": "filtered" | null,

  // The minted grant. Null on deny. Requester must pass this token on all
  // subsequent calls to the provider until it expires.
  "grant": {
    "id": "e31c...",
    "store_uri": "workshop://store/knowledge/project-acme/finance",
    "verbs": ["search"],
    "caveats": {
      "expiry": "2026-07-18T21:15:00Z",
      "filter": {
        "match": { "query": "runway", "tags": ["planning"] },
        "fields": null,
        "max_items": null
      }
    }
  } | null,

  // Disclosure of withholding. Present when decision == "partial".
  // Granularity is capped to what policy.response_modes already permits:
  // if "count-only" is in response_modes, counts are shown; otherwise only
  // truncated: true.
  "withheld": {
    "truncated": true,
    "items_excluded": 3,           // present only if "count-only" in policy.response_modes
    "fields_redacted": ["account_number"]   // present only for "redacted" mode
  } | null,

  "adjudicator": {
    "model": "claude-sonnet-4-6",
    "elapsed_s": 2.1
  } | null,

  "expires_at": "2026-07-18T21:15:00Z" | null
}
```

---

## 6. Policy record schema

Full schema; rationale in ADR 0005.

```jsonc
{
  // Longest-prefix match. Add "/<path>" for subfolder-level policy.
  "uri": "workshop://store/<app>/<name>[/<path>]",

  // true: policy applies to all URIs whose string representation starts with uri.
  // false: exact match only.
  "subtree": true,

  // open: any grant-holding agent may act. No cost. Default when no row exists.
  // gated: programmatic rules only. No LLM. Required for type "Secret".
  // mediated: broker + LLM adjudication required. One Sonnet call per grant.
  "tier": "open" | "gated" | "mediated",

  // Ceiling on verbs the broker may grant. Broker proposals exceeding this are
  // treated as adjudicator_unavailable (I1 enforcement in code, not by prompt).
  "verbs_max": ["read", "list", "search", "append", "write"],

  // mediated tier: topics the broker may authorize (checked programmatically
  // after the LLM returns). Empty = nothing allowed.
  "topics_allow": [],

  // mediated tier: topics explicitly excluded. Checked before topics_allow.
  "topics_deny": [],

  // Glob patterns on requester.id. "agent:*" permits any agent identity.
  // Validated in code before and after the broker call.
  "requesters_allow": ["agent:*"],

  // Set of response modes the broker may authorize. "summarized" requires
  // explicit opt-in here and is never in any default.
  "response_modes": ["verbatim", "filtered", "redacted", "count-only", "existence-only"],

  // Broker's default when requester does not specify or requests an unlisted mode.
  "default_response_mode": "filtered",

  // How long the minted grant is valid. Broker does not re-adjudicate within this
  // window; idempotency key returns cached verdict.
  "grant_ttl_s": 900,

  // Minimum LLM tier for adjudication. "sonnet" or "opus". Never "haiku".
  // Not configurable downward from "sonnet".
  "adjudicator_min_tier": "sonnet",

  // Prose guidance passed to the broker verbatim. Cannot widen a grant (I1).
  // An imprecise rules_text creates a usability issue, never a security hole.
  "rules_text": ""
}
```

**Constraints enforced by `resource_policy.py`:**

- `tier: "mediated"` is rejected for a Store of type `"Secret"`.
- `adjudicator_min_tier` must be `"sonnet"` or `"opus"`; any other value is
  treated as `"sonnet"`.
- `response_modes` containing `"summarized"` is valid only if `"summarized"` is
  explicitly listed; it is never inferred.
- An absent or malformed `resource-policy.json` is treated as empty (all Stores
  at `open` tier). Fail-open: a corrupt file must not lock the operator out of
  their own data.

---

## 7. Provider-side integration

What an existing app must add to participate. Three steps.

### Step 1: Register the Store

In the app's startup or in `stores.py:register_builtin_stores`:

```python
stores.declare(
    app="knowledge",
    name="project-acme",
    root=Path("$WORKSHOP/knowledge/project-acme"),
    type="Note",
)
# For vault subfolders, use the path argument once URI grammar is extended:
stores.declare("notes-vault", "vault", notes_vault.vault_root(), type="Note")
```

### Step 2: Register the StoreAdapter

```python
from services.store_adapters import register
from my_app.adapter import MyAdapter

register("Note", MyAdapter())    # once per process
```

### Step 3: Add the check + filter call to every read path

Before returning data to any caller that is not `operator` identity:

```python
from services import permissions, resource_policy

def get_items(token: str, store_uri: str, selector: dict, verb: str):
    # 1. Mechanical grant check (no LLM, no network).
    permissions.check(token, store_uri, verb)   # raises PermissionDenied on fail

    # 2. Resolve items via adapter.
    adapter = store_adapters.get(store_type_for(store_uri))
    items = adapter.resolve(store_uri, selector, verb)

    # 3. Apply filter caveat from the grant (I1: deterministic, in provider).
    grant = permissions.get_grant(token)
    if grant.caveats.get("filter"):
        caveat = FilterCaveat(**grant.caveats["filter"])
        items = adapter.apply_filter(items, caveat)

    # 4. Serialize with withheld accounting.
    withheld = original_count - len(items)
    return adapter.to_response(items, grant.caveats.get("response_mode", "verbatim"), withheld)
```

**The `check()` call is the only gate.** The provider does not call the broker;
the broker was called by the requester before it obtained the token. The provider's
job is mechanical: check the token, apply the filter, return the result.

`operator`-identity callers skip the broker entirely — `check()` is not called for
operators; `authorize()` handles them with the existing ambient-allow ladder
(`permissions.py:354-356`). The integration above applies only to `agent` identity
callers.

---

## 8. Broker HTTP API

### `POST /broker/request`

**Request body:** Request envelope (§4).

**Response `200 OK`:** Verdict envelope (§5).

**Response `400 Bad Request`:** Malformed envelope; `{"error": "<description>"}`.

**Response `429 Too Many Requests`:** Not returned (no rate-limiting — single-operator
system). If this response appears, it is from a reverse proxy and is not the broker.

The broker is not called by providers. It is called by requesters (agents and
background jobs) before they have a grant. A requester that already holds an
unexpired grant for the target URI and verb calls the provider directly, not the
broker. The broker is only consulted when a grant is absent or expired.

### Idempotency

Requests with the same `request_id` return the cached verdict within the grant's
`grant_ttl_s`. This is a determinism guarantee, not a rate limit: an agent that
retries a request due to a transient failure gets the same verdict, not a re-roll
from a potentially inconsistent LLM.

### Retry protocol (adjudicator unavailable)

When `reason_class == "adjudicator_unavailable"` and `retryable == true`:

- Retry up to 3 times with exponential backoff: 5s, 15s, 45s.
- On 3rd failure, emit a blocked Task with `blocked_reason: "adjudicator_unavailable (retryable)"`.
- Do NOT write "access denied" into notes. Do NOT conclude anything about policy.
- The blocked task is visually distinct from a policy-denial block.

---

## 9. Condition codes

`reason_class` values, what triggers them, and how the requester must handle each.

| reason_class | decision | retryable | Requester action |
|---|---|---|---|
| `policy_match` | allow or partial | false | Proceed. Check `withheld` for partiality; surface it in task output. |
| `no_policy` | allow | false | Proceed. Store has no policy row; tier is open. |
| `off_topic` | deny | false | Emit blocked Task. Do not retry with same purpose; operator must update `topics_allow` or `rules_text`. |
| `verb_not_permitted` | deny | false | Emit blocked Task. The requested verb is not in `verbs_max`; operator must change policy to allow it. |
| `requester_not_permitted` | deny | false | Emit blocked Task. The agent identity does not match `requesters_allow`. |
| `adjudicator_unavailable` | deny | true | Retry (§8). After 3 failures, emit blocked Task with "retryable" label. |

**On `partial` results:** the requester MUST surface partiality. A partial silently
treated as complete is the likeliest real-world correctness bug (agent acts on an
incomplete picture, writes a wrong ticket, closes a case that should stay open).
The convention: append `[Note: result is partial — N items withheld]` to the task's
output notes when `decision == "partial"`.

---

## 10. Worked example: Notes (knowledge scope)

### Store registration

```python
# in knowledge.py, already present:
stores.declare("knowledge", scope_key, scope_root, type="Note")
```

### Policy row

```jsonc
{
  "uri": "workshop://store/knowledge/project-acme/finance",
  "subtree": true,
  "tier": "mediated",
  "verbs_max": ["read", "list", "search"],
  "topics_allow": ["runway", "burn rate", "vendor invoices"],
  "topics_deny": ["personal account numbers"],
  "requesters_allow": ["agent:*"],
  "response_modes": ["filtered", "count-only"],
  "default_response_mode": "filtered",
  "grant_ttl_s": 900,
  "adjudicator_min_tier": "sonnet",
  "rules_text": "Prefer counts over contents for anything payroll-adjacent."
}
```

### Request

```jsonc
{
  "request_id": "req_abc1",
  "requester": { "kind": "agent", "id": "agent:q3-drafter", "on_behalf_of": "operator", "job_id": "job_99" },
  "target": {
    "store_uri": "workshop://store/knowledge/project-acme/finance",
    "verb": "search",
    "selector": { "query": "runway", "tags": ["planning"] }
  },
  "purpose": "<<<UNTRUSTED>>> need burn rate to draft the Q3 ticket <<<END>>>",
  "response_mode_requested": "filtered"
}
```

### Verdict (success path)

```jsonc
{
  "request_id": "req_abc1",
  "decision": "partial",
  "reason": "Query matches 'burn rate' in topics_allow.",
  "reason_class": "policy_match",
  "retryable": false,
  "response_mode": "filtered",
  "grant": {
    "id": "tok_e31c",
    "store_uri": "workshop://store/knowledge/project-acme/finance",
    "verbs": ["search"],
    "caveats": {
      "expiry": "2026-07-18T22:00:00Z",
      "filter": { "match": { "query": "runway", "tags": ["planning"] }, "fields": null }
    }
  },
  "withheld": { "truncated": true, "items_excluded": 2 },
  "adjudicator": { "model": "claude-sonnet-4-6", "elapsed_s": 1.8 },
  "expires_at": "2026-07-18T22:00:00Z"
}
```

The agent searches the scope, gets matching notes verbatim (no second model call),
and appends `[Note: result is partial — 2 items withheld]` to its task output.

---

## 11. Worked example: Finance (Plaid items)

### Store registration

```python
# in stores.py:register_builtin_stores, already present:
stores.declare("finance", "plaid-items", Path("~/.config/workshop/plaid-items.json"), type="Finance")
```

### Policy row

```jsonc
{
  "uri": "workshop://store/finance/plaid-items",
  "subtree": false,
  "tier": "mediated",
  "verbs_max": ["search", "list"],
  "topics_allow": ["recurring bills", "merchant names", "payment confirmation"],
  "topics_deny": ["account numbers", "balances", "income"],
  "requesters_allow": ["agent:*"],
  "response_modes": ["filtered", "count-only"],
  "default_response_mode": "filtered",
  "grant_ttl_s": 600,
  "adjudicator_min_tier": "opus",
  "rules_text": "Never release a balance. Confirming a payment happened is fine."
}
```

Note: `verbs_max` has no `read`. **No agent may ever read a raw Plaid item.** This is
a programmatic ceiling the broker LLM cannot override (I1). The broker sees this field;
a proposed grant with verb `read` is rejected by `validate_verdict` before minting.

### Request

```jsonc
{
  "request_id": "req_def2",
  "requester": { "kind": "agent", "id": "agent:bill-triage", "on_behalf_of": "operator", "job_id": "job_200" },
  "target": {
    "store_uri": "workshop://store/finance/plaid-items",
    "verb": "search",
    "selector": { "query": "electric", "date_range": ["2026-07-01", "2026-07-31"] }
  },
  "purpose": "<<<UNTRUSTED>>> confirm the electric bill posted before closing t-88 <<<END>>>",
  "response_mode_requested": "filtered"
}
```

### Execution trace

1. Broker (Opus, `tools_disabled=True`) sees: identity, URI, selector, purpose, policy.
   **It sees zero transactions.** (I2)
2. Broker decides: "payment confirmation" is in `topics_allow`; `verb: search` is in
   `verbs_max`. Returns `allow`, mode `filtered`.
3. Platform validates: `search` ∈ `verbs_max` ✓, `filtered` ∈ `response_modes` ✓,
   `agent:bill-triage` matches `agent:*` ✓.
4. Platform mints grant with `filter` caveat: `{match: {merchant_contains: "electric", month: "2026-07"}, fields: ["date", "amount", "merchant_name"]}`.
5. Agent calls Finance provider with token `tok_xyz`.
6. Provider: `check(tok_xyz, uri, "search")` passes. `apply_filter` returns 1 row,
   3 fields. **No account id. No balance. 214 other rows excluded.**
7. Agent receives: `{date: "2026-07-03", amount: 84.21, merchant_name: "Pacific Gas & Electric"}`.
8. Agent closes t-88. Appends `[Note: result is partial — 214 items withheld]`.

No second model call (filter was mechanically expressible). One Opus call, amortized
over the 600s TTL.

---

## 12. What providers must NOT do

- **Do not call the broker.** The provider's job is mechanical: check the token,
  apply the filter, return the result. The broker was already called by the requester.
- **Do not return data when `check()` raises.** A `PermissionDenied` from `check()`
  means the token is absent, revoked, expired, or wrong scope. Return the exception;
  do not fall back to a permissive path.
- **Do not return an empty list when the token is revoked.** An empty result is
  indistinguishable from "nothing matched". Revocation must return a distinct error
  so the requester knows its grant was revoked rather than concluding the store is
  empty and acting on that false premise.
- **Do not run the quarantined filter pass yourself.** The quarantined filter (Sonnet,
  `tools_disabled=True`, sees bodies, holds no token) is run by the platform, not by
  providers. Providers apply only mechanical filters from the `filter` caveat.
- **Do not register `tier: "mediated"` on a Secret-typed Store.** There is no filtered
  view of a secret; any partial release is a total release. `resource_policy.py` rejects
  this combination at load time.

---

## 13. Relationship to existing platform primitives

| Protocol primitive | Status | Location |
|---|---|---|
| Store (URI over existing data) | Exists | `services/stores.py:37-56` |
| Grant (token, verbs, caveats, attenuation) | Exists | `services/permissions.py:616-694` |
| `check(token, uri, verb)` | Exists | `services/permissions.py:710` |
| `mint_grant`, `attenuate`, `revoke` | Exists | `services/permissions.py:616-694` |
| Agent identity kind | New (ADR 0004) | `services/permissions.py:71-93` |
| Policy record + registry | New (ADR 0005) | `services/resource_policy.py` |
| Broker (adjudicate, verdict) | New | `services/broker.py`, `routes/broker.py` |
| StoreAdapter registry | New | `services/store_adapters.py` |
| Response mode registry | New | `services/response_modes.py` |
| Escalation as blocked Task | Exists | `tasks.py:61` |

Providers implement adapters and call `check()`. Everything else is platform-owned.

---

## 14. Non-goals (things this spec does not define)

- **The broker's internal prompt.** How the LLM is instructed is platform-internal
  and not part of the provider contract. Providers never see the prompt.
- **The quarantined filter pass.** Platform-internal. Providers do not invoke it.
- **The blocked-task UI flow.** Covered in `scopedness-and-escalation-2026-07-16.md`
  and the planned A2 design doc.
- **Sidecar policy files.** Importing policy from a folder's `.access.json` is an
  operator action. Providers do not auto-load sidecars. See protocol doc Part 8.
- **Agent-to-agent delegation** (nested `on_behalf_of` chains). Deferred; see ADR 0004.
