"""What a built-in app's chat assistant IS -- the platform half.

`services/app_personas.py` used to be this class plus 520 lines of four
particular apps' business logic: Plaid transaction bucketization, a commute
schedule reader, a location reverse-geocode summary, and a budget plan-patch
applier. All of it sat in a platform directory, so "which apps exist" was
readable off the platform's own source, and none of it could leave with its app.

What remains here is the only part that is genuinely the platform's: the base
class that turns a declaration file into a ContextProvider. Each app's own
persona lives beside that app (`<app>_persona.py`) and is contributed through
`AppSpec.chat_provider` / `AppSpec.chat_snapshot`.
"""
from __future__ import annotations

import logging

from services import llm_router as _llm
from services.chat_context import ContextProvider

log = logging.getLogger("workshop.app_persona")


class AppPersona(ContextProvider):
    """A built-in app's chat assistant, DECLARED in `agents/apps/<key>.md`.

    Everything an app assistant is — its name, its system prompt, the tools it
    may call, the MCP servers behind them, its model — is declaration, and used
    to be Python: the prompt a concatenated string literal, the tool list a class
    attribute, the model a constant. So "what is this assistant allowed to do"
    was a question you answered by reading code, and app personas were one of the
    competing ways an agent got defined in this system.

    They are now one way, sharing the roster's format and its parser. What stays
    in Python is only what cannot be declared: `live_context()` (a snapshot
    function) and, for Budget, `apply_actions()` (it parses a fenced block out of
    the reply and writes the plan). A subclass below is those methods and a key.

    Not roster agents. `list_agents` globs `agents/*.md` non-recursively, so
    nothing here is dispatched, triggered, or counted — an app assistant is the
    face of an installed app, reached by opening that app.
    """

    def __init__(self) -> None:
        from services import agents_registry

        d = agents_registry.app_persona(self.key)   # raises if absent/empty
        self.label = d["name"]
        self.purpose = d["purpose"]
        # Every app chat also gets the workshop toolkit (orientation + reach its
        # own uploads) on top of whatever the app declares, so no app assistant
        # arrives unable to see what it has access to. Deferred import avoids a
        # circular with chat_context, which imports this module.
        from services import chat_context as _cc
        self.allowed_tools = list(dict.fromkeys([*d["tools"], *_cc.WORKSHOP_TOOLKIT]))
        self.mcp_servers = list(dict.fromkeys([*d["mcp_servers"], "workshop"]))
        # AN APP DECLARES ITS MODEL OR IT DECLARES NOTHING. `d["model"]` is the
        # `model:` line in the app's own persona file, which IS the per-app
        # default rung 4 of `services/model_resolution` reads. An app that names
        # nothing leaves this None and falls through to the operator's default
        # and then the deployment's, rather than pinning the seed constant.
        declared = str(d.get("model") or "").strip()
        self.declared_model = _llm.TIERS.get(declared.lower(), declared) or None
        self.definition_path = d["definition_path"]
        self._persona = d["persona"]

    def persona(self) -> str:
        return self._persona
