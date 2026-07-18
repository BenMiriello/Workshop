# 0005. Resource policy uses a homegrown JSON + prose rules engine, not OPA/Rego

- Status: proposed
- Date: 2026-07-18
- Supersedes / superseded by: none

## Context

The resource-access protocol (`resource-access-protocol-2026-07-16.md`) requires a
policy layer: each Store (or Store path) needs a record declaring its access tier
(`open`, `gated`, or `mediated`), the maximum verbs an agent may hold, which topics
are in or out, and what response modes the broker may authorize. The central
invariant (I1) requires that programmatic policy fields cap what the LLM broker may
grant; prose guidance may influence narrowing within that cap but can never widen it.

Three real alternatives exist:

**OPA/Rego.** Open Policy Agent is a production-grade, CNCF-graduated policy engine
with a Rego policy language derived from Datalog. It is widely adopted for
cross-service authorization in large organizations. It provides: uniform enforcement
across heterogeneous services, a hot-reload policy distribution mechanism, a bundle
system for versioned policy sets, and a mature testing framework. The tradeoffs: it
requires a running OPA server (or embedded Go binary via Go FFI), Rego is its own
learning curve, and the value proposition scales with organizational complexity.
None of those conditions apply: Workshop has one process, one operator, one machine,
and a JSON file.

**Biscuits/Macaroons.** Capability tokens with offline attenuation via chained
HMACs (macaroons) or public-key-signed blocks (biscuits). Already evaluated and
rejected in the protocol doc for the same reason: `check()` resolves tokens by
lookup (`permissions.py:603-613`); a forged token id simply is not in the file.
Adding crypto chaining adds complexity and buys nothing on a single-process system
where the only untrusted party is content in note bodies, not a peer service.

**Bare Datalog.** The biscuit spec and OPA both use Datalog-derived languages
because Datalog is closed under composition: multiple policy files combine without
runtime surprises. For Workshop's policy rules, which amount to "topics and scope for
this folder", Datalog is expressive power looking for a problem to justify it.

**Homegrown JSON + prose.** A JSON policy record with programmatic fields enforced
by deterministic code, plus a `rules_text` prose field passed to the broker as
guidance. I1 is enforced by code that validates the LLM's proposed narrowing against
the programmatic fields before any grant is minted; the LLM never touches
`resource-grants.json` directly.

The operator's B3 ask specifies "literal programmatic rules OR textual rules". JSON
programmatic fields handle the former; `rules_text` handles the latter. Both are
first-class in the record.

## Decision

Use a homegrown JSON policy record stored in `backend/resource-policy.json`,
alongside `backend/resource-grants.json` (`permissions.py:51-54`).

### Policy record schema

```jsonc
{
  "uri": "workshop://store/<app>/<name>[/<path>]",
  "subtree": true,                // applies to all URIs with this prefix
  "tier": "open" | "gated" | "mediated",
  "verbs_max": ["read", "list", "search", "append", "write"],
  "topics_allow": ["<topic>", ...],      // mediated tier: in-scope topics
  "topics_deny": ["<topic>", ...],       // mediated tier: explicit exclusions
  "requesters_allow": ["agent:*", ...],  // glob patterns on identity ids
  "response_modes": ["verbatim", "filtered", "redacted", "count-only", ...],
  "default_response_mode": "filtered",
  "grant_ttl_s": 900,
  "adjudicator_min_tier": "sonnet",      // "sonnet" | "opus"; never "haiku"
  "rules_text": "Prefer counts over contents for payroll-adjacent queries."
}
```

Field semantics:

- `uri` and `subtree`: longest-prefix match over the Store URI grammar
  `workshop://store/<app>/<name>[/<path>]`. Mirrors `check()`'s existing subtree
  matching (`permissions.py:725-728`). No new matching logic needed.
- `tier: "open"`: any agent holding any grant reaching this URI may act, within
  its verbs. Zero cost. The default for a URI with no policy row.
- `tier: "gated"`: programmatic scope only. Verbs, path subtree, and a mechanical
  selector predicate. Deterministic. Zero cost. Required for type "Secret" stores;
  `tier: "mediated"` is rejected by the schema validator on type "Secret".
- `tier: "mediated"`: no access unless the broker adjudicates. One Sonnet call per
  grant, amortized over `grant_ttl_s`.
