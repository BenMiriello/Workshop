"""scripts/doc_status.py -- R1 of coherence-overhaul-2026-08-16.md.

Loaded from its file path the same way test_check_one_definition.py loads its
sibling script. Two kinds of test here:

  - synthetic fixtures for the status-line regex and the NOT-BUILT detector,
    covering every banner STYLE actually used in docs/platform-redesign/
    (plain "Status:", a bulleted "- Status:", a bold blockquote "> **STATUS:
    ...**") -- these are stable and should stay pinned exactly.
  - one integration check against the REAL docs/platform-redesign/ tree,
    asserting that the four docs T6 corrected
    (primitive-roles-2026-07-18.md, resource-access-protocol-2026-07-16.md,
    resource-access-interface-spec-2026-07-18.md,
    chat-scope-design-2026-07-18.md) no longer read as misleading. This is a
    regression guard on the fix itself, not just the tool: if a later edit to
    one of those banners reintroduces a bare "NOT BUILT" next to text that
    still names the built artifact, this test catches it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("doc_status", _SCRIPTS / "doc_status.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load()


class TestStatusBlock:
    @pytest.mark.parametrize("text,expected", [
        ("Status: accepted\n", "accepted"),
        ("- Status: accepted\n", "accepted"),
        ("**Status**: plan, being implemented.\n", "plan, being implemented."),
    ])
    def test_recognises_every_banner_style_in_use(self, mod, text, expected):
        status, _para = mod._status_block(f"# Title\n\n{text}")
        assert status == expected

    def test_recognises_bold_blockquote_style(self, mod):
        text = (
            "# Title\n\n"
            "> **STATUS: DESIGN. NOT BUILT.** Some more banner text.\n"
        )
        status, para = mod._status_block(text)
        assert status is not None
        assert status.startswith("DESIGN")
        assert "NOT BUILT" in para

    def test_no_status_line_returns_none(self, mod):
        status, para = mod._status_block("# Title\n\nJust a paragraph, no banner.\n")
        assert status is None
        assert para == ""

    def test_status_mention_deep_in_body_is_not_a_banner(self, mod):
        """A doc that merely discusses "status" somewhere past the opening
        block (outside `_SCAN_LINES`) must not be picked up as if it were the
        declared banner -- that would misreport a doc with no real status."""
        body_lines = ["line filler"] * 45
        text = "# Title\n\n" + "\n".join(body_lines) + "\nStatus: too late\n"
        status, _ = mod._status_block(text)
        assert status is None


class TestNotBuiltRegex:
    @pytest.mark.parametrize("phrase", [
        "NOT BUILT", "Not-built", "not built", "No code has been written",
        "No broker exists",
    ])
    def test_matches_known_phrasings(self, mod, phrase):
        assert mod._NOT_BUILT_RE.search(phrase)

    @pytest.mark.parametrize("phrase", [
        "PARTIALLY BUILT", "LARGELY BUILT", "accepted", "shipped 2026-08-16",
    ])
    def test_does_not_match_corrected_phrasings(self, mod, phrase):
        assert not mod._NOT_BUILT_RE.search(phrase)


class TestScan:
    def test_flags_doc_with_no_status(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_DOCS_DIR", tmp_path)
        (tmp_path / "a.md").write_text("# A\n\nno banner here.\n")
        report = mod.scan()
        assert report["a.md"]["has_status"] is False

    def test_flags_misleading_when_artifact_exists(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_DOCS_DIR", tmp_path)
        (tmp_path / "b.md").write_text(
            "# B\n\n> **STATUS: NOT BUILT.** Nothing here yet.\n"
        )
        monkeypatch.setitem(
            mod.ARTIFACT_CHECKS, "b.md", [("fake thing", lambda: True)]
        )
        report = mod.scan()
        assert report["b.md"]["claims_not_built"] is True
        assert report["b.md"]["misleading"] is True
        assert report["b.md"]["artifacts_that_exist"] == ["fake thing"]

    def test_not_misleading_when_artifact_absent(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_DOCS_DIR", tmp_path)
        (tmp_path / "c.md").write_text(
            "# C\n\n> **STATUS: NOT BUILT.** Nothing here yet.\n"
        )
        monkeypatch.setitem(
            mod.ARTIFACT_CHECKS, "c.md", [("fake thing", lambda: False)]
        )
        report = mod.scan()
        assert report["c.md"].get("misleading") is not True

    def test_no_status_is_never_misleading_by_itself(self, mod, tmp_path, monkeypatch):
        """A doc with NO banner at all does not claim anything, so it cannot
        be "misleading" -- it's a separate, weaker finding (`has_status`)."""
        monkeypatch.setattr(mod, "_DOCS_DIR", tmp_path)
        (tmp_path / "d.md").write_text("# D\n\nno banner.\n")
        report = mod.scan()
        assert report["d.md"].get("misleading") is not True


class TestCli:
    def test_json_mode_smoke(self, mod, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["doc_status.py", "--json"])
        rc = mod.main()
        assert rc == 0


class TestRealDocsRegression:
    """Guards the actual fix T6 made. See coherence-overhaul-2026-08-16.md R1."""

    CORRECTED_DOCS = [
        "primitive-roles-2026-07-18.md",
        "resource-access-protocol-2026-07-16.md",
        "resource-access-interface-spec-2026-07-18.md",
        "chat-scope-design-2026-07-18.md",
    ]

    def test_none_of_the_corrected_docs_still_read_as_misleading(self, mod):
        if not (_REPO / "docs" / "platform-redesign").is_dir():
            pytest.skip("docs/platform-redesign/ not present in this checkout")
        report = mod.scan()
        for name in self.CORRECTED_DOCS:
            if name not in report:
                pytest.skip(f"{name} not present in this checkout")
            assert report[name].get("misleading") is not True, (
                f"{name} banner regressed: it claims NOT BUILT for something "
                f"that exists again: {report[name].get('artifacts_that_exist')}"
            )

    def test_the_corrected_docs_now_declare_a_status(self, mod):
        if not (_REPO / "docs" / "platform-redesign").is_dir():
            pytest.skip("docs/platform-redesign/ not present in this checkout")
        report = mod.scan()
        for name in self.CORRECTED_DOCS:
            if name not in report:
                pytest.skip(f"{name} not present in this checkout")
            assert report[name]["has_status"] is True
