#!/usr/bin/env python3
"""No fact about ONE installation in PLATFORM prompt text.

THE RULE (CLAUDE.md, from docs/platform-redesign/
agent-context-and-toolkit-2026-08-20.md "The four-layer contract"). L0 (kernel),
L1 (orientation) and L2 (persona) are PLATFORM text: every installation and
every user receives them, so they may carry only generalized product doctrine.
They may not carry a fact about one checkout -- a doc path, a repo convention, a
named person, an operator's habits -- and may not assume the reader's pronouns.
L3 (services/custom_instructions.py) is the operator's own and is exempt, and so
are a repo's own AGENTS.md / CLAUDE.md.

CLAUDE.md said of that rule "it has no gate yet, so it is on you". It had not
been on anyone: on 2026-08-27 there were eleven violations, including "live for
him until the bundle is rebuilt", a host-rooted `project_tasks.py` path in two
personas, and a nine-name egress allowlist. This is the gate.

IT MEASURES THE RENDERED PROMPT, AND THAT IS THE 2026-09-16 REWRITE. Until then
it walked `ast.Constant` nodes -- the prose STRING LITERALS in the declared
surfaces -- and that is not the text a model receives. An f-string is an
`ast.JoinedStr` whose interpolated value is a `FormattedValue`, never a
`Constant`, so a path spliced in at runtime was invisible to it. The docstring
above names one of the original eleven as a host path "in two personas"; that
violation was closed by replacing the literal with a `paths.workshop_root()`
interpolation, which removed the DETECTION and left the leak. The gate then read
zero over live text for three weeks, while a project agent was being told it was
working at an absolute host path that does not exist inside its own sandbox.

So the input is now what a model is actually handed: L0/L1/L2 assembled for a
representative provider of EVERY provider kind, plus the MCP tool schemas, plus
the two prompt builders in routes/chat.py. The four detectors and the surface
list in prompt_layer_rules.json are untouched -- only the input changed. The
literal scan is KEPT ALONGSIDE the rendered one rather than replaced, because
deleting a detector is the exact move that caused this: a surface with no
renderer (app_persona, whose rendered form is an app's own declaration and not
platform text at all) still gets scanned the old way.

HOW A REPRESENTATIVE INSTALLATION IS CHOSEN, because rendering needs one and the
answer must not depend on where this script happens to run:

  * THE INSTALL ROOT IS SYNTHETIC AND FIXED (`/home/user/workshop`), and the
    `user` in it is load-bearing rather than cosmetic. It has to LOOK like a
    home path, because the host-path detector this script runs is what must
    catch it if a prompt ever renders it. It also has to be a path no real
    installation owns, because `check_no_hardcoding.sh` reads tracked source
    for one machine's layout and `/home/user/` is its declared generic
    placeholder. An earlier spelling used a plausible-looking operator name
    and satisfied the first requirement while failing the second, which took
    `check_fresh_clone.sh` red on the same day this file was written. Not the
    real one: run inside a confined agent turn the real root reads `/workspace`,
    the mountpoint, which no host-path pattern matches -- so the gate would have
    gone green again depending on who ran it. A fixed root under a home
    directory is what an installation looks like, and it makes the offense
    identity stable instead of machine-dependent.
  * NOTHING IS DECLARED. Every `WORKSHOP_*` variable is cleared, so the render
    is a FRESH installation: no operator name, no git forge, no egress hosts.
    That is precisely the platform/installation line this gate polices --
    `services/installation.py` is the sanctioned way for a fact about one
    install to reach the prompt, and a value that arrived from there is not the
    platform speaking. What remains after clearing it is what the platform says
    on its own.
  * CONFINEMENT IS ON (`WORKSHOP_SCOPED_EXEC=1`), which is the platform's
    default posture (`services/scoped_exec`: "CONFINED BY DEFAULT ... the floor,
    not an opt-in"). A build turn's real cwd is then the managed mountpoint, and
    a prompt that names a host cwd is both a leak and a wrong instruction. `=0`
    changes nothing a prompt says: `scoped_exec.spawn_argv` refuses a turn it
    cannot confine, so every turn that runs sees the mountpoint.
  * A SCOPE'S OWN AGENTS.md / CLAUDE.md IS STUBBED OUT. `_agent_rules` splices a
    repo's own rules file into L2, and that file is explicitly exempt from this
    rule -- it is the place an installation fact is SUPPOSED to live. Rendering
    it would report this repo's CLAUDE.md as dozens of platform violations.

WHAT IT SCANS. The surfaces named in prompt_layer_rules.json: their rendered
prompt text, and inside their source the PROSE STRING LITERALS -- a Python `str`
constant, excluding docstrings (comments are never parsed, so nothing here fires
on a code comment explaining the rule) and excluding literals with no
whitespace. That last exclusion is what separates prompt text from code data:
`_RULES_FILES = ("AGENTS.md", ...)` is a list of filenames the platform READS,
not text a model receives. A file whose prompt text is a minority of the module
declares `symbols`, and only those top-level constants/functions are scanned.

WHAT COUNTS. The four detectors in the declaration: gendered third-person
pronouns, `~/` and host absolute paths, `.md` filenames, and the vendor list.
Every one of them, and the surface list, lives in prompt_layer_rules.json (the
floor lives in prompt_layers_baseline.json, see below). The renderers are code
and live here, beside the gate, because assembling a prompt is not something a
JSON file can say; the providers are DISCOVERED (every `ContextProvider`
subclass in the module) rather than enumerated, so a new persona is measured
without anyone remembering to add it, and a provider that cannot be rendered
FAILS this script instead of being skipped.

THE FLOOR IS ZERO and this fails above it. The floor lives in its own file,
prompt_layers_baseline.json, rather than in the declaration beside it, because
check_ratchets_not_loosened.py globs `*baseline*.json` and must be able to see an
entry being added to `allowed` -- while the declaration's own lists (surfaces,
vendors) GROW as the rule is applied more widely, which that gate would read
backwards. `allowed` is empty as of 2026-08-27 because the eleven were fixed in
the commit that added this, so there is nothing inherited to forgive. A gate that
reports a violation and exits 0 is the exact failure mode this exists to fix.

IT RUNS UNDER THE BACKEND'S INTERPRETER. Rendering means importing
`services.chat_context`, which needs the backend's dependencies; `reviewer.sh`
invokes this as a plain `python3`. So it re-execs once into `backend/.venv` when
the interpreter it started on cannot import the backend -- the same guard, for
the same reason, that `scripts/project_tasks.py` carries. A checkout with no
venv and no importable backend EXITS 2 rather than silently falling back to the
literal-only scan that caused this rewrite.

Usage:
    python3 scripts/check_prompt_layers.py            # exit 1 on any offense
    python3 scripts/check_prompt_layers.py --json
    python3 scripts/check_prompt_layers.py --since REV # fail only on a NEW one
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

_WORKSHOP = Path(__file__).resolve().parents[1]
_RULES_PATH = Path(__file__).resolve().parent / "prompt_layer_rules.json"
_BASELINE_PATH = Path(__file__).resolve().parent / "prompt_layers_baseline.json"

_REEXEC_FLAG = "WORKSHOP_PROMPT_LAYERS_REEXEC"

# The representative installation. Fixed, synthetic, and never written to: see
# "HOW A REPRESENTATIVE INSTALLATION IS CHOSEN" above for why it is not the real
# root. `SLUG` is what a scope is called in the render; it is deliberately a
# word no detector matches, so anything the render reports came from the
# platform's own text rather than from the fixture.
_RENDER_ROOT = "/home/user/workshop"
_RENDER_SLUG = "example"

# A rendered offense is filed under this prefix instead of a file path, so the
# report can say which assembled block it came from.
_RENDERED = "rendered"


def _rules(path: Path = _RULES_PATH) -> dict:
    return json.loads(path.read_text())


def _floor(path: Path = _BASELINE_PATH) -> list[str]:
    """The pinned allowlist: offenses this tree tolerates today, and nothing
    else. Empty means the gate is absolute."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    allowed = data.get("allowed") if isinstance(data, dict) else None
    return sorted(allowed) if isinstance(allowed, list) else []


