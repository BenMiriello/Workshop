"""The operator's model defaults, as the Settings screen reads and writes them.

Three settings, each stored where the code that applies it can read it:

- the global default (`chat_store.get_default_model`), rung 5 of
  `services/model_resolution`;
- a default per scope (`chat_store.get_scope_models`), rung 4, consulted before
  what the scope itself declares;
- the compaction model, one for every chat (`local_model_provider.
  compaction_model`) and one per scope (`scope_compaction_models`), applied to a
  compaction request when the chosen model's window fits
  (`local_model_provider.compaction_model_for`).

The CHOICES are served with the values, never assumed by the client: which
models exist, which scopes a chat can be bound to, and what each scope falls
back to when the operator has not chosen for it.
"""
from __future__ import annotations

from services import (chat_context, chat_store, llm_contract, llm_providers,
                      local_model_provider, model_resolution)

# Scopes Settings does not list. `general` is the unbound chat, which the
# global default already answers for; an agent's model is in its definition.
_UNLISTED = ("general",)
_AGENT_PREFIX = "agent:"


def _model_row(info: llm_contract.ModelInfo) -> dict:
    return {"id": info.id, "label": info.label, "provider_id": info.provider_id,
            "window": info.capabilities.context_tokens}


def _scope_label(provider) -> str:
    """An app's chat and its project's build chats can share a slug, so the
    project says which of the two it is."""
    if provider.key.startswith("project:"):
        return f"{provider.key.split(':', 1)[1]} (build)"
    return provider.label


def _places() -> list:
    """The scopes a per-scope default may be set for, each once, apps first."""
    seen: dict[str, object] = {}
    for p in chat_context.place_providers():
        if p.key in _UNLISTED or p.key.startswith(_AGENT_PREFIX):
            continue
        seen.setdefault(p.key, p)
    return sorted(seen.values(), key=lambda p: not p.key.startswith("app:"))


def payload() -> dict:
    chosen = chat_store.get_scope_models()
    compaction = local_model_provider.scope_compaction_models()
    global_compaction = local_model_provider.compaction_model()
    scopes = []
    for p in _places():
        fallback, source = model_resolution.default_for(None, p.declared_model)
        scopes.append({"key": p.key, "label": _scope_label(p),
                       "model": chosen.get(p.key),
                       "fallback_model": fallback, "fallback_source": source,
                       "compaction_model": compaction.get(p.key),
                       "compaction_fallback": global_compaction})
    return {
        "default_model": chat_store.get_default_model(),
        "deployment_model": llm_providers.deployment_default().id,
        "models": [_model_row(m) for m in llm_providers.catalog()],
        "scopes": scopes,
        "compaction_model": global_compaction,
        "compaction_models": [
            _model_row(m)
            for m in local_model_provider.compaction_candidates().values()],
    }


def apply(default_model: str | None = None,
          scope_models: dict[str, str] | None = None,
          compaction_model: str | None = None,
          scope_compaction_models: dict[str, str] | None = None) -> None:
    """Write what was sent; None means not sent and "" clears. Raises
    ValueError on a model or scope that cannot be chosen."""
    if scope_models:
        places = {p.key for p in _places()}
        unknown = sorted(set(scope_models) - places)
        if unknown:
            raise ValueError(f"no such scope: {', '.join(unknown)}")
        bad = sorted(m for m in scope_models.values()
                     if m.strip() and llm_providers.find(m) is None)
        if bad:
            raise ValueError(f"unknown model {bad[0]!r}; pick one of "
                             f"{llm_providers.model_ids()}")
    if scope_compaction_models:
        places = {p.key for p in _places()}
        unknown = sorted(set(scope_compaction_models) - places)
        if unknown:
            raise ValueError(f"no such scope: {', '.join(unknown)}")
        candidates = local_model_provider.compaction_candidates()
        bad = sorted(m for m in scope_compaction_models.values()
                     if m.strip() and m.strip() not in candidates)
        if bad:
            raise ValueError(f"{bad[0]!r} is not a model the local model "
                             "server lists with a context window")
    if default_model is not None:
        chat_store.set_default_model(default_model)
    for key, model in (scope_models or {}).items():
        chat_store.set_scope_model(key, model)
    if compaction_model is not None:
        local_model_provider.set_compaction_model(compaction_model)
    for key, model in (scope_compaction_models or {}).items():
        local_model_provider.set_scope_compaction_model(key, model)
