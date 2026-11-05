"""One blocking text call on the model a chat's own turns run on.

The platform makes side calls about a chat (the handoff written at compaction,
its title) outside the chat's turns. A chat on a model the CLI reaches itself keeps the
platform's cheap cloud tier for those. A chat on a model that another provider
serves (a local model server) gets them from THAT model instead: what the
chat said stays on the model server it was said to, and the call goes through
the app's gateway when its chat declares one, so the app sees it queued with
the chat's own requests (`app_chat_gateway.gateway_route`).

Names no provider and no app: which models count is the provider's transport,
and the gateway is the app's declaration.
"""
from __future__ import annotations

from services import app_chat_gateway, llm_contract, llm_providers

__all__ = ["call", "serves", "window_tokens"]


def _provider(model_id: str | None):
    provider = llm_providers.provider_for_model(model_id)
    if provider is None or provider.transport == llm_contract.TRANSPORT_CLI:
        return None
    return provider


def serves(model_id: str | None) -> bool:
    """Whether a chat on `model_id` has its side calls made on that model."""
    return _provider(model_id) is not None


def window_tokens(model_id: str | None) -> int:
    """The model's context window as its server reported it, or 0."""
    info = llm_providers.find(model_id)
    return int(info.capabilities.context_tokens or 0) if info else 0


def call(prompt: str, *, model_id: str, thread: dict | None = None,
         timeout_s: float = 600, max_tokens: int = 8192,
         thinking: bool = True) -> dict:
    """`prompt` answered by `model_id`, in `llm_router.call_cloud`'s shape.

    `thinking=False` asks for the answer without reasoning first, for a short
    call (a title) where reasoning costs time and buys nothing.

    Raises `ValueError` for a model the CLI serves itself (use `call_cloud`),
    and whatever the provider raises when its server does not answer."""
    provider = _provider(model_id)
    if provider is None:
        raise ValueError(f"{model_id!r} is not served outside the CLI")
    kw: dict = {"timeout_s": timeout_s, "max_tokens": max_tokens}
    if not thinking:
        kw["thinking"] = False
    route = app_chat_gateway.gateway_route(thread)
    if route is not None:
        kw["base_url"], kw["headers"] = route
    return provider.run_turn(prompt, model=model_id, **kw)