def _detectors(rules: dict) -> list[tuple[str, re.Pattern[str], str]]:
    """(name, compiled, fix) per declared pattern. The vendor detector is built
    from the `vendors` list so adding a vendor is a data edit, never a code one."""
    out = []
    for name, spec in rules["patterns"].items():
        if spec.get("from_vendors"):
            src = r"\b(" + "|".join(re.escape(v) for v in rules["vendors"]) + r")\b"
        else:
            src = spec["regex"]
        out.append((name, re.compile(src, re.I if spec.get("ignorecase") else 0),
                    spec["fix"]))
    return out


def _prose_literals(src: str, symbols: list[str] | None):
    """Yield (lineno, text) for every prose string literal in `src`.

    Docstrings are skipped (they document the code, they are not sent to a
    model) and so are literals with no whitespace (a bare filename or key is
    code data). `symbols` restricts the walk to named top-level constants and
    functions.

    STILL HERE AFTER THE RENDER REWRITE, and deliberately. This cannot see an
    f-string's interpolation, which is what the rewrite is for; what it CAN see
    is a surface with no renderer, and a detector removed is how the leak this
    gate missed got missed."""
    tree = ast.parse(src)
    roots: list[ast.AST] = [tree]
    if symbols:
        roots = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in symbols:
                roots.append(node)
            elif isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id in symbols for t in node.targets):
                roots.append(node)
    skip: set[int] = set()
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                if ast.get_docstring(node, clean=False) is not None and node.body \
                        and isinstance(node.body[0], ast.Expr):
                    skip.add(id(node.body[0].value))
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in skip and any(c.isspace() for c in node.value):
                yield node.lineno, node.value


