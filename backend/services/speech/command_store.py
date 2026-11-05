"""The operator's spoken commands, kept on the server per user so every device
hears the same ones: `<user runtime>/speech/commands.json`, holding the mode
and trigger word (services/speech/modes.py), each built-in's phrases and when
it is heard, the operator's own commands, and how page labels are drawn. A built-in never saved reads as
its defaults (services/speech/commands.py).

A file saved in the older "trigger" mode reads as mixed with every command that
was heard on its own needing the trigger word, which is what that mode did.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from services import ownership
from services.speech.command_list import NUMBER_MAX
from services.speech.commands import BUILTINS, BY_ID, Table, tokens
from services.speech.modes import DEFAULT_TRIGGER, WRITING, mode_of, when_of

_LOCK = threading.Lock()

# How many controls "show labels" numbers at most, unless the operator says.
LABELS_MAX = 100
LABELS_MIN = 10


def clean_phrases(phrases: list[str]) -> list[str]:
    """Phrases as typed, trimmed, without blanks or repeats of one another."""
    seen: set[tuple[str, ...]] = set()
    out: list[str] = []
    for p in phrases:
        key = tokens(p)
        if key and key not in seen:
            seen.add(key)
            out.append(" ".join(p.split()))
    return out


def store_path() -> Path:
    return ownership.user_runtime_dir() / "speech" / "commands.json"


def _load() -> dict:
    try:
        data = json.loads(store_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def current() -> dict:
    """Every command as the current user has them: {builtins, custom}."""
    with _LOCK:
        data = _load()
    return _shape(data)


def _shape(data: dict) -> dict:
    saved = data.get("builtins") if isinstance(data.get("builtins"), dict) else {}
    was_trigger = data.get("mode") == "trigger"

    def when(mine: dict) -> str:
        w = when_of(mine)
        return "trigger" if was_trigger and w == "alone" else w

    builtins = []
    for b in BUILTINS:
        mine = saved.get(b.id) if isinstance(saved.get(b.id), dict) else {}
        phrases = mine.get("phrases")
        builtins.append({
            "id": b.id, "label": b.label, "kind": b.kind, "takes_arg": b.takes_arg,
            "phrases": clean_phrases(phrases) if isinstance(phrases, list) else list(b.phrases),
            "defaults": list(b.phrases), "when": when(mine),
        })
    custom = [{**c, "when": when(c)} for c in data.get("custom") or [] if isinstance(c, dict)
              and c.get("id") and isinstance(c.get("phrases"), list) and c.get("text")]
    back = mode_of(data.get("back"))
    return {
        "mode": mode_of(data.get("mode")), "trigger": _trigger(data.get("trigger")),
        "back": back if back in WRITING else "mixed", "builtins": builtins, "custom": custom,
        "labels": _labels(data.get("labels")),
    }


def _labels(value: object) -> dict:
    v = value if isinstance(value, dict) else {}
    n = v.get("max")
    ok = isinstance(n, int) and not isinstance(n, bool)
    return {"max": min(max(n, LABELS_MIN), NUMBER_MAX) if ok else LABELS_MAX}


def set_labels(labels: dict) -> dict:
    """How page labels are drawn: `max`, the most numbered at once."""
    with _LOCK:
        data = _load()
        data["labels"] = _labels(labels)
        _write(data)
    return current()


def _trigger(value: object) -> str:
    return " ".join(str(value).split()) if isinstance(value, str) and tokens(value) else DEFAULT_TRIGGER


def _write(data: dict) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(p)


def set_mode(mode: str, trigger: str | None = None) -> dict:
    """Change the mode (and the trigger word, when given). Going to commands
    only remembers the mode words were written in, for "start writing";
    "writing" goes back to it."""
    with _LOCK:
        data = _load()
        if data.get("mode") == "trigger":
            _keep_trigger_whens(data)
        was = mode_of(data.get("mode"))
        if mode == "writing":
            back = mode_of(data.get("back"))
            mode = back if back in WRITING else "mixed"
        data["mode"] = mode_of(mode, was)
        if data["mode"] == "commands" and was in WRITING:
            data["back"] = was
        if trigger is not None:
            data["trigger"] = _trigger(trigger)
        _write(data)
    return current()


def _keep_trigger_whens(data: dict) -> None:
    """Write down what the older "trigger" mode meant before the mode is
    replaced: every command heard on its own now needs the trigger word."""
    now = _shape(data)
    data["builtins"] = {b["id"]: {"phrases": b["phrases"], "when": b["when"]} for b in now["builtins"]}
    data["custom"] = [{k: c[k] for k in ("id", "phrases", "text", "when")} for c in now["custom"]]


def save(builtins: list[dict], custom: list[dict]) -> dict:
    """Store the commands sent back from the manager. A built-in not named
    keeps what it had; an unknown id is ignored."""
    with _LOCK:
        data = _load()
        if data.get("mode") == "trigger":
            data["mode"] = "mixed"
        kept = data.get("builtins") if isinstance(data.get("builtins"), dict) else {}
        for b in builtins:
            if b.get("id") in BY_ID:
                kept[b["id"]] = {"phrases": clean_phrases(list(b.get("phrases") or [])),
                                 "when": when_of(b)}
        mine = []
        for c in custom:
            phrases = clean_phrases(list(c.get("phrases") or []))
            if phrases and c.get("text"):
                mine.append({"id": str(c.get("id") or uuid.uuid4().hex[:12]),
                             "phrases": phrases, "text": str(c["text"]), "when": when_of(c)})
        data["builtins"], data["custom"] = kept, mine
        _write(data)
    return current()


def reset() -> dict:
    """Every command back to its defaults; the mode, the trigger word and the
    labels stay."""
    with _LOCK:
        data = _load()
        _write({k: data[k] for k in ("mode", "trigger", "back", "labels") if k in data})
    return current()


def load_table() -> Table:
    """The commands the current user's dictation listens for."""
    return Table.from_commands(current())
