# Stage 5, the two things the loader plan asserts: kit versioning and build-time distribution

- Date: 2026-08-24
- Status: design. No code changed by this document. It closes the two gaps that make
  [`stage-5-app-loader-plan-2026-08-24.md`](stage-5-app-loader-plan-2026-08-24.md) BLOCKED at its steps 5 to 9.
- Sits under: `app-separation-and-github-2026-08-20.md` section 2 (the mechanism), `stage-5-app-loader-plan-2026-08-24.md` (whose steps 1 to 4 stand as written), `stage-4b-kit-promotions-2026-08-24.md` (the CSS remainder), `packages/ui/CONTRIBUTING.md` (the versioning rules this makes enforceable for the first time).
- **Supersedes** stage 5 section 6 (versioning), stage 5 section 8 item 1 (the distribution question), and the per-app ordering in stage 5 section 7 rows 5 to 9. Everything else in stage 5 is unchanged.

Everything labelled "measured" was run against the working tree on 2026-08-24 and the command is given.
Everything labelled "inferred" is reasoning that was not executed, with the way to settle it.

---

## 1. Measurements

### 1.1 The `kit` field is dead, and its keeper procedure has never been followed

Searching `backend/`, `frontend/src/`, `packages/` and `scripts/` for the field rather than the word, every
occurrence in the tree:

| site | what it does | is it a reader? |
|---|---|---|
| `backend/services/manifests.py:27` | `DEFAULT_KIT = "@workshop/ui@^0.2"` | no, a default |
| `backend/services/manifests.py:95` | `"kit": str(raw.get("kit") or DEFAULT_KIT)` in `_normalize_entry` | no, a writer |
| `backend/routes/base_apps.py:77` | `kit: str` on the `AppManifest` response model | no, a passthrough |
| `frontend/src/api/schema.gen.ts:9290` | `kit: string` in the generated types | no, generated |

**Zero readers.** Nothing anywhere branches on the value, compares it, or displays it. The frontend does not
reference `.kit` at a single site outside the generated schema.

The two writers, measured:

- `ensure_identity_manifests` (`backend/services/app_registry.py:912`) builds its entry from six keys
  (`title`, `sensitivity`, `api_prefixes`, `provides`, `needs`, `tools`) and **never sets `kit`**.
- `upsert_manifest` writes whatever it is handed, which is that six-key dict.

So an entry's `kit` is either the normalizer's default or a hand edit that predates the seeding path. On disk
right now (`.runtime/apps.json`, 8 apps):

```
finance   '@workshop/ui@^0.2'      health     None
budget    '@workshop/ui@^0.2'      wearables  None
birds     '@workshop/ui@^0.2'      notes-app  '@workshop/ui@^0.2'
location  '@workshop/ui@^0.2'
commute   '@workshop/ui@^0.2'
```

Two apps have no `kit` key at all and nothing has ever noticed. That is the proof of deadness, not the stale
value.

**The stale value is worse than stage 5 says.** `packages/ui/package.json` is at **0.6.2**, and
`packages/ui/CONTRIBUTING.md` is explicit that this package is 0.x and **the minor is the breaking position**.
So `^0.2` is four breaking releases behind (0.3, 0.4, 0.5, 0.6). CONTRIBUTING's breaking-change procedure,
step 2, says to bump "the `kit:` pin in `backend/apps.json` plus `DEFAULT_KIT` in
`backend/services/manifests.py`". That step has been skipped every time, and `backend/apps.json` does not even
exist any more (the file moved to `.runtime/apps.json` in the Layer 0 eviction). A written procedure that
names a moved file and a field nothing reads is not a procedure, it is a comment.

### 1.2 The manifest the loader actually reads

`backend/services/app_runner.py:91` and `:177`:

```python
MANIFEST_NAMES = ("workshop.app.json", "workshop.app.jsonc")

def _read_manifest_file(project_dir: Path) -> dict | None: ...
```

One function, jsonc-comment tolerant, raises `AppError(status=400)` on malformed JSON, returns `None` when no
manifest exists. `services/app_files.py` already reuses it rather than reimplementing it. There is no
connection of any kind between this file and `apps.json`: `parse_manifest` reads `name`, `services`, `deps`
and `env_requirements`, and nothing in `manifests.py` ever opens a `workshop.app.json`. The stage 5 premise
("reuse the `kit` field") therefore proposes to join two files that share no code path.

### 1.3 Is the kit publishable at all? Yes, with one defect.

`packages/ui/package.json`, measured: **no `"private": true`**, no `publishConfig`, `"license": "MIT"`,
`"files": ["dist", "CHANGELOG.md", "README.md"]`, version `0.6.2`.

`cd packages/ui && npm pack --dry-run` succeeds:

```
name: @workshop/ui   version: 0.6.2   filename: workshop-ui-0.6.2.tgz
package size: 281.1 kB   unpacked: 1.0 MB   total files: 133
includes dist/index.js (71.1 kB), dist/Map.js, dist/Markdown.js, dist/Editor.js,
         dist/tokens.js, dist/tokens.css (58.3 kB), dist/styles.css (201.2 kB),
         dist/shared/cx-*.js, and the .d.ts tree
```