# --------------------------------------------------------------- the renderers


class RenderUnavailable(RuntimeError):
    """The prompt could not be assembled, so the gate did not measure it.

    Raised, never swallowed. An un-renderable surface is an UNMEASURED one, and
    a gate that quietly measures less than it claims is the thing this file is
    a rewrite of."""


def _neutralize_env() -> None:
    """Make the process a FRESH installation before anything imports the
    backend. Clearing every `WORKSHOP_*` var is what turns "what does this
    deployment say" into "what does the platform say"; see the header."""
    for key in [k for k in os.environ if k.startswith("WORKSHOP_")]:
        del os.environ[key]
    os.environ["WORKSHOP_ROOT"] = _RENDER_ROOT
    os.environ["WORKSHOP_SCOPED_EXEC"] = "1"


def _backend_importable(root: Path) -> bool:
    if str(root / "backend") not in sys.path:
        sys.path.insert(0, str(root / "backend"))
    try:
        __import__("services.chat_context")
        return True
    except ImportError:
        return False


def _reexec_under_backend_venv() -> None:
    """Re-exec once into `backend/.venv` when this interpreter cannot import the
    backend. Same guard and same reasoning as scripts/project_tasks.py: every
    caller spells this `python3 scripts/check_prompt_layers.py`, so making that
    spelling work is the fix rather than asking reviewer.sh to know about a venv."""
    if os.environ.get(_REEXEC_FLAG):
        return
    venv = _WORKSHOP / "backend" / ".venv" / "bin" / "python3"
    if not venv.exists():
        return
    os.environ[_REEXEC_FLAG] = "1"
    os.execv(str(venv), [str(venv), str(Path(__file__).resolve()), *sys.argv[1:]])


