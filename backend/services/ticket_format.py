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


# ---- Executable acceptance --------------------------------------------------
#
# WHY THIS IS HERE. The four labels above tell a WORKER what to do. None of them
# tells a REVIEWER when it is finished, and the `Output:` label does not either:
# it accepts prose, and prose restating the objective is what 195 of 195 open
# tickets carried on 2026-09-01 while the structured `acceptance` field was null
# on every one of them. A worker handed a spec with no stated finish line forms
# its own view of "done" and reports success against that view. That is not a
# character flaw, it is the only available behaviour.
#
# So `acceptance` gets a MEANING and creation is gated on it: something a fresh
# reviewer can run without judgment. Two forms, and both are executed by
# machinery that already exists:
#
#   {"kind": "command", "spec": "npm run typecheck"}
#       one command line. Exit 0 is the verdict.
#   {"criteria": [{"desc": ..., "cmd": ..., "expect": "exit0" | "<substring>"}]}
#       one or more assertions, run by the sweep and reported to the reviewer as
#       the `acceptance_criteria` gate (services/change_gates.CRITERIA_GATE).
#
# A screenshot comparison is the first form with a screenshot command in it, and
# that is deliberate: a "compare these two PNGs" kind that nothing can execute
# would reintroduce the judgment call this gate exists to remove.
#
# `kind` "reviewer" and "operator" remain legal values of the FIELD (they are on
# live records and `_normalize_acceptance` preserves them) and neither is
# executable on its own, so neither satisfies this rule without criteria.

# The first token of a command line. Deliberately generous, in the same spirit
# as `ticket_readiness._ENTRY_POINT`: the question is "could a reviewer paste
# this into a shell", not "is this the best possible check". A leading
# `FOO=bar` assignment is stripped before this is consulted.
RUNNERS = frozenset({
    "bash", "sh", "zsh", "env", "cd", "make", "just",
    "python", "python3", "pytest", "mypy", "ruff", "uv", "pip",
    "npm", "npx", "pnpm", "yarn", "bun", "node", "vitest", "tsc", "deno",
    "git", "curl", "wget", "jq", "grep", "rg", "sed", "awk", "test", "diff",
    "docker", "compose", "systemctl", "psql", "sqlite3", "cargo", "go",
})

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A path-shaped token: `scripts/x.sh`, `./run`, `backend/.venv/bin/python3`.
_PATH_TOKEN = re.compile(r"^[.~]?/?[\w.\-]+(?:/[\w.\-]+)+$")

ACCEPTANCE_HINT = (
    "Acceptance must be something a fresh reviewer can RUN, not a sentence. "
    "Give one of: a command, as "
    '{"kind": "command", "spec": "npm run typecheck"}; or assertions, as '
    '{"criteria": [{"desc": "the suite passes", "cmd": "pytest -q", '
    '"expect": "exit0"}]}. A screenshot check is a command too, naming the '
    "screenshot tool and the page. Prose restating the objective does not count."
)


def is_executable_command(spec: str | None) -> bool:
    """Could a reviewer paste this into a shell and get a verdict?

    Checks the SHAPE of the first token, which is the one thing that separates a
    command from a sentence: "The endpoint returns X and a test covers it" opens
    on a word that is neither a known runner nor a path, while
    `cd frontend && npm test` opens on `cd`. It does not try to judge whether the
    command is a GOOD check; that is the reviewer's job and later the operator's.
    """
    text = " ".join(str(spec or "").split()).strip()
    if not text:
        return False
    for token in text.split(" "):
        if _ASSIGNMENT.match(token):
            continue  # FOO=bar cmd ...
        head = token.strip("`'\"")
        if not head:
            continue
        return head in RUNNERS or bool(_PATH_TOKEN.match(head))
    return False


def executable_acceptance(acceptance) -> bool:
    """Does this acceptance record state a finish line a machine can check?"""
    if not isinstance(acceptance, dict):
        return False
    for c in (acceptance.get("criteria") or []):
        if isinstance(c, dict) and is_executable_command(c.get("cmd")):
            return True
    if str(acceptance.get("kind") or "").strip() == "command":
        return is_executable_command(acceptance.get("spec"))
    return False


def acceptance_refusal(acceptance) -> str | None:
    """The message to refuse a filing with, or None when it may proceed.

    One wording, used by every gate (the create route, the chat's file-ticket
    button, the CLI), so a producer that is refused in one place is told the same
    thing everywhere and a fix that satisfies one satisfies all.
    """
    if executable_acceptance(acceptance):
        return None
    if not isinstance(acceptance, dict) or not acceptance:
        return f"this ticket states no acceptance. {ACCEPTANCE_HINT}"
    kind = str(acceptance.get("kind") or "").strip() or "none"
    if kind == "command" and not str(acceptance.get("spec") or "").strip():
        return f"acceptance kind is command but no command was given. {ACCEPTANCE_HINT}"
    if kind == "command":
        return (f"acceptance spec does not read as a command: "
                f"{str(acceptance.get('spec'))[:120]!r}. {ACCEPTANCE_HINT}")
    return (f"acceptance kind {kind!r} is a judgement, not something a reviewer "
            f"can run. {ACCEPTANCE_HINT}")
