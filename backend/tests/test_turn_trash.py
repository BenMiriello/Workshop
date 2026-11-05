"""A turn's rm moves what it removes to the trash (services/turn_rm), installed
on the turn's PATH by a SessionStart hook (services/turn_trash)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services import tool_policy, tool_policy_git, turn_rm, turn_trash
from services.storage import kinds, trash

RM = Path(turn_rm.__file__)


@pytest.fixture
def top(tmp_path, monkeypatch):
    """A trash root inside tmp_path, which pytest keeps under /tmp (scratch)."""
    root = tmp_path / "top"
    root.mkdir()
    monkeypatch.setattr(turn_rm, "outright", lambda p: "node_modules" in p.parts)
    monkeypatch.setattr(turn_rm, "trash_root", lambda p: (root / ".workshop-trash-1").mkdir(
        exist_ok=True) or root / ".workshop-trash-1")
    return root


def test_rm_moves_to_the_trash_with_where_it_came_from(top, monkeypatch):
    monkeypatch.setenv("WORKSHOP_CHAT_SCOPE", "app-x")
    victim = top / "work"
    victim.mkdir()
    (victim / "f.txt").write_text("keep me")
    assert turn_rm.remove(str(victim), {"r"}, "rm") is None
    slot = next((top / ".workshop-trash-1").iterdir())
    entry = json.loads((slot / "entry.json").read_text())
    assert not victim.exists() and (slot / "payload" / "f.txt").read_text() == "keep me"
    assert entry["origin"] == str(victim) and entry["app"] == "app-x" and entry["kind"] == "removed"


def test_rm_keeps_its_own_rules(top):
    folder = top / "d"
    folder.mkdir()
    assert "Is a directory" in turn_rm.remove(str(folder), set(), "rm")
    assert "No such file" in turn_rm.remove(str(top / "nope"), set(), "rm")
    assert turn_rm.remove(str(top / "nope"), {"f"}, "rm") is None
    assert turn_rm.remove(str(folder), {"d"}, "rm") is None and not folder.exists()
    deps = top / "node_modules"
    deps.mkdir()
    assert turn_rm.remove(str(deps), {"r"}, "rm") is None
    assert not deps.exists() and not (top / ".workshop-trash-1").exists()


def test_rm_parses_options_as_rm_does():
    assert turn_rm.parse(["-rf", "--", "-x", "a"]) == ({"r", "f"}, ["-x", "a"])
    assert turn_rm.parse(["--recursive", "--interactive=never", "a"]) == ({"r"}, ["a"])
    with pytest.raises(turn_rm.Usage):
        turn_rm.parse(["-z", "a"])


def test_what_is_deleted_outright():
    for path in ("/tmp/x", "/workspace/tmp/a", "/w/app/node_modules/x", "/w/.workshop-trash-1/x/payload"):
        assert turn_rm.outright(Path(path)), path
    for path in ("/workspace/src/a.py", "/workspace/projects/.widgets-wt-x", "/w/data/db.sqlite"):
        assert not turn_rm.outright(Path(path)), path


def test_a_sandbox_path_is_recorded_as_the_host_sees_it(monkeypatch):
    monkeypatch.setenv("WORKSHOP_MOUNT_SOURCE", "/home/u/workshop")
    assert turn_rm.host(Path("/workspace/projects/a")) == "/home/u/workshop/projects/a"
    assert turn_rm.host(Path("/mnt/data/a")) == "/mnt/data/a"


def test_the_platform_trash_lists_and_restores_a_chat_removal(top, monkeypatch, tmp_path):
    monkeypatch.setattr(trash, "_turn_roots", lambda: [top / ".workshop-trash-1"])
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / "rt"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(tmp_path / "rt" / "projects.json"))
    victim = top / "notes.txt"
    victim.write_text("n")
    turn_rm.remove(str(victim), set(), "rm")
    (entry,) = trash.entries()
    trash.restore(entry["id"])
    assert victim.read_text() == "n"


def test_the_installer_puts_rm_first_and_says_so_once(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_policy, "shim_dir", lambda: str(tmp_path / "bin"))
    source = turn_trash.installer_source(30)
    env_dir = tmp_path / "session-env"
    env_dir.mkdir()
    env = {**os.environ, "CLAUDE_ENV_FILE": str(env_dir / "hook-0.sh")}

    def start(source_kind):
        out = subprocess.run([sys.executable, "-c", source], env=env, capture_output=True, text=True,
                             input=json.dumps({"session_id": "s-1", "source": source_kind}), timeout=30)
        return out.stdout.strip()

    said = json.loads(start("startup"))["hookSpecificOutput"]["additionalContext"]
    assert "30 days" in said and "/bin/rm" in said
    assert os.access(tmp_path / "bin" / "rm", os.X_OK) and (tmp_path / "bin" / "unlink").is_symlink()
    assert f'export PATH="{tmp_path / "bin"}:$PATH"' in (env_dir / "hook-0.sh").read_text()
    assert (tmp_path / "bin" / "on-s-1").exists()
    assert start("resume") == ""
    assert start("compact") != ""
    out = subprocess.run([str(tmp_path / "bin" / "rm"), "-f", str(tmp_path / "absent")],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0


def test_no_env_file_installs_nothing_and_clears_the_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_policy, "shim_dir", lambda: str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "on-s-1").write_text("")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
    subprocess.run([sys.executable, "-c", turn_trash.installer_source(30)], env=env, timeout=30,
                   input=json.dumps({"session_id": "s-1", "source": "startup"}), text=True)
    assert not (tmp_path / "bin" / "on-s-1").exists() and not (tmp_path / "bin" / "rm").exists()


def test_every_copy_of_the_rebuildable_names_agrees():
    assert turn_rm.REBUILDABLE_DIRS == kinds.REBUILDABLE_DIRS == tool_policy_git._REBUILDABLE_DIRS
    assert turn_rm.TRASH_PREFIX == kinds.TRASH_PREFIX
