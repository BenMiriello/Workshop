"""A build output that names a path this side cannot see must not fail the first build.

An agent's sandbox sees the tree at /workspace, so a venv it creates carries that
path in every console script's shebang. The backend's build then ran
`./.venv/bin/pip` and got exit 127 ("build exited 127"), and only a restart, which
skips the build, started the app. The runner stays language-blind; the table in
`services/relocated_artifacts.py` says what is broken.
"""
from services import relocated_artifacts as ra


def _venv(root, shebang, script="pip"):
    f = root / ".venv" / "bin" / script
    f.parent.mkdir(parents=True)
    f.write_text(f"#!{shebang}\n")
    return root / ".venv"


def test_venv_with_a_missing_interpreter_in_any_script_is_removed(tmp_path):
    server = tmp_path / "server"
    venv = _venv(server, "/no/such/mount/python3.12", script="uvicorn")
    assert ra.drop_broken(tmp_path, server) == [venv]
    assert not venv.exists()


def test_venv_at_the_project_root_is_found_too(tmp_path):
    venv = _venv(tmp_path, "/no/such/mount/python3.12")
    ra.drop_broken(tmp_path, tmp_path / "server")
    assert not venv.exists()


def test_working_venv_is_kept(tmp_path):
    venv = _venv(tmp_path, "/bin/sh")
    assert ra.drop_broken(tmp_path, tmp_path) == []
    assert venv.exists()


def test_symlinked_interpreter_is_not_read_as_a_script(tmp_path):
    venv = _venv(tmp_path, "/bin/sh")
    (venv / "bin" / "python").symlink_to("/no/such/target")
    assert ra.drop_broken(tmp_path, tmp_path) == []


def _venv_seen_from_another_mount(root):
    """A working venv here whose one console script was written by a pip that
    saw the tree at another path."""
    import sys
    venv = root / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python3").symlink_to(sys.executable)
    script = venv / "bin" / "tool"
    script.write_text("#!/other/mount/server/.venv/bin/python3\n"
                      "# -*- coding: utf-8 -*-\n"
                      "import sys\n"
                      "print('ran', sys.argv[1:])\n")
    script.chmod(0o755)
    return venv, script


def test_venv_whose_scripts_name_its_own_interpreter_elsewhere_is_repaired(tmp_path):
    import subprocess
    venv, script = _venv_seen_from_another_mount(tmp_path)
    keep = venv / "lib" / "site-packages" / "installed_by_hand"
    keep.mkdir(parents=True)
    assert ra.drop_broken(tmp_path, tmp_path) == []
    assert keep.exists()
    r = subprocess.run([str(script), "a b", "c"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ran ['a b', 'c']"


def test_repaired_script_runs_through_a_symlink_from_outside_the_venv(tmp_path):
    import subprocess
    venv, script = _venv_seen_from_another_mount(tmp_path)
    ra.drop_broken(tmp_path, tmp_path)
    link = tmp_path / "tool-link"
    link.symlink_to(script)
    r = subprocess.run([str(link)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_venv_whose_own_interpreter_is_gone_is_still_removed(tmp_path):
    venv, _ = _venv_seen_from_another_mount(tmp_path)
    (venv / "bin" / "python3").unlink()
    (venv / "bin" / "python3").symlink_to("/no/such/python3")
    assert ra.drop_broken(tmp_path, tmp_path) == [venv]


def test_a_row_for_another_ecosystem_is_honoured(tmp_path, monkeypatch):
    (tmp_path / "target").mkdir()
    row = ra.StackArtifact("cargo target", "target", lambda root: True)
    monkeypatch.setattr(ra, "STACK_ARTIFACTS", (row,))
    ra.drop_broken(tmp_path, tmp_path)
    assert not (tmp_path / "target").exists()
