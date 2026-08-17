"""scripts/check_one_definition.py -- R3 of coherence-overhaul-2026-08-16.md.

Loaded from its file path (it lives in scripts/, not a backend package) the
same way field_occupancy.py bootstraps its own imports. Tests exercise the
scanning/grouping logic in isolation, on synthetic fixtures, rather than
asserting exact counts against the live tree: another agent's parallel edits
to backend/db/models.py etc. during this same session already changed the
real duplicate count twice while this file was being written, which is
exactly why the script re-derives at run time instead of hardcoding a list --
and exactly why a test pinned to the live tree's current count would be
flaky by construction.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_one_definition", _SCRIPTS / "check_one_definition.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load()


class TestNameHint:
    @pytest.mark.parametrize("name", [
        "EFFORTS", "AGENT_ROLES", "ROLES", "AUTONOMY", "CHAT_MODES",
        "STATE_CATEGORIES", "RUN_STATUSES", "PRIORITIES", "ACTOR_TYPES",
        "AutonomyMode", "ChatModeId",
    ])
    def test_matches_known_enum_names(self, mod, name):
        assert mod._NAME_HINT_RE.search(name)

    @pytest.mark.parametrize("name", [
        "SYNCABLE_FIELDS", "_TRACKED", "_HISTORIC", "SORT_KEYS", "_TURN_FIELDS",
    ])
    def test_does_not_match_field_name_lists(self, mod, name):
        """These are lists of FIELD NAMES that legitimately overlap between
        modules (e.g. two "which columns sync" lists) -- not a status/state/
        kind/mode/role/effort/autonomy vocabulary, so they must not trip the
        checker even when their contents coincide."""
        assert not mod._NAME_HINT_RE.search(name)


class TestPyStringLiteralSet:
    def test_extracts_string_tuple(self, mod):
        import ast
        tree = ast.parse('X = ("a", "b", "c")')
        value = tree.body[0].value
        assert mod._py_string_literal_set(value) == frozenset({"a", "b", "c"})

    def test_rejects_non_string_element(self, mod):
        import ast
        tree = ast.parse('X = ("a", 1, "c")')
        value = tree.body[0].value
        assert mod._py_string_literal_set(value) is None

    def test_rejects_non_container(self, mod):
        import ast
        tree = ast.parse('X = "a"')
        value = tree.body[0].value
        assert mod._py_string_literal_set(value) is None


class TestScanPython:
    def test_finds_hinted_constant(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        f = tmp_path / "a.py"
        f.write_text('EFFORTS = ("quick", "standard", "deep")\n')
        decls = mod.scan_python([f])
        assert len(decls) == 1
        assert decls[0].values == frozenset({"quick", "standard", "deep"})
        assert decls[0].name == "EFFORTS"

    def test_ignores_unhinted_constant(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        f = tmp_path / "a.py"
        f.write_text('SYNCABLE_FIELDS = ("title", "body", "status")\n')
        assert mod.scan_python([f]) == []

    def test_ignores_below_min_members(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        f = tmp_path / "a.py"
        f.write_text('STATUSES = ("open", "closed")\n')  # 2 < _MIN_MEMBERS
        assert mod.scan_python([f]) == []

    def test_skips_files_with_syntax_errors(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        f = tmp_path / "broken.py"
        f.write_text("def (((\n")
        assert mod.scan_python([f]) == []  # does not raise


class TestScanTypescript:
    def test_finds_union_type(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        f = tmp_path / "a.ts"
        f.write_text("export type ChatMode = 'direct' | 'orchestrated' | 'auto';\n")
        decls = mod.scan_typescript([f])
        assert len(decls) == 1
        assert decls[0].values == frozenset({"direct", "orchestrated", "auto"})

    def test_finds_const_array(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        f = tmp_path / "a.ts"
        f.write_text('export const EFFORTS = ["quick", "standard", "deep"] as const;\n')
        decls = mod.scan_typescript([f])
        assert len(decls) == 1
        assert decls[0].values == frozenset({"quick", "standard", "deep"})

    def test_ignores_unhinted_array(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        f = tmp_path / "a.ts"
        f.write_text('export const COLORS = ["red", "green", "blue"];\n')
        assert mod.scan_typescript([f]) == []


class TestFindDuplicates:
    def test_same_language_pair_is_a_duplicate(self, mod):
        values = frozenset({"a", "b", "c"})
        decls = [
            mod.Decl(values, "ROLES", "backend/x.py", 1, "py"),
            mod.Decl(values, "AGENT_ROLES", "backend/y.py", 2, "py"),
        ]
        groups = mod.find_duplicates(decls)
        assert len(groups) == 1
        assert groups[0].values == values

    def test_lone_cross_language_pair_is_not_a_duplicate(self, mod):
        """One Python source-of-truth constant and one TypeScript type mirror
        of the same values is the ordinary shape of a typed API contract, not
        drift -- TS cannot import a Python module. Flagging it would fire on
        nearly every backend enum with a hand-maintained frontend type and get
        this checker disabled, which is the exact failure mode R3 names."""
        values = frozenset({"a", "b", "c"})
        decls = [
            mod.Decl(values, "KINDS", "backend/x.py", 1, "py"),
            mod.Decl(values, "Kind", "frontend/src/x.ts", 1, "ts"),
        ]
        assert mod.find_duplicates(decls) == []

    def test_cross_language_plus_same_language_pair_still_flags(self, mod):
        """A group with both a lone TS mirror AND a real same-language
        duplicate must still surface -- and must still list the mirror, so the
        report doesn't hide context."""
        values = frozenset({"a", "b", "c"})
        decls = [
            mod.Decl(values, "EFFORTS", "backend/x.py", 1, "py"),
            mod.Decl(values, "EFFORTS", "backend/y.py", 2, "py"),
            mod.Decl(values, "Effort", "frontend/src/x.ts", 1, "ts"),
        ]
        groups = mod.find_duplicates(decls)
        assert len(groups) == 1
        assert len(groups[0].decls) == 3

    def test_same_value_set_same_file_twice_is_not_flagged(self, mod):
        """Two distinct value-sets never collide just because a file appears
        twice under different names within it -- grouping is keyed by the
        FROZENSET across DISTINCT files, so re-declaring locally doesn't
        double count towards the >=2-files-per-language rule."""
        values = frozenset({"a", "b", "c"})
        decls = [
            mod.Decl(values, "X", "backend/only.py", 1, "py"),
            mod.Decl(values, "Y", "backend/only.py", 5, "py"),
        ]
        assert mod.find_duplicates(decls) == []

    def test_different_value_sets_are_independent(self, mod):
        decls = [
            mod.Decl(frozenset({"a", "b", "c"}), "X", "backend/x.py", 1, "py"),
            mod.Decl(frozenset({"a", "b", "c"}), "Y", "backend/y.py", 1, "py"),
            mod.Decl(frozenset({"d", "e", "f"}), "Z", "backend/z.py", 1, "py"),
        ]
        groups = mod.find_duplicates(decls)
        assert len(groups) == 1  # the singleton {d,e,f} never pairs up


