"""What a model provider IS: the vocabulary, the shapes and the protocol.

Types and constants only. This module imports nothing from `services`, and that
is the whole point of it existing separately from `llm_providers`.

WHY IT WAS SPLIT OUT (2026-09-05). `local_model_provider` needs `ModelInfo`,
`Reach`, `Capabilities` and the wire/transport/reach constants in order to BE a
provider, and `llm_providers.providers()` needs `local_model_provider` in order
to LIST it. Those two needs are mutual, so with the vocabulary living in
`llm_providers` the registration had to be a function-local import to resolve at
all, and the two modules joined the backend's largest import cycle, which grew
from 102 modules to 107 when the second provider arrived.

A cycle broken with a deferred import still is a cycle: it just moves the
failure from import time to call time and hides it from anything reading the
graph. Splitting the vocabulary out removes it instead. A provider implementation
now depends on this module and NOT on the registry that lists it, which is what
makes the seam a seam: something can implement the contract without importing
half the service layer.

`llm_providers` re-exports every name here, so `llm_providers.ModelInfo` and the
rest keep working for existing callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

class ProviderCallFailed(RuntimeError):
    """A provider could not complete a turn.

    Defined HERE and not in `llm_router` because raising it is part of BEING a
    provider, and an implementation should not have to import the CLI router to
    report that its own HTTP call failed. `llm_router.CloudCallFailed` is an
    alias of this class, so every existing raise and every existing
    `except CloudCallFailed` keeps working, including for a provider that has
    never touched the CLI.
    """


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

    def run_turn(self, prompt: str, *, model: str, **kw) -> dict:
        """One blocking turn, in `llm_router.call_cloud`'s return shape, plus
        whatever usage the provider was told -- under `usage`, or `modelUsage`
        where the transport reports per-model counts.

        A PROVIDER REPORTS WHAT IT USED AND DOES NOT RECORD IT. Metering is the
        caller's, through `usage_ledger.record_turn`, and the reason is
        attribution rather than layering: a ledger row carries the scope, the
        run and the chat it was spent on, and a provider knows none of those --
        it is handed a prompt and a model id. One provider here did meter
        itself, and every row it wrote was billed to "unbound" with no role, so
        no per-chat or per-agent view could ever attribute it. An unattributable
        write is worse for the ledger than no write, because it still counts
        against the window.

        It also made the seam circular. A provider that reaches the ledger
        reaches, transitively, the chat store that lists providers, so adding
        this module's implementations put them inside the backend's largest
        import cycle instead of beside it.
        """


# --------------------------------------------------------------- claude_cli
