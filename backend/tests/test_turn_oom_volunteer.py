"""A turn's whole process tree is the low-memory killer's preferred victim,
and a turn whose leader died takes the rest of its group with it.

Found 2026-10-25: the bias was written onto the sandbox wrapper after the
spawn, once the agent had already forked, so a low-memory killer took the
~1 MB wrapper of every running turn (freeing nothing) and each agent ran on,
orphaned, holding its input open.
"""
from __future__ import annotations

import subprocess
import time

from services import build_streaming


def test_the_bias_reaches_the_turn_and_what_it_starts():
    argv = build_streaming._volunteer_for_oom(
        ["/bin/sh", "-c", 'cat /proc/self/oom_score_adj; sh -c "cat /proc/self/oom_score_adj"'])
    out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout.split()
    assert out == [str(build_streaming.AGENT_OOM_SCORE_ADJ)] * 2


def test_the_wrapped_turn_keeps_its_pid():
    argv = build_streaming._volunteer_for_oom(["/bin/sh", "-c", "echo $$"])
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True)
    assert p.communicate()[0].strip() == str(p.pid)


def test_a_group_whose_leader_died_is_reaped():
    leader = subprocess.Popen(["/bin/sh", "-c", "sleep 60 & exit 0"], start_new_session=True)
    leader.wait()
    pgid = leader.pid
    assert build_streaming._pgid_alive(pgid)
    build_streaming._kill_turn_proc(pgid, leader, pgid=pgid)
    for _ in range(50):
        if not build_streaming._pgid_alive(pgid):
            break
        time.sleep(0.05)
    assert not build_streaming._pgid_alive(pgid)


def test_a_small_turn_ranks_above_the_backend_and_below_a_process_holding_most_memory():
    """Badness is the bias plus the share of RAM and swap in thousandths. With
    the numbers measured on 2026-10-25 (85.6 GB of RAM and swap), a 200 MB turn
    must outrank the backend (bias 100, 676 MB) and lose to the model server
    (bias 200, 47 GB)."""
    total = 85.6
    turn = build_streaming.AGENT_OOM_SCORE_ADJ + 0.2 / total * 1000
    backend = 100 + 0.676 / total * 1000
    model_server = 200 + 47.3 / total * 1000
    assert backend < turn < model_server