def _stub_provider(cls, cc, paths):
    """A provider instance built without the registries, for rendering only.

    Every attribute a persona reads is set to something REPRESENTATIVE rather
    than blank, and the distinction matters: `dir` gets a real-shaped host path
    (a blank one would hide exactly the leak being hunted), while `_persona` and
    `_body` get placeholders because an app's or an agent's declaration is that
    app's or agent's text, not the platform's."""
    p = object.__new__(cls)
    p.slug = _RENDER_SLUG
    p.key = f"agent:{_RENDER_SLUG}"
    p.name = "Example"
    p.label = "Example"
    p.purpose = "an example scope"
    p.dir = paths.projects_root() / _RENDER_SLUG
    p.project_slug = _RENDER_SLUG
    p.is_self = False
    p._body = "(agent-declared body)"
    p._persona = "(app-declared persona)"
    p.definition_path = ""
    return p


def _providers(cc, paths) -> dict[str, object]:
    """A representative instance of EVERY ContextProvider subclass in the chat
    module, keyed by class name.

    DISCOVERED, not enumerated. An enumerated list is a list somebody has to
    remember to extend, and "somebody remembers" is the failure mode this whole
    file documents: a new persona would ship unmeasured and the gate would keep
    printing zero. Construction is tried in the shapes providers actually have
    (no-arg, slug, slug+record) and falls back to `_stub_provider`; a class that
    renders under none of them raises rather than being skipped."""
    from services import app_persona as _ap

    classes: dict[str, type] = {}
    for module in (cc, _ap):
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, cc.ContextProvider):
                classes[obj.__name__] = obj

    out: dict[str, object] = {}
    for name, cls in sorted(classes.items()):
        record = {"name": "Example", "purpose": "an example scope",
                  "definition": "(agent-declared body)", "role": "maker",
                  "cwd": str(paths.projects_root() / _RENDER_SLUG)}
        attempts = (lambda c=cls: c(), lambda c=cls: c(_RENDER_SLUG),
                    lambda c=cls: c(_RENDER_SLUG, record),
                    lambda c=cls: _stub_provider(c, cc, paths))
        last: Exception | None = None
        for build in attempts:
            try:
                p = build()
                p.persona()
                out[name] = p
                break
            except Exception as exc:       # noqa: BLE001 - any failure is "try the next shape"
                last = exc
        else:
            raise RenderUnavailable(
                f"cannot build a renderable {name}; teach _providers how, or the "
                f"gate measures nothing for it (last error: {last!r})")
    return out