Every path in the tarball is inside the package. `packages/ui/vite.config.ts` externalizes react, react-dom,
lucide, maplibre, pmtiles, protomaps, react-markdown, remark-gfm and the whole CodeMirror and Lezer sets, so
the artifact carries the kit's own code and nothing else. `npm view @workshop/ui version` answers **404**, so
the name is unpublished. Whether the `@workshop` **scope** is owned by another org could not be checked from
this host: the egress guard blocks a shell network utility from reaching the public registry, and a 404 on the
package says nothing about the org. That is operator decision 1 in section 6.

**The defect.** The `exports` map carries a `workshop-source` condition on all seven subpaths, pointing at
`./src/index.ts`, `./src/Map.tsx`, `./src/styles/tokens.css` and so on. `src/` is **not** in `files`, so those
targets do not exist in a packed tarball. Exactly one consumer sets that condition, measured:
`frontend/vite.config.ts:166`, `conditions: ["workshop-source"]`. The shell therefore resolves the kit as
TypeScript source and compiles it in, which is right in the monorepo and would break instantly against a
packed kit. An app build that does not set the condition is unaffected. The rule this produces is section 4.4.

CONTRIBUTING already documents the publish command and records that a previous `exports` map pointed
`./tokens.css` at `../styles/tokens.css`, "a path that would simply not exist in a published tarball". The
`workshop-source` condition is the same bug class, still present.

### 1.4 Workspaces, and what a "local file path" actually buys

Root `package.json`: `"workspaces": ["packages/*", "frontend"]`, `"private": true`, plus an `overrides` block
pinning `react` and `react-dom` to `^18.3.1` for the whole workspace, with a comment recording a downstream
instance that got a hoisted React 19 against frontend's 18. `mobile/` is **not** a workspace. So today
`@workshop/ui` resolves by package name through a workspace symlink, which is a link into
`/workspace/packages/ui`, which is the monorepo. None of that survives the app leaving the tree.

### 1.5 The first-app candidates, measured

`frontend/src/apps/registry.tsx:169-177`, the whole health entry:

```tsx
{
  id: "health",
  Settings: HealthSettings,
  crumbs: {},
  routes: () => null,
},
```

`frontend/src/pages/apps/health/HealthSettings.tsx`: **136 lines, one file**, three imports (`react`,
`lucide-react`, `@workshop/ui`), **no stylesheet of its own**, and exactly **one** legacy class use,
`className="muted"`, once.

