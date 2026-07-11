"""Letta tool registry for the {{title}} agent.

`TOOL_REGISTRY` maps a tool NAME to `(callable, [agent handles to attach to])`,
matching the Workshop's own services/letta_tools convention. `register.py` reads
this registry, upserts each tool from its function, and attaches it to the named
agents. Add a tool: write a function (Google docstring), import it here, and add a
registry entry.
"""
from .example import record_event

# tool_name -> (callable, [agent_handles_to_attach_to])
TOOL_REGISTRY = {
    "record_event": (record_event, ["workshop-overseer"]),
}
