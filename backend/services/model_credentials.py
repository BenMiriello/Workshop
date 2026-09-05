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

WHERE THE FILE LIVES. A principal's credentials are one JSON store at
`.runtime/users/<id>/credentials.json`, reached through
`ownership.user_runtime_dir()` like every other per-owner store in this tree.
They used to sit in the FLAT `.runtime/secrets/` directory alongside per-project
app secrets under the file name `principal:<id>.json`, which is one slug
collision away from a project reading a person's key. `migrate_legacy_files()`
moves them and is safe to run repeatedly.

The ENCRYPTION SLUG did not move with the file, deliberately. `principal:<id>`
is bound into both layers of the envelope as additional authenticated data
(`secrets_crypto.encrypt`), so it is the thing that stops one principal's file
being renamed over another's. Keeping it means an already-encrypted store still
authenticates after the move; changing it would have made every stored key
unreadable at exactly the moment the operator could least afford to be told to
paste them again.

THE SUBSCRIPTION TOKEN IS A CREDENTIAL LIKE ANY OTHER, and it is settable. It
used to be readable only from the deployment's auth env file, which meant the
one credential every turn depends on was the one credential the product could
not configure: changing it was an edit to a dotfile the app never mentions. It
now resolves from the owner's own store FIRST and falls back to that file, so a
deployment that has never used this surface behaves exactly as before and one
that has does not need the file at all.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from services import model_providers, ownership, secrets_crypto, secrets_store

log = logging.getLogger("workshop.model_credentials")

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


def multi_user_is_declared() -> bool:
    """Did somebody WRITE `multi_user`, as opposed to writing a typo?

    `deployment_mode()` fails closed, so an unrecognised value resolves to
    multi-user, and that is right for every question about whose credential may
    serve whose turn: the cost of guessing wrong there is somebody's account.

    It is NOT right for a question whose wrong answer is a backend that will not
    start. A typo in one config line must not be able to lock the operator out
    of a single-operator installation, so the boot check asks this narrower
    question instead. The two differ only for a value nobody meant to write.
    """
    return (os.environ.get(MODE_ENV) or "").strip().lower() == MODE_MULTI_USER


def _slug(principal_id: int) -> str:
    """The ENCRYPTION identity of a principal's store, not its file name.

    Bound in as AAD on both envelope layers, so it must stay stable across a
    move of the file. See the module docstring.
    """
    return f"{_SLUG_PREFIX}:{int(principal_id)}"


def _key_name(provider_id: str) -> str:
    return f"{provider_id}.credential"


# The store's file name inside the owner's runtime partition.
CREDENTIALS_FILE = "credentials.json"


def credentials_path(principal_id: int) -> Path:
    """`.runtime/users/<id>/credentials.json`, the owner-partitioned store."""
    return ownership.user_runtime_dir(int(principal_id)) / CREDENTIALS_FILE


def _legacy_path(principal_id: int) -> Path:
    """Where this principal's store used to live: the flat secrets directory."""
    return secrets_store._path(_slug(principal_id))


