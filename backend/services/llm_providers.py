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

# The vocabulary, the shapes and the protocol live in `llm_contract`, which
# imports nothing from `services`. They are re-exported here so every existing
# caller of `llm_providers.ModelInfo` (and the rest) keeps working.
#
# They were moved out on 2026-09-05 to break a real import cycle: a provider
# implementation needs these names to BE a provider, and this module needs the
# implementation to LIST it. With the vocabulary defined here that was mutual,
# the registration below had to be a function-local import to resolve, and the
# backend's largest strongly-connected component grew from 102 modules to 107.
# A deferred import does not break a cycle, it hides one.
from services.llm_contract import (  # noqa: F401  (re-export)
    LlmProvider,
    Capabilities,
    ModelInfo,
    Reach,
    REACH_READY,
    REACH_STATES,
    REACH_UNCONFIGURED,
    REACH_UNREACHABLE,
    TRANSPORT_CLI,
    TRANSPORT_HTTP,
    TRANSPORTS,
    WIRE_ANTHROPIC,
    WIRE_OPENAI,
    WIRES,
)
from services import local_model_provider


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
        llm_router.TIER_OPUS_5_5,
        llm_router.TIER_OPUS_5,
        llm_router.TIER_FABLE_5_1,
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
