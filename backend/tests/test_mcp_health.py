"""`claude mcp list`, parsed.

Neither sample below is a hand-written approximation. Both are verbatim stdout
from this host on 2026-08-10, exit code 0, ellipses and check marks included.

There are TWO because the CLI prints two different things: SAMPLE_TTY is what a
terminal gets, SAMPLE_PIPED is what a subprocess gets — different glyphs
(`✔`/`✘` rather than `✓`/`✗`), a failure REASON after an em-dash, and no
`claude.ai` connectors at all. The piped form is the one the backend actually
receives, and parsing it correctly was not guessable from the terminal form.
That is the whole value of pinning the real thing: a test written against what
we WISH the CLI emitted would pass while the panel showed an empty list.

The shapes that make this harder than "split on the separators" each have their
own test: a name containing colons (`plugin:discord:discord`), a command
containing dashes (`npx -y @a-bonus/google-docs-mcp`), and a reason containing
both (`-32000: MCP error -32000: Connection closed`).
"""
import asyncio
import time

from services import mcp_health
from services.context_report import TtlCache
from services.mcp_health import (
    classify_origin, classify_state, parse_mcp_list, summarize,
)


SAMPLE_TTY = """Checking MCP server health…

claude.ai Slack: https://mcp.slack.com/mcp - ! Needs authentication
claude.ai Google Calendar: https://calendarmcp.googleapis.com/mcp/v1 - ! Needs authentication
claude.ai Google Drive: https://drivemcp.googleapis.com/mcp/v1 - ✓ Connected
claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ! Needs authentication
plugin:discord:discord: bun run --cwd /home/clawd-a/.claude/plugins/cache/clawd-a-plugins/discord/0.0.4 --shell=bun --silent start - ✗ Failed to connect
plaid: node /home/clawd-a/clawdbot/plaid-mcp/server.js - ✓ Connected
google-docs: npx -y @a-bonus/google-docs-mcp - ✗ Failed to connect
playwright: npx @playwright/mcp@latest --headless --browser chromium - ✗ Failed to connect
"""

SAMPLE_PIPED = """Checking MCP server health…

plugin:discord:discord: bun run --cwd /home/clawd-a/.claude/plugins/cache/clawd-a-plugins/discord/0.0.4 --shell=bun --silent start - ✘ Failed to connect — ENOENT: Executable not found in $PATH: "bun"
plaid: node /home/clawd-a/clawdbot/plaid-mcp/server.js - ✔ Connected
google-docs: npx -y @a-bonus/google-docs-mcp - ✘ Failed to connect — -32000: MCP error -32000: Connection closed
playwright: npx @playwright/mcp@latest --headless --browser chromium - ✘ Failed to connect — -32000: MCP error -32000: Connection closed
"""

SAMPLE = SAMPLE_TTY


def _by_name(raw=SAMPLE_TTY):
    return {s["name"]: s for s in parse_mcp_list(raw)}