def _adopt_legacy(principal_id: int) -> None:
    """Move one principal's store into the owner partition, if it is still flat.

    Called before every read and write rather than only at boot, because a
    process that was not the one that booted (a CLI, a test, a worker) must not
    see an empty store just because nobody ran a migration in ITS lifetime. The
    move is a rename, so it is atomic, idempotent, and carries an encrypted file
    intact: the AAD is the slug, which does not change.
    """
    legacy = _legacy_path(principal_id)
    if not legacy.exists():
        return
    target = credentials_path(principal_id)
    if target.exists():
        # Both present. The partitioned one is authoritative; the flat one is a
        # leftover from a rollback and is not merged, because merging two
        # versions of a secret silently resurrects a key the operator removed.
        log.warning("model credentials: leaving a legacy store in place for "
                    "principal %s because a partitioned one already exists",
                    principal_id)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(legacy, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def migrate_legacy_files() -> int:
    """Move every flat `principal:<id>.json` store into its owner partition.

    Returns how many moved, so a caller can say so. Never raises on a store it
    cannot place: one unreadable file must not stop a backend from starting.
    """
    root = secrets_store._root()
    if not root.is_dir():
        return 0
    moved = 0
    for path in sorted(root.glob(f"{_SLUG_PREFIX}:*.json")):
        raw = path.stem.split(":", 1)[1]
        try:
            principal_id = int(raw)
        except ValueError:
            log.warning("model credentials: skipping %s, not a principal id",
                        path.name)
            continue
        try:
            _adopt_legacy(principal_id)
        except OSError as e:
            log.warning("model credentials: could not move the store for "
                        "principal %s: %s", principal_id, e)
            continue
        if not path.exists():
            moved += 1
    return moved


def _load(principal_id: int) -> dict[str, str]:
    _adopt_legacy(principal_id)
    return secrets_store.load_at(credentials_path(principal_id),
                                 _slug(principal_id))


def require_encryption_in_multi_user() -> None:
    """Refuse to run a hosted deployment whose credential store is plaintext.

    A pile of other people's API keys at mode 0600 is the correct amount of
    protection for one person's own box and no protection at all for a hosted
    one: the file lands in every backup, snapshot and disk image the host makes.
    `secrets_crypto` has been built and dormant since 2026-08-11 precisely for
    this, and leaving it opt-in means it is off.

    IT CAN ONLY REFUSE A DEPLOYMENT THAT ASKED TO BE HOSTED. This is called from
    the boot path, so raising means the backend does not come up, and on a
    single-operator installation there is no worse outcome than that. It
    therefore keys on `multi_user_is_declared()` rather than `is_multi_user()`:
    an unset or mistyped `WORKSHOP_DEPLOYMENT_MODE` cannot reach the raise. The
    fail-closed reading still governs every credential decision, where refusing
    costs a turn instead of the whole product. Single-operator mode gains no new
    requirement and cannot be given one by a typo.
    """
    if multi_user_is_declared() and not secrets_crypto.enabled():
        raise RuntimeError(
            f"this deployment is in {MODE_MULTI_USER} mode, so the credential "
            f"store may not be plaintext. Set {secrets_crypto.KEK_ENV} to a 32 "
            "byte key (base64 or hex) and restart. Generate one with: python -c "
            "\"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\"")


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
    ok, why = permitted(provider_id)
    if not ok:
        raise ProviderNotPermitted(why)
    value = (value or "").strip()
    if not value:
        raise ValueError("refusing to store an empty credential")
    _adopt_legacy(principal_id)
    secrets_store.set_values_at(credentials_path(principal_id),
                                _slug(principal_id),
                                {_key_name(provider_id): value})


def key_tail(principal_id: int, provider_id: str) -> str | None:
    """The last 4 characters of a stored key, for UI masking. Never more."""
    val = _load(principal_id).get(_key_name(provider_id)) or ""
    return val[-4:] if len(val) >= 4 else None


def clear_credential(principal_id: int, provider_id: str) -> None:
    _adopt_legacy(principal_id)
    secrets_store.delete_key_at(credentials_path(principal_id),
                                _slug(principal_id), _key_name(provider_id))


def has_credential(principal_id: int, provider_id: str) -> bool:
    """Presence only. The value never comes back out through a status path."""
    return bool(_load(principal_id).get(_key_name(provider_id)))


def status(principal_id: int) -> list[dict]:
    """What a settings screen needs: every offerable provider, whether a
    credential is present, and whether a turn can actually run on it.

    THREE BOOLEANS, NOT ONE, because they answer different questions and the
    single one this used to return conflated two of them. `ready` was `stored`
    for an API key provider, so a key the runtime cannot spend
    (`runtime_ready=False` on both API providers today) displayed exactly like
    the subscription that serves every turn. A settings screen that says a
    provider is ready when nothing routes to it is the same defect as a status
    check that cannot fail.

      stored      this principal supplied a credential, and it is in their store
      configured  a credential resolves at all: stored, or, for the operator's
                  own subscription, the deployment's auth file
      usable      configured AND this deployment can execute a turn on it

    `source` says which of the two a configured credential came from, because
    "I pasted a token and it still says it is reading the file" is otherwise
    unanswerable. Presence and provenance only, never values.
    """
    out = []
    for payload in available_providers():
        pid = payload["id"]
        provider = model_providers.get(pid)
        stored = has_credential(principal_id, pid)
        source = "principal" if stored else None
        if not stored and provider.auth_mode == model_providers.AUTH_SUBSCRIPTION:
            if _deployment_subscription(provider):
                source = "deployment"
        configured = source is not None
        out.append({**payload, "stored": stored, "configured": configured,
                    "usable": configured and provider.runtime_ready,
                    "source": source})
    return out


def _deployment_subscription(provider: model_providers.Provider) -> str:
    """The subscription token from the deployment's auth env file.

    The FALLBACK, not the source of truth: a token the principal stored through
    the app wins over it (see `subscription_token`). Kept because a deployment
    that has always used the file must keep working with no action from anyone,
    and because a CLI already signed in through its own credentials needs
    neither.
    """
    from services import auth_env

    return auth_env.value(provider.env_var)


def subscription_token(principal_id: int) -> str:
    """This principal's subscription token, or "" — store first, then the file.

    THE ONE ORDER, stated once. The store wins because it is the thing the
    operator can change from the app: if the file won, pasting a new token would
    appear to succeed and change nothing, which is the failure mode this whole
    surface exists to remove. Never logs the value; a caller that needs a reason
    for an empty answer uses `resolve()`.

    An OSError is caught and the file is used instead, because a runtime
    directory that cannot be created (a read-only mount) must not take every
    turn down. A `SecretsKeyError` is NOT caught: an encrypted store this
    deployment cannot decrypt is a broken deployment, and reading it as "no
    credential" is how the operator would be told to paste a token he already
    supplied.
    """
    provider = model_providers.get(model_providers.DEFAULT_PROVIDER_ID)
    try:
        stored = _load(principal_id).get(_key_name(provider.id)) or ""
    except OSError as e:
        log.warning("model credentials: credential store unreachable for "
                    "principal %s (%s); using the deployment auth file",
                    principal_id, e.__class__.__name__)
        stored = ""
    return stored or _deployment_subscription(provider)


def current_principal() -> int | None:
    """Whose credential a call with no principal argument should spend.

    The pinned data owner when there is one, and otherwise the operator — the
    same rule `ownership.workspace_path` already uses for a store with no
    request behind it, and the reason it is safe is arithmetic: single-operator
    mode HAS exactly one principal. In multi-user mode an unpinned call gets
    None instead, never the operator, because "whoever set this box up" is the
    fallback that would serve one person's subscription to another.
    """
    pinned = ownership.owner_or_none()
    if pinned is not None:
        return int(pinned)
    if is_multi_user():
        return None
    from services import auth_store

    return auth_store.operator_id()


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

    stored = _load(principal_id).get(_key_name(provider_id)) or ""

    if provider.auth_mode == model_providers.AUTH_SUBSCRIPTION:
        # Reachable only in single-operator mode (permitted() blocks it
        # otherwise), where there is exactly one principal and the deployment's
        # own token is theirs. Same order as `subscription_token`: what the
        # operator set through the app, then the deployment's auth file.
        if stored:
            return Credential(provider_id=provider.id, env_var=provider.env_var,
                              value=stored, principal_id=int(principal_id),
                              origin="principal")
        value = _deployment_subscription(provider)
        if not value:
            raise CredentialMissing(
                f"no {provider.label} credential is configured. Add one in "
                "Settings, Providers, or sign in with `claude auth login`.")
        return Credential(provider_id=provider.id, env_var=provider.env_var,
                          value=value, principal_id=int(principal_id),
                          origin="deployment")

    value = stored
    if not value:
        raise CredentialMissing(
            f"no {provider.label} has been supplied for this account. "
            f"Add one from {provider.console_url}.")
    return Credential(provider_id=provider.id, env_var=provider.env_var,
                      value=value, principal_id=int(principal_id),
                      origin="principal")
