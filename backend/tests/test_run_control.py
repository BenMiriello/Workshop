"""The sweep's control channel: the file a bash loop reads to be steered.

Step B6 of `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`
(section 8, "The sweep needs a control channel, because it has no FIFO").

What must be true:

  - requests are FIFO, and a queued one is cancellable until it is taken;
  - there is NO priority anywhere: the verb is the urgency (section 8), so a
    caller cannot rank, reorder or jump;
  - a steer lands in the file, and THE SWEEP'S OWN READ picks it up on its next
    iteration and folds it into that iteration's prompt;
  - a malformed, truncated or unreadable control file NEVER stops a sweep.

The sweep half is exercised by sourcing `scripts/sweep_control.sh` -- the exact
file `project_ralph.sh` sources -- and running its functions in bash, rather
than by asserting that some python said the right thing. The whole risk in this
step is that the bash side does something different from the python side.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import run_control  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SWEEP_CONTROL = REPO / "scripts" / "sweep_control.sh"
CONTROL_PY = REPO / "scripts" / "control.py"


@pytest.fixture()
def channel(tmp_path, monkeypatch):
    """Point the channel at a tmp directory, the way `test_presence` does."""
    d = tmp_path / "control"
    monkeypatch.setenv("WORKSHOP_CONTROL_DIR", str(d))
    return d


def _sweep_read(run_id: str, control_dir: Path) -> tuple[int, str]:
    """Run exactly what the sweep runs: source the helper, call `control_read`.

    Returns `(CONTROL_YIELD, CONTROL_STEER)`. The two are printed with a
    separator rather than returned, because a steer is multi-line by nature.
    """
    script = f'''
set -u
export WORKSHOP={REPO}
export WORKSHOP_CONTROL_DIR={control_dir}
source {SWEEP_CONTROL}
control_read "{run_id}"
echo "YIELD=$CONTROL_YIELD"
echo "---STEER---"
printf '%s' "$CONTROL_STEER"
'''
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0, p.stderr
    head, _, steer = p.stdout.partition("---STEER---\n")
    return int(head.strip().split("=", 1)[1]), steer


# --- FIFO, and cancellable --------------------------------------------------

def test_requests_come_back_in_the_order_they_were_submitted(channel):
    for text in ("first", "second", "third"):
        run_control.submit("sweep-1", verb="queue", text=text)
    assert [r["text"] for r in run_control.pending("sweep-1")] == [
        "first", "second", "third"]


def test_a_queued_request_can_be_taken_back_out(channel):
    a = run_control.submit("sweep-1", verb="queue", text="one",
                           request_id="r-a")
    run_control.submit("sweep-1", verb="queue", text="two", request_id="r-b")
    assert len(a["requests"]) == 1
    assert run_control.cancel("sweep-1", "r-a") is True
    assert [r["text"] for r in run_control.pending("sweep-1")] == ["two"]
    # Cancelling what is not there is False, not an exception: the caller's
    # intent is satisfied either way.
    assert run_control.cancel("sweep-1", "r-a") is False


def test_a_retried_submit_is_one_request_in_its_original_place(channel):
    run_control.submit("sweep-1", verb="queue", text="one", request_id="r-a")
    run_control.submit("sweep-1", verb="queue", text="two", request_id="r-b")
    # The same request arriving again (a retried POST) must not become a third,
    # and must not jump the one queued behind it.
    run_control.submit("sweep-1", verb="queue", text="one (again)",
                       request_id="r-a")
    assert [r["text"] for r in run_control.pending("sweep-1")] == [
        "one (again)", "two"]


def test_there_is_no_priority_field_on_a_request(channel):
    """Section 8: "No priority numbers. The verb IS the urgency."

    Asserted on the stored shape rather than on the absence of an argument,
    because a scalar smuggled in as `meta` or `rank` would be the same defect.
    """
    run_control.submit("sweep-1", verb="steer", text="do this too")
    stored = json.loads(
        (channel / "sweep-1.json").read_text())["requests"][0]
    assert set(stored) == {"id", "verb", "text", "actor", "at", "seq"}
    for banned in ("priority", "rank", "tier", "urgency", "position"):
        assert banned not in stored


def test_take_is_consuming_so_a_request_is_folded_once(channel):
    run_control.submit("sweep-1", verb="steer", text="also update the docs")
    assert [r["text"] for r in run_control.take("sweep-1")] == [
        "also update the docs"]
    assert run_control.take("sweep-1") == []


def test_an_unknown_verb_and_an_empty_request_are_refused(channel):
    with pytest.raises(run_control.ControlError):
        run_control.submit("sweep-1", verb="preempt", text="stop")
    with pytest.raises(run_control.ControlError):
        run_control.submit("sweep-1", verb="queue", text="   ")


# --- the sweep's own read ----------------------------------------------------

def test_a_steer_lands_in_the_file_and_the_next_iteration_reads_it(channel):
    run_control.submit("sweep-7", verb="steer", text="also update the docs",
                       actor="operator")
    assert (channel / "sweep-7.json").exists()
    yielding, steer = _sweep_read("sweep-7", channel)
    assert yielding == 0
    assert steer == "also update the docs"
    # ...and the iteration after that does not see it again, because the sweep's
    # read consumed it.
    assert _sweep_read("sweep-7", channel) == (0, "")


def test_several_steers_are_folded_in_order_into_one_prompt(channel):
    run_control.submit("sweep-7", verb="steer", text="first thing")
    run_control.submit("sweep-7", verb="steer", text="second thing")
    _, steer = _sweep_read("sweep-7", channel)
    assert steer == "first thing\n\nsecond thing"


def test_control_fold_appends_the_request_to_the_iterations_prompt(channel):
    run_control.submit("sweep-7", verb="steer", text="also update the docs")
    script = f'''
set -u
export WORKSHOP={REPO}
export WORKSHOP_CONTROL_DIR={channel}
source {SWEEP_CONTROL}
control_read "sweep-7"
control_fold "the original task text"
'''
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.startswith("the original task text\n\n")
    assert "also update the docs" in p.stdout


def test_a_yield_is_seen_by_the_sweep_and_is_not_consumed(channel):
    run_control.request_yield("sweep-7", actor="operator", reason="taking over")
    assert _sweep_read("sweep-7", channel)[0] == 1
    # Standing instruction, not a message: still readable afterwards, so a
    # reader can see why the sweep ended.
    assert _sweep_read("sweep-7", channel)[0] == 1
    assert run_control.yield_requested("sweep-7")["actor"] == "operator"


# --- fail soft ---------------------------------------------------------------

@pytest.mark.parametrize("junk", [
    "{{{ not json at all",
    '["a list where an object belongs"]',
    '{"requests": "a string"}',
    '{"requests": [{"verb": "queue"}]}',        # no text
    '{"requests": [{"text": "hi", "verb": "nope"}]}',
    "",
])
def test_a_malformed_control_file_does_not_stop_a_sweep(channel, junk):
    channel.mkdir(parents=True, exist_ok=True)
    (channel / "sweep-9.json").write_text(junk)
    # The python side answers with an empty channel...
    assert run_control.read("sweep-9")["requests"] == []
    assert run_control.pending("sweep-9") == []
    # ...and the sweep's own read exits 0 with nothing to fold, which is the
    # thing that actually matters: a bookkeeping file must never be able to end
    # a run.
    assert _sweep_read("sweep-9", channel) == (0, "")


def test_an_unreadable_channel_directory_does_not_stop_a_sweep(tmp_path,
                                                               monkeypatch):
    monkeypatch.setenv("WORKSHOP_CONTROL_DIR",
                       str(tmp_path / "does" / "not" / "exist"))
    assert run_control.read("sweep-9")["requests"] == []
    assert run_control.take("sweep-9") == []
    assert run_control.channels() == []


def test_an_unusable_run_id_reads_empty_and_refuses_to_write(channel):
    # Refusing rather than escaping, so two ids can never collide on one file.
    assert run_control.read("../../etc/passwd")["requests"] == []
    with pytest.raises(run_control.ControlError):
        run_control.submit("../../etc/passwd", verb="queue", text="hi")


# --- the CLI the sweep actually calls ---------------------------------------

def _cli(channel: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CONTROL_PY), *args],
                          capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin",
                               "WORKSHOP_CONTROL_DIR": str(channel)})


def test_the_cli_take_prints_nothing_and_exits_zero_on_an_empty_channel(channel):
    p = _cli(channel, "take", "sweep-1", "--format", "text")
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_the_cli_yielding_exit_code_is_the_answer(channel):
    assert _cli(channel, "yielding", "sweep-1").returncode == 1
    run_control.request_yield("sweep-1", actor="operator")
    assert _cli(channel, "yielding", "sweep-1").returncode == 0


# --- the sweep is actually wired to it --------------------------------------

def test_project_ralph_reads_the_channel_at_the_top_of_each_iteration():
    """An unwired channel is not a channel.

    Asserted against the script's text because that is the only thing that
    decides it: `control_read` has to be INSIDE the `while` that walks tasks,
    not before it, or a request sent after the sweep started is never seen.
    """
    src = (REPO / "scripts" / "project_ralph.sh").read_text()
    assert "source \"$WORKSHOP/scripts/sweep_control.sh\"" in src
    loop = src[src.index("while (( n < MAX_TASKS )); do"):]
    assert 'control_read "$PRESENCE_ID"' in loop
    # The read is the FIRST thing the iteration does, before the budget check
    # and before a task is selected.
    assert loop.index("control_read") < loop.index("tick_remaining")


def test_a_yield_leaves_through_the_normal_finalize_path():
    """Section 8: yield is "snapshot, release, exit cleanly".

    `break` and not `exit`: the tail of the script closes the Job, revokes the
    grant and releases presence (which releases the lease, section 6's join
    rule). An `exit` inside the loop would skip the Job close and leave a run
    that looks like it is still going.
    """
    src = (REPO / "scripts" / "project_ralph.sh").read_text()
    block = src[src.index("control_read \"$PRESENCE_ID\""):]
    block = block[:block.index("REMAIN=$(tick_remaining)")]
    assert "control_yield_snapshot" in block
    assert "break" in block
    assert "exit " not in block


def test_the_steer_is_folded_into_the_prompt_that_iteration_runs():
    src = (REPO / "scripts" / "project_ralph.sh").read_text()
    assert 'TASK_TEXT=$(control_fold "$TASK_TEXT")' in src
    # ...and after TASK_TEXT is assigned, or it would fold into nothing.
    assert src.index('TASK_TEXT=$(jf "$NEXT" text)') < src.index(
        'TASK_TEXT=$(control_fold "$TASK_TEXT")')
