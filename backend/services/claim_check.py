"""The claim-to-diff check: does the patch support what the summary says?

Step C6 of `docs/platform-redesign/changes-and-review-2026-08-22.md`, §5.2.

WHY THIS EXISTS. A change carries two things that are not the same kind of
thing: a DIFF, which is what the branch actually did, and a SUMMARY, which is
what its author says it did. §5 of the design is built on distrusting the
second one, because the measured number-one defect in agent-authored changes is
exactly this -- a description claiming work that is not in the patch (45.4% of
inconsistent PRs; §9.3). The worker here writes both the code and the claim, so
nothing in the loop compares them. This module is that comparison.

IT REPORTS A FINDING, NOT A VERDICT. §5.2 asks for claims with nothing behind
them to be FLAGGED, and flagging is all this does: an entry in `gates` that a
person reads. It does not merge, refuse a merge, or move a ticket, and nothing
in the tree reads a gate's status to block anything today. When C7's low-risk
classifier arrives it must exclude this gate by name -- that is what
`ADVISORY_GATES` is for -- because a judgement that can be wrong must not be a
precondition for landing code. A wrong answer here should cost ten seconds of
reading, and nothing else.

IT ONLY EVER DOUBTS THE SUMMARY. The diff is the artifact; it cannot be wrong
about itself. So every finding is phrased from that side -- "the summary says it
added a test; no changed file is a test" -- and a reviewer can check any one of
them against the file list in about ten seconds. A finding nobody can verify
that fast is a finding that gets skimmed, and a skimmed gate is worse than none.

WHERE IT CANNOT JUDGE, IT SAYS SO. `skipped` (with the reason in `detail`) is a
first-class outcome here, not a fallback: a summary that names nothing checkable
-- the sweep's own boilerplate is exactly this -- gets `skipped`, never `pass`.
Recording `pass` for a change nobody actually checked spends the reviewer's
trust on nothing, and the second time they notice, every green gate on the
surface is worth less. `GATE_STATUSES` has no "unjudgeable"; `skipped` is that
status and `db/models.py:1072` already documents it as "did not apply", which is
what this is.

THE CHEAP LAYER COMES FIRST, AND IT IS THE ONE THAT ALWAYS RUNS. Naming a file,
a directory, a function or a script and then not touching it is a mechanical
fact: it needs a regex and a set of paths, it takes a millisecond, and it is
never a matter of opinion. The model layer is OPTIONAL and off by default,
because a check nobody can afford to run is a check that does not run -- an
HTTP handler cannot spend two minutes in a `claude -p` subprocess, and a gate
that only appears when someone remembers to pay for it is a gate the review
surface cannot rely on. So: mechanics everywhere and always, the model only
where a subprocess is affordable (the sweep, the CLI), and the answer records
WHICH LAYER produced it so a reviewer knows whether they are reading a fact or
a judgement.

LAYERING. Above `changes` (which it reads through its published surface) and
beside `change_gates` (whose column it writes, superseding by
`changes.merge_gates` so there is exactly one rule for how a gate list is
superseded). Nothing below imports this and nothing
in `services/` imports it at all -- its callers are `routes/changes.py`, the
sweep and its own CLI -- so unlike `change_gates` it can name `changes` and
`llm_router` at module scope without joining or widening the backend's import
cycle (measured: `scripts/check_module_layout.py` reports the same SCC either
way). The write goes through `changes.update_change` onto the existing `gates`
column: there is no second column and no second results channel, for the same
reason the gates column exists at all.

AND IT NEVER RAISES INTO ITS CALLER, the same contract `change_gates` states:
the change is open and the work is committed whatever this decides, so a broken
regex, an unreachable store or a hung model must cost a log line and nothing
else.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import NamedTuple

from services import changes, llm_router, ownership

__all__ = ["ClaimCheck", "GATE_NAME", "ADVISORY_GATES", "judge",
           "check_change", "record_claim_check"]


# The name this check writes under. One name, one row on the surface.
GATE_NAME = "claim_to_diff"

# Gates that are a FINDING for a person rather than a precondition for landing
# code. Published for step C7: its low-risk classifier requires "every gate
# green", and a judgement gate must not be able to hold a change hostage on an
# opinion. Read this rather than hardcoding a name there.
ADVISORY_GATES = (GATE_NAME,)

# How much of the patch the model layer is shown, and how much of a finding
# survives into `detail`. Both are caps on somebody's attention: the model's
# context and the reviewer's screen.
_DIFF_CHARS = 12000
_DETAIL_CHARS = 600
_SHOWN_FINDINGS = 3


class ClaimCheck(NamedTuple):
    """One comparison of a claim against its artifact.

    `status` is a `GATE_STATUSES` value (`pass`, `fail` or `skipped`; never
    `running` -- this check is synchronous). `findings` are the unsupported
    claims, one plain sentence each. `claims` is what it managed to extract and
    check, which is what makes a `pass` mean something: "nothing to check" and
    "checked and clean" are different answers and only one of them is `pass`.
    `layer` is `mechanical` or `model` and says which one produced `status`.
    """
    status: str
    detail: str
    layer: str
    findings: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()

    def as_gate(self, at: str | None = None) -> dict:
        """The `{name, status, detail, at}` shape `changes.update_change`
        validates. The layer is written into `detail` rather than into a field
        of its own: the column's shape is fixed by the design and a reviewer
        needs the provenance in the same sentence as the claim."""
        stamp = at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {"name": GATE_NAME, "status": self.status, "at": stamp,
                "detail": f"{self.detail} (layer: {self.layer})"[:_DETAIL_CHARS]}


# --------------------------------------------------------------------------
# the cheap layer: what the summary names, and whether the diff contains it
# --------------------------------------------------------------------------

# A clause only carries a CLAIM if it asserts that something was done. Without
# this, "see docs/applying-changes.md for the mechanics" reads as a claim to
# have edited that doc, and the check's first finding on its first real change
# is a false one -- which is how a gate stops being read.
_CHANGE_VERB = re.compile(
    r"\b(add(?:s|ed)?|creat(?:e|es|ed)|introduc(?:e|es|ed)|writ(?:e|es)|wrote"
    r"|updat(?:e|es|ed)|chang(?:e|es|ed)|edit(?:s|ed)?|modif(?:y|ies|ied)"
    r"|fix(?:es|ed)?|refactor(?:s|ed)?|renam(?:e|es|ed)|mov(?:e|es|ed)"
    r"|remov(?:e|es|ed)|delet(?:e|es|ed)|drop(?:s|ped)?|retir(?:e|es|ed)"
    r"|replac(?:e|es|ed)|extend(?:s|ed)?|implement(?:s|ed)?|wir(?:e|es|ed)"
    r"|switch(?:es|ed)?|split|convert(?:s|ed)?|teach(?:es)?|taught"
    r"|port(?:s|ed)?|land(?:s|ed)?|touch(?:es|ed)?)\b", re.I)

_FILE_EXT = (r"py|pyi|ts|tsx|js|jsx|mjs|sh|bash|md|json|css|scss|html|sql"
             r"|ya?ml|toml|cfg|ini|txt|env")
_FILE_RE = re.compile(rf"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.(?:{_FILE_EXT}))"
                      r"(?![\w/])")
# A directory, i.e. something with a slash that is not a file: `backend/tests/`,
# `packages/ui/src`. Trailing slash optional.
_DIR_RE = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]*)(?![\w.])")
# An identifier a person would only write if they meant code: snake_case, an
# UPPER_CONSTANT, or anything called like a function. CamelCase is deliberately
# NOT matched outside backticks -- "Design System" and "Edit mode" are English.
_SYMBOL_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b")
_CALL_RE = re.compile(r"\b([A-Za-z_][\w.]{3,})\(\)")
_BACKTICK_RE = re.compile(r"`([^`]{1,120})`")

# Asserting a test, and asserting a removal. The two claims §5.2's cheap layer
# can check without naming anything at all.
_TEST_CLAIM = re.compile(
    r"\b(add(?:s|ed)?|new|wrote|writ(?:e|es)|includ(?:e|es|ed)|introduc(?:e|es|ed)"
    r"|cover(?:s|ed)?|extend(?:s|ed)?)\b[^.;\n]{0,40}?\b(unit |integration |regression )?"
    r"tests?\b", re.I)
# "deprecated" is deliberately absent: deprecating something usually ADDS a
# notice and removes nothing, so it would flag patches that are exactly right.
_DELETION_CLAIM = re.compile(
    r"\b(remov(?:e|es|ed)|delet(?:e|es|ed)|drop(?:s|ped)?"
    r"|retir(?:e|es|ed)|strip(?:s|ped)?|purg(?:e|es|ed))\b", re.I)

_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)"
                        r"|(^|/)test_[\w.-]+$"
                        r"|_test\.\w+$|\.(test|spec)\.[jt]sx?$")

# Tokens that are code-shaped but say nothing about this diff.
_NOISE = {"self", "true", "false", "none", "null", "return", "import", "class"}


def _asserts_work(clause: str) -> bool:
    """Does this clause say something was DONE, in its prose?

    The names are masked out before the verb is looked for, because a path is
    full of words that are also verbs: `docs/applying-changes.md` contains
    "changes", `update_change` contains "update", and without this the sentence
    "see docs/applying-changes.md for the mechanics" reads as a claim to have
    edited that file. The verb has to come from the author, not from a filename.
    """
    masked = _SYMBOL_RE.sub(" ", _DIR_RE.sub(" ", _FILE_RE.sub(" ", clause)))
    return bool(_CHANGE_VERB.search(masked))


def _clauses(summary: str) -> list[str]:
    """The parts of a summary that assert something was done.

    Sentence-ish, because a claim is made in a sentence: "renamed the helper;
    see docs/x.md for why" is one claim and one pointer, and only the first half
    is checkable.
    """
    parts = re.split(r"(?<=[.;:!?])\s+|\n+|(?:^|\s)[-*]\s+", summary)
    return [p.strip() for p in parts if p and p.strip() and _asserts_work(p)]


def _named(summary: str) -> tuple[list[str], list[str], list[str]]:
    """`(files, dirs, symbols)` the summary claims to have touched.

    Backticked spans are read first and whole, because a backtick is the
    author saying "this is a literal": `bash scripts/apply.sh` yields the
    script, `AgentDetail.tsx` yields the file, and a CamelCase name only counts
    when it arrived that way.
    """
    files: list[str] = []
    dirs: list[str] = []
    symbols: list[str] = []
    for clause in _clauses(summary):
        spans = [clause] + _BACKTICK_RE.findall(clause)
        ticked = " ".join(_BACKTICK_RE.findall(clause))
        for span in spans:
            files.extend(_FILE_RE.findall(span))
            dirs.extend(_DIR_RE.findall(span))
            symbols.extend(_SYMBOL_RE.findall(span))
            symbols.extend(_CALL_RE.findall(span))
        # CamelCase, but only when the author backticked it.
        symbols.extend(re.findall(r"\b([A-Z][a-z0-9]+[A-Z][\w.]*)\b", ticked))

    files = _dedupe(f.strip("./") for f in files)
    # A directory match also fires on every file path; drop those, and drop the
    # bare parents of a file that was named outright (naming
    # `backend/services/x.py` is not separately a claim about `backend/`).
    covered = {f for f in files} | {p for f in files for p in _parents(f)}
    dirs = _dedupe(_as_dir(d) for d in dirs)
    dirs = [d for d in dirs if d and d not in covered
            and _is_repo_path(d) and not _FILE_RE.fullmatch(d)]
    symbols = _dedupe(s for s in symbols
                      if len(s) >= 4 and s.lower() not in _NOISE
                      and not any(s in f for f in files))
    return files, dirs, symbols


def _is_repo_path(token: str) -> bool:
    """Is this slashed token a path in THIS repository?

    Two things are shaped like a path and are not one, and both turn up in real
    summaries: a host (`github.com/org/repo`, whose first segment has a dot) and
    a branch (`wt/proj/thing`, which is the name this system gives its own
    worktree branches and appears in almost every sweep-written note). Reporting
    either as "a directory the diff does not touch" would be a finding a
    reviewer cannot act on, which is the kind that teaches them to skim.
    """
    segments = [seg for seg in token.split("/") if seg]
    if not segments or segments[0] == "wt":
        return False
    return "." not in segments[0]


def _as_dir(token: str) -> str:
    """A directory claim, without the sentence's punctuation stuck to it. A
    trailing full stop is prose, not a path segment, and `foo/bar.py.` left as
    written would be reported as a directory nobody named."""
    return token.strip().strip("/").rstrip(".,;:").strip("/")


def _dedupe(items) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        if item:
            seen.setdefault(item, None)
    return list(seen)


def _parents(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[:i + 1]) for i in range(len(parts))]


def _path_hit(claim: str, paths: list[str]) -> bool:
    """Does a claimed path name one of the changed files?

    Suffix-matched in both directions, because a summary and a diff do not
    agree on where the tree is rooted: a change in a project worktree reports
    `services/x.py` while the summary calls it `backend/services/x.py`, and
    neither is wrong.
    """
    claim = claim.strip("/")
    for path in paths:
        if path == claim or path.endswith("/" + claim) or claim.endswith("/" + path):
            return True
    return False


def _dir_hit(claim: str, paths: list[str]) -> bool:
    needle = claim.strip("/")
    return any(needle == p or f"/{needle}/" in f"/{p}" for p in paths)


def judge(summary: str, diff: dict) -> ClaimCheck:
    """Compare a claim against its artifact, mechanically. Pure; no I/O.

    Everything here is a fact about two strings, which is what makes it cheap
    enough to run on every open and every refresh, and what makes any finding
    it produces checkable by eye against the diff's file list.
    """
    files = [str(f.get("path") or "") for f in (diff.get("files") or [])
             if isinstance(f, dict)]
    files = [f for f in files if f]
    statuses = [str(f.get("status") or "") for f in (diff.get("files") or [])
                if isinstance(f, dict)]
    text = "\n".join(str(f.get("diff") or "") for f in (diff.get("files") or [])
                     if isinstance(f, dict))
    summary = (summary or "").strip()

    if not summary:
        return ClaimCheck("skipped", "cannot judge: this change carries no "
                          "summary, so there is no claim to compare the diff "
                          "against", "mechanical")
    if not files:
        return ClaimCheck("skipped", "cannot judge: the diff for this change "
                          "came back with no files, so there is nothing to "
                          "check the summary against", "mechanical")

    named_files, named_dirs, named_symbols = _named(summary)
    findings: list[str] = []
    claims: list[str] = []

    for claim in named_files:
        claims.append(claim)
        if not _path_hit(claim, files):
            findings.append(f"the summary says it changed `{claim}`, and no "
                            f"file in the diff has that path")
    for claim in named_dirs:
        claims.append(claim + "/")
        if not _dir_hit(claim, files):
            findings.append(f"the summary points at `{claim}/`, and the diff "
                            f"touches nothing under it")
    for claim in named_symbols:
        claims.append(claim)
        if claim not in text:
            findings.append(f"the summary names `{claim}`, and that name does "
                            f"not appear anywhere in the patch")

    # Both of these are read from the ASSERTING clauses only, for the same
    # reason the names are: "see the tests for why" is a pointer, not a claim to
    # have written one.
    asserted = " ".join(_clauses(summary))
    if _TEST_CLAIM.search(asserted):
        claims.append("a test")
        if not any(_TEST_PATH.search(p) for p in files):
            findings.append("the summary says it added a test, and no changed "
                            "file is a test file")
    if _DELETION_CLAIM.search(asserted):
        claims.append("a removal")
        removed = any(s.startswith("D") for s in statuses) or any(
            line.startswith("-") and not line.startswith("---")
            for line in text.splitlines())
        if not removed:
            findings.append("the summary says something was removed, and the "
                            "patch is purely additive -- it deletes no file "
                            "and no line")

    if not claims:
        return ClaimCheck(
            "skipped",
            "cannot judge: this summary names no file, directory, symbol or "
            "command the diff could confirm or contradict, and asserts no test "
            "and no removal. Read the diff; the summary is not evidence",
            "mechanical")
    if findings:
        return ClaimCheck("fail", _fail_detail(findings), "mechanical",
                          tuple(findings), tuple(claims))
    return ClaimCheck(
        "pass",
        f"the diff supports the summary: {len(claims)} checkable claim(s) "
        f"({', '.join(claims[:6])}) are all present in the patch",
        "mechanical", (), tuple(claims))


def _fail_detail(findings: list[str]) -> str:
    shown = "; ".join(findings[:_SHOWN_FINDINGS])
    more = ("" if len(findings) <= _SHOWN_FINDINGS
            else f" (+{len(findings) - _SHOWN_FINDINGS} more)")
    return f"the summary claims work this patch does not contain: {shown}{more}"


# --------------------------------------------------------------------------
# the optional layer: a second model, when one is affordable
# --------------------------------------------------------------------------

# Reached through `services/llm_router.call_cloud` with `tools_disabled=True`,
# which is how every other single-shot judgement in this backend reaches a model
# (`triager._decide_llm`, the chat namers). Tools off is load-bearing and not an
# optimization: a check that reads a diff has no business holding a tool that
# could edit one.
_PROMPT = """You are checking whether a code change's DESCRIPTION matches its PATCH.

