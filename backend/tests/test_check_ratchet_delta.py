"""Delta mode for the three whole-tree gates (2026-08-23).

WHAT BROKE, AND WHAT THESE TESTS PIN. `scripts/check_module_layout.py`,
`scripts/check_no_app_names.py` and `scripts/check_no_hardcoding.sh` all
measured the WHOLE tree against a STATIC absolute reference -- a pinned
baseline for the first two, plain zero for the third. Run that way as
`scripts/reviewer.sh`'s completion gate they rejected every run for debt the
run had inherited -- 2,066 loop jobs from 2026-07-18
onward all reported `tasks_done: 0`, because each worker collected a rejection
it had not caused, retried, and tripped ralph.sh's no-progress thrash guard.
`--since <ref>` compares the run's base ref against the working tree and fails
only on what the run itself made worse.

There are exactly two properties worth pinning, and they pull in opposite
directions, which is why both are here for all three scripts:

  * inherited debt must PASS   -- or the loop stalls again
  * a NEW regression must FAIL -- or the fix has quietly deleted the ratchet

Each test builds a THROWAWAY GIT REPO with the checker copied into it, rather
than asserting numbers against the live tree. The live counts move under any
run: while this fix was being written, `literal_sites` went 314 -> 302 in the
working tree from another agent's concurrent edits, which is the very coupling
the delta fix exists to break. A test pinned to live counts would be flaky by
construction -- the same reasoning `test_check_one_definition.py` records.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# The fixture app ids (`demoapp`, `otherapp`) are INVENTED, not this operator's.
# CLAUDE.md's no-app-names rule says "not a test fixture" in as many words, and
# `backend/` is scanned, so naming a real app here would add literal sites to
# the very ratchet under test -- which is exactly what happened on the first
# draft of this file: the gate caught it at `0 -> 10`, correctly. The checker
# derives its roster from the baseline it is given, so any id works.


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


def _run(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / script), *args],
        cwd=repo, capture_output=True, text=True,
    )


def _repo_with(tmp_path: Path, script: str, baseline_name: str, baseline: dict) -> Path:
    """A git repo holding just the checker under test and its pinned baseline."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in (script, "_ratchet_delta.py"):
        shutil.copy(_SCRIPTS / name, repo / "scripts" / name)
    (repo / "scripts" / baseline_name).write_text(json.dumps(baseline, indent=2) + "\n")
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _literals(repo: Path) -> int:
    """`literal_sites` as the checker currently counts it in this fixture.

    Read rather than hardcoded because the fixture repo contains a COPY of
    `check_no_app_names.py`, which carries app ids of its own in trailing
    comments on scanned lines. In the real tree those are already inside the
    pinned baseline; in the fixture they are just a constant offset, and the
    thing under test is the DELTA anyway."""
    r = _run(repo, "check_no_app_names.py", "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)["literal_sites"]


def _big_module(lines: int = 900) -> str:
    return "\n".join(f"x{i} = {i}" for i in range(lines)) + "\n"


class TestModuleLayoutDelta:
    """`big_file_count` is the metric driven here: it is the one that moves by a
    known amount from a single added file, so a synthetic fixture can state the
    expected delta exactly instead of asserting on an emergent graph property."""

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        # Pinned baseline says zero big files; the BASE COMMIT already has one.
        # That gap is the inherited debt every worker was being blamed for.
        repo = _repo_with(
            tmp_path, "check_module_layout.py", "module_layout_baseline.json",
            {"function_local_imports": 0, "scc_count": 0, "largest_scc_size": 0,
             "upward_violations": 0, "big_file_count": 0},
        )
        (repo / "backend" / "services").mkdir(parents=True)
        (repo / "backend" / "services" / "inherited.py").write_text(_big_module())
        _commit(repo, "base: one oversized module already here")
        return repo

    def test_absolute_mode_still_reports_the_inherited_debt(self, repo: Path):
        r = _run(repo, "check_module_layout.py")
        assert r.returncode == 1, r.stdout
        assert "big_file_count: 0 -> 1" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        """A diff that touches nothing relevant must not inherit the blame."""
        (repo / "docs").mkdir()
        (repo / "docs" / "note.md").write_text("docs-only change\n")
        r = _run(repo, "check_module_layout.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout
        # ...and the debt is still REPORTED, just not gated on.
        assert "PINNED baseline" in r.stdout
        assert "big_file_count: 0 -> 1" in r.stdout

    def test_delta_fails_on_a_new_regression(self, repo: Path):
        (repo / "backend" / "services" / "added.py").write_text(_big_module())
        r = _run(repo, "check_module_layout.py", "--since", "HEAD")
        assert r.returncode == 1, r.stdout
        assert "big_file_count: 1 -> 2" in r.stdout
        assert "THIS CHANGE made the backend layout worse" in r.stdout

    def test_delta_judges_against_the_ref_not_the_working_tree(self, repo: Path):
        """The regression must be caught even when it is already COMMITTED --
        the worker commits its own work, so a base-ref comparison is the only
        one that can see it."""
        (repo / "backend" / "services" / "added.py").write_text(_big_module())
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                               capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker's commit")
        r = _run(repo, "check_module_layout.py", "--since", base)
        assert r.returncode == 1, r.stdout
        assert "big_file_count: 1 -> 2" in r.stdout

    def test_unresolvable_ref_fails_loudly(self, repo: Path):
        """An empty extract would make every metric look like an improvement,
        so a bad ref must never be mistaken for a clean base."""
        r = _run(repo, "check_module_layout.py", "--since", "no-such-ref")
        assert r.returncode == 2
        assert "cannot read ref" in r.stderr


class TestNoAppNamesDelta:
    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        # Pinned at 1 literal; the base commit carries 3. Inherited debt again.
        repo = _repo_with(
            tmp_path, "check_no_app_names.py", "no_app_names_baseline.json",
            {"watched": ["demoapp"], "content_files": 0,
             "import_sites": 0, "literal_sites": 1},
        )
        (repo / "backend").mkdir(parents=True)
        (repo / "backend" / "legacy.py").write_text(
            'A = "demoapp"\nB = "demoapp"\nC = "demoapp"\n'
        )
        _commit(repo, "base: three inherited app-name literals")
        return repo

    def test_absolute_mode_still_reports_the_inherited_debt(self, repo: Path):
        n = _literals(repo)
        r = _run(repo, "check_no_app_names.py")
        assert r.returncode == 1, r.stdout
        assert f"FAIL literal_sites: 1 -> {n}" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        n = _literals(repo)
        (repo / "docs").mkdir()
        (repo / "docs" / "note.md").write_text("docs-only change\n")
        r = _run(repo, "check_no_app_names.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout
        assert "PINNED baseline" in r.stdout
        assert f"literal_sites: pinned 1 -> now {n}" in r.stdout

    def test_delta_fails_on_a_new_literal(self, repo: Path):
        n = _literals(repo)
        (repo / "backend" / "legacy.py").write_text(
            'A = "demoapp"\nB = "demoapp"\nC = "demoapp"\nD = "demoapp"\n'
        )
        r = _run(repo, "check_no_app_names.py", "--since", "HEAD")
        assert r.returncode == 1, r.stdout
        assert f"FAIL literal_sites: {n} -> {n + 1} against the base ref HEAD" in r.stdout

    def test_delta_fails_on_a_new_literal_in_a_committed_file(self, repo: Path):
        n = _literals(repo)
        (repo / "backend" / "added.py").write_text('D = "demoapp"\n')
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                               capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker's commit")
        r = _run(repo, "check_no_app_names.py", "--since", base)
        assert r.returncode == 1, r.stdout
        assert f"literal_sites: {n} -> {n + 1}" in r.stdout

    def test_delta_reports_an_improvement_rather_than_failing(self, repo: Path):
        n = _literals(repo)
        (repo / "backend" / "legacy.py").write_text('A = "demoapp"\n')
        r = _run(repo, "check_no_app_names.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout
        assert f"literal_sites improved {n} -> {n - 2}" in r.stdout

    def test_roster_is_taken_from_the_working_tree_for_both_sides(self, repo: Path):
        """The roster is partly drawn from untracked instance state, which no
        `git archive` extract can contain. Re-deriving it per side would watch
        fewer apps at the base, undercount it, and report a phantom regression.

        Here `otherapp` is added to the roster ONLY via untracked
        `.runtime/installed-apps.json`, and the literal exists on BOTH sides --
        so with one shared roster this is a no-op, and with a per-side roster it
        would read as 0 -> 1."""
        (repo / ".runtime").mkdir()
        (repo / ".runtime" / "installed-apps.json").write_text(
            json.dumps({"installed": ["otherapp"]})
        )
        (repo / ".gitignore").write_text(".runtime/\n")
        (repo / "backend" / "legacy.py").write_text(
            'A = "demoapp"\nB = "demoapp"\nC = "demoapp"\nE = "otherapp"\n'
        )
        _commit(repo, "otherapp literal present at the base too")
        (repo / "docs").mkdir()
        (repo / "docs" / "note.md").write_text("docs-only change\n")
        r = _run(repo, "check_no_app_names.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "otherapp" in r.stdout  # the roster really did pick it up


class TestNoHardcodingDelta:
    """`scripts/check_no_hardcoding.sh` — the third gate with the same defect.

    It is bash, not Python, and it was the WORST of the three: absolute,
    whole-tree AND unconditional in reviewer.sh, so 25 inherited identity
    references in files no worker had touched rejected every run in the tree.
    Its `--since` mode reuses `_ratchet_delta.extract_into` through that
    module's `__main__`, so there is still exactly ONE definition of "the base
    tree" across all three checkers.

    The needles come from the MACHINE, so the fixture pins one it controls:
    repo-local `git config user.name`. `WORKSHOP` is passed explicitly because
    reviewer.sh exports it — without that the script would helpfully scan the
    real workshop instead of the fixture.
    """

    NAME = "fixtureoperator"
    SCRIPT = "check_no_hardcoding.sh"

    def _run_sh(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(repo / "scripts" / self.SCRIPT), *args],
            cwd=repo, capture_output=True, text=True,
            env={**os.environ, "WORKSHOP": str(repo)},
        )

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        for name in (self.SCRIPT, "_ratchet_delta.py"):
            shutil.copy(_SCRIPTS / name, repo / "scripts" / name)
        _git(repo, "init", "-q", "-b", "main")
        # The identity this fixture's "machine" answers with. Repo-local, so it
        # cannot leak into the real tree and cannot be the real operator's.
        _git(repo, "config", "user.name", self.NAME)
        _git(repo, "config", "user.email", f"{self.NAME}@fixture.invalid")
        (repo / "backend").mkdir()
        (repo / "backend" / "legacy.py").write_text(
            f'A = "{self.NAME}"\nB = "{self.NAME}"\nC = "{self.NAME}"\n'
        )
        _commit(repo, "base: three inherited identity references")
        return repo

    def test_absolute_mode_still_reports_the_inherited_debt(self, repo: Path):
        """A bare run is unchanged: the real debt must stay visible to a human."""
        r = self._run_sh(repo)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "3 reference(s) to this deployment's own identity" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        """The whole point: a run that added no identity is not blamed for three."""
        (repo / "backend" / "unrelated.py").write_text("VALUE = 1\n")
        _commit(repo, "worker's unrelated change")
        r = self._run_sh(repo, "--since", "HEAD~1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "clean vs HEAD~1" in r.stdout
        # ...and the inherited debt is still REPORTED, just not gated on.
        assert "not gating" in r.stdout
        assert "identity: 3 reference(s), 3 of them already present" in r.stdout

    def test_delta_fails_on_a_newly_introduced_identity(self, repo: Path):
        """And the other direction: one new reference anywhere still fails, or
        the fix has quietly deleted the check."""
        (repo / "backend" / "legacy.py").write_text(
            f'A = "{self.NAME}"\nB = "{self.NAME}"\nC = "{self.NAME}"\nD = "{self.NAME}"\n'
        )
        r = self._run_sh(repo, "--since", "HEAD")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "1 NEW reference(s) to this deployment's own identity" in r.stdout
        assert "was 3 at HEAD, now 4" in r.stdout

    def test_delta_fails_on_a_new_identity_in_a_committed_file(self, repo: Path):
        """The worker commits its own work, so a base-ref comparison is the only
        one that can still see what it added."""
        (repo / "backend" / "added.py").write_text(f'D = "{self.NAME}"\n')
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker's commit")
        r = self._run_sh(repo, "--since", base)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "1 NEW" in r.stdout
        assert "backend/added.py" in r.stdout

    def test_delta_catches_a_file_this_diff_never_touched(self, repo: Path):
        """`--since`, not `--only-changed`: moving an identity into an untouched
        file is exactly what a per-file scoping would miss."""
        (repo / "backend" / "other.py").write_text("PLACEHOLDER = 0\n")
        _commit(repo, "base has an innocent second file")
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        (repo / "backend" / "other.py").write_text(f'MOVED = "{self.NAME}"\n')
        r = self._run_sh(repo, "--since", base)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "backend/other.py" in r.stdout

    def test_removing_an_identity_is_not_a_failure(self, repo: Path):
        (repo / "backend" / "legacy.py").write_text(f'A = "{self.NAME}"\n')
        r = self._run_sh(repo, "--since", "HEAD")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_unresolvable_ref_fails_loudly(self, repo: Path):
        """An empty extract would read as "the base had nothing", turning every
        inherited reference into a brand-new one."""
        r = self._run_sh(repo, "--since", "no-such-ref")
        assert r.returncode == 2, r.stdout + r.stderr
        assert "cannot read ref" in r.stderr


# ---------------------------------------------------------------------------
# Tests for the four new delta-capable checkers (added 2026-08-23).
# Each class exercises exactly the two properties that matter:
#   * inherited debt must PASS   -- or the loop stalls again
#   * a NEW regression must FAIL -- or the fix has quietly deleted the ratchet
# ---------------------------------------------------------------------------


def _backend_module(name: str = "mutglobal") -> str:
    """A backend module with one mutable process global (container-mutate evidence)."""
    return f'_{name.upper()}: dict = {{}}\n\ndef push(v):\n    _{name.upper()}[v] = 1\n'


class TestNoProcessGlobalsDelta:
    """`mutable_global_count` is the metric: a backend module that has a dict
    mutated in-place is counted as a process global."""

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        # Pinned baseline says zero globals; base commit already has one.
        repo = _repo_with(
            tmp_path, "check_no_process_globals.py", "no_process_globals_baseline.json",
            {"mutable_global_count": 0},
        )
        (repo / "backend" / "services").mkdir(parents=True)
        (repo / "backend" / "services" / "inherited.py").write_text(_backend_module())
        _commit(repo, "base: one inherited mutable global")
        return repo

    def test_absolute_mode_still_reports_the_inherited_debt(self, repo: Path):
        r = _run(repo, "check_no_process_globals.py")
        assert r.returncode == 1, r.stdout
        assert "mutable_global_count" in r.stdout or "REGRESSION" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        (repo / "docs").mkdir()
        (repo / "docs" / "note.md").write_text("docs-only\n")
        r = _run(repo, "check_no_process_globals.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "no regression" in r.stdout

    def test_delta_fails_on_a_planted_regression(self, repo: Path):
        (repo / "backend" / "services" / "added.py").write_text(_backend_module("new"))
        r = _run(repo, "check_no_process_globals.py", "--since", "HEAD")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "REGRESSION" in r.stdout

    def test_delta_fails_on_a_regression_in_a_committed_file(self, repo: Path):
        (repo / "backend" / "services" / "added.py").write_text(_backend_module("new"))
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker's commit")
        r = _run(repo, "check_no_process_globals.py", "--since", base)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "REGRESSION" in r.stdout

    def test_unresolvable_ref_fails_loudly(self, repo: Path):
        r = _run(repo, "check_no_process_globals.py", "--since", "no-such-ref")
        assert r.returncode == 2
        assert "cannot read ref" in r.stderr


class TestNoStateInSourceDelta:
    """A tracked state-like JSON under a source dir is the offender."""

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        # Pinned baseline has empty allowed list; base commit already has a state file.
        repo = _repo_with(
            tmp_path, "check_no_state_in_source.py", "no_state_in_source_baseline.json",
            {"allowed": [], "_note": "empty"},
        )
        (repo / "backend").mkdir(parents=True)
        (repo / "backend" / "inherited_state.json").write_text('{"key": "value"}\n')
        _commit(repo, "base: one inherited state file")
        return repo

    def test_absolute_mode_still_reports_the_inherited_debt(self, repo: Path):
        r = _run(repo, "check_no_state_in_source.py")
        assert r.returncode == 1, r.stdout
        assert "backend/inherited_state.json" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        (repo / "docs").mkdir()
        (repo / "docs" / "note.md").write_text("docs-only\n")
        r = _run(repo, "check_no_state_in_source.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_delta_fails_on_a_planted_regression(self, repo: Path):
        (repo / "backend" / "new_state.json").write_text('{"x": 1}\n')
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker adds a new state file")
        r = _run(repo, "check_no_state_in_source.py", "--since", base)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "backend/new_state.json" in r.stdout

    def test_unresolvable_ref_fails_loudly(self, repo: Path):
        r = _run(repo, "check_no_state_in_source.py", "--since", "no-such-ref")
        assert r.returncode == 2
        assert "cannot read ref" in r.stderr


def _chat_context_with_path(path_str: str) -> str:
    """Backend chat_context.py content containing a raw OS path."""
    return f'# agent-facing text\nCWD_BRIEFING = "Working directory: {path_str}"\n'


class TestNoRawPathsDelta:
    """A raw OS path token in a scanned agent-facing surface is the offender."""

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        # Pinned baseline has empty allowed list; base has a raw path in chat_context.
        repo = _repo_with(
            tmp_path, "check_no_raw_paths.py", "no_raw_paths_baseline.json",
            {"allowed": [], "_note": "empty"},
        )
        (repo / "backend" / "services").mkdir(parents=True)
        (repo / "backend" / "services" / "chat_context.py").write_text(
            _chat_context_with_path("/home/user/inherited_path")
        )
        _commit(repo, "base: one inherited raw path in chat_context.py")
        return repo

    def test_absolute_mode_still_reports_the_inherited_debt(self, repo: Path):
        r = _run(repo, "check_no_raw_paths.py")
        assert r.returncode == 1, r.stdout
        assert "/home/user/inherited_path" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        (repo / "docs").mkdir()
        (repo / "docs" / "note.md").write_text("docs-only\n")
        r = _run(repo, "check_no_raw_paths.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout + r.stderr

    def test_delta_fails_on_a_planted_regression(self, repo: Path):
        existing = (repo / "backend" / "services" / "chat_context.py").read_text()
        (repo / "backend" / "services" / "chat_context.py").write_text(
            existing + '\nNEW_PATH = "/home/user/new_agent_path"\n'
        )
        r = _run(repo, "check_no_raw_paths.py", "--since", "HEAD")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "/home/user/new_agent_path" in r.stdout

    def test_delta_fails_on_a_regression_in_a_committed_file(self, repo: Path):
        existing = (repo / "backend" / "services" / "chat_context.py").read_text()
        (repo / "backend" / "services" / "chat_context.py").write_text(
            existing + '\nNEW_PATH = "/home/user/committed_path"\n'
        )
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker adds a raw path")
        r = _run(repo, "check_no_raw_paths.py", "--since", base)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "/home/user/committed_path" in r.stdout

    def test_unresolvable_ref_fails_loudly(self, repo: Path):
        r = _run(repo, "check_no_raw_paths.py", "--since", "no-such-ref")
        assert r.returncode == 2
        assert "cannot read ref" in r.stderr


def _make_fixture_schema(opaque_schemas: "dict[str, list[str]]") -> dict:
    """Build a minimal OpenAPI schema with the given opaque fields per schema."""
    components: dict = {}
    for schema_name, fields in opaque_schemas.items():
        props = {f: {"type": "object"} for f in fields}
        components[schema_name] = {"type": "object", "title": schema_name, "properties": props}
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "0"},
        "paths": {},
        "components": {"schemas": components},
    }


_FIXTURE_GEN_OPENAPI = """\
#!/usr/bin/env python3
\"\"\"Minimal gen_openapi.py stub for the check_opaque_fields delta test.\"\"\"
import argparse, json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--schema-out", required=True)
ap.add_argument("--schema-only", action="store_true")
ap.add_argument("--quiet", action="store_true")
args = ap.parse_args()

fixture = Path(__file__).resolve().parents[1] / "backend" / "fixture_schema.json"
schema = json.loads(fixture.read_text()) if fixture.exists() else {
    "openapi": "3.0.0", "info": {"title": "T", "version": "0"},
    "paths": {}, "components": {"schemas": {}},
}
Path(args.schema_out).write_text(json.dumps(schema))
"""


class TestOpaqueFieldsDelta:
    """`opaque_fields` count is the metric: a schema field typed as `{type: object}`
    with no properties counts as opaque."""

    @pytest.fixture()
    def repo(self, tmp_path: Path) -> Path:
        # Pinned baseline at 0 opaque fields; base commit has 1.
        repo = _repo_with(
            tmp_path, "check_opaque_fields.py", "opaque_fields_baseline.json",
            {"schemas_with_opaque_fields": 0, "opaque_fields": 0, "per_schema": {}},
        )
        # Stub gen_openapi.py that reads backend/fixture_schema.json
        (repo / "scripts" / "gen_openapi.py").write_text(_FIXTURE_GEN_OPENAPI)
        (repo / "backend").mkdir(parents=True)
        # Base commit: one schema with one opaque field (inherited debt)
        (repo / "backend" / "fixture_schema.json").write_text(
            json.dumps(_make_fixture_schema({"ThingResponse": ["data"]}), indent=2)
        )
        _commit(repo, "base: one inherited opaque field")
        return repo

    def test_absolute_mode_still_reports_the_inherited_debt(self, repo: Path):
        r = _run(repo, "check_opaque_fields.py")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "ThingResponse" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        (repo / "docs").mkdir()
        (repo / "docs" / "note.md").write_text("docs-only\n")
        r = _run(repo, "check_opaque_fields.py", "--since", "HEAD")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "no regression" in r.stdout

    def test_delta_fails_on_a_planted_regression(self, repo: Path):
        # Add a second schema with an opaque field
        (repo / "backend" / "fixture_schema.json").write_text(
            json.dumps(_make_fixture_schema(
                {"ThingResponse": ["data"], "NewResponse": ["extra"]}
            ), indent=2)
        )
        r = _run(repo, "check_opaque_fields.py", "--since", "HEAD")
        assert r.returncode == 1, r.stdout + r.stderr
        assert "NewResponse" in r.stdout

    def test_delta_fails_on_a_regression_in_a_committed_file(self, repo: Path):
        (repo / "backend" / "fixture_schema.json").write_text(
            json.dumps(_make_fixture_schema(
                {"ThingResponse": ["data"], "CommittedResponse": ["bad"]}
            ), indent=2)
        )
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker adds an opaque field")
        r = _run(repo, "check_opaque_fields.py", "--since", base)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "CommittedResponse" in r.stdout

    def test_unresolvable_ref_fails_loudly(self, repo: Path):
        r = _run(repo, "check_opaque_fields.py", "--since", "no-such-ref")
        assert r.returncode == 2
        assert "cannot read ref" in r.stderr