def _render() -> dict[str, dict[str, str]]:
    """{surface path: {block name: rendered text}} for every renderable surface.

    This is the INPUT the detectors run over. It assembles the same blocks
    `routes/chat.py` assembles for a turn and stops exactly where platform text
    stops: `learnings_context`, `ambient_digest`, `working_state_block`,
    `custom_prompt` and `live_context` are STORE CONTENT and L3, which this rule
    exempts, so they are not rendered."""
    _neutralize_env()
    if not _backend_importable(_WORKSHOP):
        _reexec_under_backend_venv()
        raise RenderUnavailable(
            "cannot import the backend, and backend/.venv is absent or cannot "
            "import it either, so the assembled prompt was never measured")

    from services import chat_context as cc
    from services import orientation
    from wsplatform import paths

    # A scope's own AGENTS.md / CLAUDE.md is the sanctioned home for an
    # installation fact, so it is not platform text and must not be scanned.
    cc._agent_rules = lambda read: "(this scope's own agent rules)"

    out: dict[str, dict[str, str]] = {}

    chat: dict[str, str] = {"platform_kernel": cc.platform_kernel()}
    for tools in (True, False):
        chat[f"role_preamble[tools={tools}]"] = cc.role_preamble(tools_active=tools)
    # Handed the host cwd the way routes/chat.py hands it, so a WORKSPACE line
    # that echoed it would render the synthetic home path and be caught.
    chat["agent_self_preamble"] = cc.agent_self_preamble(
        name="Example", slug=_RENDER_SLUG, role="maker",
        scope=f"project:{_RENDER_SLUG}",
        cwd=str(paths.projects_root() / _RENDER_SLUG), tools=["Read"])
    for name, provider in _providers(cc, paths).items():
        chat[f"persona[{name}]"] = provider.persona()
        chat[f"runtime_briefing[{name}]"] = cc.runtime_briefing(provider)
        for tools in (True, False):
            chat[f"instruction_contract[{name},chat_tools={tools}]"] = \
                cc.instruction_contract(provider, enable_chat_tools=tools)
    out["backend/services/chat_context.py"] = chat

    # Every scope shape the manifest branches on, and both sides of every grant,
    # because a line that exists only when a tool is granted is still L1.
    orient: dict[str, str] = {}
    for scope in (None, "workshop", "development", f"project:{_RENDER_SLUG}",
                  f"app:{_RENDER_SLUG}", f"agent:{_RENDER_SLUG}", "role:maker"):
        for grant in (True, False):
            orient[f"manifest[{scope},grants={grant}]"] = orientation.manifest_text(
                scope, background=grant, wakeup=grant, notify=grant)
    out["backend/services/orientation.py"] = orient

    # A tool's name, description and schema reach the model the same way L2
    # does, so the served list IS the rendered form of this surface.
    from mcp import workshop_query_server as wqs
    out["backend/mcp/workshop_query_server.py"] = {
        f"tool[{t.get('name')}]": json.dumps(t, indent=1, default=str)
        for t in wqs._tool_list()
    }

    from routes import chat as chat_routes
    out["backend/routes/chat.py"] = {
        "distill_prompt": chat_routes._DISTILL_PROMPT,
        "summary_prompt": chat_routes._summary_prompt("(previous summary)", []),
    }
    return out


def _rendered_cached() -> dict[str, dict[str, str]]:
    global _RENDER_CACHE
    if _RENDER_CACHE is None:
        _RENDER_CACHE = _render()
    return _RENDER_CACHE


_RENDER_CACHE: dict[str, dict[str, str]] | None = None


# ----------------------------------------------------------------- the scan


def _offenses(root: Path = _WORKSHOP, rules: dict | None = None) -> list[str]:
    """`<where> :: <detector> :: <match>` for every offense under `root`.

    `<where>` is a surface's relative path for a source literal and
    `rendered:<block>` for assembled text. Line numbers are NOT part of the
    identity -- the surface, the detector and the matched text are -- so an
    unrelated edit that shifts lines never churns the result.
    """
    rules = rules or _rules()
    dets = _detectors(rules)
    found: set[str] = set()

    for surface in rules["surfaces"]:
        path = root / surface["path"]
        if not path.is_file():
            continue
        try:
            literals = list(_prose_literals(path.read_text(encoding="utf-8"),
                                            surface.get("symbols")))
        except (OSError, SyntaxError):
            continue
        for _, text in literals:
            for name, pat, _fix in dets:
                for m in pat.finditer(text):
                    found.add(f"{surface['path']} :: {name} :: {m.group(0)}")

    declared = {s["path"] for s in rules["surfaces"]}
    for surface, blocks in _rendered_cached().items():
        if surface not in declared:
            continue
        for block, text in blocks.items():
            for name, pat, _fix in dets:
                for m in pat.finditer(text):
                    found.add(f"{_RENDERED}:{block} :: {name} :: {m.group(0)}")
    return sorted(found)


def _offenses_at_ref(ref: str, rules: dict) -> list[str]:
    """The same measurement against `ref`, run as that tree's OWN checker.

    A SUBPROCESS, not an in-process scan of an extract, because rendering means
    importing a module tree and this process has already imported ours. The base
    ref's checker renders the base ref's code, which is what "was this offense
    already there" has to mean once the input is rendered text."""
    with extracted_tree(ref, _WORKSHOP) as base:
        script = Path(base) / "scripts" / "check_prompt_layers.py"
        if not script.is_file():
            return []
        env = dict(os.environ)
        env.pop(_REEXEC_FLAG, None)
        proc = subprocess.run(
            [sys.executable, str(script), "--json"],
            capture_output=True, text=True, env=env, cwd=str(base))
        if not proc.stdout.strip():
            raise RenderUnavailable(
                f"the checker at {ref} produced no measurement: "
                f"{proc.stderr.strip()[:400]}")
        return list(json.loads(proc.stdout).get("current") or [])