- `verbs_max`: the ceiling the broker may grant. The broker's proposed verbs are
  validated against this field in code; any proposal exceeding it is a broker
  failure, not a grant.
- `topics_allow` / `topics_deny`: evaluated by the broker against the request
  purpose. Programmatic fields, validated after the broker returns; `rules_text`
  is prose guidance and may influence narrowing but cannot override these.
- `requesters_allow`: glob patterns. Validated in code before and after the broker
  call.
- `response_modes`: set of modes the broker may authorize. The broker's proposed
  mode is validated against this list in code; a mode not listed is a broker failure.
- `rules_text`: passed verbatim to the broker as guidance. The I1 invariant makes
  it impossible for prose to widen a grant: the deterministic code post-validates
  every dimension against the programmatic fields and a broker that was talked out of
  its job by an injected purpose produces a grant no wider than `verbs_max` specifies.

### Enforcement service

`backend/services/resource_policy.py`:

- `load() -> list[Policy]`: loads `resource-policy.json`. On parse error, returns
  empty (fail-open posture, same as `load_grants` at `permissions.py:191-195`). An
  operator locked out of their own notes by a JSON syntax error is anti-cruft.
- `resolve(uri: str) -> Policy | None`: longest-prefix match over `policy.uri +
  policy.subtree`. Returns `None` for a URI with no policy row, which the broker
  treats as `open`.
- `save(policies: list[Policy]) -> None`: atomic write, same posture as
  `permissions.py:170-176`.
- `validate_verdict(verdict, policy) -> bool`: runs the I1 check. Validates proposed
  verbs, response mode, and requester identity against the programmatic fields.
  Returns `False` if the LLM exceeded any ceiling; caller treats this as
  `reason_class: "adjudicator_unavailable"`.

### Why the asymmetry in failure modes

The broker fails closed (no LLM available = deny). The policy loader fails open
(corrupt JSON = no gate = today's behavior). These are different failures:

- A missing adjudicator means a potentially unauthorized party is requesting access.
  Failing open would release data the operator has not authorized. Fail closed.
- A corrupt policy file means the operator's OWN tools or agents cannot reach
  resources they routinely use. Failing closed locks the operator out of their own
  system. That is the definition of anti-cruft. Fail open.

Both directions are explicit and on purpose.

## Consequences

What becomes easier:

- Policy is readable and writable by any JSON-capable tool: a text editor, a script,
  the future settings UI (Phase R5 of the protocol).
- Longest-prefix matching gives per-folder granularity and free inheritance: a policy
  on `.../project-acme` covers every subfolder until a longer prefix overrides.
  This already exists as house style in `manifests.py:219-251`.
- `rules_text` makes textual rules (B3) first-class without building a policy language.
- An operator writing imprecise prose creates a usability issue, never a hole: the
  programmatic fields are the actual gate, and the post-return validation is
  deterministic code.
- The schema is trivially extensible: add a field, update the validator, redeploy.
  No grammar change, no language migration.

What becomes harder:

- Complex policy composition (e.g., "grant X if A AND B AND NOT C, where A comes
  from one file and B from another") is not expressible. Datalog handles this; JSON
  does not. If policy composition across stores ever becomes real, OPA/Rego is the
  named migration target.
- There is no policy testing framework (OPA has `opa test`). Tests are written
  against `validate_verdict` directly.

What is now settled:

- OPA/Rego is not wrong for Workshop, it is overbuilt. If Workshop ever expands to
  multi-operator access, cross-process enforcement, or a policy bundle distribution
  need, OPA/Rego is the right move. Cite this record and the migration is clean:
  the JSON schema translates to Rego rules directly.
- Biscuit/macaroon crypto is rejected for this system permanently unless the threat
  model changes to include an untrusted peer service that needs to verify tokens
  without calling home. Name the change, not just the desire.
- `rules_text` cannot widen a grant. This is enforced by code, not by asking the
  LLM nicely. Any future implementation that passes `rules_text` to the broker
  without running `validate_verdict` afterward has dropped I1.

Deferred:

- Whether `rules_text` should support a simple template syntax (e.g., interpolating
  the requester's `on_behalf_of` or the current timestamp). Defer until a concrete
  use case appears that prose alone cannot handle.
- Per-topic per-mode granularity (e.g., "for topic 'merchant names', allow
  `count-only`; for topic 'payment confirmation', allow `filtered`"). The current
  schema cannot express this; add it if it comes up in a real use case.