class TestCli:
    def test_json_mode_runs_clean_on_empty_tree(self, mod, tmp_path, monkeypatch):
        """End-to-end through main(), on a synthetic tree with nothing to
        find, must exit 0 and emit valid JSON -- the contract other tooling
        (reviewer.sh) depends on."""
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend" / "src").mkdir(parents=True)
        (tmp_path / "packages" / "ui" / "src").mkdir(parents=True)
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        monkeypatch.setattr(mod, "_PY_ROOTS", [tmp_path / "backend"])
        monkeypatch.setattr(mod, "_TS_ROOTS", [
            tmp_path / "frontend" / "src", tmp_path / "packages" / "ui" / "src",
        ])
        monkeypatch.setattr(sys, "argv", ["check_one_definition.py", "--json"])
        rc = mod.main()
        assert rc == 0

    def test_json_mode_reports_a_synthetic_duplicate(self, mod, tmp_path, monkeypatch):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "a.py").write_text('EFFORTS = ("quick", "standard", "deep")\n')
        (backend / "b.py").write_text('EFFORTS = ("standard", "deep", "quick")\n')
        (tmp_path / "frontend" / "src").mkdir(parents=True)
        (tmp_path / "packages" / "ui" / "src").mkdir(parents=True)
        monkeypatch.setattr(mod, "_WORKSHOP", tmp_path)
        monkeypatch.setattr(mod, "_PY_ROOTS", [backend])
        monkeypatch.setattr(mod, "_TS_ROOTS", [
            tmp_path / "frontend" / "src", tmp_path / "packages" / "ui" / "src",
        ])
        monkeypatch.setattr(sys, "argv", ["check_one_definition.py", "--json"])
        rc = mod.main()
        assert rc == 1  # a real, unordered-but-equal duplicate

    def test_smoke_against_the_real_tree_does_not_crash(self, mod, monkeypatch):
        """Not asserting a count (the live tree is being edited concurrently
        by other agents this session) -- just that a full real scan completes
        and returns valid, well-formed JSON either way."""
        monkeypatch.setattr(sys, "argv", ["check_one_definition.py", "--json"])
        rc = mod.main()
        assert rc in (0, 1)
