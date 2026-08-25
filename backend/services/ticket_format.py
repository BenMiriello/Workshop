"""The canonical shape of a ticket body, in ONE place.

A ticket body is free-form markdown and always will be. But the four things a
worker actually needs from it are fixed, because a worker never sees the
conversation that produced the ticket:

    Objective:   the concrete outcome, in one sentence
    Output:      what done looks like — the artifact, or how it is judged
    Start from:  the files, commands or symbols to read first
    Boundaries:  what NOT to touch, and what not to build

WHY THIS MODULE EXISTS. The labels were previously written independently in three
places: the prompt that proposes drafts, the chat dialog that files them, and the
regexes in `ticket_readiness` that check for them. They did not agree. The dialog
emitted "Done when: ..." while the checker looked for "Done:" or "Output:", so
every ticket filed from a chat failed the acceptance check even though it plainly
stated its acceptance. A format and its checker cannot live in two files.

So: producers compose with `compose()`, the checker matches with `LABEL_RE`, and
both read the same table below. Adding a recognized synonym is one edit.

THIS MODULE OWNS THE SHAPE, NOT THE WORDS. What to actually write in each field,
how a title should read, and the plain-language rule are in `docs/ticket-writing.md`.
That page exists because this one was the whole convention for months: the labels
were enforced and nothing said how to fill them, so tickets drifted into insider
shorthand the operator could not read on his own queue.
"""
from __future__ import annotations

import re

# key -> (canonical label, recognized synonyms). Synonyms exist because the
# producers are prompts: a model told to write "Output:" will sometimes write
# "Done when:", and rejecting a ticket over the wording of a heading it got
# essentially right is pedantry that costs a run.
FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "problem": ("Problem", ("problem", "context", "background", "motivation",
                            "why", "user story")),
    "objective": ("Objective", ("objective", "goal", "outcome")),
    "output": ("Output", ("output", "done", "done when", "done looks like",
                          "acceptance", "accept", "verify", "proof",
                          "definition of done")),
    "entry_points": ("Start from", ("start from", "entry points", "entrypoints",
                                    "begin with", "starting point", "files")),
    "boundaries": ("Boundaries", ("boundaries", "out of scope", "do not touch",
                                  "don't touch", "non-goals", "not in scope")),
}

# Field order in a composed body. Problem first (the motivation, when stated),
# then Objective — a reader needs one of those two before anything else makes
# sense. `compose` omits empty fields, so a bug that gives only Problem leads
# with Problem and a task that gives only Objective leads with Objective.
ORDER = ("problem", "objective", "output", "entry_points", "boundaries")


def _label_re(key: str) -> re.Pattern:
    """A line-start match for any recognized label of this field.

    Tolerant of the punctuation a model actually emits: a colon, a dash, and an
    optional markdown bold or heading wrapper, since "**Objective:**" and
    "## Objective" are both things a model writes when asked for "Objective:".
    """
    _canon, synonyms = FIELDS[key]
    alts = "|".join(re.escape(s) for s in sorted(synonyms, key=len, reverse=True))
    return re.compile(
        rf"^\s{{0,3}}(?:#{{1,6}}\s*)?(?:\*\*|__)?\s*(?:{alts})\s*(?:\*\*|__)?\s*[:\-–—]",
        re.IGNORECASE | re.MULTILINE,
    )


LABEL_RE: dict[str, re.Pattern] = {k: _label_re(k) for k in FIELDS}


def states(body: str, key: str) -> bool:
    """Does this body state the given field?"""
    return bool(LABEL_RE[key].search(body or ""))


def compose(*, problem: str = "", objective: str = "", output: str = "",
            entry_points: str = "", boundaries: str = "", extra: str = "") -> str:
    """Build a canonical ticket body. Empty fields are omitted rather than left
    as an empty heading, so a partial draft reads as a partial draft and not as a
    template someone forgot to fill in."""
    values = {"problem": problem, "objective": objective, "output": output,
              "entry_points": entry_points, "boundaries": boundaries}
    lines: list[str] = []
    for key in ORDER:
        text = " ".join(str(values.get(key) or "").split()).strip()
        if text:
            lines.append(f"{FIELDS[key][0]}: {text}")
    body = "\n".join(lines)
    tail = str(extra or "").strip()
    if tail:
        body = f"{body}\n\n{tail}" if body else tail
    return body


# The template a prompt is shown, generated from the table so the instruction and
# the checker cannot drift apart.
def prompt_fields() -> str:
    return "\n".join(f'    "{k}": "<{FIELDS[k][0]}>"' for k in ORDER)
