#!/usr/bin/env python3
"""Emit the backend's OpenAPI schema, and the TypeScript types generated from
it. The enabling half of `docs/platform-redesign/target-architecture-
2026-08-17.md` item 2: backend/frontend drift becomes a build failure instead
of a runtime bug.

Run it with `npm run gen:api-types` from the repo root, or directly:

    python3 scripts/gen_openapi.py                      # schema + types, default paths
    python3 scripts/gen_openapi.py --schema-out a.json --ts-out b.ts
    python3 scripts/gen_openapi.py --schema-only

NO SERVER REQUIRED, DELIBERATELY. This imports `backend/main.py` and calls
`app.openapi()`. A generator that scrapes a running `/openapi.json` can only
describe whatever happens to be deployed, which is the exact drift this whole
pipeline exists to detect; and `/openapi.json` is off by default in this app
anyway (`WORKSHOP_ENABLE_DOCS`, security-audit-2026-07-18 L3), so there is
usually nothing to scrape. Importing the app describes the SOURCE TREE, which
is the thing the committed types are supposed to match.

TWO THINGS MAKE THE OUTPUT INSTANCE-INDEPENDENT, and both are load-bearing
because the generated file is committed and diffed in review:

1. THE FULL APP CATALOG IS MOUNTED. App routers (finance/budget/commute/
   location/birds/health/wearables/notes) mount only when the app appears in
   this instance's installed-apps registry (`services/installed_apps`), which
   is `.runtime/` state and gitignored. Generating against whatever this box
   happens to have installed would mean two developers regenerate the same
   source tree and get different files, and the staleness check would fire on
   a roster difference rather than a contract change. So this forces the roster
   to the entire built-in catalog. The schema then covers every route the
   source tree can serve, which is also the right answer for a frontend that
   ships all the app screens regardless of install state.

2. EVERY WRITE IS REDIRECTED TO A TEMPORARY DIRECTORY. Importing the app is
   not free of side effects: `main` calls `app_registry.ensure_identity_
   manifests()` at import, which seeds `backend/apps.json`, and the roster
   override would otherwise be written into the live `.runtime/installed-
   apps.json`. Both are instance state an offline codegen run has no business
   touching, so `WORKSHOP_RUNTIME`, `WORKSHOP_CONFIG_DIR`,
   `WORKSHOP_INSTALLED_APPS` and `WORKSHOP_APPS_MANIFEST` are all pointed at a
   scratch directory that is deleted on the way out. These MUST be set before
   the backend is imported: each of those paths is read into a module-level
   constant at import time.

The schema JSON is written with sorted keys, so `paths` comes out in
alphabetical order and nothing in the output depends on router mount order.

`scripts/check_generated_client.py` is the ratchet that runs this into a temp
location and fails when the committed types no longer match.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
_BACKEND = _WORKSHOP / "backend"

DEFAULT_SCHEMA_OUT = _WORKSHOP / "tmp" / "openapi.json"
DEFAULT_TS_OUT = _WORKSHOP / "frontend" / "src" / "api" / "schema.gen.ts"

_OPENAPI_TYPESCRIPT = _WORKSHOP / "node_modules" / ".bin" / "openapi-typescript"

_TS_HEADER = """\
/**
 * GENERATED FILE -- DO NOT EDIT BY HAND.
 *
 * TypeScript types for the Workshop HTTP API, generated from the backend's own
 * OpenAPI schema by `npm run gen:api-types` (scripts/gen_openapi.py, then
 * openapi-typescript). Every edit you make here will be erased the next time
 * anyone regenerates.
 *
 * To change a type, change the backend route that produces it -- add or widen
 * its `response_model=` -- and regenerate:
 *
 *     npm run gen:api-types
 *
 * This file is COMMITTED on purpose. A route contract changing should show up
 * as a reviewable diff here, and `scripts/check_generated_client.py` fails when
 * this file has gone stale relative to the source tree.
 *
 * A route with no `response_model=` has no response type here beyond `unknown`.
 * That is not a gap in the generator, it is the backend declining to say what
 * it returns; see scripts/check_response_model.py.
 */

