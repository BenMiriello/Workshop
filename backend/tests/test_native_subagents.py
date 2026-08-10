"""Claude Code's native subagents, read through the one parser.

They were the last mechanism defining agents outside `agents/<slug>.md`. Not a
rival FORMAT — same markdown and YAML frontmatter — but they carried none of the
declaration: no role, no scope, no statement of how they are reached. These tests
pin that they are now read as declaration records, that they stay OUT of the
roster (they are delegated, never dispatched), and that a foreign surface's agent
is named rather than silently dropped.
"""
import pytest
import yaml

from services import agents_registry as reg


@pytest.fixture
def native_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "NATIVE_AGENTS_DIR", tmp_path)
    return tmp_path


def write(d, slug, fm_lines, body="You are a probe."):
    (d / f"{slug}.md").write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n" + body + "\n")


class TestReading:
    def test_declaration_fields_are_read(self, native_dir):
        write(native_dir, "checker", [
            "name: checker",
            'description: "When to use it."',
            "role: reviewer",
            "scope: workshop",
            "purpose: Judges completeness.",
            "tools: ['Read', 'Bash']",
            "delegatable: true",
        ])
        r = reg.native_subagents()[0]
        assert r["slug"] == "checker"
        assert r["role"] == "reviewer"
        assert r["scope"] == "workshop"
        assert r["tools"] == ["Read", "Bash"]
        assert r["delegatable"] is True
        assert r["definition"] == "You are a probe."

    def test_delegated_not_dispatched(self, native_dir):
        """A native subagent only runs because something called it. It has no
        triggers to enable and no autonomy of its own to grant."""
        write(native_dir, "checker", ["name: checker"])
        r = reg.native_subagents()[0]
        assert r["dispatch"] == "delegated"
        assert r["source"] == "native"

    def test_wildcard_tools_are_flagged(self, native_dir):
        """`['*']` means every tool, and should not render as a one-item list
        containing an asterisk."""
        write(native_dir, "wide", ["name: wide", "tools: ['*']"])
        r = reg.native_subagents()[0]
        assert r["unrestricted_tools"] is True

    def test_a_narrow_list_is_not_flagged(self, native_dir):
        write(native_dir, "narrow", ["name: narrow", "tools: ['Read']"])
        assert reg.native_subagents()[0]["unrestricted_tools"] is False

    def test_name_falls_back_to_the_filename(self, native_dir):
        write(native_dir, "unnamed", ["description: no name field"])
        assert reg.native_subagents()[0]["slug"] == "unnamed"

    def test_a_missing_directory_is_empty_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(reg, "NATIVE_AGENTS_DIR", tmp_path / "nope")
        assert reg.native_subagents() == []


class TestForeignSurfaces:
    def test_the_bridges_agent_is_named_not_dropped(self, native_dir):
        """discord-handler belongs to the chat bridge. It shares this
        directory only because Claude Code has one. Filtering it silently would
        make an agent that exists invisible; naming its surface does not."""
        write(native_dir, "discord-handler", ["name: discord-handler"])
        r = reg.native_subagents()[0]
        assert r["foreign"] == "chat bridge dispatcher"

    def test_our_own_agents_are_not_foreign(self, native_dir):
        write(native_dir, "checker", ["name: checker"])
        assert reg.native_subagents()[0]["foreign"] is None


class TestNotRosterAgents:
    def test_native_subagents_do_not_enter_the_roster(self):
        """Different lifecycle, not different schema: a roster agent is
        dispatched (triggers, schedule, chat scope); a native one is delegated."""
        roster = {r["slug"] for r in reg.list_agents(include_retired=True)}
        native = {r["slug"] for r in reg.native_subagents()}
        assert not (roster & native)


class TestTheLiveFilesParse:
    """The regression that motivated reading these at all: work-reviewer's
    unquoted description contained "{complete: bool, ...}", so the frontmatter
    was invalid YAML and every field read as absent. Claude Code's parser
    tolerated it; a strict one got nothing. A lenient parser hides breakage
    until a second consumer looks."""

    def test_every_installed_native_agent_is_valid_yaml(self):
        for r in reg.native_subagents():
            with open(r["definition_path"]) as fh:
                text = fh.read()
            assert text.startswith("---\n"), r["definition_path"]
            front = text.split("---\n", 2)[1]
            yaml.safe_load(front)   # raises on the bug this pins

    def test_every_installed_native_agent_has_a_name(self):
        """The symptom of the parse failure: fields silently empty."""
        for r in reg.native_subagents():
            assert r["slug"], r["definition_path"]

    def test_the_reviewer_declares_itself_and_cannot_write(self):
        got = [r for r in reg.native_subagents() if r["slug"] == "work-reviewer"]
        if not got:
            pytest.skip("work-reviewer is not installed on this instance")
        r = got[0]
        assert r["role"] == "reviewer"
        assert r["unrestricted_tools"] is False
        # Its own hard rule: "Do not modify any files. The worker fixes; you judge."
        assert not ({"Write", "Edit", "MultiEdit"} & set(r["tools"]))
        # ...but Bash stays: running reviewer.sh, tsc and curl is how it reads.
        assert "Bash" in r["tools"]