For contrast, `notes-app` (stage 5's step 5) has 5 tracked frontend files and, per stage 4b section 0, its
layout CSS currently lives **inside the kit**: a 92-line block of 33 `.ws-note-*` selectors over 13 class names
in `packages/ui/src/styles/components.css` that no kit component renders.

### 1.6 Four facts the stage 5 and 4b plans state that the tree no longer supports

- **The app-name ratchet is green.** `python3 scripts/check_no_app_names.py --json` returns
  `content_files 102, import_sites 20, literal_sites 302`, `scripts/no_app_names_baseline.json` pins 302, and
  the script exits 0. Stage 5 section 1 says the gate fails at 305 and makes its **step 0** a prerequisite for
  every other commit. That work has landed. Step 0 is done; do not redo it.
- **`build-info.json` does not carry versions.** Stage 5 section 6 says `GET /base-apps/runtime` reads
  contract, kit, react and router "from the built `frontend/dist/build-info.json`, which
  `scripts/stamp_build.sh` already writes". The actual file is
  `{source_id, commit, commit_short, branch, dirty, built_at}`. There are no version fields and
  `stamp_build.sh` writes none. Without a change to the stamp, that endpoint would have to read
  `node_modules`, which is exactly what the plan says it is avoiding.
- **`.muted` is three declarations, not one.** `frontend/src/styles.css:52`:
  `.muted { color: var(--muted); font-size: 0.875rem; margin: 0.25rem 0; }`. A call-site replacement that only
  sets a color silently drops a font size and a margin at 222 sites.
- **The token stage 4b names does not exist.** Stage 4b commit D recommends `color: var(--text-muted)`.
  `--text-muted` appears nowhere under `packages/ui/src/styles/`. The real kit token is `--muted`, defined
  across five themes in `packages/ui/src/styles/palettes.css`.

---

## 2. Where an app declares its kit dependency

### 2.1 Delete `kit` from `manifests.py`. Do not reuse it.

The field goes entirely: `DEFAULT_KIT`, the `"kit"` line in `_normalize_entry`, the `kit: str` field on
`AppManifest` in `backend/routes/base_apps.py`, and the regenerated `schema.gen.ts`. Nothing reads it, so the
deletion is observable only in the response shape and no consumer of that shape touches the key (section 1.1).
CONTRIBUTING's breaking-change step 2 is rewritten in the same change, because it currently instructs an
editor to update a field in a file that no longer exists.

Three reasons it cannot be repaired in place, in order of weight:

1. **It is instance state, and the version an app was compiled against is not.** `apps.json` records what
   *this installation* believes about an app: the operator's `agent_button` toggle, its sensitivity, its
   capabilities. It is hand-editable by design and its reader is deliberately tolerant. A kit range is a
   property of a **built artifact**. An operator editing `apps.json` cannot change what a bundle was compiled
   against, so a gate reading `apps.json` gates a claim rather than a fact.
2. **`_normalize_entry`'s tolerance is exactly wrong here.** Its stated contract is that a malformed entry
   costs itself and nothing else, so a missing `kit` becomes `DEFAULT_KIT`. A compatibility gate needs the
   opposite: a missing declaration must refuse, because the alternative is a bundle of unknown vintage being
   told it is fine. Reusing the field means either changing that function's contract for one key, or keeping
   a gate that passes by default, which is the "looks enforced, is not" failure the audit named.
3. **It has no path to the loader.** The loader reads `workshop.app.json` through
   `app_runner._read_manifest_file` (section 1.2). Joining `apps.json` to it adds a second manifest reader,
   which stage 5's own section 2.1 forbids.

### 2.2 What replaces it: `ui.kit` in `workshop.app.json`, one reader

The app declares its kit range in the `ui` block of its own manifest, beside `contract`, exactly as stage 5's
section 2.1 sketch shows:

```jsonc
"ui": { "contract": 1, "kit": "^0.6", "dir": "dist/ui", "entry": "app.js", "styles": ["app.css"] }
```

- **Authored in exactly one file**: `workshop.app.json`, which travels with the app repo.
- **Read by exactly one function**: the `ui`-block normalizer in the new `backend/services/app_ui.py`
  (stage 5 step 3), reached through `app_runner._read_manifest_file`. No second reader, in any layer.
- **Absent is a refusal, not a default.** An app with no `ui.kit` is returned with `ui.error` set and does not
  activate. There is no `DEFAULT_KIT` anywhere in the replacement.

### 2.3 The grammar is caret-only, on purpose

`ui.kit` accepts `^MAJOR.MINOR` or `^MAJOR.MINOR.PATCH` and nothing else. Any other string is refused at the
app's own build (section 3.1) with a message naming the accepted forms.

Two reasons, and the second is the one that matters:

- It is the only form CONTRIBUTING tells a consumer to use ("Consumers pin `^0.2`"). Accepting `>=`, `~`,
  `||` and `x` ranges means implementing npm's range algebra twice, once in Python and once in TypeScript,
  which is a drift generator for a value that only ever has one shape.
- **The 0.x rule is not a special case that can be bolted on later.** npm's caret at a zero major means `^0.6`
  is `>=0.6.0 <0.7.0`, so the minor is the breaking position, which CONTRIBUTING states as this package's
  whole versioning model. Stage 5 section 6 describes the gate as major against major ("Major. Every app whose
  declared range excludes the new major stops activating"), and at 0.x that is simply the wrong comparison: it
  would let an app built against `^0.6` activate against `0.9.0`, across three breaking releases. The
  evaluator implements caret semantics including the zero-major rule, and it must not be written as a
  major-number comparison. When the kit reaches 1.0 the same function keeps working unchanged, which is the
  other reason to implement the rule rather than the shortcut.

The evaluator is one pure function in a new `backend/services/kit_range.py`, with its own unit tests, and the
0.x cases are the tests that matter: `^0.6` satisfied by `0.6.0` and `0.6.9`, refused by `0.7.0` and `0.5.9`;
`^1.2` satisfied by `1.9.0`, refused by `2.0.0`.

### 2.4 There is still only one authored value

`packages/app-build`'s preset performs two build-time refusals that keep the app repo from growing a second
copy of this number (section 3.1): the **resolved** installed `@workshop/ui` version must satisfy `ui.kit`,
and the app's `package.json` range for `@workshop/ui`, if it names one, must be string-equal to `ui.kit`.
Neither is a second declaration. `ui.kit` is authored; everything else is checked against it and the build
fails on divergence, which is what makes "one place" true rather than aspirational.

One thing that deliberately differs from `ui.kit` and is not a duplicate of it: the app's **installed** kit
specifier, which under section 4 is an exact tarball rather than a range. These answer different questions.
`ui.kit` is "what shell am I compatible with", a range, evaluated against a shell the app has never seen. The
dependency is "what bytes did this build use", exact, so `npm ci` is reproducible. The preset asserts the
second satisfies the first, so they cannot disagree.

---

## 3. The compatibility contract

Two numbers, and both are needed. Stage 5 asks whether folding React's major into `contract` is enough. It is
not, for the three reasons in 3.3, and the plan's rule for `contract` is also too narrow.

### 3.1 What is checked, when, and what happens on a mismatch

| when | who | what is checked | on mismatch |
|---|---|---|---|
| **Build time**, in the app's repo, `defineWorkshopApp`'s `closeBundle` | `@workshop/app-build` | `ui.kit` parses as a caret range; the resolved `@workshop/ui` version satisfies it; `package.json`'s range, if present, equals it; `ui.contract` equals the contract this preset implements; the emitted bundle's default export carries `contract` and `kitVersion` | **the app's build fails**, in the app's repo, before an artifact exists. No platform involvement |
| **Install time**, platform side, `services/app_ui.py` and `scripts/check_app_loader.py --app` | the platform | the same assertions, re-run from outside on the built artifact (an app built on another machine never ran the preset here), plus `ui.kit` evaluated against **this shell's** kit version | the app is returned with `ui.error` set. Its declaration still renders: nav, breadcrumb, launcher card, icon, switcher entry. Its routes render the refusal, naming the declared range and the shell's version |
| **Load time, before any fetch**, `frontend/src/apps/loader.ts` | the shell | `ui.compat`, a boolean plus reason computed at install time and carried in the contributions payload | `{ok: false, reason: "kit"}`, rendered by `AppRouteHost` as a `StateBlock`. **No bundle is fetched** |
| **Load time, after import**, `loader.ts` | the shell | `bundle.contract` equals `ui.contract`; `bundle.kitVersion` satisfies `ui.kit` | `{ok: false, reason: "manifest-lies"}`. This is the anti-lying check: the manifest is hand-editable, the bundle is not |

`loader.ts` reads a verdict, it does not compute one. The range evaluator exists once, in Python, in
`kit_range.py`, and the contributions payload carries `ui.compat = {ok, reason, needs, has}`. That is the
difference between one implementation and two.

### 3.2 How a shell kit bump reaches apps already installed

It reaches them by **the shell changing and the app not**. Nothing is pushed and no app is touched.

- **Patch or minor inside the range** (0.6.2 to 0.6.3): every declared `^0.6` still satisfies, so nothing
  happens. That is correct, because CONTRIBUTING makes a patch additive-only at 0.x.
- **Breaking bump** (0.6.x to 0.7.0): the shell rebuilds, `build-info.json` records `kit: "0.7.0"`, the next
  read of `/api/base-apps/contributions` re-evaluates every installed app's `ui.kit` against it, and every app
  declaring `^0.6` flips to `ui.compat.ok = false`. The user sees each affected app still listed and still
  navigable, with one sentence naming the range it declared, the version now served, and the CHANGELOG
  migration note. The app is repaired by rebuilding against the new kit and widening its range, in its own
  repo, on its own schedule.

Two consequences worth stating rather than discovering:

1. **A kit breaking release is a fleet-wide event.** That is the price of externalizing the kit, and it is the
   same price that makes "the Design System is mandatory" enforceable across code the platform does not
   compile: an app physically cannot ship a divergent kit. The gate makes the event visible instead of
   producing a screen of undefined components.
2. **The refusal is the first enforcement CONTRIBUTING has ever had.** Its own text says the version number
   and the changelog "are the only warning they ever get", and it records the `Menu` incident where a breaking
   change shipped with no bump because no out-of-repo consumer existed to break. The loader creates the
   consumer.

### 3.3 Is `contract` alone sufficient? No, and the plan's rule for it is too narrow

Keep both. Three reasons, each checkable:

1. **Different clocks.** `contract` describes the shape in stage 5 section 2.2: the default export, `exports`
   as a record of prop-less components, the closed mount vocabulary, the `AppHost` context. That changes
   approximately never. The kit is at 0.6.2 with four breaking releases behind it and a fifth (stage 4b
   commit B) already designed. Folding the kit into `contract` means bumping `contract` on every kit breaking
   release, which refuses **every** app for a change most of them are unaffected by, and destroys the
   integer's meaning as a shape version.
2. **An integer cannot express a range.** `contract` is an equality test. The kit needs `>=0.6.0 <0.7.0`,
   because an app built against 0.6.0 must keep working against 0.6.9. There is no encoding of that in one
   integer that is not a version number with extra steps.
3. **They gate different things.** `contract` gates the shape and the **shared runtime**; `ui.kit` gates the
   **library**. React is the clearest case: it is externalized, so an app compiled against 18 silently
   receives whatever the shell serves, and React's major is not expressible as a kit version. Stage 5 is right
   that a React major must bump `contract`.

**The plan's rule is too narrow, and this is a real gap.** It says "bumping React majors bumps `contract`".
React is not the only externalized runtime. `SHARED` is `react`, `react-dom`, `react-dom/client`,
`react/jsx-runtime`, `react/jsx-dev-runtime`, `react-router-dom`, `lucide-react`, `recharts`, and the four
`@workshop/ui` entries. `react-router-dom` has exactly React's property: app pages import it 25 times
(measured in stage 5 section 1), it is handed to the app by the import map, and a v6 to v7 move would change
hook and component behaviour underneath a bundle that cannot see it. `lucide-react` and `recharts` are the
same shape at lower stakes.

So the rule is: **a major bump of any member of `SHARED` other than `@workshop/ui` bumps `contract`.**
`@workshop/ui` is the exception because it has its own range and does not need the blunt instrument.
`GET /base-apps/runtime` already returns `react` and `router`, so the data to check this is in the design;
what is missing is that nothing writes it (section 1.6), which is why `stamp_build.sh` has to record the
resolved versions at build time instead of the endpoint reading `node_modules`.

---

## 4. Distribution: how an out-of-repo app gets its build dependencies

### 4.1 The options, against the four criteria

| option | stranger's clone builds | on a kit bump | operating cost | works with no network |
|---|---|---|---|---|
| **A. publish to public npm** | yes, one install | one string, standard tooling | needs a scope the operator owns (`@workshop/ui` is 404 but scope ownership is unverified, section 1.3), a publish step in every kit release, a token and 2FA, and it makes a personal design system public | **no**, unless the local cache happens to be warm |
| **B. vendor the kit into each app repo** | yes | N copies, and nothing tells you which app has which | low per copy, unbounded in aggregate | yes |
| **C. git dependency** | only if the kit has its own reachable repo, which it does not: it lives inside this monorepo | one ref, but a git dep installs **source**, so the consumer runs the kit's `prepare` and needs vite and tsc at install time | splitting `packages/ui` into a second repo with its own release process | only with a local path |
| **D. local file path into the monorepo** | **no**. This is the status quo (workspaces plus a symlink, section 1.4) | free | zero | yes |
| **E. the platform hands out the packed tarball** | yes, after one bootstrap command | one string, and the platform emits the tarball as part of the kit's release | one pack step in the release flow, one directory, one script | **yes** |

**B is rejected outright**, not on cost. The single largest thing externalizing the kit buys is that an app
*cannot* ship a divergent kit, which is what makes the Design System enforceable across code the platform does
not compile. Vendoring is the mechanism for shipping a divergent kit. It undoes the point of the stage.

**C is rejected** because the prerequisite (split `packages/ui` into its own repo) is larger than the problem,
and because a git dependency delivers source, so every app repo needs the kit's full build toolchain to
install. That is a heavier install than the one it replaces.

**D is not an answer**, it is the current state and the thing being fixed. It stays available as the
in-monorepo development mode, and that is all.

### 4.2 The decision: E, the platform is the channel, and it hands out versioned tarballs

`npm pack` already produces the right artifact and it is already self-contained (measured, section 1.3:
281.1 kB, 133 files, every path inside the package, `dist/` only). So the release step is a copy, not a build:

```
packages/ui        -> build + pack -> workshop-ui-0.6.2.tgz
packages/app-build -> build + pack -> workshop-app-build-0.1.0.tgz
                                  -> $WORKSHOP_KIT_CHANNEL/   (default: .runtime/kit-channel/)
```

An app repo depends on the exact tarball, in `devDependencies`, because under the loader the kit is
externalized and the app needs it only for types and for the preset:

```json
"devDependencies": {
  "@workshop/ui":        "file:.workshop/kit/workshop-ui-0.6.2.tgz",
  "@workshop/app-build": "file:.workshop/kit/workshop-app-build-0.1.0.tgz"
}
```

`.workshop/kit/` is gitignored in the app repo and populated by one command before the install:

```
bash scripts/kit_channel.sh sync <app-dir>                    # copies from $WORKSHOP_KIT_CHANNEL
bash scripts/kit_channel.sh sync <app-dir> --from <origin>    # or fetches from a running platform
```

If the remote form is ever wanted, the platform serves the same bytes beside the asset route stage 5
section 2.3 already defines:

```
GET /api/base-apps/kit                      -> { packages: [ {name, version, url, sha256}, ... ] }
GET /api/base-apps/kit/{name}/{version}.tgz -> the tarball, immutable, content type application/gzip
```

with the same resolve-then-compare containment check as `/base-apps/ui/{slug}/{path}` and the same
`require_operator` inherited from the `/base-apps` mount. That half is operator decision 2 and is not needed
for anything in section 5.

### 4.3 The operational cost, stated plainly

- **A clone of an app repo on a machine with no Workshop cannot install until one command has run.** That is
  the whole cost and it is not worth dressing up. The dependency is a path, so an install on a naked clone
  fails with a missing file rather than a helpful message, and the app repo's README has to carry
  `bash .workshop/bootstrap.sh` on the line above the install command. For this system, where "another
  machine" means the operator's other box or a build agent on the same box, that is one line. For an actual
  stranger it is a real barrier, and if that ever matters, operator decision 1 is the upgrade.
- **A kit release grows two commands**: pack twice and copy into the channel. Wire it into the kit's release
  checklist in CONTRIBUTING, not into `scripts/apply.sh`, which has one job.
- **The channel directory grows one tarball per kit release**, roughly 280 kB each. Ten releases is 3 MB. It
  lives under `.runtime/`, which is already untracked and instance-local. No pruning policy is needed at this
  scale and inventing one would be the multi-tenant reflex this repo keeps correcting.
- **The remote form needs an auth answer it may not have.** npm does not send a session cookie, and whether
  npm applies a configured `_authToken` to a bare tarball URL that is not a registry endpoint is **inferred,
  not measured**. **The design does not depend on the answer**: the `file:` form is the default, needs no auth,
  and `kit_channel.sh --from` can fetch with the operator's session and write the file, which sidesteps npm's
  auth entirely.
- **Reproducibility is better than a range, not worse.** The specifier names one version, npm records the
  tarball's integrity hash in the app's lockfile, and two builds of the same commit resolve the same bytes.

### 4.4 What this forecloses: nothing

Choosing E does not rule out A. The tarball **is** the npm artifact: a publish uploads exactly the file the
pack produced. Migrating later changes two strings in each app's `package.json` and deletes the bootstrap
line. That is the reason to take the option with the lowest operating cost now instead of paying for a
publishing pipeline before a second consumer exists.

Three things the packing path requires, all measured problems rather than speculative ones:

1. **The `workshop-source` condition must not reach a tarball consumer.** It points at `src/`, which `files`
   excludes (section 1.3). `defineWorkshopApp` must never set `resolve.conditions` to include it, and
   `check_app_loader.py --app` asserts the app's vite config does not. The alternative (adding `src/` to
   `files`) triples the tarball to ship TypeScript to a consumer that externalizes the package anyway.
2. **The app must not import the kit's stylesheets.** The tarball's two largest files, `dist/styles.css`
   (201.2 kB) and `dist/tokens.css` (58.3 kB), are exactly the two an app is forbidden to import, because the
   shell already loads both and a second copy shadows the tokens. Stage 5's preset already refuses this; the
   refusal message needs to say why, because "you installed a package and may not use its biggest file" is
   otherwise baffling.
3. **`@workshop/app-build` needs the same treatment as the kit**, including a `files` allowlist and a pack
   dry-run check, or the preset ships its own test fixtures to every app.

---

## 5. The migration path for the first real app

### 5.1 Extract `health` first, not `notes-app`

Stage 5 orders steps 5 to 9 by file count and puts `notes-app` first. Order by **CSS debt** instead, because
CSS is the measured remaining coupling (stage 4b section 0) and it is the one thing no gate catches. On that
ordering `notes-app` is not the cheapest, it is a trap.

**Why not notes-app.** Its cleanliness is borrowed. 92 lines and 33 `.ws-note-*` selectors of its layout live
in `packages/ui/src/styles/components.css`, rendered by no kit component (section 1.5; stage 4b's trap 1 calls
this out as the exact failure mode, an app made to "import nothing outside the kit" by pushing its concerns
into the kit). Extracting it first means either shipping an app that permanently depends on one app's CSS
living in the platform kit, or making stage 4b commit B (a **breaking** kit release, 0.6.2 to 0.7.0) a
prerequisite. Neither belongs on the first attempt, and the second would exercise a brand-new version gate
with a real breaking release before the gate had ever run once.

**Why health.** Measured in section 1.5: one file, 136 lines, three imports all of which are in `SHARED`, no
stylesheet, no route, no provider, no group, no status endpoint, and one legacy class use. It moves
`content_files` 102 to 101 and `literal_sites` 302 to 300. It exercises the `app.settings` mount point, which
is the one mount the fixture app is least likely to have exercised well, and its blast radius is one tab of
the Settings screen. If it fails, nothing a user navigates to is affected.

The objection is fair and worth answering: health does not exercise `app.route`, and it emits **zero**
stylesheets, so it does not exercise the CSS link path. That is deliberate. The fixture app (`app-widgets`,
stage 5 step 2) already carries a `.wsfix-probe { --wsfix-loaded: 1 }` rule specifically to prove the CSS path
under `--built`. Health's job is to prove the *real-app* path with CSS held at zero, so that if the first real
extraction fails, trap B is excluded by construction and the cause is somewhere else. `notes-app` is second,
and it is the first app that carries a real stylesheet.

### 5.2 The order

| # | app | files | literals | why here |
|---|---|---|---|---|
| 5 | **health** | 1 | 2 | zero CSS, one mount point, one tab of blast radius |
| 6 | **notes-app** | 5 | 1 | first routed app, first real stylesheet. Gated on stage 4b commit B |
| 7 | **location**, then **commute** | 2, 4 | 5, 4 | first `group` pair, first `status` endpoint. Legacy debt is `.muted`, `.card`, `.err` and `.table-scroll` only |
| 8 | **finance** | 3 | 6 | completes the `money` group |
| 9 | **budget** | 3 | 4 | 16 budget-only legacy classes into an existing `budget.css` that is already imported |
| 10 | **birds** | 44 | 32 | last, largest, the only session provider, the only `import_site`, and the one with the residual stage 4b import list |

budget moves ahead of birds and behind finance: it has the deepest legacy-CSS debt of the small apps
(16 classes), but `frontend/src/pages/apps/budget/budget.css` already exists and is already imported by
`Budget.tsx:2`, so commit D for budget is a cut and paste into a wired-up file rather than a new seam.

### 5.3 The steps for health, concretely

1. `mkdir -p apps/health` and write `apps/health/workshop.app.json` with a `ui` block:
   `{contract: 1, kit: "^0.6", dir: "dist/ui", entry: "app.js", styles: [], settings: [{export: "SettingsPanel", mount: "app.settings", label: "Health"}]}`.
   `paths.scope_root("app:health")` already resolves to `workshop_root()/apps/health` with no registry change;
   `apps/` is gitignored and `git ls-files apps/ | wc -l` is 0, so this puts nothing back in the repo.
2. `bash scripts/kit_channel.sh sync apps/health`, then install in `apps/health`.
3. Move `frontend/src/pages/apps/health/HealthSettings.tsx` to `apps/health/src/SettingsPanel.tsx`. Its three
   imports are unchanged: all three specifiers are in `SHARED` and resolve through the import map at runtime
   and through the installed tarball at build time.
4. **Replace the one `className="muted"`.** Not with a color alone: the rule is
   `color: var(--muted); font-size: 0.875rem; margin: 0.25rem 0`. Use the kit primitive if one covers it,
   otherwise inline all three declarations against kit tokens. Do **not** copy `.muted` into an app
   stylesheet, because health's whole value as the first migration is that it emits no CSS.
5. Add `apps/health/src/index.ts` exporting `{contract: 1, kitVersion: "0.6.2", exports: {SettingsPanel}}` and
   `apps/health/vite.config.js` calling `defineWorkshopApp({outDir: "dist/ui"})`. Build. The preset refuses if
   `ui.kit` and the resolved kit disagree, or if any CSS was emitted against `styles: []`.
6. `git rm --cached frontend/src/pages/apps/health/HealthSettings.tsx`, delete the health entry and the
   `HealthSettings` lazyPage line from `frontend/src/apps/registry.tsx`.
7. `python3 scripts/check_no_app_names.py` (expect 101 / 20 / 300; re-pin the baseline **down** with a
   `--because`), `npm run typecheck`, `bash scripts/check_design.sh`, `cd frontend && npm test`,
   `python3 scripts/check_app_loader.py --app apps/health`, `bash scripts/check_app_loader.sh --built`, then
   `bash scripts/apply.sh`, then open the Settings screen on the built origin and read the screenshot.

### 5.4 How this interacts with the CSS finding

Three things, and the first is the one that gets missed.

1. **Distribution cannot deliver the legacy sheet, under any of the five options.** `frontend/src/styles.css`
   is platform source. It is not in the kit, so it is not in the tarball, not on a public registry, not in a
   git dep, not in a vendored copy. So "how does an app get its build dependencies" has **no bearing** on the
   22 legacy classes: the answer for them is always "it does not, ever". They move into the app's own
   stylesheet (stage 4b commit D, per app) or the call site changes. That makes commit D a hard per-app
   prerequisite for steps 6 through 10, not a cleanup that can trail behind.
2. **The failure is invisible until the app is served alone.** While an app still renders inside the shell,
   `main.tsx` has already loaded `styles.css` globally, so a migrated app that left its classes behind looks
   correct. It goes wrong the moment `styles.css` stops being served to it. This is the same shape as the
   2026-08-09 stale-bundle incident: correct in source, wrong on screen, no gate fires. `--built` is what
   proves it, per app, and an app's migration is not finished without it.
3. **Ordering is not a problem once each app has one sheet.** The preset forces `cssCodeSplit: false` and one
   `app.[ext]`, and the shell appends the app's `<link>` after the kit's tokens, the kit's components and
   `styles.css`, so an app's own `.note-layout` wins on a specificity tie without `!important`. Load order
   stays the shell's business, which is why stage 5 is right to refuse `vite-plugin-css-injected-by-js`.

Health is chosen partly because it makes interaction 1 a single call-site edit instead of a stylesheet move,
so the first migration tests the loader rather than testing stage 4b.

**Corroboration.** [`stage-7-extraction-plan-2026-08-24.md`](stage-7-extraction-plan-2026-08-24.md), written
independently on the same day, reaches the same ordering from the other end of the pipeline and for the same
reason: it puts health first and notes-app second, and its section on notes-app names "92 lines of its layout
CSS currently live in the platform kit" as the prerequisite. Two passes that did not see each other agreeing
on this is worth more than either argument alone, and it is the reason the stage 5 row order should be
corrected rather than left as a competing opinion.

---

## 6. Genuinely the operator's decision

Not filed as tickets. Items 1 and 2 are new; 3 to 5 carry over from stage 5 section 8 and stage 4b section 6
and are restated so the open set is in one place.

1. **Whether `@workshop/ui` is ever published to a public registry, and under what scope.** Measured: the
   package name answers 404, so it is unpublished, but whether the `@workshop` **scope** is owned by another
   org could not be checked from this host (the egress guard blocks a shell network utility from reaching the
   public registry; check it from your own terminal). If the scope is taken, publishing means renaming the
   package, which is 46-plus import sites plus every `exports` subpath. Section 4 is designed so this is an
   upgrade and not a prerequisite: nothing is blocked on the answer. `veritusagent` is out of scope as a host
   for any of this.
2. **Whether the kit channel is ever served over HTTP, or stays file-path only.** The `file:` form needs no
   auth and works offline, and it is the default. The HTTP route is designed but its npm auth story is
   inferred, not measured (section 4.3). Deciding "file only, for now" costs nothing and removes two routes
   from the build.
3. **Where app UI assets are served from** (stage 5 section 8 item 2): the backend at `/api/base-apps/ui/...`,
   or copied into `frontend/dist/apps/<slug>/` at install time. Unchanged recommendation: the backend, because
   it never fights `apply.sh`.
4. **Whether birds' data move happens with its frontend or separately** (stage 5 section 8 item 3):
   `apps/birds/birdnet.db` and `apps/birds/clips/` live inside the source tree.
5. **What happens to `frontend/vite.springer.config.ts`** (stage 5 section 8 item 4, stage 4b commit C): the
   standalone birds build names an app in platform source; delete it, move it into the app repo, or keep it
   and accept the violation.

---

## 7. Tickets filed

All seven filed against this scope with
`python3 scripts/project_tasks.py add . "<title>\n<body>" --priority N --execution code --source agent`.

| id | requested | stored band | title |
|---|---|---|---|
| `4a9dbab20aff4fbe8d4b0e376a0567e0` | 82 | high | Evaluate a caret kit range in one pure backend module |
| `fbbc55a79d4d46e1bc3c41699a210085` | 80 | high | Delete the dead kit field from the app manifest store |
| `d36d4fb5d81a4e2c96ea51f7e56a374f` | 80 | high | Add packages/app-build with its build-time compatibility refusals |
| `1cb21f41010840979c2009b0469c8fa8` | 78 | medium | Record the shared runtime versions in the build stamp |
| `0a83ac019fd84aca89ebc46b4da54c73` | 76 | medium | Pack the kit into a platform-owned channel an out-of-repo app installs from |
| `b3f900d0c70f478683b9a981539a0aad` | 72 | medium | Move the health app's settings panel out of the frontend tree |
| `e5848c879b7d41878fa097d43075a756` | 70 | medium | Correct the two plan docs where the tree has moved past them |

**On the two bands.** Priority in this scope's store is a named band, not an integer
(`backend/db/legacy_map.py`: `PRIORITY_BANDS = ((100, "urgent"), (80, "high"), (40, "medium"), (1, "low"))`,
and ADR 0019 records that the integer held 38 distinct values across 112 tickets and none of the distinctions
were meant). So a requested 70 to 85 straddles the boundary at 80, and everything below it reads back as 40.
The split that resulted is the right one anyway: the three prerequisites that must land before anything else
are `high`, and the four that follow are `medium`. No ordering information was lost that the schema was
willing to keep.

**Ordering between them.** `4a9dbab2` (the range evaluator) and `fbbc55a7` (deleting the dead field) are
independent of everything and of each other. `d36d4fb5` (the preset) depends on nothing in this list but
belongs with stage 5 step 2. `1cb21f41` (the build stamp) is needed before any install-time check can compare
against a real shell version. `0a83ac01` (the channel) is needed before an app repo can install anything.
`b3f900d0` (extracting health) needs all of the above **plus** stage 5 steps 1 to 4, and its body says so and
tells the worker to block rather than build the loader. `e5848c87` (the doc corrections) can land at any time
and should land early, because two of the four things it corrects would otherwise send a worker to do work
that is already done.

---

## 8. Where this document infers rather than measures

Measured, with the command given inline: the `kit` field's writers, readers and on-disk values;
`MANIFEST_NAMES` and `_read_manifest_file`; the kit's package metadata, its pack dry-run output and the 404 on
the package name; the `workshop-source` condition and its single consumer; the root workspaces and overrides;
health's file, imports and single class use; notes-app's CSS block inside the kit; `build-info.json`'s actual
fields; `.muted`'s full rule and the absence of `--text-muted`; the ratchet's current green state at 302
against a pinned 302; the priority band table.

Inferred, each with the way to settle it:

- **That npm applies a configured `_authToken` to a bare tarball URL.** Section 4.3. Settle with one config
  set and one install against the running backend. The design defaults to `file:` and does not depend on it.
- **That the `@workshop` scope is unclaimed.** Not checkable from this host. Operator decision 1.
- **That removing health's one file leaves no dangling import.** `registry.tsx:72` is the only importer, so
  this is near-certain, but it was asserted rather than run. `npm run typecheck` in step 7 of section 5.3
  settles it.
- **That the shell's `<link>` ordering puts an app sheet last.** It follows from `loader.ts` appending to
  `<head>` after `main.tsx` has imported all three platform sheets, but it was not observed in a browser.
  `bash scripts/check_app_loader.sh --built` on notes-app (step 6) is the first run that would show it.