"""


def _reexec_in_backend_venv() -> None:
    """Re-run this script under `backend/.venv/bin/python` whenever we are not
    already there.

    The backend's dependency set lives in `backend/.venv` -- this is the same
    trap as running the backend test suite with the system `python3`. And it is
    NOT enough to fall back only when `import fastapi` fails: this box has a
    partial fastapi in `~/.local` that imports fine and then dies deep inside
    route construction on a missing `python-multipart`. A "does it import"
    probe would have picked that one. The venv is the backend's environment, so
    use it when it exists, unconditionally, and let callers -- an npm script,
    the reviewer gate, an agent at a shell -- stay ignorant of it.
    """
    venv = _BACKEND / ".venv"
    venv_python = venv / "bin" / "python"
    if not venv_python.exists():
        return  # no venv on this checkout; run with whatever we were given
    # Compare PREFIXES, not interpreter paths. `backend/.venv/bin/python` is a
    # symlink onto the same system interpreter `python3` resolves to, so
    # `Path(sys.executable).resolve() == venv_python.resolve()` is True from
    # OUTSIDE the venv as well -- it silently skipped the re-exec and the run
    # died in the wrong environment. `sys.prefix` is the thing that actually
    # differs.
    if Path(sys.prefix).resolve() == venv.resolve():
        return
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def build_schema() -> dict:
    """Import the FastAPI app in an isolated environment and return app.openapi()."""
    scratch = Path(tempfile.mkdtemp(prefix="workshop-openapi-"))
    try:
        os.environ["WORKSHOP_RUNTIME"] = str(scratch / "runtime")
        os.environ["WORKSHOP_CONFIG_DIR"] = str(scratch / "config")
        os.environ["WORKSHOP_INSTALLED_APPS"] = str(scratch / "installed-apps.json")
        os.environ["WORKSHOP_APPS_MANIFEST"] = str(scratch / "apps.json")
        # Docs being on or off does not change what `app.openapi()` returns --
        # `openapi_url=None` only unmounts the ROUTE that serves it -- but say so
        # explicitly rather than leaving the generator dependent on an env var
        # that means something else.
        os.environ.setdefault("WORKSHOP_ENABLE_DOCS", "0")
        (scratch / "runtime").mkdir(parents=True, exist_ok=True)

        sys.path.insert(0, str(_BACKEND))

        # The catalog is known only after `app_registry` imports, and the roster
        # has to be on disk before `main` imports (that is where the mount loop
        # runs). So: import the catalog, write the roster, then import the app.
        from services import app_registry, installed_apps

        roster = sorted(app_registry.BUILTIN_APPS)
        Path(installed_apps.REGISTRY_PATH).write_text(
            json.dumps({"installed": roster}, indent=2) + "\n", encoding="utf-8"
        )

        import main

        return main.app.openapi()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def normalize_operation_ids(schema: dict) -> int:
    """Make every operationId unique and stable. Returns how many were rewritten.

    FastAPI's default `generate_unique_id` ends an operationId with
    `"_" + list(route.methods)[0].lower()` -- and `route.methods` is a SET. For
    the one multi-method route in this app (`base_apps.run_proxy`, seven verbs
    on a single `@router.api_route`) that picks an arbitrary verb, so all seven
    operations get the SAME id and WHICH verb it is changes between processes
    with python's string hash seed. Measured: two back-to-back runs of this
    generator over an unchanged tree produced 21 differing lines, and
    openapi-typescript collapses the seven duplicates into one `operations`
    entry on top of that. A staleness check cannot tell that apart from a real
    contract change, so it has to be fixed before the output is committed.

    The rewrite is a no-op for every ordinary single-verb route: their id
    already ends with their own verb. It only bites where the id claims a verb
    the operation does not have.
    """
    rewritten = 0
    for path, item in (schema.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not isinstance(op_id, str):
                continue
            for verb in _HTTP_METHODS:
                suffix = "_" + verb
                if op_id.endswith(suffix):
                    if verb != method:
                        op["operationId"] = op_id[: -len(suffix)] + "_" + method
                        rewritten += 1
                    break
    return rewritten


def write_schema(schema: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_types(schema_path: Path, ts_path: Path) -> None:
    """Run openapi-typescript over the schema and prepend the do-not-edit header."""
    if not _OPENAPI_TYPESCRIPT.exists():
        raise SystemExit(
            f"gen_openapi: {_OPENAPI_TYPESCRIPT} is missing. Run `npm install` at "
            f"the repo root (openapi-typescript is a root devDependency)."
        )
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [str(_OPENAPI_TYPESCRIPT), str(schema_path)],
        cwd=_WORKSHOP,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        sys.stderr.write(res.stderr)
        raise SystemExit(f"gen_openapi: openapi-typescript failed ({res.returncode})")
    # Generate to stdout rather than with `-o`: openapi-typescript's own banner
    # names the input path it was given, which would put a temp directory into
    # the committed file and make every check run report a spurious diff.
    ts_path.write_text(_TS_HEADER + res.stdout, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--schema-out", default=str(DEFAULT_SCHEMA_OUT))
    ap.add_argument("--ts-out", default=str(DEFAULT_TS_OUT))
    ap.add_argument("--schema-only", action="store_true",
                    help="write the OpenAPI JSON and stop; do not run openapi-typescript")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    _reexec_in_backend_venv()

    schema = build_schema()
    normalize_operation_ids(schema)
    schema_path = Path(args.schema_out)
    write_schema(schema, schema_path)

    n_paths = len(schema.get("paths", {}))
    n_schemas = len(schema.get("components", {}).get("schemas", {}))

    if args.schema_only:
        if not args.quiet:
            print(f"gen_openapi: {n_paths} path(s), {n_schemas} schema(s) -> {schema_path}")
        return 0

    ts_path = Path(args.ts_out)
    write_types(schema_path, ts_path)
    if not args.quiet:
        print(f"gen_openapi: {n_paths} path(s), {n_schemas} schema(s)")
        print(f"    schema: {schema_path}")
        print(f"    types:  {ts_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
