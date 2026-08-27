"""`scripts/check_frontend_state.py` -- the one-state-mechanism ratchet.

WHAT IT GUARDS. `@tanstack/react-query` was adopted for six chat reads, and days
later a new dashboard feature was written with FOUR new `usePoll` call sites,
because both mechanisms were live and nothing made the old one un-choosable.
The checker counts the two legacy mechanisms under `frontend/src` -- `usePoll(`
call sites and polling `setInterval` timers -- and fails a run that adds one.

THE TWO PROPERTIES, and they pull in opposite directions, which is why both are
pinned here (the same pair `test_check_ratchet_delta.py` pins for the other
whole-tree gates):

  * inherited debt must PASS   -- 43 usePoll sites exist; failing every run
                                  until they are all migrated is how a checker
                                  gets deleted rather than obeyed
  * a NEW site must FAIL       -- or the ratchet is decoration

Plus the one thing specific to this checker: a timer that does NOT reach the
network (clock tick, audio meter, watchdog) must not count, and the exemption
must be keyed tightly enough that a file holding one real poll AND one clock
tick still reports the poll.

Every test builds a THROWAWAY GIT REPO with the checker copied into it rather
than asserting numbers against the live tree, for the reason
`test_check_ratchet_delta.py` records: the live counts move under any run, and a
test pinned to them would be flaky by construction.

The fixture paths avoid `frontend/src/pages/apps/**` on purpose. CLAUDE.md's
no-app-names rule says "not a test fixture" in as many words, `backend/` is
scanned by `check_no_app_names.py`, and the natural example here -- the one real
file that holds a poll and a clock tick side by side -- lives under an app
directory whose name would land in that gate's `literal_sites`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_BASELINE_NAME = "frontend_state_baseline.json"
_SCRIPT = "check_frontend_state.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout.strip()


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / _SCRIPT), *args],
        cwd=repo, capture_output=True, text=True,
    )


def _write(repo: Path, rel: str, body: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _poll_component(n: int) -> str:
    """A component with `n` usePoll reads, written the legacy way."""
    body = 'import { usePoll } from "@workshop/ui";\n\nexport function Legacy() {\n'
    for i in range(n):
        body += f"  usePoll(load{i}, 15_000);\n"
    return body + "  return null;\n}\n"


def _repo(tmp_path: Path, baseline: dict) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for name in (_SCRIPT, "_ratchet_delta.py"):
        shutil.copy(_SCRIPTS / name, repo / "scripts" / name)
    (repo / "scripts" / _BASELINE_NAME).write_text(json.dumps(baseline, indent=2) + "\n")
    _git(repo, "init", "-q", "-b", "main")
    return repo


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Pinned at 1 usePoll site; the BASE COMMIT already carries 3.

    That gap is the inherited debt -- the real tree is pinned at 43 and every
    worker starts from there."""
    r = _repo(tmp_path, {"usepoll_sites": 1, "raw_poll_intervals": 0})
    _write(r, "frontend/src/components/Legacy.tsx", _poll_component(3))
    _commit(r, "base: three inherited usePoll reads")
    return r


