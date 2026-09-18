"""script_env.load_config_env() must resolve a duplicated key the same way the
running backend does.

Measured 2026-09-17: the unit and the script previously disagreed both about
WHICH files exist (the unit's instance.env was entirely absent from the
script's file list) and, for the files they shared, which one wins a
conflicting key (the unit is last-EnvironmentFile-wins; the script's own loop
is first-file-wins). A script run by hand read a strictly different
WORKSHOP_OPERATOR_NAME than the process actually serving requests, silently.

This test parses the real unit file rather than restating its expected file
list and order as a second hardcoded constant -- the whole point is that the
two cannot drift apart again without one of them visibly failing.
"""
from __future__ import annotations

import re
from pathlib import Path

from db import script_env

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UNIT = _REPO_ROOT / "infra" / "systemd" / "workshop-backend.service"

_ENV_FILE_RE = re.compile(r"^EnvironmentFile=-?%h/\.config/workshop/(\S+)$")


def _unit_environment_files() -> list[str]:
    """Config file basenames from the unit's own EnvironmentFile= lines, in
    the order systemd loads them (so the LAST entry wins a conflicting key)."""
    files = []
    for line in _UNIT.read_text(encoding="utf-8").splitlines():
        m = _ENV_FILE_RE.match(line.strip())
        if m:
            files.append(m.group(1))
    return files


def test_unit_still_declares_the_two_files_this_pins():
    # A guard on the parser itself: if the unit's EnvironmentFile= lines ever
    # change shape, this fails loudly instead of the real assertion below
    # silently comparing against an empty list.
    assert _unit_environment_files() == ["secrets.env", "instance.env"]


def test_config_files_visit_the_units_files_in_winning_order():
    unit_files = _unit_environment_files()
    # systemd: LAST EnvironmentFile= wins a conflicting key.
    # load_config_env(): FIRST file in CONFIG_FILES to set a key wins.
    # So the same effective precedence means CONFIG_FILES must lead with the
    # unit's files in REVERSE.
    winning_order = tuple(reversed(unit_files))
    assert script_env.CONFIG_FILES[:len(winning_order)] == winning_order


def test_config_files_extra_entries_are_not_in_the_unit():
    unit_files = set(_unit_environment_files())
    extra = script_env.CONFIG_FILES[len(unit_files):]
    for name in extra:
        assert name not in unit_files, (
            f"{name!r} is both a unit file and listed again after CONFIG_FILES' "
            "unit-matching prefix -- that hides a real precedence conflict "
            "rather than declaring one"
        )
