"""What a role is allowed to write, and the tools that follow from it.

THE CLAIM THIS IMPLEMENTS. From `primitive-roles-2026-07-18`: *a role is not a
job title, it is the set of writes an agent is permitted to make.* Until now the
enum was a label — `role` changed exactly one expression in the whole codebase
(`tools_active = role == "maker"`), so reviewer, triager, keeper and broker were
behaviourally identical to each other and to no role at all.

TWO HALVES, AND ONLY ONE OF THEM IS ENFORCEABLE IN PROCESS.

  * Writes we mediate (tickets, notes, learnings, verdicts) go through our own
    Python. `may_write()` is the check those paths ask.
  * Writes to the TREE happen inside the agent's own `claude -p` subprocess,
    through its tools. No in-process check can see them. The only real control
    is not granting the tool in the first place, which is what `tools_for_role`
    is for. This is why the two must ship together: a role that gates ticket
    writes but hands out Edit is not a role, it is a label with extra steps.

WHAT THIS IS NOT. It is coordination, not confinement. The autonomy loop runs as
the same uid as everything else (verified 2026-08-04: `queue_runner.sh` runs as
`clawd-a`), so an agent that wanted to could rewrite the definition that grants
it these permissions. It prevents accidents and makes intent reviewable; it does
not stop a determined or confused agent. Real confinement is a separate
execution identity — SEC-A1, still open. Nothing here may be described in the UI
as a security boundary until that lands.
"""
from __future__ import annotations

# Everything an agent can write that we can name. `nothing` is not a target; it
# is the empty set, and belongs to broker.
WRITE_TARGETS = ("tree", "tasks", "notes", "learnings", "verdict")

# The table from primitive-roles §2, as code. A role absent here writes nothing,
# which is the safe direction: an agent with no declared role is inert rather
# than unrestricted.
_ROLE_WRITES: dict[str, frozenset[str]] = {
    # does the work: the tree, and the state of the ticket it claimed
    "maker": frozenset({"tree", "tasks"}),
    # judges the work. Deliberately cannot touch the tree or the tasks: a
    # reviewer that can edit is a second maker, and its verdict stops meaning
    # anything the moment it can fix what it is judging.
    "reviewer": frozenset({"verdict", "learnings"}),
    # routes work. Files, links and escalates tickets; never edits the code.
    "triager": frozenset({"tasks"}),
    # tends memory. Writes what was learned, never what was built.
    "keeper": frozenset({"notes", "learnings"}),
    # releases information only. The empty set is the point.
    "broker": frozenset(),
}

# Tools implied by each write target, in the CLI's vocabulary. Read tools are
# granted to everyone: reading is not a write, and an agent that cannot read
# cannot do anything useful. Web tools are added by the caller's existing
# policy, not here.
_READ_TOOLS = ("Read", "Grep", "Glob")
_TARGET_TOOLS: dict[str, tuple[str, ...]] = {
    # Bash rides with tree access: a maker runs tests and git. It is also the
    # widest grant here by far, which is exactly why it is tied to the one role
    # whose job is to change the tree.
    "tree": ("Write", "Edit", "MultiEdit", "Bash"),
    # tasks/notes/learnings/verdict are written through our own protocol blocks
    # and endpoints, not through a tool, so they imply no additional tool.
    "tasks": (),
    "notes": (),
    "learnings": (),
    "verdict": (),
}


def writes_for_role(role: str | None) -> frozenset[str]:
    """The set of things this role may write. Unknown/unset role -> empty."""
    if not role:
        return frozenset()
    return _ROLE_WRITES.get(str(role).strip().lower(), frozenset())


def may_write(role: str | None, target: str) -> bool:
    """May an agent in `role` write `target`?

    The single question every mediated write path asks. Unknown target answers
    False: a typo must not read as permission.
    """
    if target not in WRITE_TARGETS:
        return False
    return target in writes_for_role(role)


def tools_for_role(role: str | None) -> list[str]:
    """The tools this role's permitted writes require, plus reads.

    Derived rather than declared, so the grant cannot drift from the authority.
    An explicit `tools:` list on an agent may only NARROW this (see
    `narrow_tools`); nothing may widen it.

    Reads go to any role that writes SOMETHING — a reviewer must read the diff
    it judges, a triager the work it routes. They do NOT go to a role with an
    empty write set, for two different reasons that agree: an agent with no
    declared role should be inert rather than quietly able to read the tree,
    and a broker's contract is specifically that it never reads the data it
    adjudicates. Reading is not free here: same-uid reads reach every owner's
    personal data (SEC-A5), so the surface is not widened without a reason.
    """
    writes = writes_for_role(role)
    if not writes:
        return []
    granted: list[str] = list(_READ_TOOLS)
    for target in sorted(writes):
        for t in _TARGET_TOOLS.get(target, ()):
            if t not in granted:
                granted.append(t)
    return granted


