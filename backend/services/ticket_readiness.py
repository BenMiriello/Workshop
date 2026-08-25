"""Is this ticket specified well enough to hand to a worker?

WHY THIS EXISTS. A worker never sees the conversation that produced its ticket.
It gets the title and the body and nothing else, so anything the ticket does not
say is either guessed at or asked about, and both cost a run. The operator's ask
was to stop work being "scoped poorly or pushed through under bad scope"; that
needs a check a machine can run, not a prompt asking producers to try harder.

WHAT IT IS NOT. It is not a quality judgement and it cannot be. It asks whether
the ticket ANSWERS six questions, not whether the answers are good — that is the
reviewer's job, and later the operator's. A ticket can pass every check here and
still be a bad idea.

WHY IT IS ADVISORY BY DEFAULT. Measured against the live stores when it was
written, most open tickets fail at least one check. Turning it into a hard gate
on day one would wedge the existing queue, so the check REPORTS by default and
each caller decides what to do with the report. `blocking()` names the subset
that is worth refusing a run over: a ticket with no title or a dependency that
cannot be resolved is not underspecified, it is broken.
"""
from __future__ import annotations

import re
from collections.abc import Collection

from services import tasks, ticket_format

# A body that names a file, a command or a symbol tells a worker where to start.
# Deliberately generous: a path with a separator, a fenced block, an inline-code
# span, or a shell-looking line all count as "there is somewhere to begin".
_ENTRY_POINT = re.compile(
    r"(?:[\w.\-/]+/[\w.\-]+\.\w+)"        # a repo-relative path
    r"|(?:`[^`\n]+`)"                      # inline code
    r"|(?:^\s{0,3}```)"                    # a fenced block
    r"|(?:\b(?:run|npm|pytest|python3?|bash|curl|git|make)\s+\S+)",
    re.MULTILINE,
)

# The labels come from `services/ticket_format`, which is also what the producers
# compose with. They used to be written out here independently, and they did not
# match: the chat dialog emitted "Done when: ..." while this file looked for
# "Done:" or "Output:", so a ticket that plainly stated its acceptance failed the
# acceptance check. A format and its checker cannot live in two files.

# Checks worth refusing a RUN over. The rest are advice: they make a run more
# likely to succeed, but a run without them is a judgement call, not an error.
BLOCKING = ("title", "dependencies")


def check(task: dict, *, known_ids: Collection[str] | None = None) -> dict:
    """``{"ok": bool, "missing": [{"check", "why"}], "warnings": [str]}``.

    ``known_ids`` lets the dependency check resolve ids; omit it to skip that
    check rather than fail it. Only membership is tested, so a `dict.keys()`
    view is fine and is what the LIST path passes: it runs per row, and
    materialising a fresh set of the whole scope each time is O(n^2). Omitting
    it entirely is also fine: a caller without the ticket list cannot know.
    """
    title = str(task.get("title") or "").strip()
    body = str(task.get("body") or "").strip()
    missing: list[dict] = []

    def fail(check_name: str, why: str) -> None:
        missing.append({"check": check_name, "why": why})

    if not title:
        fail("title", "the ticket has no title")

    if not (ticket_format.states(body, "objective")
            or ticket_format.states(body, "problem") or len(body) >= 40):
        fail("objective", "the body does not say what this is trying to achieve")

    if not (ticket_format.states(body, "output") or task.get("acceptance")):
        fail("acceptance", "the ticket does not say what done looks like")

    # A one-edit ticket does not need to name its entry points; anything larger
    # is asking the worker to go looking.
    # `effort` is canonicalized by the store, so one value to compare.
    if task.get("effort") != "quick":
        if not (_ENTRY_POINT.search(body) or ticket_format.states(body, "entry_points")):
            # Phrased as "does not say ..." like its siblings, deliberately. The
            # UI composes one line of prose out of these strings rather than
            # keeping a second set of wordings, so a `why` that reads fine alone
            # ("the body names no file, command or symbol to start from") but
            # ungrammatically in a list ("... and no file, command or symbol to
            # start from") forces the UI to invent its own phrasing, which is the
            # divergence this module exists to prevent. One vocabulary, and it
            # has to read well in both places.
            fail("entry_points", "the body does not say where to start")

    if not (task.get("touches") or task.get("latitude") == "open"
            or ticket_format.states(body, "boundaries")):
        fail("boundaries", "the ticket does not say which files it may change")

    if known_ids is not None:
        for dep in (task.get("depends_on") or []):
            if str(dep) not in known_ids:
                fail("dependencies", f"it depends on a ticket that does not exist: {dep}")
                break

    return {
        "ok": not missing,
        "missing": missing,
        "warnings": tasks.title_warnings(title),
    }


def blocking(result: dict) -> list[dict]:
    """The failures that justify refusing to run, out of a `check` result."""
    return [m for m in result.get("missing", []) if m["check"] in BLOCKING]


def summary(result: dict) -> str:
    """One line for a log or an error body."""
    if result.get("ok"):
        return "ready"
    return "not ready: " + "; ".join(m["why"] for m in result.get("missing", []))