class TestUsePollRatchet:
    def test_absolute_mode_reports_the_inherited_debt(self, repo: Path):
        r = _run(repo)
        assert r.returncode == 1, r.stdout
        assert "FAIL usepoll_sites: 1 -> 3" in r.stdout
        assert "only goes down" in r.stdout

    def test_delta_passes_on_inherited_debt(self, repo: Path):
        """A diff that touches nothing relevant must not inherit the blame."""
        _write(repo, "docs/note.md", "docs-only change\n")
        r = _run(repo, "--since", "HEAD")
        assert r.returncode == 0, r.stdout
        # ...and the debt is still REPORTED, just not gated on.
        assert "PINNED baseline" in r.stdout
        assert "usepoll_sites: pinned 1 -> now 3" in r.stdout

    def test_a_planted_new_use_poll_site_fails_and_is_named(self, repo: Path):
        """The incident, in miniature: a fourth read written the old way."""
        _write(repo, "frontend/src/components/Legacy.tsx", _poll_component(4))
        r = _run(repo, "--since", "HEAD")
        assert r.returncode == 1, r.stdout
        assert "FAIL usepoll_sites: 3 -> 4 against the base ref HEAD" in r.stdout
        # The FAIL must say WHICH site, or the reader has to re-find it.
        assert "new: frontend/src/components/Legacy.tsx" in r.stdout
        assert "usePoll(load3, 15_000);" in r.stdout
        assert "queryClient.ts" in r.stdout

    def test_a_new_site_in_a_brand_new_file_fails(self, repo: Path):
        """How the real regression arrived: four new sites in four NEW files,
        already committed by the worker. Only a base-ref comparison sees it."""
        _write(repo, "frontend/src/components/dashboard/Card.tsx", _poll_component(1))
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout.strip()
        _commit(repo, "worker's commit")
        r = _run(repo, "--since", base)
        assert r.returncode == 1, r.stdout
        assert "usepoll_sites: 3 -> 4" in r.stdout
        assert "new: frontend/src/components/dashboard/Card.tsx" in r.stdout

    def test_removing_a_site_passes_and_reports_the_improvement(self, repo: Path):
        _write(repo, "frontend/src/components/Legacy.tsx", _poll_component(2))
        r = _run(repo, "--since", "HEAD")
        assert r.returncode == 0, r.stdout
        assert "usepoll_sites improved 3 -> 2" in r.stdout

    def test_a_moved_site_is_not_reported_as_new(self, repo: Path):
        """Line numbers shift under any edit above them. A site named as `new`
        because something was inserted above it sends the reader to fix code
        that did not change, so the delta matches on source text."""
        moved = "// a comment inserted above\n" + _poll_component(4)
        _write(repo, "frontend/src/components/Legacy.tsx", moved)
        r = _run(repo, "--since", "HEAD")
        assert r.returncode == 1, r.stdout
        assert r.stdout.count("new: ") == 1
        assert "usePoll(load3, 15_000);" in r.stdout

    def test_tests_and_the_kit_definition_are_not_counted(self, repo: Path):
        """`packages/ui` is where the hook is DEFINED -- counting it would mean
        the number could never reach zero without a breaking kit change -- and a
        `usePoll(` in a test describes the mechanism rather than choosing it."""
        _write(repo, "packages/ui/src/usePoll.ts", "export function usePoll() {}\n")
        _write(repo, "frontend/src/components/Legacy.test.tsx", _poll_component(9))
        _commit(repo, "a kit definition and a test")
        r = _run(repo, "--since", "HEAD~1")
        assert r.returncode == 0, r.stdout


class TestInlineQueryKeys:
    """The third metric, and the only one pinned at ZERO rather than at a debt.

    A key spelled at its call site is invisible to every other check in this
    repo: TanStack types a key as `unknown[]`, so two spellings of one fact
    compile, lint and test clean while being two cache entries nothing keeps in
    step. That is not hypothetical -- `["chat","threads",null]` and
    `["chat","threads"]` shipped together, and archiving a chat on the page
    holding the second left the dashboard listing it."""

    def test_a_key_literal_at_a_call_site_fails(self, repo: Path):
        # Committed, because the scan reads TRACKED files: an untracked probe
        # would pass for the wrong reason and prove nothing.
        _write(repo, "frontend/src/components/Feed.tsx",
               'const q = useQuery({ queryKey: ["jobs", "feed", filter] });\n')
        base = _head(repo)
        _commit(repo, "a key spelled at its call site")
        r = _run(repo, "--since", base)
        assert r.returncode == 1, r.stdout
        assert "FAIL inline_query_keys: 0 -> 1" in r.stdout
        assert "new: frontend/src/components/Feed.tsx" in r.stdout
        # The advice must be about KEYS. It used to fall through to the timer
        # branch and tell the reader to add their query to a timer allowlist.
        assert "queryKeys.ts" in r.stdout
        assert "_TIMER_ALLOW" not in r.stdout.split("FAIL inline_query_keys")[1]

    def test_a_key_built_by_a_factory_passes(self, repo: Path):
        _write(repo, "frontend/src/components/Feed.tsx",
               "const q = useQuery({ queryKey: queryKeys.jobs.feed(filter) });\n")
        base = _head(repo)
        _commit(repo, "a key built by a factory")
        r = _run(repo, "--since", base)
        assert r.returncode == 0, r.stdout

    def test_a_key_module_may_hold_literals_because_that_is_its_job(self, repo: Path):
        """The registry itself, and an app's own `queries.ts`, are where a key
        literal IS the definition. Exempted by file NAME, not by path, so the
        checker names no app."""
        _write(repo, "frontend/src/lib/queryKeys.ts",
               'export const k = { feed: (f) => ["jobs", "feed", f] };\n'
               'const q = useQuery({ queryKey: ["jobs", "feed", f] });\n')
        _write(repo, "frontend/src/pages/widgets/queries.ts",
               'const q = useQuery({ queryKey: ["widget", "list"] });\n')
        base = _head(repo)
        _commit(repo, "two key modules")
        r = _run(repo, "--since", base)
        assert r.returncode == 0, r.stdout


