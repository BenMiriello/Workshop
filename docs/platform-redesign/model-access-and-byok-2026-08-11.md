# Model access, credentials, and the road to multi-user

Status: **design + first seam built, nothing switched over.** 2026-08-11.

This answers one question the operator asked directly, and the answer constrains
the whole architecture, so it comes first.

---

## 1. The question

> Can other individual people put their own Claude subscription credential into
> our system so they can use their Claude in our system?

**No. That specific thing is explicitly prohibited, and it is already enforced
in software.**

This is not a gray area and not a risk to be weighed. The primary source is
Anthropic's own Claude Code legal page, under "Authentication and credential
use":

> **OAuth authentication** is intended exclusively for purchasers of Claude
> Free, Pro, Max, Team, and Enterprise subscription plans and is designed to
> support ordinary use of Claude Code and other native Anthropic applications.
>
> **Developers** building products or services that interact with Claude's
> capabilities, including those using the Agent SDK, should use API key
> authentication through Claude Console or a supported cloud provider.
> **Anthropic does not permit third-party developers to offer Claude.ai login or
> to route requests through Free, Pro, or Max plan credentials on behalf of
> their users.**
>
> Anthropic reserves the right to take measures to enforce these restrictions
> and may do so without prior notice.

That last sentence is the one describing our exact proposed feature, and it
names it as not permitted. The Consumer Terms say the same thing from the user's
side (§2): *"You may not share your Account login information, Anthropic API
key, or Account credentials with anyone else. You also may not make your Account
available to anyone else."* A user pasting their Max token into our service is
that user violating their own agreement, and us inducing it.

**It is also enforced technically, not just legally.** Through the first half of
2026 Anthropic shipped server-side blocks on subscription OAuth tokens presented
by anything other than the official client, removed the ability of third parties
to spoof the Claude Code client identity, and completed enforcement in April
2026. Tools built on subscription passthrough (OpenClaw, OpenCode and others)
stopped working. So even setting the terms aside, building on this would be
building on something that is currently broken by design and will stay broken.

**Do not design around it, and do not treat it as a toggle we might flip.**

## 2. What IS allowed

Three things, and they are enough.

**(a) The user brings their own API key.** This is the documented path: *"should
use API key authentication through Claude Console or a supported cloud
provider."* It is the standard pattern across the industry in 2026 (Cline,
Continue, LibreChat, Open WebUI, JetBrains AI Assistant, GitHub Copilot since
April 2026). A useful property: when the user's own key is used, the provider's
**API** terms attach to their usage rather than our consumer terms, and API
terms are more protective on data retention than consumer ones. This is our
launch path.

**(b) We serve users from our own commercial API account.** Anthropic's
Commercial Terms §A.1: *"Anthropic gives Customer permission to use the
Services, including to power products and services Customer makes available to
its own customers and end users."* That is exactly a "default model included"
tier. The boundary is §D.4: we may not *"resell the Services"* or build a
competing product. Selling our product with Claude inside it is fine; selling
raw metered Claude access is not. This is the later "get started without a key"
tier, and it is permitted, not a hack.

**(c) The operator keeps using `claude -p` locally.** Running the official
Claude Code CLI on your own machine, under your own subscription, for your own
work, is ordinary first-party use. It is what the subscription is for. The
operative limit is the phrase *"ordinary, individual usage"* in the acceptable
use section, which is about volume, not about mechanism. Nothing changes here,
and the operator explicitly wants to keep it.

The line between (c) and the prohibition is not "which binary runs". It is
**whose request is being served**. One person's subscription serving that same
person is (c). One person's subscription serving anyone else, or any
subscription reached through our service on a user's behalf, is the prohibited
thing.

## 3. The other providers

The same rule, for a simpler reason: nobody grants what we would need.

- **OpenAI / Codex.** The terms are silent on BYOK and prohibit buying, selling
  or transferring API keys with third parties (aimed at key trading, not at a
  user entering their own key into a tool they use). There is no explicit
  permission for "Sign in with ChatGPT" inside a third-party service, and the
  Codex CLI's subscription sign-in has never been blessed for that. Absence of
  permission is not permission.
- **Google, and everyone else.** Same shape.

So the platform rule is uniform and provider-independent, which is what makes it
implementable as one check rather than a table of vendor exceptions:

> **A subscription credential is single-operator and local. It may never serve a
> request on behalf of anyone but the person who owns it. Multi-user access is
> API keys: the user's own, or the platform's.**

## 4. What this means for the design

The system already partitions **data** per user, correctly and fail-closed:
`services/ownership.py` pins an owner per request and `current_owner()` raises
rather than defaulting to the operator, so a forgotten set-point is a crash and
never a cross-owner leak. That is the right pattern and the new work copies it.

