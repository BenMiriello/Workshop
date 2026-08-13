"""The `execution` classifier's heuristics, pinned in both directions.

WHY THIS MATTERS MORE IN ONE DIRECTION. The two errors are not symmetric:

  missed `external`  one unattended run tries, fails, and releases the ticket.
  false `external`   `--apply` sets `status: parked` and writes a
                     `blocked_reason` that is not true. `tasks.update` also
                     clears `awaiting` on any status outside blocked/in_review,
                     so it silently discards a pending question too.

The first draft used a bare `\\broot\\b`, which is everyday engineering
vocabulary: "root cause", "project root", "doc root", "the store root". Every
one of those is ordinary code work that would have been parked as host
administration. This repo's own knowledge index has a note titled "Chat bugs
need root-cause fixes".
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

_spec = importlib.util.spec_from_file_location(
    "classify_open_tickets", REPO / "scripts" / "classify_open_tickets.py")
classify_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(classify_mod)
classify = classify_mod.classify


def _c(title: str, body: str = "") -> str:
    return classify({"title": title, "body": body})[0]


# --- the word `root` is not a privilege claim --------------------------------

@pytest.mark.parametrize("title,body", [
    ("Fix the root cause of the duplicate-chat bug", ""),
    ("Move the project root resolution into paths.py", ""),
    ("Serve the doc root from dist", ""),
    ("Dedup the store list", "the store root is counted twice by a glob"),
    ("Rename the root task store", "the repo root holds .tasks.json"),
    ("Fix chown handling in the installer", "the installer calls chown on the data dir"),
    ("Harden generated app containers", "add a non-root user to generated compose files"),
])
def test_ordinary_code_work_is_not_external(title: str, body: str) -> None:
    assert _c(title, body) != "external", f"would have been PARKED: {title}"


# --- but a real privilege claim is --------------------------------------------

@pytest.mark.parametrize("title,body", [
    ("[SEC-M3] sshd: disable password authentication (root)", "add a drop-in"),
    ("[SEC-L5] Hygiene (root): Samba restrict anonymous", "bind smbd to one interface"),
    ("Lock down redis", "Needs root for the firewall change."),
    ("Bind ComfyUI to loopback", "Owned by another host user + root for firewall."),
    ("Replace the NOPASSWD sudo git rule", "drop the sudoers rule"),
    ("Loopback-bind Ollama", "Stack owner + root."),
    ("Firewall audit", "Run: sudo ufw status verbose + sudo ip6tables -L -n"),
    ("Disable a unit", "systemctl disable the old timer"),
])
def test_privileged_work_is_external(title: str, body: str) -> None:
    assert _c(title, body) == "external", f"would have been swept: {title}"


def test_the_title_marker_is_not_end_anchored() -> None:
    """Both conventions are live in the store and only one ends the line. This
    was end-anchored and let SEC-L5 read as `code`."""
    assert _c("Hygiene (root): Samba restrict anonymous") == "external"
    assert _c("sshd: disable password authentication (root)") == "external"


# --- judgement is the SAFE direction, so it may over-reach --------------------

@pytest.mark.parametrize("title", [
    "Audit the toolbar and chat-adjacent pages",
    "Design the resource-access protocol",
    "Revisit deferred chat-plan items",
    "Define external tracker sync semantics",
])
def test_judgement_verbs_are_caught(title: str) -> None:
    assert _c(title) == "judgement"


def test_deep_effort_reads_as_judgement() -> None:
    assert classify({"title": "Do a thing", "body": "", "effort": "deep"})[0] == "judgement"


def test_known_judgement_over_reach_is_tolerated() -> None:
    """`Audit trail` and `Design tokens` are noun phrases, not verbs, and both
    classify as `judgement`. Pinned rather than fixed BECAUSE the direction is
    safe: a `code` ticket wrongly marked `judgement` waits for a human, which is
    the status quo and costs nothing. Only `external` mutates the record."""
    assert _c("Audit trail for grants") == "judgement"
    assert _c("Design tokens: add a spacing scale") == "judgement"


# --- external beats judgement -------------------------------------------------

def test_external_wins_over_a_judgement_verb() -> None:
    """A ticket can be both a design task and one that needs root. The privilege
    is the fact that actually stops a run, so it is tested first."""
    assert _c("Audit the firewall", "Run: sudo ufw status") == "external"


def test_the_residual_is_code() -> None:
    assert _c("Bind a scope to an external tracker",
              "Objective: one binding per scope.") == "code"
