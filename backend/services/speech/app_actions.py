"""Actions a page or app declared by voice (packages/ui/src/spokenActions.ts),
as the client sends them: `{"t": "actions", "list": [...], "off": [...]}` on
the live socket (routes/speech_live.py). `list` is every action the open pages
declare, shown or not, so the meaning classifier is not retrained on every
page switch; `off` names the commands and actions that do not apply now, whose
phrases are then heard as words and which a loose wording never means.

Each is heard like one of the operator's own commands set to "on its own"
(services/speech/modes.py): in mixed mode said alone between pauses, or after
the trigger word; in commands mode always; in dictation mode never. A built-in
phrase said the same way wins. `phrases` are said alone, or followed by the
argument when `takesArg`; each of `fixed` is a wording meaning one choice,
which is its argument. The id is the client's (`app:<n>`), so it can
never be a built-in's, and what it does is the client's too.
"""
from __future__ import annotations

import re

ID = re.compile(r"app:\d{1,9}")
MAX_ACTIONS = 100
MAX_FIXED = 200
MAX_OFF = 400
MAX_PHRASES = 12
MAX_EXAMPLES = 40
MAX_CHARS = 200


def _texts(value: object, most: int) -> list[str]:
    if not isinstance(value, list):
        return []
    out = [" ".join(v.split())[:MAX_CHARS] for v in value if isinstance(v, str)]
    return list(dict.fromkeys(t for t in out if t))[:most]


def _pairs(value: object) -> tuple[tuple[str, str], ...]:
    out: dict[str, tuple[str, str]] = {}
    for v in value if isinstance(value, list) else []:
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v):
            words, arg = (" ".join(x.split())[:MAX_CHARS] for x in v)
            if words and arg:
                out.setdefault(words.lower(), (words, arg))
    return tuple(out.values())[:MAX_FIXED]


def clean_off(raw: object) -> frozenset[str]:
    """The ids the client says do not apply now."""
    return frozenset(v for v in raw[:MAX_OFF] if isinstance(v, str)) if isinstance(raw, list) else frozenset()


def clean(raw: object) -> tuple[dict, ...]:
    """The well-formed entries of a client's list: {id, phrases, fixed,
    examples, takes_arg}, the examples holding its title and description."""
    out: dict[str, dict] = {}
    for a in raw[:MAX_ACTIONS] if isinstance(raw, list) else []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not ID.fullmatch(a["id"]):
            continue
        phrases = _texts(a.get("phrases"), MAX_PHRASES)
        fixed = _pairs(a.get("fixed"))
        if not phrases and not fixed:
            continue
        said = _texts([a.get("title"), a.get("description")], 2)
        out[a["id"]] = {
            "id": a["id"], "phrases": phrases, "fixed": fixed,
            "examples": _texts(said + _texts(a.get("examples"), MAX_EXAMPLES), MAX_EXAMPLES),
            "takes_arg": a.get("takesArg") is True,
        }
    return tuple(out.values())