What does not exist yet is the same discipline for **credentials** and
**providers**. Today one global `CLAUDE_CODE_OAUTH_TOKEN` is read from one file
and every turn uses it. In a multi-user deployment that single fact IS the
violation: the second user's turn would run on the first user's subscription.

So the seam being built now is: *a turn does not have a credential, a
**principal** has a credential, and the turn gets one by asking on behalf of a
named principal.* Once that is true, the prohibited configuration stops being
reachable rather than being something we remember not to do.

### Deployment mode is explicit, not inferred

Two modes, named in config, because the safe behaviour differs and guessing
would be the bug:

| mode | who runs | subscription path | credential scope |
|---|---|---|---|
| `single_operator` (today) | one person, their own box | allowed | one principal, the operator |
| `multi_user` (target) | many, hosted | **refused, in code** | per principal, isolated |

In `multi_user` the subscription provider is not "discouraged"; resolving it
raises. The rule from §3 becomes an exception, not a doc.

## 5. Staged plan

Deliberately not everything at once. Each stage is useful on its own and
nothing is switched over until the operator says so.

**Stage 1 (BUILT TODAY, inert).** The provider catalog and the credential seam.
`model_providers.py` describes each provider and auth mode, including whether it
is multi-user-legal and why. `model_credentials.py` resolves a credential *for a
principal* and refuses the prohibited combination. Wired so that in
`single_operator` mode it returns exactly what the system uses today: no
behaviour change, no switch-over. This is the load-bearing part, because it is
what makes stage 4 mechanical instead of a rewrite.

**Stage 2. Encryption at rest for stored credentials.** `secrets_store` writes
plaintext JSON at 0600, which is right for one user on one box and wrong the
moment a second user's key is in the same store. Envelope encryption: a
per-principal data key wrapped by a deployment key encryption key, AES-256-GCM,
KEK from the environment now and from a KMS later. Built opt-in (dormant until a
KEK is configured) so it changes nothing today.

**Stage 3. The BYOK surface.** A Settings screen where a user pastes a provider
key, it is verified before it is stored, and only presence and validity ever
come back out. The pattern already exists in this codebase and shipped today for
GitHub (`PUT /api/trackers/credentials`), so this is repetition, not invention.

**Stage 4. Flip the runtime to the seam.** `build_streaming` and `llm_router`
stop reading a global token and take the principal's credential. In
`single_operator` that resolves to the same subscription and nothing changes for
the operator. This is the switch-over, and it is the operator's call.

**Stage 5. Real tenant isolation for execution.** See §6. This is the largest
piece and it is not a credentials problem.

**Stage 6. The platform default tier.** Permitted by Commercial Terms §A.1.
Where it plugs in is marked in code (`model_providers.PLATFORM_DEFAULT`): a
provider whose credential comes from the platform's own account, gated by a
per-principal quota. The quota is the whole feature; the provider entry is four
lines. Not built.

## 6. The part that is harder than credentials

**This system runs agentic turns with shell access.** In single-operator mode
that is fine: the operator's agent runs as the operator. In multi-user mode,
"user supplies a prompt, we run an agent with Bash" is arbitrary remote code
execution by design, and every user's turn currently lands on the same host, as
the same Unix user, with the same filesystem and the same network.

Per-user directories do not address this. A turn that can run `cat` can read
another user's directory; a turn that can run `curl` can reach the backend's own
loopback ports.

The 2026 consensus for untrusted agent code is a microVM per sandbox
(Firecracker, ~125ms boot, hardware-level isolation via KVM) or gVisor as a
weaker but cheaper middle ground, and essentially every platform doing this
converged on "a container is not a sandbox." This is a real infrastructure
project, not a refactor, and it gates going multi-user far harder than
credentials do. **Credentials are stage 1-4; this is the actual blocker for
opening the door to a second user,** and the plan should say so rather than
letting a working BYOK screen imply the system is ready.

## 7. Recommendation

1. Keep `claude -p` for the operator's own use indefinitely. It is allowed, it
   is cheaper, and stage 1 keeps it working unchanged.
2. Build toward BYO API key as the only multi-user path. Never plan a feature
   that accepts someone else's subscription credential.
3. Do stages 1-3 at leisure; they are inert and safe.
4. Treat stage 5 as the real gate on multi-user, and decide whether this becomes
   a hosted product before paying for it.
5. If a "bring your Claude subscription" tier ever looks commercially necessary,
   the path is Anthropic's partnerships team, not a technical workaround.
   Anthropic does not issue OAuth client ids to third parties at all, so there
   is nothing to build against.

## Sources

- Claude Code, Legal and compliance: https://code.claude.com/docs/en/legal-and-compliance
- Anthropic Consumer Terms of Service: https://www.anthropic.com/legal/consumer-terms
- Anthropic Commercial Terms of Service: https://www.anthropic.com/legal/commercial-terms
- Anthropic Usage Policy: https://www.anthropic.com/legal/aup
