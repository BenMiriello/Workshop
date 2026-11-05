"""Labels for the read-aloud voices, from the Kokoro voice id convention.

An id is two letters, an underscore and a name: "af_heart" is an American
English female voice called Heart. The first letter is the language, the
second the gender. An id outside the convention keeps its id as its name and
lands under "Other".
"""
from __future__ import annotations

import re
from typing import Literal, TypedDict

LANGUAGES = {
    "a": "American English",
    "b": "British English",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "j": "Japanese",
    "p": "Brazilian Portuguese",
    "z": "Mandarin Chinese",
}
OTHER = "Other"

Gender = Literal["female", "male"]
GENDERS: dict[str, Gender] = {"f": "female", "m": "male"}

_ID = re.compile(r"([a-z])([a-z])_(.+)")
_ORDER = {name: i for i, name in enumerate([*LANGUAGES.values(), OTHER])}


class VoiceLabel(TypedDict):
    id: str
    name: str
    language: str
    gender: Gender | None


def label(voice_id: str) -> VoiceLabel:
    m = _ID.fullmatch(voice_id)
    if not m:
        return {"id": voice_id, "name": voice_id, "language": OTHER, "gender": None}
    lang, gender, rest = m.groups()
    name = rest.replace("_", " ")
    return {
        "id": voice_id,
        "name": name[:1].upper() + name[1:],
        "language": LANGUAGES.get(lang, OTHER),
        "gender": GENDERS.get(gender),
    }


def labeled(voice_ids: list[str]) -> list[VoiceLabel]:
    """Every voice labeled, grouped by language in the order above, then by name."""
    out = [label(v) for v in voice_ids]
    return sorted(out, key=lambda v: (_ORDER[v["language"]], v["name"].lower(), v["id"]))