class TestParseTheRealOutput:
    def test_every_configured_server_is_found(self):
        assert len(parse_mcp_list(SAMPLE)) == 8

    def test_the_health_banner_is_not_a_server(self):
        assert "Checking MCP server health…" not in _by_name()

    def test_the_three_states_this_host_actually_reports(self):
        s = _by_name()
        assert (s["plaid"]["kind"], s["plaid"]["state"]) == ("ok", "Connected")
        assert (s["claude.ai Slack"]["kind"], s["claude.ai Slack"]["state"]) \
            == ("warn", "Needs authentication")
        assert (s["playwright"]["kind"], s["playwright"]["state"]) \
            == ("danger", "Failed to connect")

    def test_a_name_may_contain_colons(self):
        # `plugin:discord:discord: bun run ...` — the split is the FIRST ": ",
        # not the first ":", or the name becomes "plugin".
        s = _by_name()["plugin:discord:discord"]
        assert s["kind"] == "danger"
        assert s["target"].startswith("bun run --cwd /home/clawd-a/.claude/plugins")

    def test_a_command_may_contain_dashes(self):
        # The state split is the LAST " - ". `npx -y @a-bonus/...` must not be
        # mistaken for one, or the target is truncated and the state is garbage.
        s = _by_name()["google-docs"]
        assert s["target"] == "npx -y @a-bonus/google-docs-mcp"
        assert s["state"] == "Failed to connect"

    def test_the_target_is_kept_whole(self):
        s = _by_name()
        assert s["claude.ai Gmail"]["target"] == "https://gmailmcp.googleapis.com/mcp/v1"
        assert s["playwright"]["target"] == "npx @playwright/mcp@latest --headless --browser chromium"

    def test_origin_and_transport_come_from_the_line(self):
        s = _by_name()
        assert (s["claude.ai Gmail"]["origin"], s["claude.ai Gmail"]["transport"]) == ("claude.ai", "http")
        assert (s["plugin:discord:discord"]["origin"], s["plugin:discord:discord"]["transport"]) == ("plugin", "stdio")
        assert (s["plaid"]["origin"], s["plaid"]["transport"]) == ("local", "stdio")

    def test_broken_servers_sort_to_the_top(self):
        # A broken server must be visibly broken at a glance; the list that
        # opens with the failure has already answered the question.
        kinds = [s["kind"] for s in parse_mcp_list(SAMPLE)]
        assert kinds[:3] == ["danger", "danger", "danger"]
        assert kinds[-1] == "ok"

    def test_the_summary_matches_this_host(self):
        c = summarize(parse_mcp_list(SAMPLE))
        assert c == {"ok": 2, "warn": 3, "danger": 3, "stopped": 0, "total": 8}


class TestParseWhatTheBACKENDActuallyReceives:
    """The piped form. Different glyphs, a reason, and fewer servers."""

    def test_the_other_glyph_set_is_read_the_same(self):
        # `✔`/`✘` down a pipe, `✓`/`✗` on a terminal. Getting this wrong made
        # every server on the real code path read as "Unknown".
        s = {x["name"]: x for x in parse_mcp_list(SAMPLE_PIPED)}
        assert s["plaid"]["kind"] == "ok"
        assert s["playwright"]["kind"] == "danger"

    def test_the_reason_is_kept_and_kept_out_of_the_state(self):
        # "bun is not in $PATH" is the only string here that tells the operator
        # what to do; folding it into the state would make a pill the width of
        # the panel, and dropping it would waste the feature.
        s = {x["name"]: x for x in parse_mcp_list(SAMPLE_PIPED)}
        d = s["plugin:discord:discord"]
        assert d["state"] == "Failed to connect"
        assert d["detail"] == 'ENOENT: Executable not found in $PATH: "bun"'

    def test_a_reason_full_of_dashes_and_colons_survives_intact(self):
        s = {x["name"]: x for x in parse_mcp_list(SAMPLE_PIPED)}
        g = s["google-docs"]
        assert g["target"] == "npx -y @a-bonus/google-docs-mcp"
        assert g["detail"] == "-32000: MCP error -32000: Connection closed"

    def test_a_terminal_line_has_no_reason_and_says_so(self):
        assert _by_name()["playwright"]["detail"] == ""

    def test_this_host_via_a_pipe(self):
        c = summarize(parse_mcp_list(SAMPLE_PIPED))
        assert c == {"ok": 1, "warn": 0, "danger": 3, "stopped": 0, "total": 4}


class TestClassify:
    def test_not_connected_is_not_read_as_connected(self):
        # "connected" is a substring of "not connected"; order in the map is
        # load-bearing, so this is the test that keeps it that way.
        assert classify_state("Not connected")[0] == "danger"
        assert classify_state("Connected")[0] == "ok"

    def test_case_and_padding_do_not_matter(self):
        assert classify_state("  ✓ CONNECTED ")[0] == "ok"

    def test_an_unknown_phrase_keeps_its_own_words(self):
        kind, label = classify_state("Rate limited by upstream")
        assert kind == "stopped"
        assert label == "Rate limited by upstream"

    def test_an_unknown_phrase_behind_a_known_mark_still_lands_right(self):
        assert classify_state("Handshake refused", "✗")[0] == "danger"

    def test_nothing_at_all_is_unknown_not_ok(self):
        assert classify_state("") == ("stopped", "Unknown")

    def test_a_bare_http_server_is_remote_not_claude_ai(self):
        assert classify_origin("weather", "https://example.test/mcp") == ("remote", "http")


