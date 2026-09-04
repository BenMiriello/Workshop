#!/usr/bin/env python3
"""The completion gate: a machine decision about "is this done", with evidence.

Run it through `scripts/done.sh`, which is the operator-facing name and the
thing wired into the Stop hook. This file is the logic.

WHY THIS EXISTS, and why it is not another rule in CLAUDE.md.

An agent asked "are you done?" answers yes more often than it should, and that
does not improve when it tries harder -- the generator cannot be the grader. The
gap between the checks an agent chooses to run and what actually broke widens
with codebase size, and this tree is ~292K lines. So the finish line has to be
computed, not asserted, and it has to carry evidence: argv, exit codes, output,
and for a visual change a screenshot of the origin the operator actually looks
at. A summary sentence is not evidence and this gate never accepts one.

The same research says the obvious alternative makes things worse: adding a
procedural sentence ("always run the tests") to an agent instructions file RAISED
regressions in a controlled comparison, while replacing it with a structural
mechanism dropped them well below baseline. This is the mechanism. Nothing was
added to CLAUDE.md for it.

WHAT IT DECIDES ON. Not the whole working tree -- the files THIS turn touched.
Several agents share this checkout and their unfinished work is usually sitting
in it; a gate that blamed a turn for someone else's half-written file would be
switched off inside a day, which is the failure mode every ratchet in this
directory already learned (see the --since work in reviewer.sh). The turn's set
is the git diff narrowed by the turn's OWN record of what it wrote -- the
file_path of every Edit/Write and the text of every Bash command, read from the
transcript -- with mtime as the fallback when there is no transcript to read.
The turn begins at the Stop hook's own mark or the last message in it.

WHAT IT COMPOSES. Gates that already exist, in this order:

  1. `apply.sh --check`   Did the change reach the surface the operator uses?
                          This is the single most repeated "you said it was done
                          and it wasn't" in this project's record, so it is
                          first, and it is judged per surface and per owner.
  2. stub markers         In the turn's files only, same marker set as reviewer.sh.
  3. `reviewer.sh`        The existing deterministic gate, run whole, its JSON
                          recorded verbatim -- WHEN the working tree holds
                          nothing but this turn's work. It judges the whole tree
                          against a base ref, so with a stranger's edits in it
                          the verdict is neither attributable nor cheap: it cost
                          4m53s and rejected on somebody else's files when this
                          gate was built. In that case the same TOOLS reviewer.sh
                          drives (mypy, tsc, check_design.sh) are run scoped to
                          this turn's files instead, and the fact that the full
                          gate was not attributable is recorded rather than
                          quietly skipped.
  4. `shoot.sh`           A visual change owes a screenshot of the built origin.

EXIT CODES. 0 = done, 1 = not done, 2 = the gate itself could not run.

REENTRY. `WORKSHOP_DONE_GATE_RUNNING=1` is exported around every subprocess this
runs, and both entry points return immediately when they see it. That is the
same fix `check_backend_tests.py` needed when a test ran reviewer.sh which ran
the suite which ran the test; this gate calls reviewer.sh, so it inherits the
loop and has to break it in the same place.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
EVIDENCE = RUNTIME / "done-evidence.json"
MARKS = RUNTIME / "done-gate"
LOG = RUNTIME / "done-gate.log"
SENTINEL = "WORKSHOP_DONE_GATE_RUNNING"

# How far back to look when neither a stop mark nor a transcript timestamp can
# place the start of this turn. Wide enough for a long turn, narrow enough that
# yesterday's abandoned work in a shared checkout is not adopted as ours.
FALLBACK_LOOKBACK_S = 3 * 3600

# Paths whose contents are never anybody's deliverable.
IGNORED_PREFIXES = (
    ".runtime/", "scratch/", "tmp/", "node_modules/", "frontend/dist/",
    "packages/ui/dist/", "projects/", "branches/", "build-agents/",
    ".pytest_cache/", ".playwright-mcp/",
)

# --- what each running surface is built from -------------------------------
# Mirrors scripts/ui_source_id.sh and scripts/backend_source_id.sh. Those two
# are the ONE definition of each surface's inputs; this is the file-pattern
# view of the same lists, used only to decide whether a surface is any of this
# turn's business. Keep them in step.
UI_PREFIXES = ("frontend/src/", "packages/ui/src/")
UI_FILES = (
    "frontend/index.html", "frontend/vite.config.ts", "frontend/package.json",
    "frontend/package-lock.json", "packages/ui/package.json",
    "packages/ui/vite.config.ts",
)
EDGE_FILE = "backend/workshop_public_app.py"
VITE_ENV_FILE = "frontend/.env"

# A change a person can SEE. Logic-only .ts under the same tree is excluded on
# purpose: demanding a screenshot for a pure reducer edit is the kind of tax
# that gets a gate turned off.
VISUAL_SUFFIXES = (".tsx", ".css")

STUB_PATTERNS = [
    (re.compile(r"\bTODO:"), "TODO"),
    (re.compile(r"^\s*(?:#|//|/\*|\*)\s*TODO\b"), "TODO-comment"),
    (re.compile(r"\bFIXME:"), "FIXME"),
    (re.compile(r"^\s*(?:#|//|/\*|\*)\s*FIXME\b"), "FIXME-comment"),
    (re.compile(r"\bXXX:"), "XXX"),
    (re.compile(r"\bHACK:"), "HACK"),
    # Spelled in halves on purpose: this file is itself scanned by this list and
    # by reviewer.sh's copy of it, and a scanner that trips over its own pattern
    # table can never pass its own gate.
    (re.compile("NotImplemented" + "Error"), "not-implemented-error"),
    (re.compile(r"raise\s+" + "NotImplemented"), "raise-not-implemented"),
    (re.compile(r"^\s*pass\s*#\s*(?:stub|todo|placeholder)", re.I), "pass-stub"),
    (re.compile(r"^\s*\.\.\.\s*$"), "ellipsis-stub"),
    (re.compile(r"throw new Error\(['\"](?:not implemented|TODO|unimplemented)", re.I), "js-not-impl"),
]
STUB_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go"}
STUB_SKIP_DIRS = {"node_modules", ".venv", "__pycache__", "dist", "build", ".git", "coverage"}


# ---------------------------------------------------------------------------
# process plumbing
# ---------------------------------------------------------------------------

def run(argv, cwd=None, timeout=600, env_extra=None, full=False):
    """Run a command and return the record that goes into the evidence file.

    `full` keeps the whole stdout rather than its tail. Output is otherwise
    trimmed so the evidence file stays readable -- but a caller that PARSES the
    output must ask for all of it. Trimming first is not a cosmetic choice: the
    reviewer's JSON runs past the tail on a large diff, and a truncated object
    does not parse, which this gate reported as "reviewer.sh produced no JSON"
    on the first run big enough to hit it.
    """
    env = dict(os.environ, **{SENTINEL: "1"})
    if env_extra:
        env.update(env_extra)
    started = time.time()
    try:
        p = subprocess.run(
            argv, cwd=str(cwd or ROOT), capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        out, err, code = "", f"timed out after {timeout}s", 124
    except OSError as exc:
        out, err, code = "", str(exc), 127
    return {
        "cmd": " ".join(argv),
        "exit": code,
        "seconds": round(time.time() - started, 2),
        "stdout": out if full else out[-4000:],
        "stderr": err[-2000:],
    }


def git(*args):
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


# ---------------------------------------------------------------------------
# what this turn touched
# ---------------------------------------------------------------------------

def _transcript_path(session_id, explicit):
    # The Stop hook is handed the path by the harness; a caller that drives this
    # gate from outside a session (a queue runner, a test) names it here.
    explicit = explicit or os.environ.get("WORKSHOP_DONE_TRANSCRIPT") or ""
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    if not session_id:
        return None
    # The per-project transcript directory is named for the cwd with separators
    # replaced by dashes -- Claude Code's own convention.
    slug = str(ROOT).replace("/", "-")
    cand = Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"
    return cand if cand.is_file() else None


def _turn_start_ts(transcript, after=None):
    """Epoch seconds of the EARLIEST real user message after `after`.

    A tool_result also arrives typed `user`, so those are skipped: they mark the
    middle of a turn, not its start.

    EARLIEST, NOT LATEST, and that is the second half of the same deadlock. The
    `isMeta` clause below stops this gate's OWN rejection from moving the
    boundary; it does not stop the operator from doing it. Typing mid-turn is a
    supported and encouraged path here, and those messages are real user
    messages carrying no `isMeta`, so each one dragged the boundary forward past
    commits the turn had already made.

    Measured 2026-09-03, the same session: the turn opened at 20:46 and
    committed at 20:51, 21:07 and 21:17; the operator typed at 21:27 and again
    at 21:40; the boundary followed him, `base` resolved to the turn's own last
    commit, and reviewer.sh answered `no_diff` against an empty range with
    eleven commits sitting in front of it.

    `after` is the last mark, written when a turn ends, so the earliest message
    since then is this turn's opener. Erring EARLY is the safe direction: it
    attributes too much rather than too little, and `touched_files` narrows the
    set afterwards. Attributing too little is what fails closed and cannot be
    cleared by rerunning.

    AND SO IS THIS GATE'S OWN REJECTION, which is the deadlock this clause
    exists to break. The Stop hook's feedback is injected into the transcript as
    a `user` message carrying `isMeta`, so counting it moved the turn boundary
    to a moment AFTER the turn's own commits. `turn_boundary` then resolved
    `base` to HEAD, `reviewer.sh` was handed a baseline with the work already
    behind it and answered `no_diff`, and the retry failed for that reason
    however correct the work was. Every later attempt failed the same way, so
    the gate could reject once and then never be satisfiable again. Measured
    2026-09-03.

    `isMeta` is the discriminator rather than the message text: a real user
    message does not carry it, and matching on wording would break the moment
    the hook's copy changed.
    """
    if not transcript:
        return None
    best = None
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"user"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") != "user":
                    continue
                # Injected context (this gate's own Stop-hook feedback among it),
                # not a turn the operator started. See the docstring.
                if ev.get("isMeta"):
                    continue
                content = (ev.get("message") or {}).get("content")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    continue
                ts = ev.get("timestamp")
                if not ts:
                    continue
                try:
                    from datetime import datetime
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                if after is not None and when <= after:
                    continue          # belongs to a turn that has already ended
                if best is None or when < best:
                    best = when
    except OSError:
        return None
    return best


_PATHISH = re.compile(r"(?<![\w/.-])(?:/workspace/)?((?:[\w.-]+/)+[\w.-]+\.[A-Za-z]{1,5})")


# A shell command that could have WRITTEN something. Path-shaped tokens in a
# command with none of these are being read, not written, and adopting a file
# because this turn ran `sed -n` over it is how the gate would end up demanding
# delivery of somebody else's work.
_WRITEISH = re.compile(
    r">|\btee\b|\bsed\b[^|]*-i|\bcp\b|\bmv\b|\btouch\b|\bchmod\b|<<"
    r"|\bgit\s+(?:add|rm|mv|commit|restore|checkout)\b")


def declared_edits(transcript, since_ts):
    """(certain, likely) paths this turn's tool calls named.

    mtime alone cannot tell two concurrent agents apart, and this checkout runs
    several at once. What CAN tell them apart is the turn's own record of what
    it did.

    CERTAIN is the file_path of an Edit/Write: that tool wrote that file, full
    stop. LIKELY is a path-shaped token in a shell command that could have
    written something -- necessary, because a turn in auto mode edits with sed
    and heredocs and no file_path field ever sees those, and imprecise, because
    the same command line often names files it only read. A likely path counts
    only if the file also changed during the turn, which is exactly the
    corroboration a certain one does not need.
    """
    if not transcript:
        return set(), set()
    from datetime import datetime
    certain, likely = set(), set()
    try:
        with open(transcript, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                ts = ev.get("timestamp")
                if ts:
                    try:
                        if datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() < since_ts:
                            continue
                    except ValueError:
                        pass
                content = (ev.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    inp = block.get("input") or {}
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if isinstance(fp, str):
                        certain.add(fp)
                    cmd = inp.get("command")
                    if isinstance(cmd, str) and _WRITEISH.search(cmd):
                        likely.update(m.group(1) for m in _PATHISH.finditer(cmd))
    except OSError:
        return set(), set()
    return _relative(certain), _relative(likely)


def _relative(paths):
    rels = set()
    for f in paths:
        try:
            rels.add(str(Path(f).resolve().relative_to(ROOT)) if f.startswith("/") else f)
        except ValueError:
            continue
    return rels


def _mark_path(session_id):
    return MARKS / f"{(session_id or 'nosession')}.json"


def read_mark(session_id):
    try:
        return json.loads(_mark_path(session_id).read_text())
    except (OSError, ValueError):
        return {}


def write_mark(session_id):
    MARKS.mkdir(parents=True, exist_ok=True)
    try:
        _mark_path(session_id).write_text(json.dumps({
            "at": time.time(),
            "head": git("rev-parse", "HEAD").strip(),
        }))
    except OSError:
        pass


def turn_boundary(session_id, transcript):
    """(epoch seconds, base commit, how it was established).

    `how` is "fallback" when nothing in the session could place the start of the
    turn. That is not a licence to judge the turn against a three-hour window on
    a checkout several agents are writing to -- it means the question cannot be
    answered, and callers treat it that way rather than blaming the turn for
    everything dirty in the tree.
    """
    mark = read_mark(session_id)
    marked = mark.get("at")
    # The opener of the turn running NOW: the first thing the operator said
    # after the last turn ended. Anything said since is an interjection INTO
    # this turn, not the start of a new one.
    spoken = _turn_start_ts(transcript, after=marked)
    if spoken:
        at, how = spoken, "transcript"
    elif marked:
        at, how = marked, "mark"
    else:
        at, how = time.time() - FALLBACK_LOOKBACK_S, "fallback"
    base = mark.get("head") or ""
    if base:
        # A mark from a previous checkout can name a commit this tree lost.
        if subprocess.run(["git", "cat-file", "-e", base + "^{commit}"],
                          cwd=str(ROOT), capture_output=True).returncode != 0:
            base = ""
    if not base:
        iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(at))
        base = git("rev-list", "-1", f"--before={iso}", "HEAD").strip()
    return at, (base or "HEAD"), how


def touched_files(since_ts, base_rev, explicit=None, certain=(), likely=()):
    """The files this turn is answerable for.

    Working-tree changes, narrowed to what this turn actually wrote.

    THE TURN'S OWN RECORD WINS WHEN THERE IS ONE. The transcript names the
    file_path of every Edit/Write and carries the text of every Bash command, so
    it says what this turn wrote with no guessing. mtime is only the fallback,
    and it is a poor one on a shared checkout: measured while this was being
    built, two runs eleven seconds apart adopted three and then six more files
    as other agents saved them. A gate that grows a stranger's work between one
    run and the next is a gate nobody can satisfy.

    A commit CORROBORATES, it does not confer ownership: on a checkout where
    several agents commit inside one turn's window, counting a commit on its own
    adopted 54 of their files in a single run. It only counts for a path the
    record already names, which is what lets a file edited last turn and
    committed this one still be checked for delivery.
    """
    if explicit:
        return sorted({f for f in explicit if not _ignored(f)})
    certain, likely = set(certain), set(likely)
    recorded = bool(certain or likely)
    names = set()
    for chunk in (
        git("diff", "--name-only", base_rev),
        git("diff", "--cached", "--name-only"),
        git("ls-files", "--others", "--exclude-standard"),
    ):
        names.update(n for n in chunk.splitlines() if n.strip())
    out = []
    for rel in sorted(names):
        if _ignored(rel):
            continue
        p = ROOT / rel
        try:
            recent = p.is_file() and p.stat().st_mtime + 1 >= since_ts
        except OSError:
            recent = False
        committed = rel in _committed_since(base_rev)
        if recorded:
            mine = rel in certain or (rel in likely and (recent or committed))
        else:
            mine = recent or committed
        if mine:
            out.append(rel)
    return out


_COMMITTED_CACHE = {}


def _committed_since(base_rev):
    if base_rev not in _COMMITTED_CACHE:
        if base_rev in ("", "HEAD"):
            _COMMITTED_CACHE[base_rev] = set()
        else:
            _COMMITTED_CACHE[base_rev] = {
                n for n in git("diff", "--name-only", f"{base_rev}..HEAD").splitlines() if n
            }
    return _COMMITTED_CACHE[base_rev]


def _ignored(rel):
    return rel.startswith(IGNORED_PREFIXES) or rel.startswith(".git/")


def fingerprint(files, base_rev):
    """Identifies exactly this state of exactly these files.

    The Stop hook recomputes it. If it does not match the evidence on disk, the
    evidence describes a different tree than the one about to be reported done,
    and that is the whole trick -- a passing run cannot be reused for the edit
    that came after it.
    """
    h = hashlib.sha1()
    h.update(git("rev-parse", "HEAD").strip().encode())
    for rel in sorted(files):
        h.update(rel.encode())
        p = ROOT / rel
        try:
            h.update(hashlib.sha1(p.read_bytes()).digest())
        except OSError:
            h.update(b"<absent>")
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# surfaces
# ---------------------------------------------------------------------------

def surface_of(rel):
    if rel == EDGE_FILE:
        return "public edge code"
    if rel == VITE_ENV_FILE:
        return "vite env"
    if rel.startswith(UI_PREFIXES) or rel in UI_FILES:
        return "ui bundle"
    if rel.startswith("backend/") and not rel.startswith("backend/tests/"):
        if rel.endswith(".py") or "requirements" in Path(rel).name:
            return "backend"
    return None


def dirty_paths():
    out = set()
    for line in git("status", "--porcelain", "-uall").splitlines():
        if len(line) > 3:
            out.add(line[3:].strip().split(" -> ")[-1])
    return out


def _restart_is_armed():
    """True when a deferred backend restart is parked and still alive.

    `scripts/restart_backend.sh --deferred` records its waiter here and clears
    the file after the restart lands, so a live pid means the restart is still
    coming. A stale pid is not trusted: the waiter may have been killed, or the
    machine rebooted under it.
    """
    f = RUNTIME / "restart-armed.pid"
    try:
        pid = int(f.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def stale_surfaces(apply_stdout):
    """Surfaces apply.sh --check reported STALE, by the names it prints."""
    stale = set()
    for line in apply_stdout.splitlines():
        s = line.strip()
        if not s.startswith("STALE"):
            continue
        rest = s[len("STALE"):].strip()
        for name in ("ui bundle", "backend", "vite env", "public edge code",
                     # The schema is the surface apply.sh REPORTS and never
                     # fixes. It has to be readable here, because it is what
                     # tells a stale backend apart from a blocked one.
                     "schema"):
            if rest.startswith(name):
                stale.add(name)
                break
    return stale


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def scan_stubs(files):
    hits = []
    for rel in files:
        p = ROOT / rel
        if not p.is_file() or p.suffix not in STUB_EXTS:
            continue
        if any(part in STUB_SKIP_DIRS for part in rel.split("/")):
            continue
        try:
            for ln, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for pat, name in STUB_PATTERNS:
                    if pat.search(line):
                        hits.append(f"{rel}:{ln}:{name}")
                        break
        except OSError:
            continue
    return hits


def check_delivery(files, evidence):
    """Did this turn's change reach the surface the operator is looking at?

    Judged per surface AND per owner. Three outcomes, and the middle one is what
    keeps this gate usable on a shared checkout:

      pass    the surface is in sync, so the change is live.
      fail    the surface is stale and this turn is why -- either its own files
              are uncommitted (apply.sh will not publish somebody's unfinished
              work, and that is correct) or nothing else is dirty there.
      foreign the surface is stale and only somebody ELSE's uncommitted work is
              holding the build. There is no action this turn can take that does
              not publish their half-written code, so it does not block. It is
              still said out loud, in the report and to the operator, because a
              silent undelivered UI change is the exact defect on record.
    """
    mine = {}
    for rel in files:
        s = surface_of(rel)
        if s:
            mine.setdefault(s, []).append(rel)
    if not mine:
        return {"state": "pass", "detail": "no running surface touched", "surfaces": {}}

    rec = run(["bash", str(ROOT / "scripts" / "apply.sh"), "--check"], timeout=120)
    evidence["commands"].append(rec)
    if rec["exit"] not in (0, 1):
        return {"state": "fail", "detail": "apply.sh --check could not run", "surfaces": {}}

    stale = stale_surfaces(rec["stdout"])
    dirty = dirty_paths()
    result, worst = {}, "pass"
    for surface, my_files in sorted(mine.items()):
        if surface not in stale:
            result[surface] = {"state": "pass", "files": my_files}
            continue
        my_dirty = [f for f in my_files if f in dirty]
        foreign_dirty = sorted(
            f for f in dirty
            if surface_of(f) == surface and f not in set(files)
        )
        if my_dirty:
            result[surface] = {
                "state": "fail", "files": my_files, "uncommitted": my_dirty,
                "why": "your own files are uncommitted, so the build will not publish them",
            }
            worst = "fail"
        elif foreign_dirty:
            result[surface] = {
                "state": "foreign", "files": my_files, "blocked_by": foreign_dirty,
                "why": "another agent's uncommitted work is holding this build",
            }
            if worst == "pass":
                worst = "foreign"
        else:
            # AN UNAPPLIED MIGRATION IS NOT "RUN APPLY.SH AGAIN".
            #
            # apply.sh DECLINES the backend restart when the tree's migrations
            # are ahead of the live database, and it declines deliberately and
            # forever: starting code whose migration has not landed is an
            # outage, not a risk, and nothing in this repo's deploy path runs
            # alembic. So telling an agent to run apply.sh here sends it round
            # a loop that cannot terminate, and the honest answer is that this
            # surface is blocked on an act that is not the agent's to perform.
            #
            # Measured 2026-09-01: this gate said "run: bash scripts/apply.sh"
            # for a backend held back by exactly that, having just watched
            # apply.sh refuse.
            if surface == "backend" and "schema" in stale:
                result[surface] = {
                    "state": "blocked", "files": my_files,
                    # NOT "ask the operator". This is a development
                    # installation and applying a migration in it is ordinary
                    # work; apply.sh declines only because the migration has to
                    # land BEFORE the restart, not because it needs anyone's
                    # permission. Telling the agent to hand the command back is
                    # what produced the same stop-short four times in one
                    # session on 2026-09-01, and the operator's answer was "do
                    # the migration of course why wouldn't you just do that?"
                    "why": "the tree's migrations are not applied to the "
                           "database the backend uses, so apply.sh declines the "
                           "restart (order, not danger: a missing column fails "
                           "at query time). Run it, then apply.sh:\n"
                           "  set -a; . ~/.config/workshop/db-admin.env; set +a\n"
                           "  cd backend && .venv/bin/python -m alembic upgrade head\n"
                           "  bash scripts/apply.sh\n"
                           "If migration_state.py said 'per a direct "
                           "connection', you are in a sandbox that reaches a "
                           "DIFFERENT database: say so instead of migrating it.",
                }
                if worst == "pass":
                    worst = "blocked"
            elif surface == "backend" and _restart_is_armed():
                # A DEFERRED RESTART IS THE CORRECT STATE MID-TURN, NOT A DEBT.
                #
                # apply.sh does not restart the backend while a turn is
                # running: it arms a waiter that fires once the reply lands,
                # because restarting underneath a live turn kills the process
                # holding it. So between "the agent ran apply.sh" and "the reply
                # is delivered" the backend is STALE and cannot be anything
                # else, by design.
                #
                # This gate demanded a state apply.sh deliberately refuses to
                # produce. Any turn that changed backend code therefore failed
                # its own completion gate, was told to "run: bash
                # scripts/apply.sh", ran it, got the same answer, and looped.
                # Measured 2026-09-03, and it is the third instance of this
                # shape in one session: a check that cannot be satisfied by the
                # thing it asks for.
                #
                # The waiter is the evidence. A live pid in
                # `.runtime/restart-armed.pid` means the restart is coming and
                # this turn has done its part; a missing or dead one means it
                # has not, and that still fails.
                result[surface] = {
                    "state": "armed", "files": my_files,
                    "why": "a deferred restart is armed and fires when this "
                           "reply lands; apply.sh will not restart under a live "
                           "turn. Verify next turn: bash scripts/apply.sh --check",
                }
                if worst == "pass":
                    worst = "pass"
            else:
                result[surface] = {
                    "state": "fail", "files": my_files,
                    "why": "stale with a clean tree -- run: bash scripts/apply.sh",
                }
                worst = "fail"
    return {"state": worst, "surfaces": result}


def check_correctness(files, base_rev, evidence, foreign):
    """The existing gate when it can speak for this turn; its tools when it cannot.

    reviewer.sh is the repo's deterministic completion gate and running it whole
    is the right answer -- when the working tree contains only this turn's work.
    Measured on the day this was built, a full run on a tree carrying six other
    agents' edits took 4m53s and rejected with `fresh_clone_regression` and
    `backend_tests_regressed`, neither of which the turn being judged had caused
    or could fix. Blocking on that produces exactly the outcome every ratchet in
    this directory was rewritten to avoid, and a five-minute wait for it besides.

    So: clean tree -> reviewer.sh, blocking. Shared tree -> the same tools it
    drives (mypy, tsc, check_design.sh), pointed at this turn's files only, and
    the fact that the whole gate could not be attributed is written down.
    """
    if not foreign:
        # PASS THE TURN'S BASELINE. reviewer.sh judges the working tree against
        # a baseline commit-ish and defaults to HEAD, so on a CLEAN tree it sees
        # zero changed files and rejects with `no_diff`.
        #
        # That made this branch unreachable in the one case it is meant for.
        # CLAUDE.md says "commit your own work as you go; never leave it
        # uncommitted", so a finished turn's tree is clean BY THE RULES, and the
        # only reason the gate had been passing was that an untracked file kept
        # the tree dirty and sent every run down the scoped path instead.
        # Measured 2026-09-01: cleaning the tree turned a passing gate into
        # `no_diff` with no change to the work being judged.
        #
        # `base_rev` is the turn boundary this function is already given, which
        # is exactly the revision the turn's commits sit on top of.
        rec = run(["bash", str(ROOT / "scripts" / "reviewer.sh"), base_rev],
                  timeout=1200, full=True)
        evidence["commands"].append(rec)
        try:
            verdict = json.loads(rec["stdout"])
        except ValueError:
            return {"state": "fail", "mode": "reviewer.sh",
                    "reasons": ["reviewer.sh produced no JSON"]}
        return {
            "state": "pass" if verdict.get("complete") else "fail",
            "mode": "reviewer.sh",
            "reviewer_verdict": verdict.get("verdict"),
            "reasons": verdict.get("reasons") or [],
            "stub_hits": verdict.get("stub_hits") or [],
        }

    reasons, ran = [], []
    py = [f for f in files if f.endswith(".py") and (ROOT / f).is_file()]
    mypy = ROOT / "backend" / ".venv" / "bin" / "mypy"
    if py and mypy.exists():
        rec = run([str(mypy), "--no-error-summary", "--ignore-missing-imports", *py], timeout=300)
        evidence["commands"].append(rec)
        ran.append("mypy")
        if rec["exit"] != 0:
            reasons.append("py_typecheck_failed")

    ts = [f for f in files if f.endswith((".ts", ".tsx")) and
          f.startswith(("frontend/", "packages/ui/"))]
    if ts:
        tsc = ROOT / "frontend" / "node_modules" / ".bin" / "tsc"
        if not tsc.exists():
            tsc = ROOT / "node_modules" / ".bin" / "tsc"
        if tsc.exists():
            rec = run([str(tsc), "-b", "--noEmit"], cwd=ROOT / "frontend", timeout=300)
            evidence["commands"].append(rec)
            ran.append("tsc")
            if rec["exit"] != 0:
                reasons.append("ts_typecheck_failed")

    tsx = [f for f in files if f.endswith(".tsx")]
    design = ROOT / "scripts" / "check_design.sh"
    if tsx and design.exists():
        rec = run(["bash", str(design), "--added", base_rev], timeout=300)
        evidence["commands"].append(rec)
        ran.append("check_design.sh --added")
        if rec["exit"] != 0 and any(f in rec["stdout"] for f in tsx):
            reasons.append("design_violations")

    return {
        "state": "fail" if reasons else "pass",
        "mode": "scoped (" + ", ".join(ran) + ")" if ran else "scoped (nothing to check)",
        "reasons": reasons,
        "not_attributable": "reviewer.sh judges the whole working tree; "
                            f"{len(foreign)} file(s) in it belong to another agent",
        "foreign_sample": sorted(foreign)[:8],
    }


# BOTH WIDTHS, ALWAYS, and this is the point of the check rather than a detail
# of it.
#
# frontend/CLAUDE.md has said "shoot it at 390 wide and at 1440 wide before you
# report it done" since 2026-08-22. This gate shot 1440 only, and the phone shot
# was left to whoever remembered, so in practice a turn produced ONE image and
# the other width was never seen. The kit's own row breaks at 720px: on
# 2026-09-01 `.ws-listrow__actions` was placed in the same grid cell as the meta
# at every width above that, so the archive and overflow buttons drew on top of
# the model name, and the phone -- where the two are on separate rows -- looked
# perfect. Three rounds of "fixed" went out over it, and the operator reported
# the same page wrong each time.
#
# One shot cannot answer a question about a responsive layout. Two can.
SHOT_WIDTHS = (("390", 390, 900), ("1440", 1440, 900))


def check_screenshot(files, shot_routes, evidence, delivery_state):
    visual = [f for f in files if f.endswith(VISUAL_SUFFIXES) and
              (f.startswith(UI_PREFIXES) or f.startswith("mobile/"))]
    if not visual and not shot_routes:
        return {"state": "pass", "detail": "no visual change", "shots": []}
    if visual and delivery_state == "fail":
        return {"state": "pending", "detail": "delivery failed; a shot would show old code",
                "files": visual, "shots": []}
    if not shot_routes:
        return {
            "state": "fail", "files": visual, "shots": [],
            "detail": "a visual change owes a screenshot of the built origin: "
                      "bash scripts/done.sh --shot /<route>",
        }
    shots = []
    for route in shot_routes:
        stem = "shot" + (route.rstrip("/").replace("/", "-") or "-root")
        (ROOT / "scratch" / "done-gate").mkdir(parents=True, exist_ok=True)
        for label, w, h in SHOT_WIDTHS:
            rel = f"scratch/done-gate/{stem}-{label}.png"
            rec = run(["bash", str(ROOT / "scripts" / "shoot.sh"), route, str(w), str(h), rel],
                      timeout=180)
            evidence["commands"].append(rec)
            ok = rec["exit"] == 0 and (ROOT / rel).is_file()
            shots.append({"route": route, "width": w, "path": str(ROOT / rel), "ok": ok})
    return {
        "state": "pass" if all(s["ok"] for s in shots) else "fail",
        "files": visual, "shots": shots,
        # Said in the result, not only in this file, because the agent reading
        # the gate's output is the one who has to open both files.
        "look_at_both": "a defect can exist at one width and not the other; "
                        "read every path above, not the first one",
    }


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def evaluate(session_id, transcript, explicit_files, shot_routes):
    since, base, how = turn_boundary(session_id, transcript)
    certain, likely = declared_edits(transcript, since)
    files = touched_files(since, base, explicit_files, certain, likely)
    evidence = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session": session_id or "",
        "base": base,
        "since": since,
        "boundary": how if not explicit_files else "explicit",
        "files": files,
        "fingerprint": fingerprint(files, base),
        "commands": [],
        "checks": {},
        "verdict": "pass",
    }
    if not files:
        evidence["checks"]["scope"] = {"state": "pass", "detail": "this turn changed nothing"}
        return evidence

    foreign = dirty_paths() - set(files)
    evidence["foreign_wip"] = sorted(foreign)

    stubs = scan_stubs(files)
    evidence["checks"]["stubs"] = {
        "state": "fail" if stubs else "pass", "hits": stubs[:20],
    }
    delivery = check_delivery(files, evidence)
    evidence["checks"]["delivery"] = delivery
    evidence["checks"]["correctness"] = check_correctness(files, base, evidence, foreign)
    evidence["checks"]["screenshot"] = check_screenshot(
        files, shot_routes, evidence, delivery["state"])

    failed = [k for k, v in evidence["checks"].items() if v.get("state") == "fail"]
    evidence["verdict"] = "fail" if failed else "pass"
    evidence["failed_checks"] = failed
    return evidence


def render(evidence, quiet=False):
    """Silent on a clean pass. Specific and short on anything else."""
    lines = []
    n = len(evidence["files"])
    if evidence["verdict"] == "pass":
        notes = []
        d = evidence["checks"].get("delivery", {})
        for surface, s in (d.get("surfaces") or {}).items():
            if s.get("state") == "foreign":
                notes.append(
                    f"NOT LIVE: {surface} is stale because of another agent's "
                    f"uncommitted work ({', '.join(s['blocked_by'][:3])}"
                    f"{'...' if len(s['blocked_by']) > 3 else ''}). "
                    f"Your files are committed; the build publishes when theirs are."
                )
            elif s.get("state") == "blocked":
                # Same class as foreign: real, named, and not the agent's to
                # perform. It must be SAID, loudly, every time -- an agent that
                # reports done over this has told the operator his backend is
                # live when it is not.
                notes.append(f"NOT LIVE: {surface}. " + s.get("why", ""))
        c = evidence["checks"].get("correctness", {})
        if c.get("not_attributable"):
            notes.append("reviewer.sh was NOT run: " + c["not_attributable"]
                         + ". Checked instead: " + c.get("mode", "?") + ".")
        for shot in (evidence["checks"].get("screenshot", {}).get("shots") or []):
            notes.append(f"shot {shot['route']}: {shot['path']}")
        if quiet and not notes:
            return ""
        lines.append(f"done: PASS  ({n} file{'s' if n != 1 else ''} this turn)")
        lines.extend("  " + x for x in notes)
        return "\n".join(lines)

    lines.append(f"done: NOT DONE  ({n} file{'s' if n != 1 else ''} this turn)")
    for name in evidence.get("failed_checks", []):
        c = evidence["checks"][name]
        if name == "stubs":
            lines.append("  stubs: " + "; ".join(c["hits"][:6]))
        elif name == "delivery":
            for surface, s in c["surfaces"].items():
                if s.get("state") == "fail":
                    lines.append(f"  delivery: {surface} is STALE -- {s['why']}")
                    for f in s.get("uncommitted", [])[:6]:
                        lines.append(f"      uncommitted: {f}")
        elif name == "correctness":
            lines.append(f"  {c.get('mode', 'correctness')}: "
                         + (", ".join(c.get("reasons", [])) or "rejected"))
            for h in c.get("stub_hits", [])[:5]:
                lines.append(f"      {h['file']}:{h['line']} {h['marker']}")
        elif name == "screenshot":
            lines.append("  screenshot: " + c.get("detail", "missing"))
            for f in c.get("files", [])[:5]:
                lines.append(f"      changed: {f}")
    lines.append(f"  evidence: {EVIDENCE}")
    return "\n".join(lines)


def save(evidence):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    tmp = EVIDENCE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(evidence, indent=2))
    tmp.replace(EVIDENCE)
    try:
        with open(LOG, "a") as fh:
            fh.write(f"[{evidence['when']}] {evidence['verdict']} "
                     f"session={evidence.get('session') or '?'} "
                     f"files={len(evidence['files'])} fp={evidence['fingerprint']} "
                     f"failed={','.join(evidence.get('failed_checks', []) or ['none'])}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# the Stop hook
# ---------------------------------------------------------------------------

def stop_hook():
    """Refuse to let a turn end on an unverified claim.

    Fast by construction: it does not re-run the gate. It recomputes the turn's
    file set and its fingerprint (milliseconds) and asks whether the evidence on
    disk describes exactly this state. It does re-run `apply.sh --check`, which
    costs about a tenth of a second and is the one thing that can go stale
    between the gate passing and the turn ending.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    # Claude Code sets this on a stop that is already the result of a block.
    # Honouring it is what stops a refusal from becoming a loop.
    if payload.get("stop_hook_active"):
        return 0
    session = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    transcript = _transcript_path(session, payload.get("transcript_path"))

    since, base, how = turn_boundary(session, transcript)
    if how == "fallback":
        # Nothing in this session places the start of the turn, so nothing here
        # can be attributed to it. Record the boundary for next time and let it
        # end -- refusing over a tree it may not have written is the behaviour
        # that gets a gate switched off, and a gate that is off catches nothing.
        write_mark(session)
        return 0
    certain, likely = declared_edits(transcript, since)
    files = touched_files(since, base, certain=certain, likely=likely)
    if not files:
        write_mark(session)
        return 0

    fp = fingerprint(files, base)
    try:
        ev = json.loads(EVIDENCE.read_text())
    except (OSError, ValueError):
        ev = {}

    reasons = []
    if ev.get("fingerprint") != fp:
        reasons.append(
            "no completion evidence for this state of the tree. "
            f"{len(files)} file(s) changed this turn and `scripts/done.sh` has not "
            "been run over them (or they changed after it ran)."
        )
    elif ev.get("verdict") != "pass":
        reasons.append("the completion gate last returned NOT DONE: " +
                       ", ".join(ev.get("failed_checks", [])))
    else:
        rec = run(["bash", str(ROOT / "scripts" / "apply.sh"), "--check"], timeout=60)
        stale = stale_surfaces(rec["stdout"])
        mine_stale = sorted({s for s in (surface_of(f) for f in files) if s and s in stale})
        # Only the surfaces the evidence itself recorded as deliverable. A
        # surface the evidence called `foreign` is not this turn's to fix, and
        # neither is one it called `armed`.
        #
        # `armed` IS THE STATE THIS HOOK RUNS IN, which is why it has to be
        # honoured here as well as in check_delivery. The backend restart is
        # deferred precisely so it does not fire under a live turn: it waits for
        # THIS reply to land. So at the moment the Stop hook asks, a turn that
        # changed backend code has necessarily armed a restart that has
        # necessarily not run yet, and re-reading apply.sh --check will always
        # say STALE. Blocking on that is a loop with no exit: the hook tells the
        # agent to run apply.sh, apply.sh arms the restart it already armed, and
        # the hook asks again. Measured 2026-09-03, immediately after the same
        # deadlock was fixed one function away.
        #
        # The waiter being alive is what makes this safe rather than a hole: if
        # nothing is parked, the restart is genuinely owed and the surface still
        # fails below.
        blocked = set()
        for surface, s in ((ev.get("checks", {}).get("delivery", {}) or {}).get("surfaces") or {}).items():
            if s.get("state") == "foreign":
                blocked.add(surface)
            elif s.get("state") == "armed" and _restart_is_armed():
                blocked.add(surface)
        mine_stale = [s for s in mine_stale if s not in blocked]
        if mine_stale:
            reasons.append("these surfaces went stale again after the gate passed: "
                           + ", ".join(mine_stale) + ". Run: bash scripts/apply.sh")

    if not reasons:
        write_mark(session)
        try:
            with open(LOG, "a") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] stop-hook: pass "
                         f"session={session or '?'} files={len(files)} fp={fp}\n")
        except OSError:
            pass
        return 0

    msg = (
        "STOP BLOCKED by the completion gate. Done is a machine decision here, "
        "not a summary you write about yourself.\n\n"
        + "\n".join("- " + r for r in reasons)
        + "\n\nRun `bash scripts/done.sh` (add `--shot /<route>` if you changed "
          "something visible). It composes apply.sh --check, the stub scan and "
          "reviewer.sh and writes the evidence this hook reads. Fix what it "
          "names, then it will let you finish. If it names another agent's "
          "uncommitted work, say so in your report -- that is not yours to fix."
    )
    try:
        with open(LOG, "a") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] stop-hook: BLOCKED "
                     f"session={session or '?'} files={len(files)} fp={fp} "
                     f":: {reasons[0][:120]}\n")
    except OSError:
        pass
    print(json.dumps({"decision": "block", "reason": msg}))
    return 0


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="done.sh", add_help=True)
    ap.add_argument("--stop-hook", action="store_true",
                    help="run as a Claude Code Stop hook (reads the event on stdin)")
    ap.add_argument("--shot", action="append", default=[], metavar="ROUTE",
                    help="screenshot this route of the built origin as evidence")
    ap.add_argument("--files", nargs="*", default=None,
                    help="judge exactly these paths instead of inferring the turn's set")
    ap.add_argument("--json", action="store_true", help="print the evidence instead of a summary")
    ap.add_argument("--quiet", action="store_true", help="print nothing on a clean pass")
    args = ap.parse_args(argv)

    if os.environ.get(SENTINEL) == "1":
        # Already inside a gate run. Saying so beats deadlocking, and it is the
        # same reentry break check_backend_tests.py needed for the same reason.
        return 0
    if args.stop_hook:
        return stop_hook()

    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    evidence = evaluate(session, _transcript_path(session, None), args.files, args.shot)
    save(evidence)
    if args.json:
        print(json.dumps(evidence, indent=2))
    else:
        text = render(evidence, quiet=args.quiet)
        if text:
            print(text)
    return 0 if evidence["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