class TestRawPollIntervals:
    def test_a_new_polling_interval_fails_and_points_at_both_fixes(self, repo: Path):
        _write(repo, "frontend/src/components/Feed.tsx",
               "const t = setInterval(loadFeed, 5_000);\n")
        _commit(repo, "a hand-rolled poll")
        r = _run(repo, "--since", "HEAD~1")
        assert r.returncode == 1, r.stdout
        assert "FAIL raw_poll_intervals: 0 -> 1" in r.stdout
        assert "new: frontend/src/components/Feed.tsx" in r.stdout
        # Both exits have to be on the message: allowlist it if it is not a
        # poll, migrate it if it is. A FAIL with only one of them reads as
        # "there is nothing you can do".
        assert "_TIMER_ALLOW" in r.stdout
        assert "queryClient.ts" in r.stdout

    def test_an_allowlisted_timer_does_not_count_but_a_poll_in_the_same_file_does(
            self, repo: Path):
        """The allowlist is keyed by (file, line pattern), not by file, because
        at least one real file holds a clock tick AND a genuine poll. A
        file-level exemption there would hide the poll."""
        _write(repo, "frontend/src/components/Chat.tsx",
               "const clock = window.setInterval(() => setNowMs(Date.now()), 1000);\n"
               "const poll = setInterval(loadTurn, 5_000);\n")
        _commit(repo, "one clock tick and one poll, same file")
        r = _run(repo, "--json")
        out = json.loads(r.stdout)
        assert out["raw_poll_intervals"] == 1
        assert [s["file"] for s in out["intervals"]] == ["frontend/src/components/Chat.tsx"]
        assert "loadTurn" in out["intervals"][0]["text"]
        assert len(out["exempt_timers"]) == 1
        assert "clock tick" in out["exempt_timers"][0]["why"]

    def test_a_stale_allowlist_entry_is_reported_and_never_fails(self, repo: Path):
        """Deleting an allowlisted timer is unambiguously progress. Failing on
        the now-unmatched entry would point the ratchet backwards; it is
        reported so the entry can be pruned."""
        r = _run(repo, "--since", "HEAD")
        assert r.returncode == 0, r.stdout
        assert "stale allowlist entry" in r.stdout


class TestBaselineDiscipline:
    def test_an_upward_pin_is_refused_without_a_reason(self, repo: Path):
        r = _run(repo, "--write-baseline")
        assert r.returncode == 1, r.stdout
        assert "pin would RAISE usepoll_sites: 1 -> 3" in r.stdout
        assert "--because" in r.stdout
        # And the baseline on disk is untouched.
        pinned = json.loads((repo / "scripts" / _BASELINE_NAME).read_text())
        assert pinned["usepoll_sites"] == 1

    def test_an_upward_pin_with_a_reason_records_it_and_keeps_repeating_it(
            self, repo: Path):
        r = _run(repo, "--write-baseline", "--because", "the kit renamed the hook")
        assert r.returncode == 0, r.stdout + r.stderr
        pinned = json.loads((repo / "scripts" / _BASELINE_NAME).read_text())
        assert pinned["usepoll_sites"] == 3
        assert pinned["raised"]["usepoll_sites"]["because"] == "the kit renamed the hook"
        # An upward pin cannot be quiet: it prints on every subsequent run.
        assert "the kit renamed the hook" in _run(repo).stdout

    def test_a_downward_pin_needs_no_reason(self, repo: Path):
        _write(repo, "frontend/src/components/Legacy.tsx", _poll_component(0))
        r = _run(repo, "--write-baseline")
        assert r.returncode == 0, r.stdout + r.stderr
        assert json.loads((repo / "scripts" / _BASELINE_NAME).read_text())["usepoll_sites"] == 0


def test_unresolvable_ref_fails_loudly(repo: Path):
    """An empty extract would make every count look like an improvement, so a
    bad ref must never be mistaken for a clean base."""
    r = _run(repo, "--since", "no-such-ref")
    assert r.returncode == 2
    assert "cannot read ref" in r.stderr
