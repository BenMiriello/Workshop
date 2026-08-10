"""The `/context` report, parsed.

The sample below is the CLI's real output, captured on 2026-08-10 from
`claude -p --output-format json "/context"`. Pinning the real thing rather than
a hand-written approximation is the whole point: the format is a vendor's, and a
test written against what we WISH it emitted would pass while the panel showed
nothing.
"""
from services.context_report import parse_context_report, parse_tokens


SAMPLE = """## Context Usage

**Model:** claude-opus-5
**Tokens:** 30.6k / 200k (15%)

### Estimated usage by category

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 8.7k | 4.4% |
| System tools | 13.1k | 6.5% |
| MCP tools (deferred) | 6.6k | 3.3% |
| System tools (deferred) | 11.7k | 5.9% |
| Custom agents | 241 | 0.1% |
| Memory files | 5k | 2.5% |
| Skills | 1.3k | 0.7% |
| Messages | 2.2k | 1.1% |
| Free space | 136.4k | 68.2% |
| Autocompact buffer | 33k | 16.5% |

### MCP Tools

| Tool | Server | Tokens |
|------|--------|--------|
| mcp__claude_ai_Gmail__authenticate | claude_ai_Gmail | 173 |
| mcp__claude_ai_Gmail__complete_authentication | claude_ai_Gmail | 225 |
| mcp__plaid__get_balances | plaid | 109 |
| mcp__plaid__list_transactions | plaid | 242 |

### Custom Agents

| Agent Type | Source | Tokens |
|------------|--------|--------|
| discord-handler | User | 103 |
| work-reviewer | User | 138 |

### Memory Files

| Type | Path | Tokens |
|------|------|--------|
| User | /home/clawd-a/.claude/CLAUDE.md | 1.8k |
| Project | /home/clawd-a/workshop/CLAUDE.md | 1.8k |

### Skills

| Skill | Source | Tokens |
|-------|--------|--------|
| audio-rig-diagnostics | User | ~150 |
| review | Built-in | < 20 |
"""


class TestParseTokens:
    def test_plain_numbers(self):
        assert parse_tokens("241") == 241
        assert parse_tokens("1,024") == 1024

    def test_thousands(self):
        assert parse_tokens("8.7k") == 8700
        assert parse_tokens("5k") == 5000
        assert parse_tokens("136.4k") == 136_400

    def test_the_cli_hedges_and_they_still_count(self):
        # Dropping these would silently zero the whole Skills section.
        assert parse_tokens("~150") == 150
        assert parse_tokens("< 20") == 20

    def test_nothing_is_zero_rather_than_an_error(self):
        assert parse_tokens("") == 0
        assert parse_tokens("n/a") == 0


class TestParseContextReport:
    def test_reads_the_model_and_the_totals(self):
        r = parse_context_report(SAMPLE)
        assert r["model"] == "claude-opus-5"
        assert r["cli_used_tokens"] == 30_600
        assert r["cli_window_tokens"] == 200_000

    def test_free_space_is_not_something_filling_the_context(self):
        # A bar of "what is using your window" that includes free space is a pie
        # chart of pie. The autocompact buffer is the same kind of not-a-thing.
        names = [c["name"].lower() for c in parse_context_report(SAMPLE)["categories"]]
        assert "free space" not in names
        assert "autocompact buffer" not in names

    def test_categories_come_back_largest_first(self):
        cats = parse_context_report(SAMPLE)["categories"]
        assert [c["tokens"] for c in cats] == sorted((c["tokens"] for c in cats), reverse=True)
        assert cats[0]["name"] == "System tools"

    def test_the_conversation_itself_is_a_category(self):
        cats = {c["name"]: c["tokens"] for c in parse_context_report(SAMPLE)["categories"]}
        assert cats["Messages"] == 2_200
        assert cats["System prompt"] == 8_700

    def test_mcp_is_grouped_by_server_with_its_own_total(self):
        # Twenty-two tool rows is a wall. "plaid costs 351" is actionable.
        mcp = {s["server"]: s for s in parse_context_report(SAMPLE)["mcp"]}
        assert mcp["plaid"]["tokens"] == 351
        assert mcp["claude_ai_Gmail"]["tokens"] == 398
        assert len(mcp["plaid"]["tools"]) == 2

    def test_servers_come_back_most_expensive_first(self):
        mcp = parse_context_report(SAMPLE)["mcp"]
        assert [s["tokens"] for s in mcp] == sorted((s["tokens"] for s in mcp), reverse=True)

    def test_the_smaller_sections_are_read_too(self):
        r = parse_context_report(SAMPLE)
        assert {a["name"] for a in r["agents"]} == {"discord-handler", "work-reviewer"}
        assert r["memory"][0]["tokens"] == 1_800
        assert {s["name"] for s in r["skills"]} == {"audio-rig-diagnostics", "review"}

    def test_percentages_are_not_carried_across(self):
        # Rule 1: the CLI computes against ITS window (200k here); Workshop
        # threads run 1M. Carrying its percentages would put a second,
        # disagreeing context figure a few pixels from the meter.
        r = parse_context_report(SAMPLE)
        assert all("percent" not in k for k in r)
        assert all("percent" not in k for c in r["categories"] for k in c)


class TestItDegradesRatherThanBreaks:
    def test_empty_input(self):
        r = parse_context_report("")
        assert r["categories"] == [] and r["mcp"] == []
        assert r["cli_used_tokens"] == 0

    def test_a_session_with_no_mcp_servers(self):
        md = SAMPLE.split("### MCP Tools")[0]
        r = parse_context_report(md)
        assert r["mcp"] == []
        assert r["categories"], "the categories must survive a missing later section"

    def test_an_unrecognisable_report_does_not_raise(self):
        # A vendor formatting change must degrade this panel, never break the
        # chat it hangs off.
        r = parse_context_report("## Context Usage\n\nsomething entirely new\n")
        assert r["categories"] == []
