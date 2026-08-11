#!/usr/bin/env bash
# check_dev_bundle.sh — the phase 6 check for the FRONTEND half of developer
# mode. ADR 0014, design §11 phase 6.
#
# THE CLAIM UNDER TEST: "in a normal build the Development scope and the
# surfaces that reach it are not in the bundle at all." That sentence is written
# in three places (ADR 0014, frontend/src/lib/devMode.ts, the design doc) and
# until this script nothing checked it. `devMode.test.ts` passes `devMode: false`
# by hand, which tests the RULES; the property that matters is what a real build
# freezes into `DEV_MODE`, and a unit run can only ever observe whichever mode it
# happened to be built in.
#
# WHAT IT DOES: strips VITE_DEV_MODE from frontend/.env, compiles
# src/lib/devMode.bundleProbe.ts through the real Vite pipeline and the real
# vite.config.ts, executes the emitted module, and asserts every dev gate reads
# OFF at its default argument. Then it repeats the build with VITE_DEV_MODE=1 in
# the environment (Vite's loadEnv lets process.env win over .env) and asserts
# every gate reads ON. That second pass is a positive control: without it a
# broken probe that always reports "absent" would pass forever.
#
# WHY IT IS NOT IN THE UNIT SUITE: it has to write frontend/.env, which on this
# deployment carries VITE_DEV_MODE=1 and must survive. A vitest case cannot take
# a file the whole dev server reads out from under a parallel run, and a suite
# that mutates deployment config is a suite nobody can run twice. It is a
# separate script for a human or the reviewer gate to run after a change to
# devMode.ts, chatScope.ts, or anything else the probe imports.
#
# WHY IT DOES NOT GREP THE APP BUNDLE. That is the obvious implementation and it
# cannot work. The string "development" is in a non-dev bundle no matter what,
# because `orderScopes` has to compare against it in order to FILTER it out, and
# `scopeGroups(devMode)` takes a parameter so its dev branch is never
# tree-shaken. Grepping would either always pass or always fail, and either way
# would say nothing. The only honest question is what the compiled code DOES, so
# this runs it.
#
# WHAT IT DOES NOT PROVE: this is a product capability boundary and a safety
# interlock, not a security boundary. It shows a non-dev bundle does not OFFER
# the Development surfaces. Anyone with a shell on the box still edits the
# source directly; that is [SEC-A1] and still open. The backend half is checked
# by backend/tests/test_dev_mode_gate.py.
#
# Usage: bash scripts/check_dev_bundle.sh
# Exit:  0 all assertions held, 1 an assertion failed, 2 setup failed.

set -uo pipefail

WORKSHOP="${WORKSHOP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FRONTEND="$WORKSHOP/frontend"
ENV_FILE="$FRONTEND/.env"
BACKUP=""

# frontend/.env is deployment config, not a test fixture. Restore it on ANY exit
# path, including a failed assertion, a crash inside node, or a Ctrl-C.
restore_env() {
  local code=$?
  if [[ -n "$BACKUP" && -f "$BACKUP" ]]; then
    cp -p "$BACKUP" "$ENV_FILE" && rm -f "$BACKUP"
  fi
  exit "$code"
}

cd "$FRONTEND" || { echo "check_dev_bundle: no $FRONTEND" >&2; exit 2; }
if [[ ! -x node_modules/.bin/vite ]]; then
  echo "check_dev_bundle: frontend deps are not installed (run npm install)" >&2
  exit 2
fi

if [[ -f "$ENV_FILE" ]]; then
  BACKUP="$(mktemp "${TMPDIR:-/tmp}/workshop-frontend-env.XXXXXX")" || exit 2
  cp -p "$ENV_FILE" "$BACKUP"
fi
trap restore_env EXIT INT TERM

# The build state a normal install has: the flag is not in the file at all.
if [[ -n "$BACKUP" ]]; then
  grep -v -E '^[[:space:]]*VITE_DEV_MODE[[:space:]]*=' "$BACKUP" > "$ENV_FILE"