def _locate(where: str, detector: str, match: str, rules: dict) -> tuple[str, str]:
    """(location, excerpt) for the human-readable report only.

    A source literal locates to `file:line`; a rendered block locates to its own
    name and shows the sentence the match sits in, which is the only way to act
    on a match like `/home/` that a format string produced."""
    pat = dict((n, p) for n, p, _ in _detectors(rules))[detector]
    if where.startswith(_RENDERED + ":"):
        block = where.split(":", 1)[1]
        for blocks in _rendered_cached().values():
            text = blocks.get(block)
            if text is None:
                continue
            for m in pat.finditer(text):
                if m.group(0) == match:
                    start = max(0, m.start() - 90)
                    return where, "..." + text[start:m.end() + 90].replace("\n", " ") + "..."
        return where, ""
    surface = next(s for s in rules["surfaces"] if s["path"] == where)
    for lineno, text in _prose_literals((_WORKSHOP / where).read_text(),
                                        surface.get("symbols")):
        for m in pat.finditer(text):
            if m.group(0) == match:
                return f"{where}:{lineno}", ""
    return where, ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--rules", default=str(_RULES_PATH))
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--since", metavar="REV", default=None,
                    help="DELTA MODE: fail only on an offense that is new since REV.")
    args = ap.parse_args()

    rules = _rules(Path(args.rules))
    allowed = _floor(Path(args.baseline))
    try:
        current = _offenses(rules=rules)
    except RenderUnavailable as exc:
        print(f"check_prompt_layers: {exc}", file=sys.stderr)
        return 2

    since: list[str] | None = None
    if args.since is not None:
        try:
            since = _offenses_at_ref(args.since, rules)
        except (subprocess.CalledProcessError, OSError, RenderUnavailable,
                json.JSONDecodeError) as exc:
            print(f"check_prompt_layers: cannot read ref {args.since!r}: {exc}",
                  file=sys.stderr)
            return 2

    # Delta mode still honours the pinned floor: an offense the base ref also had
    # is inherited, but one on the `allowed` list is forgiven either way.
    inherited = set(since or []) | set(allowed)
    offenders = [o for o in current if o not in inherited]

    if args.json:
        print(json.dumps({"current": current, "allowed": allowed,
                          "since_ref": args.since, "since": since,
                          "offenders": offenders}, indent=2))
        return 1 if offenders else 0

    if offenders:
        print(f"check_prompt_layers: {len(offenders)} installation-specific "
              f"fact(s) in PLATFORM prompt text:")
        fixes = {n: f for n, _, f in _detectors(rules)}
        for o in offenders:
            where, detector, match = o.split(" :: ", 2)
            loc, excerpt = _locate(where, detector, match, rules)
            print(f"    {loc}  [{detector}] {match!r}")
            if excerpt:
                print(f"        {excerpt}")
            print(f"        {fixes[detector]}")
        print("\nL0/L1/L2 ship to every installation and every user. A fact about "
              "one checkout belongs in that repo's AGENTS.md / CLAUDE.md, in the "
              "operator's L3 custom instructions, or in the installation "
              "declaration (backend/services/installation.py).")
        return 1

    scanned = ", ".join(s["path"] for s in rules["surfaces"])
    blocks = sum(len(b) for b in _rendered_cached().values())
    print(f"check_prompt_layers: platform prompt text is clean ({len(current)} "
          f"offense(s), floor {len(allowed)}) across {blocks} rendered block(s) "
          f"and the prose literals of: {scanned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
