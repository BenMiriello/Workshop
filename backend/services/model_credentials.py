"""Which credential serves THIS principal's turn, and whether that is allowed.

Today one global `CLAUDE_CODE_OAUTH_TOKEN` is read from one file and every turn
in the process uses it. On a one-person box that is correct and this module
changes nothing about it. In a hosted deployment that single fact IS the
licensing violation: the second user's turn would run on the first user's
subscription, which Anthropic prohibits and blocks. See
`services/model_providers` for the sourced rule and
`docs/platform-redesign/model-access-and-byok-2026-08-11.md` for the plan.

So the seam is: a turn does not have a credential. A PRINCIPAL has a credential,
and a turn gets one by asking on behalf of a named principal. Once that is true
the prohibited configuration is unreachable rather than something we remember
not to build.

MODELLED ON `services/ownership`, deliberately and for the same reason. That
module pins the data owner per request and RAISES when it is unset rather than
defaulting to the operator, so a forgotten set-point is a crash and never a
cross-owner leak. Credentials get the same treatment: in a multi-user
deployment, asking for a subscription credential raises. Fail closed, because
the failure mode here is not a bug report, it is somebody's account.

DEPLOYMENT MODE IS DECLARED, NOT INFERRED. There is no way to look at a running
process and tell whether a second user is about to sign up, and guessing wrong
in the permissive direction is exactly the mistake worth preventing. So the mode
is a config value with the safe behaviour attached to it.

NOTHING IS SWITCHED OVER YET. In `single_operator` mode `resolve()` returns
exactly what the system already uses, from the same file, so this module is
inert until stage 4 of the plan wires the runtime to it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from services import model_providers

# One person, their own machine, their own subscription. Today's system.
MODE_SINGLE_OPERATOR = "single_operator"
# Many people, hosted, each with their own credentials. The target.
MODE_MULTI_USER = "multi_user"

MODES = (MODE_SINGLE_OPERATOR, MODE_MULTI_USER)

MODE_ENV = "WORKSHOP_DEPLOYMENT_MODE"

# Where a principal's own credentials live in `secrets_store`.
_SLUG_PREFIX = "principal"


class CredentialError(RuntimeError):
    """Base for the two things that can go wrong asking for a credential."""


class ProviderNotPermitted(CredentialError):
    """This provider may not serve this principal in this deployment mode.

    A licensing refusal, not a permission system. The message is written to be
    shown to the person who asked, because the fix is theirs: bring an API key.
    """


class CredentialMissing(CredentialError):
    """The provider is allowed here; this principal has not supplied one."""


@dataclass(frozen=True)
class Credential:
    """A resolved credential, and where it came from.

    Carries the secret. It is never logged, never returned through an API, and
    never put in a subprocess's argv (argv is world readable through /proc);
    `as_env()` exists so the only way to spend it is the correct way.
    """

    provider_id: str
    env_var: str
    value: str
    principal_id: int
    # "principal" (this user supplied it) or "deployment" (the operator's own
    # file, single-operator mode only). Useful to a settings screen, and the
    # thing an audit of "whose key paid for this" would need.
    origin: str

    def as_env(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """An environment carrying this credential and no other provider's.

        Other providers' variables are STRIPPED rather than left alone. A
        process that inherits both an inactive subscription token and the API
        key it is supposed to use will, depending on the tool, quietly prefer
        the wrong one. That is how a hosted deployment would end up billing the
        operator's subscription for a stranger's turn without anything looking
        broken.
        """
        env = dict(os.environ if base is None else base)
        for provider in model_providers.all_providers():
            env.pop(provider.env_var, None)
        env[self.env_var] = self.value
        return env

    def __repr__(self) -> str:  # pragma: no cover - trivial, but load bearing
        return (f"Credential(provider_id={self.provider_id!r}, "
                f"principal_id={self.principal_id!r}, origin={self.origin!r}, "
                "value=<redacted>)")


def deployment_mode() -> str:
    """This deployment's mode. Unknown values fail closed to multi-user.

    A typo in the config resolving to the permissive mode is precisely the
    accident worth not having, so an unrecognised value is treated as the strict
    one rather than as the default.
    """
    raw = (os.environ.get(MODE_ENV) or "").strip().lower()
    if not raw:
        return MODE_SINGLE_OPERATOR
    if raw not in MODES:
        return MODE_MULTI_USER
    return raw


def is_multi_user() -> bool:
    return deployment_mode() == MODE_MULTI_USER


def _slug(principal_id: int) -> str:
    return f"{_SLUG_PREFIX}:{int(principal_id)}"


def _key_name(provider_id: str) -> str:
    return f"{provider_id}.credential"


def permitted(provider_id: str) -> tuple[bool, str]:
    """May this provider serve a principal here? `(ok, why not)`."""
    provider = model_providers.get(provider_id)
    if provider.multi_user or not is_multi_user():
        return True, ""
    return False, provider.restriction


def available_providers() -> list[dict]:
    """The providers offerable in this deployment, as API payloads.

    In multi-user mode the subscription provider is absent rather than shown
    disabled: offering a control that cannot legally work invites someone to ask
    for it to be enabled.
    """
    providers = (model_providers.multi_user_providers() if is_multi_user()
                 else model_providers.all_providers())
    return [model_providers.describe(p) for p in providers]


def set_credential(principal_id: int, provider_id: str, value: str) -> None:
    """Store a principal's own credential for a provider.

    Refuses a provider that may not serve anyone here, so a credential for a
    prohibited path cannot even be parked for later.
    """
    from services import secrets_store

    ok, why = permitted(provider_id)
    if not ok:
        raise ProviderNotPermitted(why)
    value = (value or "").strip()
    if not value:
        raise ValueError("refusing to store an empty credential")
    secrets_store.set_values(_slug(principal_id), {_key_name(provider_id): value})


def clear_credential(principal_id: int, provider_id: str) -> None:
    from services import secrets_store

    secrets_store.delete_key(_slug(principal_id), _key_name(provider_id))


def has_credential(principal_id: int, provider_id: str) -> bool:
    """Presence only. The value never comes back out through a status path."""
    from services import secrets_store

    return bool((secrets_store.load(_slug(principal_id)) or {}).get(
        _key_name(provider_id)))


def status(principal_id: int) -> list[dict]:
    """What a settings screen needs: every offerable provider and whether this
    principal can currently use it. Presence booleans, never values."""
    out = []
    for payload in available_providers():
        pid = payload["id"]
        provider = model_providers.get(pid)
        stored = has_credential(principal_id, pid)
        if provider.auth_mode == model_providers.AUTH_SUBSCRIPTION:
            # The operator's subscription lives in the deployment's auth file,
            # not in a per-principal store, so presence means something else.
            ready = bool(_deployment_subscription(provider))
        else:
            ready = stored
        out.append({**payload, "stored": stored, "ready": ready})
    return out


def _deployment_subscription(provider: model_providers.Provider) -> str:
    """The operator's own subscription token, from the deployment's auth file.

    Single-operator only. This is the existing behaviour of the running system,
    reached through one loader instead of read directly, so that stage 4 can
    move the call site without moving the semantics.
    """
    from services import auth_env

    return auth_env.value(provider.env_var)


def resolve(principal_id: int, provider_id: str | None = None) -> Credential:
    """The credential that may serve `principal_id` on `provider_id`.

    Raises `ProviderNotPermitted` when the combination is not allowed here, and
    `CredentialMissing` when it is allowed but nothing has been supplied.
    Never returns a credential belonging to a different principal, and never
    falls back to one: a fallback is how one person's subscription would end up
    paying for another person's work.
    """
    provider_id = provider_id or model_providers.DEFAULT_PROVIDER_ID
    provider = model_providers.get(provider_id)

    ok, why = permitted(provider_id)
    if not ok:
        raise ProviderNotPermitted(why)

    if provider.auth_mode == model_providers.AUTH_SUBSCRIPTION:
        # Reachable only in single-operator mode (permitted() blocks it
        # otherwise), where there is exactly one principal and the deployment's
        # own token is theirs.
        value = _deployment_subscription(provider)
        if not value:
            raise CredentialMissing(
                f"no {provider.label} credential is configured. Sign in with "
                "`claude auth login`, or set "
                f"{provider.env_var} in the auth env file.")
        return Credential(provider_id=provider.id, env_var=provider.env_var,
                          value=value, principal_id=int(principal_id),
                          origin="deployment")

    from services import secrets_store

    value = (secrets_store.load(_slug(principal_id)) or {}).get(
        _key_name(provider_id)) or ""
    if not value:
        raise CredentialMissing(
            f"no {provider.label} has been supplied for this account. "
            f"Add one from {provider.console_url}.")
    return Credential(provider_id=provider.id, env_var=provider.env_var,
                      value=value, principal_id=int(principal_id),
                      origin="principal")