fi

# One node program, run twice. `--input-type=module` reads it from stdin, so the
# expected mode arrives in WANT_DEV_MODE rather than argv.
NODE_PROGRAM=$(cat <<'NODEEOF'
import path from "node:path";
import { build } from "vite";

const want = process.env.WANT_DEV_MODE === "on";
const label = want ? "VITE_DEV_MODE=1 (positive control)" : "VITE_DEV_MODE unset (a normal build)";

// The real config, the real root, so .env loading and env replacement are the
// ones a `npm run build` would apply. publicDir is off only because copying
// 141MB of map tiles and audio to compare two booleans is absurd; it cannot
// affect what the flag compiles to. write:false keeps the artifact in memory,
// so this check never has an output directory to collide with frontend/dist.
const out = await build({
  configFile: path.resolve("vite.config.ts"),
  root: process.cwd(),
  publicDir: false,
  logLevel: "error",
  build: {
    ssr: "src/lib/devMode.bundleProbe.ts",
    write: false,
    minify: false,
    rollupOptions: { output: { format: "es" } },
  },
});

const chunks = (Array.isArray(out) ? out : [out]).flatMap((o) => o.output ?? []);
const entry = chunks.find((c) => c.type === "chunk" && c.isEntry);
if (!entry) {
  console.error("no entry chunk emitted");
  process.exit(2);
}

// Execute what the build produced. The probe's imports resolve to nothing
// outside the bundle, so a data URL is a complete module here.
const url = "data:text/javascript;base64," + Buffer.from(entry.code).toString("base64");
const { bundledDevSurfaces } = await import(url);
const s = bundledDevSurfaces();

const failures = [];
const check = (name, ok, detail) => {
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}${detail ? `: ${detail}` : ""}`);
  if (!ok) failures.push(name);
};

console.log(`built ${label}`);
check("compiled DEV_MODE", s.devMode === want, String(s.devMode));
check(
  want ? "offers the development scope" : "offers no dev-only scope",
  want ? s.devScopes.includes("development") : s.devScopes.length === 0,
  JSON.stringify(s.devScopes),
);
check(
  want
    ? "keeps development in a scope list the server sent, first"
    : "drops development from a scope list the server sent",
  want
    ? s.orderedServerScopes[0] === "development"
    : !s.orderedServerScopes.includes("development"),
  JSON.stringify(s.orderedServerScopes),
);
check(
  want ? "chat scope picker has a Development group" : "chat scope picker has no Development group",
  s.scopeGroupKinds.includes("development") === want,
  JSON.stringify(s.scopeGroupKinds),
);
check(
  want
    ? "the frozen SCOPE_GROUPS constant has it too"
    : "the frozen SCOPE_GROUPS constant has none either",
  s.frozenScopeGroupKinds.includes("development") === want,
  JSON.stringify(s.frozenScopeGroupKinds),
);

if (failures.length) {
  console.error(`FAILED (${label}): ${failures.join(", ")}`);
  process.exit(1);
}
NODEEOF
)

status=0

echo "== a normal build: no VITE_DEV_MODE anywhere =="
if ! WANT_DEV_MODE=off node --input-type=module <<<"$NODE_PROGRAM"; then
  status=1
fi

echo
echo "== positive control: VITE_DEV_MODE=1 in the environment =="
if ! WANT_DEV_MODE=on VITE_DEV_MODE=1 node --input-type=module <<<"$NODE_PROGRAM"; then
  status=1
fi

echo
if [[ $status -eq 0 ]]; then
  echo "dev bundle check PASSED: a build without VITE_DEV_MODE offers no Development surface,"
  echo "and the same build with it offers all of them, so the check is measuring the flag."
else
  echo "dev bundle check FAILED. See the assertions above." >&2
fi

exit "$status"
