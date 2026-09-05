"""The seam a model call crosses: which providers exist, what each can run.

WHY THIS IS SEPARATE FROM `services/model_providers`. That module answers a
LICENSING question — may this credential serve this person — and it is data, not
runtime. This one answers the RUNTIME question: how is the call actually made,
what models can it make it against, and what does each of those models support.
The two are different axes and were conflated for as long as there was exactly
one way to reach a model.

`transport` IS A FIRST-CLASS AXIS AND NOT A BASE URL. The provider serving every
turn today is a SUBPROCESS: tools, permission modes and effort are CLI FLAGS
built by `llm_router.tool_flags()` and `build_streaming._build_cmd`, not API
fields. Anything that models it as "an Anthropic endpoint with a different base
URL" discovers that at the worst moment, so there are exactly two transports and
the CLI is one of them. `wire` is the orthogonal axis: which request/response
grammar the transport speaks.

NOTHING INFERS A CAPABILITY FROM THE WIRE FORMAT. A `ModelInfo` carries an
explicit `Capabilities` record, populated from the catalog for a cloud provider
and from a PROBE for a local one, because two servers speaking the same wire
disagree about cache control, token counting and tool choice. A wire name is not
a capability claim.

THE CATALOG IS COMPOSED, NEVER RESTATED. The Claude model ids come from
`llm_router.TIERS`, the effort ladders from `services/model_caps`, the context
windows from `services/context_window` and the prices from
`services/model_prices`. This module owns none of those facts; it joins them, so
adding a model stays a one-line edit in the file that already owns that fact.

Reading order for the design: `docs/platform-redesign/
providers-credentials-and-limits-2026-08-23.md` sections 4.1 to 4.3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from services import model_providers

log = logging.getLogger("workshop.llm_providers")

# HOW the call is made. Two values, and the difference is not cosmetic: one is a
# child process whose controls are argv, the other is an HTTP request whose
# controls are JSON fields.
TRANSPORT_CLI = "cli_subprocess"
TRANSPORT_HTTP = "http"
TRANSPORTS: tuple[str, ...] = (TRANSPORT_CLI, TRANSPORT_HTTP)

# WHICH GRAMMAR the transport speaks. `anthropic_messages` first because this
# platform's turn model already is that one — effort ladders, permission modes,
# typed thinking, tool_use blocks and the streaming parser are all shaped by it.
WIRE_ANTHROPIC = "anthropic_messages"
WIRE_OPENAI = "openai_chat"
WIRES: tuple[str, ...] = (WIRE_ANTHROPIC, WIRE_OPENAI)

# Is the provider there right now? A cloud provider is assumed up and handles
# its own 429/5xx; for a local one this is a REAL, DISPLAYED state, because a
# model server that is not running is the normal case rather than an error.
REACH_READY = "ready"
REACH_UNREACHABLE = "unreachable"
REACH_UNCONFIGURED = "unconfigured"
REACH_STATES: tuple[str, ...] = (REACH_READY, REACH_UNREACHABLE, REACH_UNCONFIGURED)


@dataclass(frozen=True)
class Capabilities:
    """What a model can actually do, declared rather than inferred.

    `context_tokens` is 0 when nothing knows, which callers must render as
    unknown rather than as zero — the same rule `model_prices` applies to an
    unpriced model.
    """

    cache: bool = False
    thinking: bool = False
    tools: bool = False
    vision: bool = False
    count_tokens: bool = False
    context_tokens: int = 0

    def as_dict(self) -> dict:
        return {
            "cache": self.cache,
            "thinking": self.thinking,
            "tools": self.tools,
            "vision": self.vision,
            "count_tokens": self.count_tokens,
            "context_tokens": self.context_tokens,
        }


@dataclass(frozen=True)
class ModelInfo:
    """One model, and which provider reaches it.

    `efforts` is on the MODEL and not on the provider because which rungs exist
    is a fact about the model (`model_caps`), which is also why effort resolves
    only after the model is settled.
    """

    id: str
    label: str
    provider_id: str
    wire: str
    capabilities: Capabilities = field(default_factory=Capabilities)
    efforts: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "provider_id": self.provider_id,
            "wire": self.wire,
            "capabilities": self.capabilities.as_dict(),
            "efforts": list(self.efforts),
        }


@dataclass(frozen=True)
class Reach:
    """Whether this provider can be reached, as a state and a sentence."""

    state: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == REACH_READY

    def as_dict(self) -> dict:
        return {"state": self.state, "detail": self.detail}


@runtime_checkable
class LlmProvider(Protocol):
    """One way of reaching a model.

    `models()` may PROBE. It is called from request paths, so an implementation
    that talks to the network must cache and must never raise: an unreachable
    local server returns an empty catalog and a `reach()` that says why.
    """

    id: str
    transport: str
    wire: str
    base_url: str | None
    auth_mode: str

    def models(self) -> list[ModelInfo]: ...

    def reach(self) -> Reach: ...

    def run_turn(self, prompt: str, *, model: str, **kw) -> dict: ...


# --------------------------------------------------------------- claude_cli


def _claude_label(model_id: str) -> str:
    """"claude-opus-4-8" -> "Claude Opus 4.8". Derived, so a new tier handle in
    `llm_router` needs no second edit here."""
    from services import model_prices

    base = model_prices.normalize_model(model_id) or model_id
    words = []
    for part in base.split("-"):
        if part.isdigit():
            words.append(part)
        else:
            words.append(part.capitalize())
    # "Claude Opus 4 8" reads as two numbers; the vendor writes one.
    out: list[str] = []
    for word in words:
        if out and word.isdigit() and out[-1].isdigit():
            out[-1] = f"{out[-1]}.{word}"
        else:
            out.append(word)
    return " ".join(out)


class ClaudeCliProvider:
    """The subscription CLI. The provider every turn runs on today.

    THIS IMPLEMENTATION CHANGES NO BEHAVIOUR. `run_turn` is `llm_router
    .call_cloud`, `models()` is the tier table already in `llm_router` joined to
    the capability tables already in `model_caps` and `context_window`. It exists
    so that a second provider is a registration rather than a fork of the turn
    path.
    """

    transport = TRANSPORT_CLI
    wire = WIRE_ANTHROPIC
    base_url = None

    def __init__(self) -> None:
        entry = model_providers.get(model_providers.CLAUDE_CLI.id)
        self.id = entry.id
        self.label = entry.label
        self.auth_mode = entry.auth_mode

    def models(self) -> list[ModelInfo]:
        from services import context_window, model_caps

        out: list[ModelInfo] = []
        # Ordered by the tier table so the picker's order is owned in one place.
        for model_id in _cli_model_order():
            window = context_window.CONTEXT_WINDOW.get(model_id, 0)
            out.append(ModelInfo(
                id=model_id,
                label=_claude_label(model_id),
                provider_id=self.id,
                wire=self.wire,
                # Every model this CLI reaches is an Anthropic one with the full
                # first-party feature set. Stated, not inferred from the wire.
                capabilities=Capabilities(
                    cache=True, thinking=True, tools=True, vision=True,
                    count_tokens=True, context_tokens=window,
                ),
                efforts=model_caps.efforts_for(model_id),
            ))
        return out

    def reach(self) -> Reach:
        """Always ready. The CLI is a local binary and a missing credential is
        reported by the Providers screen, which asks `model_credentials`; making
        this probe would spawn a subprocess on every model list."""
        return Reach(REACH_READY)

    def run_turn(self, prompt: str, *, model: str, **kw) -> dict:
        from services import llm_router

        return llm_router.call_cloud(prompt, model=model, **kw)


def _cli_model_order() -> tuple[str, ...]:
    """The CLI's models, strongest tier first, from `llm_router.TIERS`.

    Spelled as an explicit order rather than `TIERS.values()` because the picker
    shows this list and a dict's insertion order is not a promise anybody made.
    """
    from services import llm_router

    return (
        llm_router.TIER_OPUS_5,
        llm_router.TIER_OPUS,
        llm_router.TIER_FABLE,
        llm_router.TIER_SONNET_5,
        llm_router.TIER_SONNET,
        llm_router.TIER_HAIKU,
    )


# ------------------------------------------------------------------ registry


def providers() -> list[LlmProvider]:
    """Every provider CONFIGURED in this deployment, the default one first.

    A list rather than a constant because it is a function of configuration:
    what is here is what the deployment can actually run a turn on today.

    The local HTTP provider appends itself only when a base URL is configured,
    so a deployment that has not set one is unchanged and `configured_provider`
    answers None rather than a provider that cannot reach anything.
    """
    out: list[LlmProvider] = [ClaudeCliProvider()]
    # Function-local to break a cycle: `local_model_provider` imports this
    # module for `Capabilities`, `ModelInfo` and the `LlmProvider` protocol, so
    # importing it at module scope here would not resolve. This is the one
    # registration point, and it being missing is why the module sat unimported
    # and `check_dead_surface` called it a new dead surface: it was written and
    # never plugged in.
    from services import local_model_provider

    local = local_model_provider.configured_provider()
    if local is not None:
        out.append(local)
    return out


def get(provider_id: str) -> LlmProvider:
    """The configured provider with this id, or ValueError.

    Unknown is an error and never a fallback, for the same reason
    `model_providers.get` refuses one: a mistyped id silently resolving to the
    default would route a turn somewhere nobody asked for.
    """
    for provider in providers():
        if provider.id == provider_id:
            return provider
    known = ", ".join(sorted(p.id for p in providers()))
    raise ValueError(f"unknown llm provider: {provider_id!r} (configured: {known})")


def catalog() -> list[ModelInfo]:
    """Every model reachable from this deployment right now.

    A provider that cannot be reached contributes nothing rather than raising,
    which is what makes a stopped local server a smaller catalog instead of a
    broken model picker.
    """
    out: list[ModelInfo] = []
    seen: set[str] = set()
    for provider in providers():
        try:
            models = provider.models()
        except Exception as e:  # pragma: no cover - a provider must not sink the list
            log.warning("llm provider %s could not list its models: %s", provider.id, e)
            continue
        for info in models:
            if info.id in seen:
                # FIRST PROVIDER WINS. Two providers can offer the same id (a
                # local server serving an Anthropic-named weight), and silently
                # preferring the later one would move where a turn runs.
                continue
            seen.add(info.id)
            out.append(info)
    return out


def model_ids() -> list[str]:
    """The pickable model ids, in catalog order."""
    return [m.id for m in catalog()]


def find(model_id: str | None) -> ModelInfo | None:
    """The catalog entry for this id, or None. Never raises."""
    if not model_id:
        return None
    wanted = model_id.strip()
    if not wanted:
        return None
    for info in catalog():
        if info.id == wanted:
            return info
    return None


def provider_for_model(model_id: str | None) -> LlmProvider | None:
    """Which provider reaches this model, or None if nothing configured does."""
    info = find(model_id)
    if info is None:
        return None
    try:
        return get(info.provider_id)
    except ValueError:
        return None


def deployment_default() -> ModelInfo:
    """The last rung of the resolution order: what runs when nothing else said.

    `model_providers.DEFAULT_PROVIDER_ID` names the provider; the model is that
    provider's own last entry, which is the cheap tier every unconfigured chat
    has always run on. Falls back to the first thing in the catalog if the
    default provider has somehow been removed, because a deployment with any
    model at all must still be able to answer.
    """
    from services import llm_router

    default_id = model_providers.DEFAULT_PROVIDER_ID
    for info in catalog():
        if info.provider_id == default_id and info.id == llm_router.TIER_HAIKU:
            return info
    for info in catalog():
        if info.provider_id == default_id:
            return info
    entries = catalog()
    if not entries:
        raise RuntimeError(
            "no model provider is configured, so no turn can run. Add a "
            "credential in Settings, Providers.")
    return entries[0]
