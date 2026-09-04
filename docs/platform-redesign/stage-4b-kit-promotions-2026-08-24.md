# Stage 4b: what is left to promote, and what must not be

- Date: 2026-08-24
- Status: plan only. No code changed by this document.
- Supersedes the "Stage 4b" paragraph in
  [`HANDOFF-app-platform-and-github-2026-08-20.md`](HANDOFF-app-platform-and-github-2026-08-20.md) section 2.
- Context for why this keeps coming back:
  [`app-separation-why-it-recurs-2026-08-24.md`](app-separation-why-it-recurs-2026-08-24.md).

---

## 0. The headline, measured

**The import half of Stage 4b already shipped.** Commit `754facd9`
("frontend: promote app-facing platform-internal modules into @workshop/ui
(stage 4b)") did the work the handoff describes. Every module the handoff names
is either gone from platform source or no longer reached by any app page.

**Five import edges remain, three of them are one line each, and none of them is
a promotion candidate.** Measured, not estimated: see section 1.

**The real remaining coupling is not an import at all.** It is CSS. Twenty-two
class names used across roughly 320 sites in app pages are defined only in the
platform's legacy sheet `frontend/src/styles.css`. No import scan sees this,
`npm run build` will not fail on it, and `python3 scripts/check_no_app_names.py`
does not count it. Gitignore the app directories and the build stays green;
extract them at stage 7 and they render unstyled. That is the same failure shape
as the 2026-08-09 stale-bundle incident: correct in source, wrong on screen, and
invisible to every gate.

**And the promotion pressure has already produced one instance of exactly the
kit bloat this ticket is meant to prevent.** `packages/ui/src/styles/components.css`
carries a 92-line block headed `/* ---- NOTES app (pages/apps/notes) --- */`
with 33 `.ws-note-*` rule selectors, 13 distinct class names, that no kit component renders. The notes app,
the handoff's "existence proof" that an app can import nothing outside the kit,
is clean partly because its layout CSS was moved into the platform kit. That is
a regression against the constraint, not evidence for it, and it should come
back out.

**The thing actually blocking `git rm --cached frontend/src/pages/apps/<app>`
is `frontend/src/apps/registry.tsx`,** which names and imports sixteen app page
modules. That is stage 5 (the loader), not stage 4b. The handoff's sentence
"until this is done, gitignoring `frontend/src/pages/apps/` breaks a fresh
clone's build" is true, but the cause it attributes it to is no longer the
cause.

---

## 1. Measured inventory

### Method

Enumerated every import specifier (`import ... from`, bare `import "..."`, and
dynamic `import("...")`) in every `.ts` / `.tsx` / `.css` file under
`frontend/src/pages/apps/`, resolved each relative specifier against its
importer, and kept the ones that land outside the importing app's own directory
and outside `@workshop/ui` (any subpath). React, react-dom, react-router-dom,
lucide-react, recharts and vitest are excluded as ordinary npm dependencies and
handled separately in section 5.

**Scope note, and it matters.** "App source" here is exactly what
`scripts/check_no_app_names.py` counts as `content`:
`frontend/src/pages/apps/<app>/**` plus the sibling entry files
`Birds.tsx`, `Budget.tsx`, `Commute.tsx`, `Finance.tsx`, `Location.tsx`.
That is 102 files and the per-app split reproduces the gate's number exactly
(birds 48, commute 21, finance 9, notes-app 9, budget 7, location 5, health 3).

`frontend/src/pages/apps/tabs/` is **not** app source. It is the platform's App
entity detail tabs (Chats, Agents, Memory, Files, Tickets, Runs, Deploys), it is
routed from `App.tsx`, and it legitimately imports `lib/apps`, `lib/tasks`,
`lib/jobs`, `lib/holders`, `components/shell/*` and `components/holders/*`.
Anyone re-running this measurement over the whole of `pages/apps/` will find
about a dozen extra edges that are not violations. They are excluded here on
purpose.

### The table

| # | Module reached | Resolves to | Uses | Apps | Kind |
|---|---|---|---|---|---|
| 1 | `@shared/native-bridge` | `shared/native-bridge.ts` | 1 | commute | type-only |
| 2 | `../../api/schema.gen` | `frontend/src/api/schema.gen.ts` | 1 | location | type-only |
| 3 | `../../../../styles.css` | `frontend/src/styles.css` | 1 | birds | CSS side-effect |
| 4 | `../../Birds` | `frontend/src/pages/apps/Birds.tsx` | 1 | birds | intra-app, wrong level |
| 5 | `../Budget` | `frontend/src/pages/apps/Budget.tsx` | 1 | budget | intra-app, wrong level |

Exact sites:

1. `frontend/src/pages/apps/<slug>/useCommuteNative.ts:9`
   `import type { CommuteActivityState, ActivityStatus } from "@shared/native-bridge";`
2. `frontend/src/pages/apps/Location.tsx:21`
   `import type { components } from "../../api/schema.gen";`
3. `frontend/src/pages/apps/<slug>/standalone/main.tsx:11`
   `import "../../../../styles.css";`
4. `frontend/src/pages/apps/<slug>/standalone/main.tsx:16`
   `import Birds from "../../Birds";`
5. `frontend/src/pages/apps/<slug>/Projection.tsx` importing `../Budget`

Two additional hits my first regex pass produced were false positives, and I am
recording them so the next person does not chase them: the strings
`"Safari on iOS"` (in `birds/Devices.tsx`) and `"went quiet"` (in `birds/api.ts`)
sit inside prose that the `from "..."` pattern matched. Neither is an import.

**No cross-app frontend import exists.** Nothing in `birds/` reaches into
`commute/`, and so on for all 21 ordered pairs. That is worth stating because
the backend had two such edges (budget to finance, commute to location) that
stage 4 had to convert to call-time.

### How this differs from the handoff

The handoff says: *"Promote the 11 platform-internal modules app pages reach for
(`lib/usePoll` 18 uses, `lib/time`, `components/appNav`, `components/AppSettings`,
`lib/storage`, `components/AppChatDock`)."*

Measured today, **zero of the six named modules is reached by any app page**,
and the count of remaining edges is five, not eleven. Per named module:

| Handoff's module | State on disk today | Reached by an app page? |
|---|---|---|
| `frontend/src/lib/usePoll` | **Deleted.** Now `packages/ui/src/usePoll.ts`, exported as `usePoll`, `pollShouldRun`, `WhenHidden`. | No. All 18 sites import `@workshop/ui`. |
| `frontend/src/lib/time` | **Deleted.** Now `packages/ui/src/time.ts`. | No. |
| `frontend/src/components/AppSettings` | **Deleted.** Now `packages/ui/src/AppSettings.tsx`. | No. |
| `frontend/src/components/appNav.tsx` | **Still exists**, platform-only. | No. |
| `frontend/src/components/AppChatDock.tsx` | **Still exists**, platform-only. Only the *types* `AppChatDockRenderer` / `AppChatDockOptions` went into the kit; the component is injected into an app page as a render prop by `apps/registry.tsx`. | No. |
| `frontend/src/lib/storage.ts` | **Still exists**, reduced to Workshop's own instantiation over the kit engine (`packages/ui/src/storage.ts`). | No. |

The handoff also says "11 modules" while naming six. I could not reconstruct
which five it meant; the other five are presumably `lib/bytes`, `lib/httpError`,
`lib/breadcrumbs`, `native/bridge` and `native/location`, all five of which are
in the kit's `index.ts` under the same 2026-08-20 comment block. Treat the
number 11 as satisfied.

**Conclusion: the handoff's list is stale by one commit and should be struck.**
Section 3 of this document is the replacement work.

---

## 2. Classification

The instruction is PROMOTE / MOVE INTO THE APP / INLINE-DELETE, and the point of
the exercise is that promoting the wrong thing puts one operator's app logic
permanently into a package every future installation compiles against.

### 1. `@shared/native-bridge` types, from `commute/useCommuteNative.ts`

**PROMOTE (already 95% done; finish the export).** The two types it wants,
`ActivityStatus` and `CommuteActivityState`, already exist verbatim in
`packages/ui/src/native/types.ts` (lines 95 and 97). They are simply not
re-exported from `packages/ui/src/index.ts`, so the app had nowhere to import
them from and reached back to `shared/`. This is a barrel omission, not a
missing capability.

**Sub-finding, and it is the interesting one:** the kit's own type is named
`CommuteActivityState`. **The kit names an app.** `scripts/check_no_app_names.py`
does not catch it because its literal patterns only match quoted strings and
path shapes, never an identifier, so `CommuteActivityState` is invisible to the
gate that exists to prevent exactly this. Rename it on the way out (section 3).

### 2. `frontend/src/api/schema.gen.ts`, from `Location.tsx`

**LEAVE AS IS. Already decided, already documented, do not reopen.**
`Location.tsx:12-20` carries an eight-line comment from the 754facd9 worker
explaining that this is the one deliberately-kept cross-boundary import: the
type is a wire contract with the platform's own backend, not platform UI
behaviour, and hand-writing a duplicate is the documented worse option
(`frontend/CLAUDE.md` names this exact file as the case that motivated
generating types at all, after hand-written versions declared `accuracy_m?: number`
for four fields the backend serialises as `number | null`).

Promoting a 32,319-line generated OpenAPI schema into the design system kit
would be the single largest piece of kit bloat available in this repo. Do not.
The real resolution is stage 7+: an extracted app generates its own client from
its own service.

### 3. `frontend/src/styles.css`, from `birds/standalone/main.tsx`

**MOVE INTO THE APP, together with the whole standalone entry.** See section 4,
trap 2. `birds/standalone/main.tsx` is the Springer Birds root-mounted build.
It is app source that happens to be an entry point, and the two files that build
it (`frontend/springer.html`, `frontend/vite.springer.config.ts`) are app-specific
build config sitting in the platform's frontend root. The stylesheet import goes
away when the app owns its own CSS (section 3, commit C).

### 4 and 5. `../../Birds` and `../Budget`

**MOVE INTO THE APP (structural, zero behaviour change).** These are an app's
own root component, which lives one directory *above* the app's directory. The
app boundary is currently "`<app>/` plus a sibling `<App>.tsx`", which is two
places. Fold each entry file into its own directory as `<app>/index.tsx` so that
one gitignore line and one `git mv` cover the whole app.

This is not urgent and it is not a promotion, but doing it makes stages 5 and 7
mechanical instead of fiddly, and it makes `check_no_app_names.py`'s content
pattern collapse from two alternatives to one.

### Summary counts

- PROMOTE: **1** (the native activity types, an export-barrel change)
- MOVE INTO THE APP: **3** (the standalone entry and its two build files; the
  notes CSS block coming back out of the kit; the two app root entry files)
- INLINE / DELETE: **0**
- LEAVE, with the reason already written down: **1** (`schema.gen`)
- GENUINELY AMBIGUOUS: **1** (the 22 legacy CSS classes; see section 6)

---

## 3. Proposed kit surface, and the ordered commits

### Commit A: export the native activity types, and stop the kit naming an app

**Change.** In `packages/ui/src/native/types.ts`, rename the interface
`CommuteActivityState` to `LiveActivityState` and update its three internal
references (`WorkshopNative.liveActivity.start`, `.update`, and the
`liveActivity:ended` event payload if it carries one). In
`packages/ui/src/index.ts`, add:

```ts
export {
  type LiveActivityState, type ActivityStatus, type LiveActivityHandle,
} from "./native/types";
```

Then in `frontend/src/pages/apps/<slug>/useCommuteNative.ts`, replace the
`@shared/native-bridge` import with the kit one and rename the local type use.

**Exported surface.**

```ts
/** Severity of a live-activity card, mapped to the five status kinds. */
export type ActivityStatus = "ok" | "info" | "caution" | "urgent";

/** State an app hands the native shell to draw a lock-screen live activity. */
export interface LiveActivityState { /* shape unchanged from CommuteActivityState */ }

/** Handle returned by liveActivity.start(), used to update or end it. */
export interface LiveActivityHandle { /* unchanged */ }
```

**Additive or breaking: ADDITIVE, patch (0.6.1 -> 0.6.2).**
Reasoning against `packages/ui/CONTRIBUTING.md`'s table. Three names are added
to the public surface and none is removed: `api/ui.api.md` is a names-only
snapshot (`packages/ui/scripts/api-snapshot.mjs` says so explicitly, "the
snapshot covers the export NAMES of each public entry point"), and
`CommuteActivityState` is **not currently in it**, so the rename removes no
public name. It is reachable only structurally, through the already-public
`WorkshopNative` type, where a consumer writes an object literal rather than
naming the interface. No in-repo or plausible out-of-repo call site can break.

**This is a judgement call and I am flagging it as one.** A strict reading of
"change a prop's type" could argue for a minor. I recommend patch plus an
explicit CHANGELOG paragraph saying the interface was renamed and why, which is
the information a consumer would actually need either way. If the operator
prefers the conservative call, it is a minor (0.7.0) and then per CONTRIBUTING
step 2 it also requires bumping the `kit:` pin in `backend/apps.json` and
`DEFAULT_KIT` in `backend/services/manifests.py`.

**Dependencies dragged into the kit: none.** These are pure type declarations in
a file the kit already ships. No runtime code, no new peer dep, no subpath
needed.

**Can it ride with another commit?** It can ride with commit B. It must not ride
with commit C, which is a different kind of change.

### Commit B: take the notes app's CSS back out of the kit

**Change.** Cut lines 3680 to about 3771 of
`packages/ui/src/styles/components.css` (the `/* ---- NOTES app ---- */` block,
33 `.ws-note-*` rule selectors over 13 class names) into a new `frontend/src/pages/apps/notes/notes.css`,
imported by `notes/Notes.tsx`. Rename the classes off the reserved `ws-` prefix
while they move: `.ws-note-layout` becomes `.note-layout`, and so on. This is
the pattern the other apps already use (`birds/birds.css` imported by
`BirdsShell.tsx`, `budget/budget.css` imported by `Budget.tsx`,
`birds/aviary/aviary.css` imported by `Aviary.tsx`).

**Additive or breaking: BREAKING, minor (0.6.2 -> 0.7.0).**
CONTRIBUTING's table is unambiguous: "Rename a `.ws-*` class: yes, breaking,
minor", and removal is strictly worse than a rename. No in-repo consumer other
than the notes app uses these classes (verified: zero `ws-note` occurrences in
`packages/ui/src/*.tsx`), but the version bump is the only signal an out-of-repo
consumer ever gets, and the last time that rule was skipped, `Menu` broke every
external app silently. Per CONTRIBUTING step 2 this also bumps the `kit:` pin in
`backend/apps.json` and `DEFAULT_KIT` in `backend/services/manifests.py`.

Note in passing: `DEFAULT_KIT` currently reads `"@workshop/ui@^0.2"` while the
package is at 0.6.1, so that pin has been stale across at least four breaking
releases. Worth correcting in this commit since you are already editing the
line, but say so in the message rather than folding it in silently.

**Dependencies dragged into the kit: none. This commit removes from the kit.**

**Must this be alone?** It can ride with commit A (both are kit-surface changes
landing one version bump between them, and a single 0.7.0 covering both is
honest). It must not ride with C or D.

### Commit C: the standalone birds build leaves platform source

**Change.** Move `frontend/springer.html` and `frontend/vite.springer.config.ts`
next to the app, and point them at the app's own entry. Drop
`import "../../../../styles.css"` from `birds/standalone/main.tsx` once commit D
has given birds its own sheet.

**This is not a kit change at all** and involves no version bump. It is listed
here because it is one of the five measured edges and because it is currently
invisible: `check_no_app_names.py`'s `PLATFORM_DIRS` is
`("backend/", "frontend/src/", "packages/", "scripts/", "infra/", "mobile/")`,
so nothing in `frontend/`'s root is scanned, and those two files contain 15
occurrences of the string `<slug>` including `/apps/<slug>` path literals that the
gate's own pattern would match if it ever looked. Widening `PLATFORM_DIRS` to
`frontend/` will raise `literal_sites`; that is a true reading and per the gate's
own `--because` rule it should be pinned with the reason, not avoided.

**Must be alone.** It moves a build entry point; if it breaks, it breaks a
separate bundle that nothing else in this sequence touches, and you want the
bisect to land on it.

### Commit D: the legacy CSS classes (the real Stage 4b, and the largest piece)

**Change.** Give each app the classes it uses. Twenty-two class names, roughly
320 use sites, measured:

| Class | Uses in app pages | Apps |
|---|---|---|
| `.muted` | 222 | birds, budget, commute, finance, health, location |
| `.card` | 58 | birds, budget, commute, location |
| `.err` | 14 | budget, commute, location |
| `.error` | 8 | birds |
| `.mono` | 8 | birds |
| `.v` | 6 | budget |
| `.stat` | 5 | budget |
| `.k`, `.slider` | 4 each | budget |
| `.inline-edit-wrap` | 3 | budget |
| `.table-scroll` | 2 | commute, location |
| `.ctl-grid`, `.ctl-label`, `.ctl-val`, `.form-actions-sticky`, `.grad`, `.inline-edit`, `.inline-edit-dot`, `.inline-edit-err`, `.inline-edit-input`, `.scn-btns`, `.stat-row` | 1 to 3 each | budget |

These are **not** kit classes. The kit's equivalents are `.ws-card`, and the
`--text-muted` token. These are the pre-design-system sheet at
`frontend/src/styles.css` (3,216 lines), loaded globally by `main.tsx:14` after
the kit's sheet, which is why everything looks right today.

**Per-class recommendation:**

- `.muted`, `.card`, `.err`, `.error`, `.mono`: **INLINE / DELETE.** Replace at
  the call site with the kit primitive that already exists (`Card`, `StateBlock`,
  `Notice`) or the token (`color: var(--text-muted)`). 310 of the 320 sites are
  these five. Do NOT promote them: adding `.muted` and `.card` to the kit
  enshrines the sheet the design system replaced, and `frontend/CLAUDE.md`
  already forbids new code building on it.
- `.table-scroll`: **PROMOTE, candidate.** Generic, used by two apps, and the
  kit already ships `Table`. Fold it in as `Table`'s own overflow behaviour
  rather than a new export, which makes it additive with no new name.
- The remaining 16, all budget-only (`.ctl-*`, `.inline-edit-*`, `.stat*`,
  `.grad`, `.slider`, `.k`, `.v`, `.scn-btns`, `.form-actions-sticky`):
  **MOVE INTO THE APP.** `frontend/src/pages/apps/budget/budget.css` already
  exists and is already imported by `Budget.tsx:2`. This is a cut-and-paste into
  a file that is already wired up. Promoting one operator's projection-editor
  controls into the platform kit is precisely the outcome this ticket exists to
  avoid.

**Additive or breaking:** the `.table-scroll` fold is additive (patch). Nothing
else in commit D touches the kit.

**Must be split.** This is one commit per app, in ascending order of size:
health, location, finance, commute, budget, birds. Budget and birds are large
enough to want their own. A single 320-site commit is unreviewable and, because
none of it is compile-checked, unverifiable by anything except looking at it.

### Commit E (optional, structural): fold the app entry files into their directories

`git mv frontend/src/pages/apps/Birds.tsx frontend/src/pages/apps/<slug>/index.tsx`
and the same for Budget, Commute, Finance, Location. Update the five importers in
`frontend/src/apps/registry.tsx` and the two intra-app importers (edges 4 and 5).
Then narrow `_app_content_patterns` in `check_no_app_names.py` accordingly.

**Must be alone**, and it should probably wait for stage 5, which rewrites
`registry.tsx` anyway.

### Ordering

```
A (kit: export native types, de-name the interface)   ─┐ one 0.7.0 bump
B (kit: notes CSS out, .ws-note-* removed)            ─┘ between them
C (standalone birds build leaves frontend/ root)        alone
D1..D6 (legacy CSS, one per app, small to large)        one per app
E (fold entry files in)                                 alone, after stage 5
```

A and B are independent of C, D and E and can land first, today. D is the bulk
of the work and the only part that is not a few lines.

---

## 4. Traps

**Trap 1: the kit already contains one app's CSS, and the "existence proof" app
is the one that put it there.** Stated in section 0. The lesson generalises: the
cheapest way to make an app import nothing outside the kit is to push the app's
own concerns into the kit, and nothing currently measures that direction. If
Stage 4b is judged by "does any app page import outside the kit", it can be
passed by making the kit worse. Judge it by the pair of numbers, not one.

**Trap 2: `frontend/springer.html` and `frontend/vite.springer.config.ts` are
app-specific build files in platform source that no gate can see.**
`check_no_app_names.py`'s `PLATFORM_DIRS` starts at `frontend/src/`, so the
frontend's own root is unscanned. 15 occurrences of `birds` live there,
including a Vite transform plugin named `rewriteBirdsPaths` that string-replaces
`/apps/<slug>` out of every module in that build. Also note the plugin's own
comment: it deliberately depends on the fact that import specifiers spell the
prefix as `pages/apps/<slug>` (mid-string) while nav links spell it as
`"/apps/<slug>` (right after the quote). Commit E, which renames
`pages/apps/Birds.tsx` to `pages/apps/<slug>/index.tsx`, does not disturb that,
but any future change to app path shape can silently corrupt this build.

**Trap 3: `check_no_app_names.py` does not see dynamic imports.** Its TypeScript
matcher is `from\s+["'][^"']*pages/apps/{short}[^"']*["']`. `registry.tsx`
reaches app pages fifteen times via `lazyPage(() => import("../pages/apps/..."))`
and once via a static `from`, and the gate reports **one** import site for that
file (birds, the eager `RecorderProvider`). The other fifteen are real structural
dependencies that the `import_sites` number does not include. Do not read
`imports: 20` as "20 structural app dependencies remain"; it is 20 that this
matcher can see. Fixing the matcher will raise the number, which is correct, and
per the gate's own `--because` rule the raise gets pinned with the reason.

**Trap 4: `frontend/src/lib/storage.ts` registers two birds storage keys
eagerly, and it cannot simply move.** Its own header explains: `registerDeviceScoped`
must run before the first `setCurrentUser(id)` migration sweep;
`auth/AuthProvider.tsx` imports this module eagerly at boot while every birds
page is lazy. Move the registrations into the birds app and the migration sweep
can see a bird key before it knows the key is exempt, and namespace it under a
user. This is a documented, deliberate exception that leaves with birds at stage
7 when the app has its own eager entry point. **Do not "clean it up" during 4b.**

**Trap 5: `recharts` is declared only in `frontend/package.json`.** Three app
files import it directly (`birds/ui.tsx`, `birds/ForecastWeek.tsx`,
`budget/Projection.tsx`). It resolves today only because the npm workspace
hoists it to the root `node_modules`. The kit deliberately does **not** depend on
recharts: `packages/ui/src/chart.tsx` exports `CHART_SERIES`, `CHART_TARGET`,
`ChartFrame` and `ChartTooltip` as colors and a wrapper, with a comment saying
recharts needs concrete color strings. **Do not add recharts to the kit to
"solve" this.** It is an app dependency and it belongs in the app's own
`package.json` at stage 7. If it were ever added to the kit it would need the
optional-peer-on-a-subpath treatment that maplibre (`@workshop/ui/map`) and
CodeMirror (`@workshop/ui/editor`) already have, which is a reason not to.

**Trap 6: `scripts/check_design.sh` hardcodes three birds file paths** (lines
121 to 123) as an inline-hex exemption list: `birds/recorder.tsx`,
`birds/Spectrogram.tsx`, `birds/LiveFeed.tsx`. Extract birds and the design gate
carries three dangling paths. It is counted (3 literal sites) and it is small,
but it will fail closed or fail open depending on how the script handles a
missing path, and nobody has checked which.

**Trap 7: the class scan cannot see template literals.** My measurement found 11
"undefined anywhere" class fragments ending in `$`, all in birds
(`birds-nav__item$`, `birds-scope--$`, `bird-cal__day$`, and so on). Those are
artifacts of `className={\`birds-tab ${x}\`}` interpolation, not missing classes.
`scripts/check_undefined_css.py` exists and passes today; it will need to keep
passing after each commit D, and it is the only automated check that covers any
of this.

**Trap 8: nothing in commit D is compile-checked.** `npm run typecheck` will
pass whether or not you got the CSS right. `cd frontend && npm test` is vitest
over pure logic modules with no DOM harness, so it will not catch it either. The
only verification is looking at the built app. See section 5.

---

## 5. Verification

Per commit, in order:

```bash
npm run typecheck                          # from the repo root; the real tsc, ~8s
cd frontend && npm test && cd ..
bash scripts/check_design.sh
python3 scripts/check_undefined_css.py
python3 scripts/check_no_app_names.py      # three numbers into the commit message
python3 scripts/check_no_raw_storage.py
```

For any commit touching `packages/ui/`:

```bash
npm run api:check --workspace @workshop/ui   # fails if the surface moved and api/ui.api.md did not
npm run api:snapshot --workspace @workshop/ui # then commit api/ui.api.md
npm test --workspace @workshop/ui
```

Never use `npx tsc`. It resolves a deprecated stub package, prints "This is not
the tsc command you are looking for", and **exits 0**.

**For commit D, and only measurable by eye:** build and look.

```bash
bash scripts/apply.sh          # then load each app page on the built origin
bash scripts/check_design.sh   # "the public bundle is STALE" means the operator sees old code
```

`apply.sh` is the final action of every commit here and is safe to run at any
time, from any agent, concurrently.

**A re-measurement command for whoever picks this up next,** so the inventory in
section 1 can be re-derived rather than trusted:

```bash
grep -rEoh 'from +["'"'"'][^"'"'"']+["'"'"']' frontend/src/pages/apps \
  --include=*.ts --include=*.tsx | sort | uniq -c | sort -rn
```

then filter out `@workshop/ui*`, the npm packages, and every specifier that
resolves inside the importing app's own directory. Remember to exclude
`pages/apps/tabs/`, which is platform code.

---

## 6. What is genuinely the operator's decision

**1. Whether `.muted` and `.card` get inlined or promoted.** These are 280 of
the 320 legacy-class sites. Inlining them means touching 280 call sites across
six apps and replacing each with a kit primitive or a token, which is real work
and carries real risk of visual drift with no test to catch it. Promoting
`.ws-muted` and reusing `.ws-card` is a fraction of the effort. My recommendation
is inline, because `frontend/CLAUDE.md` says every change under `frontend/` must
build from the kit primitives and there is no "systematize it later" pass. But
the effort ratio is roughly ten to one and this is a cost the operator is paying,
so it is his call. **This is the one place in the plan where I would not proceed
without an answer.**

**2. Patch or minor for commit A.** Section 3 argues patch and says why. The
conservative call is minor. Either is defensible; the difference is one version
number and two pin updates.

**3. Whether `check_no_app_names.py`'s `PLATFORM_DIRS` should widen to
`frontend/`, and whether the import matcher should learn dynamic `import()`.**
Both make the gate more honest and both make the numbers go **up** on the run
that lands them (traps 2 and 3). The gate's own doctrine says a number that is
wrong upward gets disbelieved and then ignored, and that a ratchet you re-pin
whenever it fails is not a ratchet. Raising the counts to make them true is the
opposite failure from the one the doctrine warns about, but it still looks like
regression on the day it lands, and the `--because` note is what carries the
explanation forward. Worth deciding before, not during.

**4. Whether Stage 4b is now declared done after commits A through D**, given
that the thing the handoff said it was blocking (gitignoring the app directories)
is blocked by `registry.tsx` and therefore by stage 5. My reading is that A
through D are worth doing on their own merits and should be called what they
are, rather than being called "the thing that unblocks extraction", which they
are not. This has been believed done twice already; the way to not make it three
is to describe the outcome in terms of the measured numbers and nothing else.