You are not reviewing the code. You are answering one question: does the patch
contain the work the description claims? The description was written by whoever
produced the patch and may overstate it. The patch is the fact.

DESCRIPTION (the author's claim):
{summary}

PATCH (truncated; file list first):
{diff}

Answer "unsupported" ONLY when the description asserts something specific that
you can see is absent from the patch, and say which sentence and what is
missing. If the patch plausibly does what is described, answer "supported". If
you cannot tell -- the patch is truncated past the relevant part, or the claim
is too vague to check -- answer "unsure". "unsure" is a good answer; guessing is
not.

Reply with ONLY a JSON object, no prose and no code fence:
  {{"verdict": "supported"|"unsupported"|"unsure",
    "doubt": "<one sentence: which claim, and what the patch does not show; empty unless unsupported>"}}
"""


def _digest(diff: dict) -> str:
    """The patch, small enough to send. File list first, then as much of the
    patch text as fits: the list alone answers most claims, so it must never be
    the part that gets truncated away."""
    files = [f for f in (diff.get("files") or []) if isinstance(f, dict)]
    header = "\n".join(f"  {f.get('status', '?')} {f.get('path', '?')}"
                       for f in files)
    body = "\n".join(str(f.get("diff") or "") for f in files)
    room = max(0, _DIFF_CHARS - len(header))
    truncated = "" if len(body) <= room else "\n... (patch truncated)"
    return f"files changed:\n{header}\n\n{body[:room]}{truncated}"


def _ask_model(summary: str, diff: dict, model: str | None) -> dict | None:
    """One `claude -p` call, or None if it could not be made or understood.

    NEVER RAISES and never half-answers: an import error, a missing CLI, a
    timeout, a fence, a non-object, a verdict outside the closed set are all the
    same outcome -- None -- and None means the caller keeps the mechanical
    answer. Degrading has to be the cheapest path in the function, because it is
    the one that runs on any box without the CLI logged in.
    """
    try:
        res = llm_router.call_cloud(
            _PROMPT.format(summary=summary[:4000], diff=_digest(diff)),
            model=model or llm_router.TIER_SONNET,
            tools_disabled=True, max_turns=1, timeout_s=180)
    except Exception as exc:
        print(f"[claim_check] model layer unavailable ({type(exc).__name__}: "
              f"{exc}); keeping the mechanical answer", file=sys.stderr)
        return None
    text = (res or {}).get("text") or ""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in ("supported", "unsupported", "unsure"):
        return None
    return {"verdict": verdict,
            "doubt": str(raw.get("doubt") or "").strip()[:300]}


def check_change(change: dict, diff: dict, *, use_model: bool = False,
                 model: str | None = None) -> ClaimCheck:
    """The check itself: one change, its diff, one structured answer.

    THE ORDER IS THE DESIGN. The mechanical layer runs first and always. If it
    already found a concrete unsupported claim, that is the answer and no model
    is called -- the finding is a fact, a model cannot improve on it, and
    spending two minutes to re-say it is how a check becomes too expensive to
    keep. The model is only asked when the mechanics came back `pass` or
    `skipped`, which is precisely where they are blind: "adds caching" names
    nothing and no regex can check it.

    A model that is unavailable, unparseable or unsure changes nothing: the
    mechanical answer stands and `layer` still reads `mechanical`, so the gate
    never implies a judgement nobody made.
    """
    mech = judge(str(change.get("summary") or ""), diff)
    if not use_model or mech.status == "fail":
        return mech

    answer = _ask_model(str(change.get("summary") or ""), diff, model)
    if answer is None:
        return mech._replace(
            detail=f"{mech.detail}; the second-model layer was unavailable, so "
                   f"this is the mechanical answer only")
    if answer["verdict"] == "unsupported":
        doubt = answer["doubt"] or ("a claim in the summary is not visible in "
                                    "the patch")
        return ClaimCheck(
            "fail",
            f"a second model reading the patch doubts the summary: {doubt}. "
            f"This is a judgement, not a mechanical fact -- check it against "
            f"the diff",
            "model", (doubt,), mech.claims)
    if answer["verdict"] == "supported":
        return ClaimCheck(
            "pass",
            "a second model read the patch against the summary and found the "
            "work described in it",
            "model", (), mech.claims)
    return mech._replace(
        detail=f"{mech.detail}; a second model read both and could not tell "
               f"either")


# --------------------------------------------------------------------------
# the write: onto the change's `gates`, through the one existing channel
# --------------------------------------------------------------------------

def record_claim_check(*, change_id: str | None = None,
                       project_slug: str | None = None,
                       branch_slug: str | None = None,
                       use_model: bool = False,
                       model: str | None = None) -> dict | None:
    """Judge a change and put the answer on its `gates`. Returns the updated
    change, or None when there was nothing to write to.

    NEVER RAISES, by the same contract `change_gates.record_reviewer_run`
    states and for the same reason: the change is open and its commits exist
    whatever this decides. A store that is down, a branch that was reaped, a
    model that hangs -- each is a log line and a None.

    Addressed by change id or by `(project_slug, branch_slug)`, the pair every
    published entry point in `services/worktrees.py` takes, because the sweep
    knows the branch before it knows the change id.
    """
    try:
        # Pin the data owner when nothing else has. A request has already set
        # one and must keep it; a CLI or a sweep has none, and without this the
        # call dies on OwnerUnset and the gate is silently never written.
        if ownership.owner_or_none() is None:
            ownership.pin_operator()

        if change_id:
            change = changes.get_change(change_id)
        elif project_slug and branch_slug:
            change = changes.change_for_branch(project_slug, branch_slug)
            if change is None:
                print(f"[claim_check] no open change for {project_slug}/"
                      f"{branch_slug}; claim check not recorded", file=sys.stderr)
                return None
        else:
            print("[claim_check] no change id and no project/branch; claim "
                  "check not recorded", file=sys.stderr)
            return None

        result = check_change(change, changes.diff_for(change["id"]),
                              use_model=use_model, model=model)
        return changes.update_change(
            change["id"],
            gates=changes.merge_gates(change.get("gates"),
                                      [result.as_gate()]))
    except Exception as exc:
        print(f"[claim_check] claim check not recorded "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    """`python3 -m services.claim_check --change-id X [--model]`.

    Exits 0 whatever happens: its callers are the sweep and the reviewer leg,
    whose results this must never be able to change.
    """
    ap = argparse.ArgumentParser(
        description="Check a change's summary against its diff.")
    ap.add_argument("--change-id", default="")
    ap.add_argument("--project", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--model", action="store_true",
                    help="allow the second-model layer when the mechanical "
                         "layer finds nothing (costs a claude -p call)")
    ap.add_argument("--model-name", default="")
    args = ap.parse_args(argv)

    written = record_claim_check(
        change_id=args.change_id or None,
        project_slug=args.project or None, branch_slug=args.branch or None,
        use_model=args.model, model=args.model_name or None)
    if written is None:
        return 0
    gate = next((g for g in written["gates"] if g.get("name") == GATE_NAME),
                None)
    if gate:
        print(f"[claim_check] {gate['status']}: {gate['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
