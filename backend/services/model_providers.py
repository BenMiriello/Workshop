"""Which model providers exist, how each authenticates, and who it may serve.

WHY THIS FILE EXISTS. The platform is meant to become a hosted product where
separate users bring their own model access. The single most important fact
about that plan is a licensing one, and it is easy to get wrong in a way that
only shows up as an account termination:

    A SUBSCRIPTION credential may only ever serve the person who owns it.

Anthropic states this directly (Claude Code, "Legal and compliance", under
Authentication and credential use): OAuth authentication "is intended
exclusively for purchasers of" the subscription plans, and "Anthropic does not
permit third-party developers to offer Claude.ai login or to route requests
through Free, Pro, or Max plan credentials on behalf of their users." Developers
building products "should use API key authentication through Claude Console".
The Consumer Terms say it from the user's side too: an account, its credentials
and its API key may not be shared or made available to anyone else.

It is enforced in software as well as in the terms. Through the first half of
2026 Anthropic blocked subscription OAuth tokens presented by non-official
clients and removed third parties' ability to spoof the official client
identity. Tools built on subscription passthrough stopped working. So this is
not a risk to be weighed; it is a thing that does not work.

The same rule covers every other provider, for a plainer reason: none of them
grant what would be needed either. OpenAI's terms are silent on BYOK and forbid
transferring keys with third parties; nothing blesses "Sign in with ChatGPT"
inside someone else's service. Absence of permission is not permission.

So the rule is written HERE, as data, once, rather than as a paragraph in a
design doc that a future change can quietly contradict. `multi_user=False` on a
provider is a licensing fact, not a feature flag, and `model_credentials`
enforces it. Full reasoning and the staged plan:
`docs/platform-redesign/model-access-and-byok-2026-08-11.md`.

WHAT IS ALLOWED, so the shape of the target is on the record:

  - a user supplying their OWN API key (the documented path, and what every
    comparable tool does);
  - this platform serving users from its OWN commercial API account, which
    Anthropic's Commercial Terms §A.1 permits ("including to power products and
    services Customer makes available to its own customers and end users"),
    bounded by §D.4's prohibition on reselling the Services as such;
  - the operator running the official CLI on their own machine under their own
    subscription, which is ordinary first-party use and is not affected by any
    of this.
"""

from __future__ import annotations

from dataclasses import dataclass

# How a provider proves who it is. Not a free-form string: the whole point of
# this module is that `subscription_oauth` is treated differently from
# `api_key`, and a typo in a new provider entry must not silently make a
# subscription look like a key.
AUTH_SUBSCRIPTION = "subscription_oauth"
AUTH_API_KEY = "api_key"
AUTH_PLATFORM = "platform_account"

AUTH_MODES = (AUTH_SUBSCRIPTION, AUTH_API_KEY, AUTH_PLATFORM)


@dataclass(frozen=True)
class Provider:
    """One way of reaching a model, and the terms that come with it."""

    id: str
    label: str
    auth_mode: str
    # The environment variable the runtime hands the credential to a child in.
    env_var: str
    # MAY THIS SERVE SOMEONE OTHER THAN THE CREDENTIAL'S OWNER? A licensing
    # fact, not a preference. See the module docstring.
    multi_user: bool
    # Quotable to a user when the answer is no. Empty when it is yes.
    restriction: str
    # Where a person goes to obtain this credential themselves.
    console_url: str
    # Can THIS deployment actually execute a turn on this provider today? Kept
    # honest and separate from `multi_user`: a provider can be perfectly legal
    # for multi-user and still have no runtime here yet. Stage 4 of the plan is
    # what makes the API-key providers runnable.
    runtime_ready: bool
    # A cheap shape check on a pasted credential, so an obvious paste error is
    # caught before a network call. Never a validity check.
    key_prefix: tuple[str, ...] = ()


_SUBSCRIPTION_RESTRICTION = (
    "A Claude subscription may only be used by the person who bought it. "
    "Anthropic does not permit routing requests through Free, Pro or Max plan "
    "credentials on behalf of other users, and blocks it server side. Use an "
    "API key from the Claude Console instead."
)

