"""Filesystem roots for the platform.

Every path the backend needs is derived from where the repository actually sits,
not from any one operator's home directory. A deployment that lives somewhere
else needs no source edits; the env overrides exist for layouts that split the
runtime or config directories off the repo.

    WORKSHOP_ROOT        the repository root (default: two levels above this file)
    WORKSHOP_RUNTIME     mutable per-instance state (default: <root>/.runtime)
    WORKSHOP_CONFIG_DIR  operator config + secrets (default: ~/.config/workshop)
"""

from __future__ import annotations

import os
from pathlib import Path

_THIS = Path(__file__).resolve()


def workshop_root() -> Path:
    """The repository root. `backend/services/paths.py` -> up two."""
    env = os.environ.get("WORKSHOP_ROOT")
    if env:
        return Path(env).expanduser()
    return _THIS.parents[2]


def runtime_dir() -> Path:
    env = os.environ.get("WORKSHOP_RUNTIME")
    if env:
        return Path(env).expanduser()
    return workshop_root() / ".runtime"


def config_dir() -> Path:
    env = os.environ.get("WORKSHOP_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "workshop"


def root(*parts: str) -> Path:
    return workshop_root().joinpath(*parts)


def runtime(*parts: str) -> Path:
    return runtime_dir().joinpath(*parts)


def shared(*parts: str) -> Path:
    return workshop_root().joinpath("shared", *parts)


def scripts(*parts: str) -> Path:
    return workshop_root().joinpath("scripts", *parts)


def config(*parts: str) -> Path:
    return config_dir().joinpath(*parts)


def env_path(var: str, *default_parts: str, base: str = "runtime") -> Path:
    """Read `var`, else fall back to a derived path under root/runtime/config.

    Lets a call site keep its documented env var while losing the machine
    specific literal it used to carry as the default.
    """
    raw = os.environ.get(var)
    if raw:
        return Path(raw).expanduser()
    bases = {"root": root, "runtime": runtime, "shared": shared, "config": config}
    return bases[base](*default_parts)