class TestItDegradesRatherThanBreaks:
    def test_no_servers_configured(self):
        assert parse_mcp_list("No MCP servers configured. Use `claude mcp add`.") == []

    def test_empty_and_whitespace(self):
        assert parse_mcp_list("") == []
        assert parse_mcp_list("\n\n   \n") == []

    def test_prose_without_a_target_is_not_a_server(self):
        assert parse_mcp_list("something entirely new\n") == []

    def test_a_line_with_no_status_is_a_server_in_an_unknown_state(self):
        # The one thing this panel must never do is hide a configured server
        # because it could not read its health.
        s = parse_mcp_list("weather: npx weather-mcp")
        assert len(s) == 1
        assert (s[0]["name"], s[0]["kind"], s[0]["state"]) == ("weather", "stopped", "Unknown")

    def test_a_mark_with_no_target(self):
        s = parse_mcp_list("weather: ✓ Connected")
        assert (s[0]["name"], s[0]["kind"], s[0]["target"]) == ("weather", "ok", "")

    def test_ansi_colour_is_stripped(self):
        raw = "\x1b[1mplaid\x1b[0m: node server.js - \x1b[32m✓ Connected\x1b[0m"
        s = parse_mcp_list(raw)
        assert (s[0]["name"], s[0]["kind"]) == ("plaid", "ok")

    def test_a_duplicate_name_is_listed_once(self):
        s = parse_mcp_list("plaid: node a.js - ✓ Connected\nplaid: node a.js - ✓ Connected")
        assert len(s) == 1


class TestTheCliBeingMissingIsAnAnswer:
    def test_no_cli_reports_unavailable_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(mcp_health, "CLAUDE_CLI", "/nonexistent/claude-binary")
        out = asyncio.run(mcp_health.run_mcp_health("/tmp"))
        assert out["available"] is False
        assert out["reason"]
        # Never a fabricated list.
        assert "servers" not in out


class TestHealthCacheTtl:
    def setup_method(self):
        mcp_health._HEALTH_CACHE.clear()

    def _counting(self, monkeypatch):
        calls = []

        async def fake(cwd):
            calls.append(cwd)
            return {"available": True, "servers": [], "counts": summarize([])}

        monkeypatch.setattr(mcp_health, "run_mcp_health", fake)
        return calls

    def test_a_second_open_within_the_ttl_does_not_respawn_the_cli(self, monkeypatch):
        calls = self._counting(monkeypatch)
        a = asyncio.run(mcp_health.cached_mcp_health("/tmp"))
        b = asyncio.run(mcp_health.cached_mcp_health("/tmp"))
        assert len(calls) == 1
        assert b[2] is True and a[2] is False
        # And the age is counted from the run, not from the cache hit.
        assert b[1] == a[1]

    def test_refresh_bypasses_the_cache(self, monkeypatch):
        calls = self._counting(monkeypatch)
        asyncio.run(mcp_health.cached_mcp_health("/tmp"))
        asyncio.run(mcp_health.cached_mcp_health("/tmp", force=True))
        assert len(calls) == 2

    def test_a_different_directory_is_a_different_reading(self, monkeypatch):
        # A project's .mcp.json adds servers the workshop root does not have.
        calls = self._counting(monkeypatch)
        asyncio.run(mcp_health.cached_mcp_health("/tmp/one"))
        asyncio.run(mcp_health.cached_mcp_health("/tmp/two"))
        assert len(calls) == 2

    def test_a_failure_is_not_cached(self, monkeypatch):
        calls = []

        async def fake(cwd):
            calls.append(cwd)
            return {"available": False, "reason": "nope"}

        monkeypatch.setattr(mcp_health, "run_mcp_health", fake)
        asyncio.run(mcp_health.cached_mcp_health("/tmp"))
        asyncio.run(mcp_health.cached_mcp_health("/tmp"))
        assert len(calls) == 2, "a CLI that was busy once must be retried, not remembered"

    def test_the_entry_expires(self, monkeypatch):
        calls = self._counting(monkeypatch)
        monkeypatch.setattr(mcp_health, "_HEALTH_CACHE", TtlCache(0.05))
        asyncio.run(mcp_health.cached_mcp_health("/tmp"))
        time.sleep(0.12)
        asyncio.run(mcp_health.cached_mcp_health("/tmp"))
        assert len(calls) == 2
