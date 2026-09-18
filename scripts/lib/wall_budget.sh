#!/usr/bin/env bash
# THE per-task wall budget, in seconds. ONE definition; every script that needs
# it sources this file instead of restating the number.
#
#   source "$WORKSHOP/scripts/lib/wall_budget.sh"
#
# WHY THIS FILE EXISTS, measured 2026-09-17: the budget was raised from 1200 to
# 2700 in scripts/queue_runner.sh (d154fd17) and the raise DID NOT REACH THE
# WORKER, because the same number was also a literal in two other files —
# scripts/project_ralph.sh (`RALPH_MAX_WALL="${RALPH_MAX_WALL:-1200}"`) and
# cron_agent.sh (`RALPH_MAX_WALL=1200`, vestigial). The sweep budget
# queue_runner.sh computes is `MAX_TASKS * PER_TASK_WALL + 120`, and the live
# child argv that day read `--max-wall 3720` = 3*1200+120, so the running loop
# was still sized from the old literal in a file nobody edited.
#
# A single-point raise did not hold because three copies existed. So: one
# definition, and the other sites DEFAULT to it rather than repeating it. If you
# find yourself typing a per-task wall number into another script, source this
# instead.
#
# 2700 (45 min) is sized from evidence, not taste — see the long note in
# scripts/queue_runner.sh, which records the five consecutive `exit=124` kills at
# 1200 and the committed-but-severed work they threw away.
#
# Anything already in the environment wins, so a one-off run or a test can
# override without editing any script.
WORKSHOP_PER_TASK_WALL="${WORKSHOP_PER_TASK_WALL:-2700}"
export WORKSHOP_PER_TASK_WALL