CLAUDE_CLI = Provider(
    id="claude_cli",
    label="Claude subscription (Claude Code CLI)",
    auth_mode=AUTH_SUBSCRIPTION,
    env_var="CLAUDE_CODE_OAUTH_TOKEN",
    multi_user=False,
    restriction=_SUBSCRIPTION_RESTRICTION,
    console_url="https://claude.com/product/claude-code",
    runtime_ready=True,
)

ANTHROPIC_API = Provider(
    id="anthropic_api",
    label="Anthropic API key",
    auth_mode=AUTH_API_KEY,
    env_var="ANTHROPIC_API_KEY",
    multi_user=True,
    restriction="",
    console_url="https://platform.claude.com/settings/keys",
    runtime_ready=False,
    key_prefix=("sk-ant-",),
)

OPENAI_API = Provider(
    id="openai_api",
    label="OpenAI API key",
    auth_mode=AUTH_API_KEY,
    env_var="OPENAI_API_KEY",
    multi_user=True,
    restriction="",
    console_url="https://platform.openai.com/api-keys",
    runtime_ready=False,
    key_prefix=("sk-",),
)

# THE PLATFORM DEFAULT TIER PLUGS IN HERE, and nothing else has to move.
#
# The operator's plan has a later tier where somebody can start using the
# product without holding any credential of their own, either free or at a
# higher price, backed by this platform's own commercial API account. That is
# permitted: Anthropic's Commercial Terms §A.1 gives permission to use the
# Services "to power products and services Customer makes available to its own
# customers and end users". The boundary is §D.4, which forbids reselling the
# Services as such: selling our product with a model inside it is fine, selling
# metered model access is not.
#
# It is deliberately NOT defined yet, because the provider entry is the small
# part. The feature is the per-principal quota and the metering behind it, and
# adding a Provider now would claim a capability that has no accounting behind
# it. When it is built it is one entry with auth_mode=AUTH_PLATFORM, whose
# credential resolves from the deployment's own account rather than the
# principal's, plus a quota check in `model_credentials.resolve`.
PLATFORM_DEFAULT_TIER_GOES_HERE = None

_ALL = (CLAUDE_CLI, ANTHROPIC_API, OPENAI_API)
BY_ID = {p.id: p for p in _ALL}

# What a fresh single-operator install runs on: the CLI it already has.
DEFAULT_PROVIDER_ID = CLAUDE_CLI.id


def get(provider_id: str) -> Provider:
    """The provider, or ValueError. Unknown is an error, never a fallback:
    falling back to a default would mean a mistyped id silently routing to the
    subscription, which is the one outcome this module exists to prevent."""
    try:
        return BY_ID[provider_id]
    except KeyError:
        raise ValueError(
            f"unknown model provider: {provider_id!r} "
            f"(known: {', '.join(sorted(BY_ID))})") from None


def all_providers() -> list[Provider]:
    return list(_ALL)


def multi_user_providers() -> list[Provider]:
    """The providers a hosted deployment may offer its users."""
    return [p for p in _ALL if p.multi_user]


def is_subscription(provider_id: str) -> bool:
    return get(provider_id).auth_mode == AUTH_SUBSCRIPTION


def looks_like_key(provider_id: str, value: str) -> bool:
    """A shape check on a pasted credential, not a validity check.

    Catches pasting an Anthropic key into the OpenAI field, or pasting a URL.
    A provider that declares no prefix accepts anything non-empty: guessing at
    an unknown format would reject valid credentials, which is worse than
    letting the verify step do its job.
    """
    provider = get(provider_id)
    value = (value or "").strip()
    if not value:
        return False
    if not provider.key_prefix:
        return True
    return any(value.startswith(p) for p in provider.key_prefix)


def describe(provider: Provider) -> dict:
    """The provider as an API payload. Carries no credential and never will."""
    return {
        "id": provider.id,
        "label": provider.label,
        "auth_mode": provider.auth_mode,
        "multi_user": provider.multi_user,
        "restriction": provider.restriction,
        "console_url": provider.console_url,
        "runtime_ready": provider.runtime_ready,
    }