def narrow_tools(role: str | None, requested: list[str] | None) -> list[str]:
    """Intersect a requested tool list with what the role actually permits.

    Least privilege with the role as the upper bound: an agent definition can
    ask for less than its role allows, and asking for more is silently reduced
    rather than honoured. Silently, because a definition asking for Edit under a
    keeper role is a mistake to correct in the definition, not a run to fail.
    """
    allowed = tools_for_role(role)
    if not requested:
        return allowed
    keep = [t for t in requested if t in allowed]
    return keep


# Actions that cannot be undone, as CLI tool-deny patterns.
#
# The rule current practice converged on: an agent runs freely through
# reversible steps and stops before anything irreversible. Editing a file in a
# worktree, committing locally, writing a note or a ticket, running tests — all
# undoable, none worth interrupting a human for. Pushing, merging, deploying,
# sending, spending and deleting outside the worktree are not.
#
# Enforced with --disallowedTools rather than a hook, deliberately: the hooks
# directory is user-scope and SHARED with the masini bridge, so a guard added
# there changes another surface. This is per-run, in our own flag builder, and
# affects exactly the process it is passed to.
#
# `Bash(...)` patterns match the command prefix. They are a coordination
# boundary, not a sandbox — a determined agent can spell a command differently,
# and the real fix is SEC-A1's separate execution identity. What they stop is
# the ordinary case: an unattended run doing the one thing nobody can take back.
IRREVERSIBLE_PATTERNS: tuple[str, ...] = (
    "Bash(git push:*)",
    "Bash(git push --force:*)",
    "Bash(gh pr merge:*)",
    "Bash(gh release:*)",
    "Bash(npm publish:*)",
    "Bash(docker push:*)",
    "Bash(rm -rf /:*)",
    "Bash(sudo:*)",
    "Bash(shutdown:*)",
    "Bash(reboot:*)",
)


def disallowed_for(*, unattended: bool) -> list[str]:
    """Tool patterns denied for this run.

    Keyed on whether anyone is WATCHING, not on the role. "Irreversible actions
    require approval" is only satisfiable when someone is there to approve: a
    manually invoked agent can ask the operator who just invoked it, an
    unattended one has nobody, so for it the answer is no rather than a prompt
    into the void.

    TWO CALLERS, and for a long time only one of them mattered. `routes/chat.py`
    asks on the @mention path, where the operator has just typed the mention, so
    the answer there is always the empty list. The list was therefore designed
    for a run that never consulted it. `scripts/ralph.sh` is that run, and it now
    asks through `python -m services.agent_authority denied --unattended`.
    """
    return list(IRREVERSIBLE_PATTERNS) if unattended else []


def is_unattended(autonomy: str | None) -> bool:
    """Does this agent run without a human present? `reactive` agents run only
    on manual invocation, so the operator is there by definition."""
    return (autonomy or "reactive") in ("supervised", "autonomous")


def describe(role: str | None) -> str:
    """One line for the UI: what this role may write, in words.

    Rendered on the agent page so the contract is legible without reading the
    table. Phrased as the declaration it is, never as a guarantee.
    """
    w = writes_for_role(role)
    if not w:
        if role == "broker":
            return "Writes nothing. Releases information only."
        return "No role declared, so it writes nothing."
    names = {
        "tree": "the working tree",
        "tasks": "tickets",
        "notes": "notes",
        "learnings": "learnings",
        "verdict": "review verdicts",
    }
    listed = [names[t] for t in sorted(w)]
    if len(listed) == 1:
        return f"Writes {listed[0]}."
    return f"Writes {', '.join(listed[:-1])} and {listed[-1]}."


def main(argv: list[str]) -> int:
    """`python -m services.agent_authority denied --unattended` — the shell's
    entry point.

    ralph.sh builds its `--disallowedTools` flag through this rather than
    repeating the pattern list in bash, so the denylist has one definition and
    the sweep cannot drift from the module that documents it. One pattern per
    line; the caller reads it into an array.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="agent_authority")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("denied")
    sp.add_argument("--unattended", action="store_true",
                    help="nobody is watching this run, so nothing irreversible")
    args = ap.parse_args(argv)
    for pattern in disallowed_for(unattended=args.unattended):
        print(pattern)
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path

    _BACKEND = str(Path(__file__).resolve().parent.parent)
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    raise SystemExit(main(sys.argv[1:]))
