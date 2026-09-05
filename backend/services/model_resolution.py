"""Which model runs this turn, decided ONCE, in one documented order.

THERE IS EXACTLY ONE RESOLVER AND IT IS `resolve_model`. Before this module the
answer to "which model ran" depended on which caller you asked: `routes/chat.py`
had a three-rung chain for the composer, `_start_agent_mention_turn` had a
different one for an @mentioned agent, `_agent_effort` had a third for effort,
and `voice_worker` had a fourth expression of the same idea. Four chains means
four places for the answer to drift, and the same discipline already applies to
tools (`llm_router.tool_flags()` is the single enforcement point there).

THE ORDER, most specific first. Each rung is named on the way out, in
`ModelChoice.source`, so an operator can see WHY a turn ran where it ran:

  1 SOURCE_REQUEST     the explicit ask — a composer click, an API `model`
  2 SOURCE_PIN         the thread's stored model, or a Job's recorded choice
  3 SOURCE_AGENT       the agent definition's own `model:`
  4 SOURCE_SCOPE       the app or project default, declared by that scope
  5 SOURCE_PRINCIPAL   "which model do my chats use", the operator's own default
  6 SOURCE_DEPLOYMENT  what this installation runs when nobody said

TOLERANCE IS ASYMMETRIC AND THAT IS DELIBERATE. An invalid value at rung 1 is a
400, because somebody is looking at the control that produced it. An invalid
value at any LOWER rung is skipped with a named reason and the next rung is
consulted, because a stored pin the operator cannot see must never fail a turn.
That is what lets a local model id vanish between restarts — a model server
restarted with a different weight loaded — and the turn still answer, on the
deployment default, saying so.

EFFORT RESOLVES ONLY AFTER THE MODEL IS SETTLED, and permission mode alongside
it. Which effort rungs exist is a fact about the model (`model_caps
.EFFORT_BY_MODEL`), so validating an effort against a model that has not been
chosen yet is checking it against the wrong thing. Neither chain's ORDER changed
when it moved here; only the point at which it runs did.

Specified by `docs/platform-redesign/providers-credentials-and-limits-2026-08-23
.md` section 6.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field

from services import llm_providers, model_caps

log = logging.getLogger("workshop.model_resolution")

# The six rungs, most specific first. A `ModelChoice.source` is always one of
# these, and `notes` says what was skipped to get there.
SOURCE_REQUEST = "request"
SOURCE_PIN = "pin"
SOURCE_AGENT = "agent"
SOURCE_SCOPE = "scope"
SOURCE_PRINCIPAL = "principal"
SOURCE_DEPLOYMENT = "deployment"

SOURCES: tuple[str, ...] = (
    SOURCE_REQUEST, SOURCE_PIN, SOURCE_AGENT,
    SOURCE_SCOPE, SOURCE_PRINCIPAL, SOURCE_DEPLOYMENT,
)


class UnknownModel(ValueError):
    """An EXPLICIT model this deployment cannot run. A 400 at the route.

    Only ever raised for rung 1. Every lower rung absorbs the same condition,
    because the person did not type it and cannot see it.
    """


@dataclass(frozen=True)
class ModelRequest:
    """Everything the resolver reads, named by the rung it belongs to.

    Nothing here is looked up FOR the caller: a route already holds the thread
    document and the agent record, and having the resolver re-read them would
    make it a second store reader with its own idea of freshness. The two rungs
    it does read itself are the ones with no caller-side representation — the
    principal's default and the deployment's.
    """

    # Rung 1. The explicit ask. Invalid here is a 400.
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    # Rung 2. The thread's stored model, or a Job's recorded choice.
    pinned_model: str | None = None
    pinned_effort: str | None = None
    pinned_permission_mode: str | None = None
    # Rung 3. The agent definition. Present only on an agent's own turn.
    agent_slug: str | None = None
    agent_model: str | None = None
    agent_effort: str | None = None
    agent_permission_mode: str | None = None
    # Rung 4. The scope's DECLARED default, None when it merely inherits.
    scope_model: str | None = None
    # Does this turn need the agentic harness (tools, permission modes)? A
    # provider whose transport cannot supply one must not be handed such a turn;
    # see `_runnable`.
    agentic: bool = True

    @classmethod
    def from_thread(cls, thread: dict, *, model: str | None = None,
                    effort: str | None = None, permission_mode: str | None = None,
                    scope_model: str | None = None, agentic: bool = True,
                    ) -> "ModelRequest":
        """The composer's request: an explicit click over the thread's pins."""
        thread = thread or {}
        return cls(
            model=model,
            effort=effort,
            permission_mode=permission_mode,
            pinned_model=thread.get("model"),
            pinned_effort=thread.get("effort"),
            pinned_permission_mode=thread.get("permission_mode"),
            scope_model=scope_model,
            agentic=agentic,
        )

    @classmethod
    def for_agent(cls, slug: str, record: dict, *, chat_model: str | None,
                  chat_effort: str | None = None,
                  chat_permission_mode: str | None = None,
                  agentic: bool = True) -> "ModelRequest":
        """The request for a turn an @mentioned agent answers.

        RUNG 2 IS EMPTY HERE AND THAT IS THE POINT. `thread.model` is the CHAT
        ASSISTANT's pin, not this agent's — the agent answers with its own
        definition, in its own session inside the thread. So the definition
        (rung 3) is the most specific thing that exists for this turn, and the
        chat's ALREADY-SETTLED model drops to rung 4, which is what it is: the
        answer this scope gives when the agent says nothing. Nothing is
        reordered to arrange that; the thread's pin was consumed one level up,
        in producing `chat_model`.

        This is what makes an agent declared to run on a cheap tier keep running
        on it inside a chat set to an expensive one, and vice versa. That
        silently depended on a dropdown about the chat's assistant before the
        declaration existed.
        """
        record = record or {}
        return cls(
            agent_slug=slug,
            agent_model=record.get("model"),
            agent_effort=record.get("effort"),
            agent_permission_mode=record.get("permission_mode"),
            scope_model=chat_model,
            pinned_effort=chat_effort,
            pinned_permission_mode=chat_permission_mode,
            agentic=agentic,
        )


@dataclass(frozen=True)
class ModelChoice:
    """The settled answer, and why.

    `notes` is the seventh field and it is the point of the sixth. `source`
    names the rung that WON; without a place to say what was skipped, a turn
    that quietly dropped a vanished pin and ran on the deployment default is
    indistinguishable from one that never had a pin. The ticket asks for a
    NAMED reason; this is where the name goes.
    """

    provider_id: str
    model_id: str
    wire: str
    effort: str | None
    permission_mode: str
    source: str
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "wire": self.wire,
            "effort": self.effort,
            "permission_mode": self.permission_mode,
            "source": self.source,
            "notes": list(self.notes),
        }


# ------------------------------------------------------------------ the model


@dataclass
class _Trace:
    """What was skipped on the way down the ladder."""

    notes: list[str] = field(default_factory=list)

    def skipped(self, rung: str, value: str, why: str) -> None:
        self.notes.append(f"{rung} {value!r} was not used: {why}")
        log.info("model resolution: %s %r skipped (%s)", rung, value, why)


def _lookup(value: str | None) -> llm_providers.ModelInfo | None:
    """A model id or short tier alias, resolved against the CONFIGURED catalog.

    This is the lookup that replaced `chat_context.normalize_model`'s hardcoded
    list. It returns None rather than raising: whether an unknown value is an
    error depends on which rung asked, and only the caller knows that.
    """
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    from services import llm_router

    # A DECLARATION may name a tier ("sonnet"), because a file the operator
    # hand-edits should keep working when the handle behind a tier rotates.
    candidate = llm_router.TIERS.get(raw.lower(), raw)
    return llm_providers.find(candidate)


def _runnable(info: llm_providers.ModelInfo, request: ModelRequest) -> str:
    """"" if this model can serve this turn, else why not.

    The one condition today is the agentic harness: tools and permission modes
    on this platform are CLI FLAGS, so a turn that needs them cannot run on a
    provider whose transport is plain HTTP. Stated as a capability question
    rather than a provider-id check, so a second CLI-transport provider would
    need no edit here.
    """
    if not request.agentic:
        return ""
    try:
        provider = llm_providers.get(info.provider_id)
    except ValueError:
        return f"provider {info.provider_id!r} is not configured"
    if provider.transport != llm_providers.TRANSPORT_CLI:
        return (f"provider {info.provider_id!r} has no tool harness, and this "
                "turn needs one")
    return ""


def _model(request: ModelRequest, principal_default: str | None,
           trace: _Trace) -> tuple[llm_providers.ModelInfo, str]:
    """Walk the six rungs. Returns (model, the rung that won)."""
    # Rung 1. The explicit ask. The ONLY rung that raises.
    if request.model and str(request.model).strip():
        info = _lookup(request.model)
        if info is None:
            raise UnknownModel(
                f"unknown model {request.model!r}; pick one of "
                f"{llm_providers.model_ids()}")
        why = _runnable(info, request)
        if why:
            raise UnknownModel(f"model {info.id!r} cannot run this turn: {why}")
        return info, SOURCE_REQUEST

    ladder = (
        (SOURCE_PIN, request.pinned_model),
        (SOURCE_AGENT, request.agent_model),
        (SOURCE_SCOPE, request.scope_model),
        (SOURCE_PRINCIPAL, principal_default),
    )
    for rung, value in ladder:
        if not value or not str(value).strip():
            continue
        info = _lookup(value)
        if info is None:
            trace.skipped(rung, str(value), "this deployment cannot reach it")
            continue
        why = _runnable(info, request)
        if why:
            trace.skipped(rung, str(value), why)
            continue
        return info, rung

    # Rung 6. Always answers, so a turn never fails for want of a model.
    return llm_providers.deployment_default(), SOURCE_DEPLOYMENT


# ----------------------------------------------------------------- the effort


def _effort(request: ModelRequest, model_id: str, principal_default: str | None,
            trace: _Trace) -> str | None:
    """The effort level, resolved against the model that will ACTUALLY run.

    Same order as before this moved: the agent's declaration, then the explicit
    ask, then the thread's pin, then the operator's default, then None (which
    means "send no --effort at all" — the model's own default, and byte
    identical to the argv an untouched chat has always spawned).

    Two rules about a level the settled model has no rung for, both preserved:
    from the explicit ask it drops to None, full stop, because that is the live
    click and it wins even when winning means falling back; from a STORED tier
    it is skipped and the next rung down is consulted, because a thread carrying
    `xhigh` from when it was on Opus should not blank out an operator default of
    `max` just because this model has no `xhigh` rung.
    """
    if request.agent_effort:
        try:
            level = model_caps.normalize_effort(request.agent_effort, model_id)
        except ValueError:
            trace.skipped(SOURCE_AGENT, str(request.agent_effort),
                          "that is not an effort level")
            level = None
        if level:
            return level

    # The explicit ask. Raises for a value that is not a level at all, which is
    # a 400 at the route; a level this model lacks is a silent None.
    explicit = model_caps.normalize_effort(request.effort, model_id)
    if explicit:
        return explicit
    if (request.effort or "").strip():
        return None

    for rung, candidate in ((SOURCE_PIN, request.pinned_effort),
                            (SOURCE_PRINCIPAL, principal_default)):
        if not candidate:
            continue
        try:
            level = model_caps.normalize_effort(candidate, model_id)
        except ValueError:
            trace.skipped(rung, str(candidate), "that is not an effort level")
            continue
        if level:
            return level
    return None


def _permission_mode(request: ModelRequest, principal_default: str | None) -> str:
    """The permission mode. Order unchanged: agent, explicit ask, pin, operator
    default, platform default.

    Always returns a mode (never None): unlike effort there is no "let the
    vendor decide" here — the CLI has its own default and it is not the one this
    system runs at, so the value is made explicit at every spawn.
    """
    # An agent's declaration is tolerated exactly like its model is: nothing in
    # a file the operator may not have written gets to fail a turn.
    try:
        declared = model_caps.normalize_permission_mode(request.agent_permission_mode)
    except ValueError:
        log.warning("model resolution: agent declares permission mode %r, which "
                    "is not a mode; ignoring", request.agent_permission_mode)
        declared = None
    if declared:
        return declared
    # The explicit ask raises on an unknown mode, which is a 400 at the route.
    explicit = model_caps.normalize_permission_mode(request.permission_mode)
    if explicit:
        return explicit
    try:
        stored = model_caps.normalize_permission_mode(request.pinned_permission_mode)
    except ValueError:
        stored = None
    return stored or principal_default or model_caps.DEFAULT_PERMISSION_MODE


# ------------------------------------------------------------------- the seam


def _principal_defaults(principal_id: int | None) -> tuple[str | None, str | None, str | None]:
    """Rung 5, read once: the operator's own model, effort and mode defaults.

    Reads through `chat_store`, which is already owner-partitioned and already
    treats an unreadable prefs file as "no preference" rather than as an error.
    `principal_id` is accepted and not yet used as a selector because the store
    resolves the pinned owner itself; naming it in the signature is what makes
    the multi-user shape a change to one function instead of to every caller.
    """
    from services import chat_store

    del principal_id  # the store is already owner-scoped; see the docstring
    return (chat_store.get_default_model(),
            chat_store.get_default_effort(),
            chat_store.get_default_permission_mode())


def resolve_model(principal_id: int | None, scope: str | None,
                  request: ModelRequest) -> ModelChoice:
    """THE resolver. Chats, agents and background runs all settle here.

    `scope` is the chat's context key ("general", "project:x", "agent:x"). It is
    read for one thing only: rung 4, the scope's DECLARED default model. A
    caller that already holds the scope provider passes `request.scope_model`
    instead and this argument is ignored.
    """
    trace = _Trace()
    if request.scope_model is None and scope:
        request = dataclasses.replace(request, scope_model=declared_model_for(scope))
    pref_model, pref_effort, pref_mode = _principal_defaults(principal_id)
    info, source = _model(request, pref_model, trace)
    return ModelChoice(
        provider_id=info.provider_id,
        model_id=info.id,
        wire=info.wire,
        effort=_effort(request, info.id, pref_effort, trace),
        permission_mode=_permission_mode(request, pref_mode),
        source=source,
        notes=tuple(trace.notes),
    )


def declared_model_for(scope: str | None) -> str | None:
    """Rung 4: the model this app or project DECLARES, or None.

    None is the interesting value. A scope that declares nothing must fall
    through to the principal's default, so "the class attribute every provider
    inherits" cannot be allowed to answer here — that constant is the DEPLOYMENT
    default (rung 6) wearing a scope's clothes, and treating it as a declaration
    is what would make rung 5 unreachable.
    """
    from services import chat_context

    try:
        provider = chat_context.get_provider(scope)
    except Exception as e:  # pragma: no cover - get_provider is total by contract
        log.warning("model resolution: no provider for scope %r (%s)", scope, e)
        return None
    return provider.declared_model
