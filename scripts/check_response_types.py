#!/usr/bin/env python3
"""A response shape is NAMED FROM THE GENERATED CLIENT, not written by hand.
Measured, ratcheted, only goes down.

Rule: where the frontend decodes an HTTP response body, the type it gives that
body comes from `frontend/src/api/schema.gen.ts` --
`components["schemas"]["Name"]` for a model, `paths["/route"][...]` for a
response. A type spelled out by hand at that point, and a decode with no type
at all, are the two things this counts.

THE FINDING THIS PROTECTS, and it is not hypothetical. `GET /api/tasks` and
`GET /api/jobs` moved to a `Page[T]` envelope on 2026-08-17 (`7e4aacbf`):
`tasks`/`jobs` became `items` and `total` moved inside `page`. SEVEN frontend
call sites never followed. Each had hand-written its own response type, so each
still typechecked while reading `undefined`. For two weeks `/tickets` printed
"No tickets yet" over 188 open tickets, the board drew empty lanes, the Runs
feed was empty, a run's children never appeared, and the agent rows showed no
last run. There was no error anywhere, because AN EMPTY LIST IS EXACTLY WHAT A
MISSING KEY LOOKS LIKE. Fixed in `17a6ad96` by naming
`components["schemas"]["Page_Task_"]`, after which the same rename would be a
red typecheck. `check_generated_client.py` keeps that file honest against the
backend; this checker is what makes the frontend USE it.

WHY THE UNIT IS A BOUNDARY AND NOT A TYPE DECLARATION. The number quoted since
2026-08-17 -- "528 hand-written types" (ADR 0023, `target-architecture`) -- was
a count of type DECLARATIONS. That number is not reproducible today and, more
importantly, it is not the thing that breaks: a declaration is inert. The bug
lives at the point where a `Response` body is given a type, because that is the
one place a claim about the wire is made. So the unit here is the RESPONSE
BOUNDARY, and every one of the seven failures above is exactly one of them.
Converting a boundary is also a self-contained edit a reviewer can check, which
a declaration count never was.

WHAT IS COUNTED, and why exactly three numbers:

  handwritten_response_types   A boundary carrying a type the tree wrote
                               itself: `(await r.json()) as { items?: Task[] }`,
                               `const d: Foo = await r.json()`,
                               `getJson<Bar>(url)`. It typechecks, and it is a
                               claim nothing verifies.

  untyped_response_reads       A boundary with no type at all: `setData(await
                               r.json())`, `.then((r) => r.json())`. Worse than
                               the first, not better: `any` flows out and every
                               field read off it is unchecked. Counted
                               separately because the remedy differs -- one is
                               a replacement, the other is an addition.

  handwritten_list_reads       The subset of the two above that reads a
                               COLLECTION. This is the blast-radius number and
                               it is the one to drive down first. A wrong shape
                               on a detail page usually breaks loudly (a name
                               renders as `undefined`); a wrong shape on a list
                               renders the empty state, which is
                               indistinguishable from "there is nothing here"
                               and is what hid the failure above for two weeks.

DECODERS ARE DISCOVERED, NOT LISTED. This tree wraps `fetch` in about a dozen
generic helpers (`jget<T>`, `getJson<T>`, `j<T>`, `req<T>`, `readJson<T>`,
plus a layer of `fetchX = <T>() => jget<T>(...)` forwarders). For those, the
boundary is the CALL SITE that supplies a concrete `T`, not the `as T` inside
the helper -- the helper is the mechanism and is deliberately not counted. The
set of helpers is computed as a fixed point over the tree (a generic function
whose body decodes a response, plus anything that forwards to one), so a NEW
wrapper in a new file is counted the day it is written. A hand-curated list
would have been the obvious thing and it is exactly how this check would be
evaded without anyone meaning to.

GENERATED IS RECOGNISED THROUGH ONE ALIAS HOP. `components["schemas"]["X"]`
written at the boundary counts as generated, and so does a local
`type Task = components["schemas"]["Task"]` named there, because naming the
alias IS the idiom this repo uses (see `pages/apps/tabs/AppChats.tsx`). The
alias map is built from the whole tree and shared by both sides of a delta, so
it cannot shift underneath one of them.

WHAT IT DOES NOT CATCH, stated so a green run is not over-read. It cannot see
whether a NAMED generated type is the RIGHT one for that endpoint -- naming
`Page_Job_` where the route answers `AgentJobsOut` typechecks and this check is
happy. It also cannot see a route that declares no `response_model=` at all:
that route has no generated type to name, only `unknown`, and the debt is
tracked one layer down by `check_response_model.py`. Both are why the remedy
text points at the route, not just at the call site.

A RATCHET, not an absolute, and it has to be. There are hundreds of boundaries
here; a check that failed until every one was converted would be turned off in
a week, which is the documented fate of the gate `check_frontend_state.py`'s
header describes. So the debt is frozen and the direction is enforced.

PIN FROM A COMMITTED TREE. The counts come from `git ls-files`, so pinning with
work in progress records whatever the index happened to hold. Commit, then
--write-baseline. (Same trap that mis-pinned `no_app_names_baseline.json`.)

DELTA MODE (`--since <ref>`) is the same mechanism as `check_no_app_names.py`
and `check_frontend_state.py`, for the same reason: judged against a static pin,
a whole-tree count blames a worker for inherited debt, which is what stalled
2,066 loop jobs (see `scripts/_ratchet_delta.py`). It runs the SAME whole-tree
scan on a `git archive` extract of the base ref and on the working tree and
fails only on a count this run made worse.

Usage:
    python3 scripts/check_response_types.py                # report + gate on baseline
    python3 scripts/check_response_types.py --json
    python3 scripts/check_response_types.py --list         # every counted site
    python3 scripts/check_response_types.py --since HEAD   # gate mode
    python3 scripts/check_response_types.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "response_types_baseline.json"

# The one scan root. `packages/ui` is outside it on purpose: the kit is a
# general-purpose library with no API of its own to name.
SCAN_PREFIX = "frontend/src/"

# Where a response type comes from.
GENERATED = "frontend/src/api/schema.gen.ts"
REGEN_COMMAND = "npm run gen:api-types"

# A decode: `.json()` on a Response. `JSON.parse` is not one -- that is a string
# the caller already holds, not a wire contract.
_JSON = re.compile(r"\.json\s*\(\s*\)")

# The generated client, named at a boundary. `operations` is included for
# completeness; nothing in the tree uses it yet.
_GEN_REF = re.compile(r"\b(?:components|paths|operations)\s*\[")

# A type given to a decode, in the three shapes this tree writes:
#   (await r.json()) as X        /  r.json() as Promise<X>
#   const d: X = await r.json()  /  const d = (await r.json()) as X
#   ((await r.json()) as { ... }).items
_AS_CAST = re.compile(r"\.json\s*\(\s*\)\s*\)?\s*as\s+(?!T\b)(.+)$")
_AS_PROMISE = re.compile(r"\.json\s*\(\s*\)\s*as\s+Promise\s*<\s*(?!T\s*>)(.+)$")
_CONST_ANNOT = re.compile(r"\b(?:const|let)\s+\w+\s*:\s*([^=]+?)\s*=\s*(?:await\s+)?[\w.]*\.json\s*\(")

# The helper's OWN passthrough, which is the mechanism and not a boundary:
# `return (await r.json()) as T` / `return r.json() as Promise<T>`.
_PASSTHROUGH = re.compile(r"\.json\s*\(\s*\)\s*(?:\)\s*)?as\s+(?:Promise\s*<\s*T\s*>|T)\s*[;)]?\s*$")

# A generic declaration that could be a decoder:
#   async function jget<T>(...)   |   const j = async <T>(...)
_GENERIC_FN = (
    re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*<\s*T\b"),
    re.compile(r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=\s*(?:async\s*)?<\s*T\b"),
)
# A call that forwards the caller's own T onward: `jget<T>(...)`.
_FORWARD = re.compile(r"\b([A-Za-z_$][\w$]*)\s*<\s*T\s*>\s*\(")
# How far past a generic declaration its body is read. Every decoder in this
# tree is a three-to-ten line wrapper; a longer window starts swallowing the
# next declaration and calling it a decoder too.
_DECL_WINDOW = 12

# A local alias for a generated type: `type Task = components["schemas"]["Task"]`.
_ALIAS = re.compile(r"\btype\s+([A-Za-z_$][\w$]*)\s*=\s*(.*)$")

# Any local shape declaration, aliased or interface, whose body is read to
# decide whether the type it names is a collection.
_SHAPE_DECL = re.compile(r"\b(?:type|interface)\s+([A-Za-z_$][\w$]*)\b")
_SHAPE_WINDOW = 8

# The read is a COLLECTION. Deliberately generous on the field names this
# tree's envelopes actually use, because a false negative here is the failure
# this whole file is about, while a false positive only ranks a site higher.
_LIST = re.compile(
    r"\[\]|Array\s*<|\?\?\s*\[|\|\|\s*\[|\.map\s*\(|\.length\b"
    r"|\bitems\b|\brows\b|\bjobs\b|\btasks\b|\bagents\b|\bscopes\b|\bthreads\b"
    r"|\bnotes\b|\bfiles\b|\bstores\b|\bapps\b|\bruns\b|\bdevices\b|\bchanges\b"
    r"|\bgrants\b|\bdelegates\b|\btargets\b|\bproviders\b|\btemplates\b"
)
# The window a site is judged over: the line, the one before it (for a `const x:
# T =` that wrapped), and the two after (for `.then((d) => setJobs(d.jobs))`,
# where the collection is only visible one line down).
_BACK, _FWD = 1, 2

# A nearby `fetch(` gives the endpoint, for the report only. Never gates.
_URL = re.compile(r"""fetch\s*\(\s*[`"']([^`"']+)""")
_HELPER_URL = re.compile(r"""[({,]\s*[`"'](/[^`"']*)""")


def tracked_files(root: Path = ROOT) -> list[str]:
    """Repo-relative paths of every tracked file under the scan root.

    For a checkout that is `git ls-files`; for a `git archive` extract (delta
    mode) there is no `.git`, and the walk IS the answer because an archive
    holds exactly the tracked tree. `.git` is a FILE in a worktree, so
    `.exists()` rather than `.is_dir()`."""
    if (root / ".git").exists():
        out = subprocess.run(
            ["git", "ls-files", "--", SCAN_PREFIX],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout
        return [ln for ln in out.splitlines() if ln]
    base = root / SCAN_PREFIX
    if not base.exists():
        return []
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())


def _is_source(rel: str) -> bool:
    """A scanned file: TS/TSX under the scan root, tests and the generated
    client excluded. A test that stubs a response is describing a fixture, not
    choosing a contract -- same carve-out `check_frontend_state.py` makes."""
    if not rel.startswith(SCAN_PREFIX) or not rel.endswith((".ts", ".tsx")):
        return False
    if rel == GENERATED:
        return False
    return ".test." not in Path(rel).name


def _is_comment(line: str) -> bool:
    s = line.strip()
    return s.startswith(("//", "*", "/*"))


def generated_aliases(bodies: dict[str, list[str]]) -> set[str]:
    """Type names that resolve to the generated client in ONE hop.

    `type Task = components["schemas"]["Task"]` is the idiom this repo already
    uses, and a boundary naming `Task` is naming the generated type. One hop
    only: a chain of aliases is not something the tree does, and following an
    arbitrary depth would mean re-implementing TypeScript's resolver in a
    ratchet."""
    names: set[str] = set()
    for lines in bodies.values():
        for i, line in enumerate(lines):
            if _is_comment(line):
                continue
            m = _ALIAS.search(line)
            if not m:
                continue
            tail = " ".join(lines[i:i + 3])
            if _GEN_REF.search(tail):
                names.add(m.group(1))
    return names


def collection_types(bodies: dict[str, list[str]]) -> set[str]:
    """Locally declared type names whose shape holds an array.

    Needed because the commonest hand-written envelope hides its collection
    behind a name: `type JobsResp = { jobs: Job[]; count: number }` and then
    `(await r.json()) as JobsResp` at the boundary, where nothing on the line
    says "list". That is the exact pair that left the Runs feed empty, so a
    ranking that could not see it would rank the real cases last."""
    names: set[str] = set()
    for lines in bodies.values():
        for i, line in enumerate(lines):
            if _is_comment(line):
                continue
            m = _SHAPE_DECL.search(line)
            if not m:
                continue
            body = "\n".join(lines[i:i + _SHAPE_WINDOW])
            if "[]" in body or "Array<" in body:
                names.add(m.group(1))
    return names


def decoders(bodies: dict[str, list[str]]) -> set[str]:
    """Generic helpers whose T is the shape of a response body.

    A fixed point, in two rounds' worth of rules:
      seed  -- a function generic in T whose body decodes a response
      grow  -- a function generic in T whose body forwards T to a known decoder

    The forwarding search deliberately skips the declaration line itself.
    Without that, `capRows<T>(rows: T[])` reads its own name as a forward and
    every non-HTTP generic in the tree joins the set."""
    cands: list[tuple[str, str, str]] = []  # (name, decl line, body after it)
    for rel, lines in bodies.items():
        for i, line in enumerate(lines):
            if _is_comment(line):
                continue
            for rx in _GENERIC_FN:
                m = rx.search(line)
                if not m:
                    continue
                body = "\n".join(lines[i + 1:i + 1 + _DECL_WINDOW])
                cands.append((m.group(1), line, body))
    found = {n for n, _decl, body in cands if _JSON.search(body)}
    for _ in range(4):  # depth of forwarding in this tree is 2; 4 is slack
        grew = False
        for name, _decl, body in cands:
            if name in found:
                continue
            if any(f in found for f in _FORWARD.findall(body)):
                found.add(name)
                grew = True
        if not grew:
            break
    return found


def _endpoint(lines: list[str], i: int) -> str:
    """The endpoint this boundary reads, for the report. Best effort by design:
    it is never compared, never pinned, and never gates. A `Promise.all` of two
    fetches will attribute both boundaries to one of them, which is fine for a
    ranking and would not be fine for a count."""
    back = "\n".join(lines[max(0, i - 8):i + 1])
    urls = _URL.findall(back)
    if urls:
        return urls[-1]
    here = _HELPER_URL.findall(lines[i])
    return here[0] if here else ""


def scan(root: Path = ROOT) -> dict:
    bodies: dict[str, list[str]] = {}
    for rel in tracked_files(root):
        if not _is_source(rel):
            continue
        try:
            bodies[rel] = (root / rel).read_text(errors="replace").splitlines()
        except OSError:
            continue

    aliases = generated_aliases(bodies)
    coll = collection_types(bodies)
    decs = decoders(bodies)
    # Built once here and reused for both sides of a delta: re-deriving it per
    # tree would let the helper set shift underneath the base scan and report a
    # regression that is really the discovery moving.
    call = re.compile(
        r"\b(" + "|".join(sorted((re.escape(d) for d in decs), key=len, reverse=True))
        + r")\s*<\s*([^;]{0,200}?)\s*>\s*\("
    ) if decs else None

    handwritten: list[dict] = []
    untyped: list[dict] = []
    generated: list[dict] = []

    for rel, lines in bodies.items():
        for i, line in enumerate(lines):
            if _is_comment(line):
                continue
            sites: list[tuple[str | None, str]] = []  # (declared type or None, kind)

            if _JSON.search(line) and not _PASSTHROUGH.search(line):
                declared = None
                for rx in (_AS_PROMISE, _AS_CAST, _CONST_ANNOT):
                    m = rx.search(line)
                    if m:
                        declared = m.group(1).strip()
                        break
                if declared is None and i > 0:
                    m = _CONST_ANNOT.search(lines[i - 1] + " " + line)
                    if m:
                        declared = m.group(1).strip()
                sites.append((declared, "decode"))

            if call is not None:
                for m in call.finditer(line):
                    arg = m.group(2).strip()
                    if arg in ("T", "T = unknown"):
                        continue  # the helper forwarding its own T
                    sites.append((arg, "helper"))

            if not sites:
                continue
            window = "\n".join(lines[max(0, i - _BACK):i + 1 + _FWD])
            endpoint = _endpoint(lines, i)
            for declared, kind in sites:
                is_list = bool(_LIST.search(window)) or (
                    declared is not None
                    and any(re.search(rf"\b{re.escape(n)}\b", declared) for n in coll)
                )
                rec = {
                    "file": rel, "line": i + 1, "text": line.strip()[:160],
                    "endpoint": endpoint, "via": kind, "list": is_list,
                }
                if declared is not None and (
                    _GEN_REF.search(declared)
                    or any(re.search(rf"\b{re.escape(a)}\b", declared) for a in aliases)
                ):
                    generated.append(rec)
                elif declared is not None:
                    handwritten.append({**rec, "type": declared[:120]})
                else:
                    untyped.append(rec)

    lists = [s for s in handwritten + untyped if s["list"]]
    return {
        "handwritten_response_types": len(handwritten),
        "untyped_response_reads": len(untyped),
        "handwritten_list_reads": len(lists),
        "generated_boundaries": len(generated),
        "handwritten": handwritten,
        "untyped": untyped,
        "lists": lists,
        "generated": generated,
        "decoders": sorted(decs),
    }


def _new_sites(now: list[dict], before: list[dict]) -> list[dict]:
    """Sites present now and not at the base ref, matched by (file, source
    line). By TEXT and not by line number: a site that moved because something
    was inserted above it is the same site, and calling it new sends the reader
    to fix code that did not change."""
    seen: dict[tuple[str, str], int] = {}
    for s in before:
        seen[(s["file"], s["text"])] = seen.get((s["file"], s["text"]), 0) + 1
    out = []
    for s in now:
        key = (s["file"], s["text"])
        if seen.get(key):
            seen[key] -= 1
        else:
            out.append(s)
    return out


_METRICS = (
    "handwritten_response_types",
    "untyped_response_reads",
    "handwritten_list_reads",
)
_SITES = {
    "handwritten_response_types": "handwritten",
    "untyped_response_reads": "untyped",
    "handwritten_list_reads": "lists",
}

_COMMENT = (
    "Ratchet baseline for check_response_types.py. These counts may only go "
    "DOWN. A response body's type is NAMED from frontend/src/api/schema.gen.ts "
    "(components[\"schemas\"][\"Name\"]), never written by hand: a hand-written "
    "one typechecks green while reading undefined, which is how GET /api/tasks "
    "and GET /api/jobs moving to Page[T] on 2026-08-17 left seven surfaces "
    "silently empty for two weeks (188 tickets under 'No tickets yet'). "
    "handwritten_response_types counts boundaries carrying a type this tree "
    "wrote itself; untyped_response_reads counts boundaries with no type at "
    "all; handwritten_list_reads is the subset of both that reads a COLLECTION "
    "and is the number to drive down first, because a wrong shape on a list "
    "renders the empty state and nobody sees it. A route with no "
    "response_model= has no generated type to name -- that debt is "
    "check_response_model.py's, and it is the reason this is a ratchet and not "
    "an absolute."
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print every counted site, list surfaces first")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--since", metavar="REV", default=None,
                    help="DELTA MODE: scan REV's tree as well and fail only on a "
                         "count this run made worse. See this script's header.")
    ap.add_argument("--because", default="",
                    help="Required when a pin RAISES a count. Recorded in the "
                         "baseline and printed on every subsequent run.")
    args = ap.parse_args()

    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    result = scan(ROOT)

    since_counts = None
    if args.since is not None:
        try:
            with extracted_tree(args.since, ROOT) as base_tree:
                since_counts = scan(base_tree)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_response_types: cannot read ref {args.since!r}: {exc}",
                  file=sys.stderr)
            return 2

    if args.write_baseline:
        # AN UPWARD PIN MUST SAY WHY. A ratchet you re-pin whenever it fails is
        # not a ratchet. The escape hatch exists so a genuine increase (a new
        # app landing with its own unconverted fetchers) is recorded and
        # reprinted on every run rather than done quietly in an editor.
        raised = {
            k: (base.get(k), result[k])
            for k in _METRICS
            if base.get(k) is not None and result[k] > base[k]
        }
        if raised and not args.because:
            for k, (was, now) in sorted(raised.items()):
                print(f"pin would RAISE {k}: {was} -> {now}")
            print("\nRefusing. Re-pin an increase with --because '<why>'.")
            return 1
        payload = {"_comment": _COMMENT, **{k: result[k] for k in _METRICS}}
        if raised:
            payload["raised"] = {
                k: {"from": was, "to": now, "because": args.because}
                for k, (was, now) in sorted(raised.items())
            }
        elif base.get("raised"):
            keep = {k: v for k, v in base["raised"].items()
                    if result.get(k, 0) > v.get("from", 0)}
            if keep:
                payload["raised"] = keep
        BASELINE.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
        return 0

    if args.json:
        payload = dict(result)
        if since_counts is not None:
            payload["since_ref"] = args.since
            payload["since"] = {k: since_counts[k] for k in _METRICS}
        print(json.dumps(payload, indent=2))
        return 0

    if args.list:
        for label, rows in (("LIST", result["lists"]),
                            ("handwritten", [s for s in result["handwritten"] if not s["list"]]),
                            ("untyped", [s for s in result["untyped"] if not s["list"]])):
            print(f"\n=== {label} ({len(rows)})")
            for s in sorted(rows, key=lambda r: (r["file"], r["line"])):
                where = f"{s['file']}:{s['line']}"
                print(f"  {where:<62} {s['endpoint'][:48]}")
                print(f"      {s['text'][:120]}")
        return 0

    print(f"response-types: shapes the frontend states by hand under {SCAN_PREFIX}")
    print(f"  handwritten_response_types {result['handwritten_response_types']:>4}  "
          f"a type written at a decode instead of named from the client")
    print(f"  untyped_response_reads     {result['untyped_response_reads']:>4}  "
          f"a decode with no type at all")
    print(f"  handwritten_list_reads     {result['handwritten_list_reads']:>4}  "
          f"of those, the ones reading a COLLECTION (highest blast radius)")
    print(f"  (generated_boundaries      {result['generated_boundaries']:>4}  "
          f"already naming {GENERATED})")
    print(f"  (decoders discovered       {len(result['decoders']):>4}  "
          f"generic fetch wrappers whose call sites are the boundary)")

    if not base:
        print("\nno baseline; run --write-baseline to pin one")
        return 0

    for key, info in sorted((base.get("raised") or {}).items()):
        print(f"  note {key} was RAISED {info['from']} -> {info['to']}: {info['because']}")

    judge = since_counts if since_counts is not None else base
    against = f"the base ref {args.since}" if since_counts is not None else "the pinned baseline"

    failed = False
    for key in _METRICS:
        was, now = judge.get(key), result[key]
        if was is None:
            continue
        if now > was:
            print(f"\nFAIL {key}: {was} -> {now} against {against}. "
                  f"The rule is that this only goes down.")
            if since_counts is not None:
                for s in _new_sites(result[_SITES[key]], since_counts[_SITES[key]]):
                    print(f"       new: {s['file']}:{s['line']}  {s['text'][:100]}")
            print(f"       Name the generated type instead: "
                  f"components[\"schemas\"][\"Name\"] from "
                  f"{GENERATED.replace('frontend/src/', '')} "
                  f"(import type {{ components }} from \".../api/schema.gen\").")
            print(f"       If the route has no model to name, it declares no "
                  f"response_model= -- add one to the route, run "
                  f"`{REGEN_COMMAND}`, then name it here.")
            failed = True
        elif now < was:
            print(f"  ok  {key} improved {was} -> {now} against {against}")

    if since_counts is not None and base:
        stale = [f"{k}: pinned {base[k]} -> now {result[k]}"
                 for k in _METRICS
                 if base.get(k) is not None and result[k] > base[k]]
        if stale:
            print(f"\n  FYI {len(stale)} count(s) still past the PINNED baseline, "
                  f"inherited and not caused by this change (not gating):")
            for line in stale:
                print(f"      {line}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
