"""What a process, or a process and everything it started, holds in memory.

Read from `/proc/<pid>/smaps_rollup`, which the kernel sums itself, so this does
not trust what a program says about its own footprint. Resident and swapped
memory are both counted: memory the kernel pushed out to swap is still memory
the program owns, and the machine's RAM room reads it as owed back.
"""
from __future__ import annotations

import os

KB_PER_MIB = 1024


def rollup_mib(pid: int, proc: str = "/proc") -> tuple[int, int] | None:
    """`(resident, swapped)` MiB of one process, or None when it cannot be read
    (gone, or owned by someone this process may not inspect)."""
    rss = swap = 0
    try:
        with open(f"{proc}/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Rss:"):
                    rss = int(line.split()[1]) // KB_PER_MIB
                elif line.startswith("Swap:"):
                    swap = int(line.split()[1]) // KB_PER_MIB
    except (OSError, ValueError, IndexError):
        return None
    return rss, swap


def _parent_of(stat: str) -> int | None:
    # The command name is parenthesised and may hold spaces and parentheses, so
    # the fields are counted from the last closing parenthesis.
    try:
        return int(stat[stat.rindex(")") + 2:].split()[1])
    except (ValueError, IndexError):
        return None


def descendants(pid: int, proc: str = "/proc") -> list[int]:
    """`pid` and every process below it, parents before children."""
    children: dict[int, list[int]] = {}
    for entry in os.listdir(proc):
        if not entry.isdigit():
            continue
        try:
            with open(f"{proc}/{entry}/stat") as f:
                parent = _parent_of(f.read())
        except OSError:
            continue
        if parent is not None:
            children.setdefault(parent, []).append(int(entry))
    out, queue = [], [pid]
    while queue:
        current = queue.pop(0)
        out.append(current)
        queue.extend(children.get(current, []))
    return out


def tree_mib(pid: int, proc: str = "/proc") -> tuple[int, int, list[int]]:
    """`(resident, swapped, pids counted)` over `pid` and its descendants."""
    rss = swap = 0
    counted: list[int] = []
    for p in descendants(pid, proc):
        got = rollup_mib(p, proc)
        if got is None:
            continue
        rss, swap = rss + got[0], swap + got[1]
        counted.append(p)
    return rss, swap, counted
