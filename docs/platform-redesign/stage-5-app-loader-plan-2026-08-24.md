# Stage 5: the runtime app loader, as a build plan for this repo

- Date: 2026-08-24
- Status: plan, not built. The counts in `scripts/no_app_names_baseline.json` are the status of the work (convention 7.1 of `app-separation-why-it-recurs-2026-08-24.md`). No line in this file may ever read "done".
- Sits under: `app-separation-and-github-2026-08-20.md` section 2 (the model of record for the mechanism), `HANDOFF-app-platform-and-github-2026-08-20.md` (stage state and traps), `app-separation-why-it-recurs-2026-08-24.md` (why this has never been built).

**The mechanism is decided and is not reopened here.** Import maps plus dynamic `import()` of a built ESM bundle, with declare-then-activate and named mount points. Not module federation, not iframes. What follows is only how that lands on this repo's actual build, this repo's actual manifest reader, and this repo's actual serving path.

Everything labelled "measured" was run against the working tree on 2026-08-24 and the command is given. Everything labelled "inferred" is reasoning I did not execute, and each one carries the command that would settle it.

---

## 1. What is actually in the way, measured

```
python3 scripts/check_no_app_names.py --json
  content_files 102     import_sites 20     literal_sites 305   (baseline pins 302, so the gate FAILS on clean HEAD)
```

Of the 305 literals, `frontend/src/apps/registry.tsx` carries 54, and it is also the one frontend `import_site` (the eager `RecorderProvider` import at line 85). Broken down by app, measured:

| app | literals in registry.tsx | tracked frontend content files |
|---|---|---|
| birds | 32 | 44 |
| finance | 6 | 3 |
| location | 5 | 2 |
| budget | 4 | 3 |
| commute | 4 | 4 |
| health | 2 | 1 |
| notes-app | 1 | 5 |
| **total** | **54** | **62** |

The other 40 content files are backend modules and agent definitions and are not this stage's business.

The three literals that put the tree over its own baseline right now are **not** the docstring lines `app-separation-why-it-recurs-2026-08-24.md` section 5 names. Measured: they are three `"app:budget"` fixture strings at `frontend/src/components/chat/uiCardActions.test.ts` lines 163, 164 and 165, which were absent 25 commits ago. Renaming them to a fictional app id takes the count to exactly 302 and the gate goes green. That is commit 0 below, and it is a prerequisite for every other commit, because the gate is unconditional in `scripts/reviewer.sh` and while it fails it rejects unrelated work.

The relevant facts about the build, all measured:

- `frontend/vite.config.ts` has **no `build` block and no `optimizeDeps` block**. So `cssCodeSplit` is at Vite's default of `true`, and `frontend/dist/assets/` currently holds 5 CSS files, 4 of which are per-app chunk CSS (`Birds`, `BirdsLayout`, `Budget`, `Location`). Trap (b) is not hypothetical here, it is the current shape of the output.
- `react@18.3.1` in this install is **CommonJS only**: `"main": "index.js"`, no `"module"` field, and an `exports` map with no ESM condition. Same for `react-dom@18.3.1`.
- `@workshop/ui@0.6.1` resolves through the `workshop-source` export condition to **TypeScript source**, not `dist/`. The shell compiles the kit into its own bundle today.
- The root `package.json` already pins one React for the whole workspace with an `overrides` block, and its comment records a downstream instance that hit a duplicate React. The loader makes that hazard reachable from a second direction.
- `/base-apps` is mounted in `backend/main.py` with `dependencies=_OPERATOR`, and it is **not** on the public edge's `_BLOCKED_PREFIXES` (`/relay`, `/events`, `/auto-pr`). So a new route under `/base-apps` reaches the operator's origin with no edge change.
- `backend/workshop_public_app.py` serves `frontend/dist` from disk per request, `index.html` with `Cache-Control: no-cache`, and `/assets` immutable because every emitted filename is content hashed.
- App pages import, outside their own tree: `react` (48), `@workshop/ui` (46), `lucide-react` (38), `react-router-dom` (25), `recharts` (3), `@workshop/ui/{markdown,map,editor}` (1 each), `react-dom/client` (1), `react-dom` (1).
- Stage 4b is further along than the handoff says, and a concurrent plan landed the same day with more detail: [`stage-4b-kit-promotions-2026-08-24.md`](stage-4b-kit-promotions-2026-08-24.md). Read it before step 5 below. Its central finding is a dependency of this stage: roughly 320 uses of ~22 class names in app pages are defined only in the platform's legacy `frontend/src/styles.css`, no import scan sees it, and an app extracted without them renders unstyled. That is the same failure shape as trap B in section 4.2, arriving from a different direction, and the per-app migrations in section 7 cannot be called done for an app until its class names travel with it. `usePoll`, `userStorage`, `timeAgoSeconds` and `AppChatDockRenderer` are already in `packages/ui/api/ui.api.md`. The residual platform reach from app pages, measured, is: `lib/apps` (8), `api/schema.gen` (3), `components/shell/takeover` (2), and one each of `lib/scope`, `lib/useMediaQuery`, `lib/tasks`, `lib/jobs`, `lib/holders`, `lib/chatSelection`, `components/holders/useHolders`, `components/holders/RefPill`, `components/shell/directory`, `memory/MemoryDocument`. Those twelve are the real remainder of 4b and they block the last app, not the loader.

---

## 2. The runtime contract

### 2.1 What the app declares, and who reads it

An app declares its UI in **`workshop.app.json`**, in a new top-level `ui` block. That file is already read by exactly one function, `app_runner._read_manifest_file`, which owns the filename set (`MANIFEST_NAMES`) and the jsonc comment tolerance, and which `services/app_files.py` already reuses rather than reimplementing. **The loader reuses it too. There is no second manifest reader anywhere in this plan.**

```jsonc
{
  "name": "widgets",
  "ui": {
    "contract": 1,                       // platform-owned integer, see section 6
    "kit": "^0.6",                       // the @workshop/ui range this build was compiled against
    "dir": "dist/ui",                    // relative to the app dir, holds the built output
    "entry": "app.js",                   // the ESM bundle, relative to dir
    "styles": ["app.css"],               // every stylesheet the build emitted, see trap B
    "provider_entry": "provider.js",     // optional, see 2.5
    "icon": "sliders",                   // an icon token, a url, or absent (lib/appIcon rules)
    "group": "money",                    // switcher group, optional
    "link":   { "label": "Widgets", "to": "/apps/widgets" },
    "status": { "endpoint": "/api/widgets/status" },   // returns {text, tone}
    "redirects": { "/widgets": "/apps/widgets" },      // legacy paths this app claims
    "pages": [
      { "path": "/apps/widgets",         "mount": "app.route",  "export": "Home",  "crumb": { "label": "Widgets", "parent": "/apps" } },
      { "path": "/apps/widgets/settings","mount": "app.route",  "export": "Setup", "crumb": { "label": "Settings", "parent": "/apps/widgets" }, "layout": "Shell" }
    ],
    "layouts":  [ { "export": "Shell", "mount": "app.layout" } ],
    "settings": [ { "export": "SettingsPanel", "mount": "app.settings", "label": "Widgets" } ]
  }
}
```

Everything above the `pages` array is **data the shell renders without executing a line of app code**. That is declare-then-activate, taken from VS Code by way of section 2.2 of the design doc: breadcrumbs, the dock tile, the launcher card, the switcher group, the icon, the legacy redirects and the status line all exist for an app whose bundle has never been fetched. The bundle is fetched the first time the user opens one of the declared paths.

`mount` values come from a **closed vocabulary the platform publishes**, which is the Red Hat Developer Hub pattern from the same section. The whole vocabulary, and it is deliberately small because it is exactly what `AppModule` carries today:

| mount point | what it is | needs the bundle? |
|---|---|---|
| `app.route` | one routed page under `/apps/<slug>` | yes, on first navigation |
| `app.layout` | a wrapper route around some of this app's own pages | yes, with the first page under it |
| `app.settings` | a panel on the Settings screen | yes, when that tab opens |
| `app.session-provider` | a context provider that must outlive navigation | yes, at shell start, see 2.5 |

No other mount point exists, and no host source file names an app. The shell reads `mount` and never reads `slug` to decide anything.

**Icons, groups, crumbs and status are not mount points**, on purpose. They are values, they load with the manifest, and adding an app must never require the shell to run app code to draw a launcher.

### 2.2 What the bundle exports

One built ESM file, one default export, and it is a plain object of components keyed by the names the manifest referenced:

```ts
// The whole contract. Nothing is imported from the platform to write this.
export type WorkshopAppBundle = {
  contract: 1;
  /** Every export the manifest may name, by name. Components take no props. */
  exports: Record<string, ComponentType>;
};
export default { contract: 1, exports: { Home, Setup, Shell, SettingsPanel } } satisfies WorkshopAppBundle;
```

Three rules, each of which exists because of something in the current registry:

1. **A page component takes no props.** Today `registry.tsx` passes `groupNav={<AppGroupNav />}` and `renderChatDock={makeChatDock("finance")}` into four pages, and those props are the reason the registry has to know the app's id. In the loader they come from context instead (2.4).
2. **The app never constructs a `<Route>`.** It declares `{path, export, layout}` and the shell builds the router elements. This is what makes the mount vocabulary closed, and it is also the mitigation for trap C (section 5.3).
3. **`contract` is on the default export as well as in the manifest.** The manifest says what the app claims; the bundle says what it actually is. They are checked against each other, because a manifest is hand edited and a bundle is not.

### 2.3 Discovery: manifest to bundle URL

```
GET /api/base-apps/contributions      ->  { apps: [ { id, slug, ui: {...}, urls: {...} } ] }
```

Served from `backend/routes/base_apps.py`, backed by a new `backend/services/app_ui.py` that, for each **installed** app (`services/installed_apps`):

1. resolves the app dir with `paths.scope_root(f"app:{slug}")`, the single mapping every per-scope path already derives from,
2. reads `workshop.app.json` through `app_runner._read_manifest_file`,
3. takes the `ui` block, normalizes it the way `services/manifests._normalize_entry` normalizes its own (tolerant: a malformed entry costs itself and nothing else),
4. hashes the bytes of `entry` and of each `styles` file, and returns absolute URLs with the hash as a query parameter:

```
urls: { entry:  "/api/base-apps/ui/widgets/app.js?v=9f2c1b7e",
        styles: ["/api/base-apps/ui/widgets/app.css?v=3ad0e441"],
        provider: "/api/base-apps/ui/widgets/provider.js?v=71bb0c02" }
```

The hash is computed server side rather than baked into the filename because the app's own build then does not need content hashing, the manifest can name stable files, and a rebuilt app is picked up with no manifest edit. It also makes the asset route safely immutable.

```
GET /api/base-apps/ui/{slug}/{path:path}
```

serves a file under `scope_root("app:<slug>")/<ui.dir>` only. Containment is the same resolve-then-compare check `workshop_public_app.spa` already uses (`candidate.is_relative_to(root)`), because `path` is caller controlled. `Cache-Control: public, max-age=31536000, immutable`, which is honest because the URL carries the content hash. Content types: `text/javascript` for `.js`, `text/css` for `.css`, refuse everything else, because a module loaded with the wrong MIME type fails in a way that reads like a corrupt bundle.

Both routes inherit `require_operator` from the `/base-apps` mount and neither needs an edge blocklist change (measured, section 1).

### 2.4 How the shell activates it

Four files replace `frontend/src/apps/registry.tsx`, and none of them may contain an app name:

- **`frontend/src/apps/contract.ts`** the types above, the closed mount vocabulary, and the contract integer. Pure data, no imports from the shell.
- **`frontend/src/apps/declarations.ts`** fetches `/api/base-apps/contributions` once per session, normalizes, and exposes the same functions the shell already calls today so `App.tsx`, `appNav.tsx`, `useShell.ts` and `Apps.tsx` change as little as possible: `appRoutes()`, `appCrumbs()`, `appGroups()`, `appPlaces()`, `appIcon(id)`, `appStatuses()`, `appSettingsPanels()`. Signatures stay; the bodies read declarations instead of a literal array.
- **`frontend/src/apps/loader.ts`** `loadApp(decl): Promise<WorkshopAppBundle>`, memoized per app id. It links the declared stylesheets and waits for them, then `await import(/* @vite-ignore */ url)`. The `@vite-ignore` is required: without it Rollup tries to analyze a runtime URL and warns, and in some configurations rewrites it.
- **`frontend/src/apps/AppRouteHost.tsx`** the component the shell puts in every declared `<Route>`. It owns the three states (loading, ready, failed) and it is the failure boundary (section 5).

Host services reach the app through **one context provided by the shell and read through the kit**, which is what removes the prop passing that forces `registry.tsx` to know app ids:

```ts
// exported from @workshop/ui, so an app imports nothing from the platform
export type AppHost = {
  appId: string;
  GroupNav: ComponentType;                    // was the groupNav prop
  renderChatDock: AppChatDockRenderer;        // was the renderChatDock prop, already a kit type
};
export function useAppHost(): AppHost;
```

`AppRouteHost` renders `<AppHostProvider value={...}>` around the loaded component. `AppChatDockRenderer` is already exported by the kit (measured in `packages/ui/api/ui.api.md`), so this is a small addition rather than a new idea.

### 2.5 The one thing that cannot be lazy

`registry.tsx` lines 78 to 85 already state the problem and name this stage as the fix: `AppProviders` reduces over the provider list synchronously and cannot await a chunk without unmounting the app underneath it, so `RecorderProvider` is imported eagerly.

The loader keeps that property and makes it declared instead of imported:

- An app that needs a session-scoped provider declares `ui.provider_entry`, a **separate, tiny ESM entry** whose only job is to export `{ Provider }`. The rule "a provider entry imports no CSS and no page" is enforced by the build preset (section 3.2), which is what bounds the cost, exactly as the current comment bounds it for `recorder.tsx`.
- The shell awaits every declared `provider_entry` **before first render of the routed area**, in `main.tsx` beside the existing `Guard`, with a hard timeout (3 seconds is the proposal). Awaiting before first render means nothing unmounts.
- On timeout or failure the shell renders anyway without that provider, and the affected app's routes render their failed state naming the provider. The shell is never held hostage to an app.

This is the only eager fetch in the design, it happens only for apps that declare it, and it is one small module per such app.

---

## 3. How the bundle is built, and where it is served from

### 3.1 The shell publishes its runtime

Today the shell compiles React, the router, lucide, recharts and the kit into its own chunks. For an app to import the same instances, those instances need URLs. The change is confined to `frontend/vite.config.ts` and one new plugin.

Add one **re-export module per shared specifier** under `frontend/src/runtime/shared/`:

```ts
// frontend/src/runtime/shared/react.ts
import "./guard";              // see section 5.1
export * from "react";
export { default } from "react";
```

one each for `react`, `react-dom`, `react-dom/client`, `react/jsx-runtime`, `react/jsx-dev-runtime`, `react-router-dom`, `lucide-react`, `recharts`, `@workshop/ui`, `@workshop/ui/map`, `@workshop/ui/markdown`, `@workshop/ui/editor`. That list is not invented, it is the measured import surface of the current app pages (section 1) plus the two jsx runtimes, which the tooling note in design doc section 2.1 specifically warns are not covered by the `react` entry.

Then in `frontend/vite.config.ts`:

```ts
build: {
  rollupOptions: {
    input: {
      index: "index.html",
      ...SHARED_ENTRIES,           // one entry per file above
    },
  },
},
resolve: { dedupe: ["react", "react-dom", "react-router-dom"] },
optimizeDeps: { include: ["react", "react-dom", "react-dom/client", "react/jsx-runtime",
                          "react-router-dom", "lucide-react", "recharts"] },
```

**One Rollup build, several entries.** A module imported by two entries is hoisted into a shared chunk that both import, so `frontend/src/main.tsx` and `frontend/src/runtime/shared/react.ts` end up importing the same `react` chunk. There is one instance, and the re-export entry is a stable public URL for it. Note what is deliberately absent: `manualChunks`. Do not add one for React here; splitting by hand is how the shared chunk stops being shared.

A new plugin `frontend/plugins/importmap.ts` emits the map. It uses `transformIndexHtml` with `order: "post"`, which receives the bundle in a build and therefore knows the content hashed names, and injects a **single** `<script type="importmap">` into `<head>` **before** the module entry. One map, built once at page load, which is what design doc section 2.1 requires: multiple maps in one document are not supported in Firefox, and external maps via `src=` are not standardised anywhere.

In `serve` mode the same plugin emits the same map with dev URLs pointing at the same source files (`/src/runtime/shared/react.ts`). It never points at `/node_modules/`. See 5.1 for why that is the whole trap.

### 3.2 The app side: a preset, not a template

A copyable template is a thing seven apps drift away from. Ship a config factory instead, as a workspace package `packages/app-build` published as `@workshop/app-build`:

```js
// apps/widgets/vite.config.js, in full
import { defineWorkshopApp } from "@workshop/app-build";
export default defineWorkshopApp({ outDir: "dist/ui" });
```

What `defineWorkshopApp` sets, and none of it is negotiable per app:

```js
build: {
  lib: { entry: "src/index.ts", formats: ["es"], fileName: () => "app.js" },
  cssCodeSplit: false,                                   // trap B
  outDir,
  rollupOptions: {
    external: SHARED,                                    // the same list as 3.1
    output: { assetFileNames: "app.[ext]", inlineDynamicImports: true },
  },
},
```

`SHARED` is exported from the same package, so the externals list and the import map cannot drift apart: one array, imported by the platform's vite config and by every app's.

Two build-time refusals live in the preset's `closeBundle`, and both are there because the failure they catch is invisible later:

1. more than one `.css` in `outDir`, or a `.css` the sibling `workshop.app.json` does not list in `ui.styles`,
2. an import of `@workshop/ui/tokens.css` or `@workshop/ui/styles.css` anywhere in the app's graph. The kit is `sideEffects: ["*.css"]` and the shell already loads both in `main.tsx`; an app loading them again ships a second copy of the whole design system stylesheet and can shadow the shell's tokens.

A second entry is added when the manifest declares `provider_entry`, with the same externals, `cssCodeSplit` moot, and a refusal if that entry's graph reaches any CSS.

### 3.3 Where the bytes live

`apps/<slug>/dist/ui/` on the machine, served by `GET /api/base-apps/ui/{slug}/{path}` (2.3). `apps/` is already gitignored (`.gitignore:160`) and `git ls-files apps/ | wc -l` is 0, so nothing about this puts an app back into the repo.

This deliberately does **not** copy app assets into `frontend/dist`. `scripts/apply.sh` compares `frontend/dist/build-info.json` against `scripts/ui_source_id.sh` and rebuilds the whole bundle on drift; anything an installer wrote into `dist/` would be at the mercy of the next rebuild, and `apply.sh` is documented as safe to run at any time from any agent. Keeping app assets outside `dist/` keeps that promise true. It is listed in section 8 as an operator decision because it costs one proxy hop.

`scripts/apply.sh` needs no change for this stage. An app rebuild changes the app's own `dist/ui`, the server hashes it on the next contributions read, and the shell picks up a new `?v=`. The shell's own import map changes only when the shell rebuilds, which `apply.sh` already handles.

---

## 4. The traps

Both of the named traps share one property: **a developer who does the normal things sees nothing wrong.** So each one gets a config change and an assertion that runs without a human deciding to look.

### 4.1 Trap A: a second React from Vite's dev pre-bundler

**What the design doc says.** Set `optimizeDeps: { exclude: ['react', 'react-dom'] }`.

**Do not do that in this repo, and here is the measurement.** `react@18.3.1` and `react-dom@18.3.1` as installed here are CommonJS only: `"main": "index.js"`, no `"module"` field, and an `exports` map whose only conditions are `react-server` and `default`, both pointing at CJS. Vite's dev server does not convert CommonJS in `node_modules` on the fly; converting it is precisely what dep pre-bundling is for. Excluding React from pre-bundling therefore does not produce one React, it produces a dev server that cannot serve React at all. (Inferred for the exact failure text, measured for the package metadata. Settle it with: `cd frontend && npx vite --config /tmp/probe.config.ts` after adding the exclude, and load `/`.)

**What actually causes the second instance**, and it is subtler than a pre-bundler existing: the browser keys modules by **URL**. In dev the shell imports React as `/node_modules/.vite/deps/react.js?v=abc123`, where `?v=` is a re-optimization token that changes. If the import map points a foreign bundle at `/node_modules/.vite/deps/react.js` without that token, the browser fetches a second URL, evaluates a second module, and there are two Reacts with identical bytes. Every hook in the app then throws, in dev, and never in production, because in production the import map points at a real hashed file that both sides share.

**The config change.**

1. Add the `build.rollupOptions.input` shared entries from 3.1, so production has one URL per shared specifier and it is a chunk the shell itself imports.
2. Add `resolve.dedupe: ["react", "react-dom", "react-router-dom"]`, so a nested copy under `node_modules` cannot win a resolution.
3. Add `optimizeDeps.include` for the shared list, so dev pre-bundles them deterministically instead of discovering them.
4. **Point the import map at shell-owned module URLs in both modes**, never at a `node_modules` path: `/src/runtime/shared/react.ts` in dev, `/assets/react-<hash>.js` in production. There is then exactly one URL for `react` in the browser's module map, in both modes, and the `?v=` token problem cannot arise because no map entry contains one.

Do not add `optimizeDeps.exclude`.

**The detection, three layers, cheapest first.**

- **`frontend/src/apps/importmap.test.ts`**, in the ordinary `cd frontend && npm test` run. It imports the plugin, generates the map for `serve` and for `build`, and asserts: every specifier in `SHARED` is present including every subpath (`react/jsx-runtime` explicitly, since it is not covered by `react`); **no value contains `/node_modules/`**; no value contains `?v=`; in build mode every value names a file present in `frontend/dist`. That single "no `/node_modules/`" assertion is the whole trap expressed as a string comparison, and it runs on every test invocation with no browser.
- **`scripts/check_app_loader.py --dist`**, run after a build. It scans `frontend/dist/assets/*.js` for a React internal marker (`__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED`) and asserts it occurs in **exactly one** chunk. Two chunks means the shared entry did not share and production has two Reacts too.
- **The runtime guard, `frontend/src/runtime/shared/guard.ts`**, imported by every re-export module:

  ```ts
  const KEY = "__workshop_shared_runtime__";
  const marker = { id: import.meta.url };
  const g = globalThis as Record<string, unknown>;
  if (g[KEY] && g[KEY] !== marker) {
    throw new Error(
      "Workshop: two copies of the shared runtime are loaded. An app bundle resolved " +
      "react outside the import map. Check the map in index.html and the externals in " +
      "@workshop/app-build.",
    );
  }
  g[KEY] = marker;
  ```

  A module body runs once per URL, so a second copy under any other URL fires this immediately, in dev and in production, with a sentence that names the cause. It replaces "Invalid hook call", which names a symptom four layers away from the mistake.

- The definitive dev check is a browser, because a dev-only failure has no artifact: `scripts/check_app_loader.sh --dev` starts `vite dev`, loads `/apps/widgets` (the fixture app) in the Playwright chromium already installed at `~/.cache/ms-playwright/`, and fails on any page error or on a console message matching `/Invalid hook call|two copies of the shared runtime/`. This is the one slow check and it belongs in the stage's own verification, not in `npm test`.

### 4.2 Trap B: cssCodeSplit renders an app unstyled in production only

**Why it bites here specifically.** `frontend/vite.config.ts` has no `build` block, so `cssCodeSplit` is `true`, and the current `frontend/dist/assets/` contains four per-app chunk stylesheets. Today those load because the dynamic `import()` is inside the shell's own Rollup graph, so Vite emits its preload helper and injects the `<link>` for you. A foreign bundle fetched by URL gets no helper. The stylesheet is emitted, is served, and is never requested. In dev there is no chunk CSS at all (Vite injects styles as they are imported), so the app looks right on `:5173`, right in `npm test`, right in typecheck, right in `check_design.sh`, and wrong on the built origin, which is the only origin the operator uses.

**The config change, in two places, and both are needed.**

1. **App side**, in the preset and therefore not per app: `build.cssCodeSplit: false` plus `output.assetFileNames: "app.[ext]"`. The app emits at most one stylesheet, with a name the manifest can state.
2. **Platform side**, in `loader.ts`: before `await import(entryUrl)`, for each URL in `urls.styles`, append a `<link rel="stylesheet">` to `<head>` if not already present and **await its `load` event**. Only then import the module. Awaiting matters: importing first paints one frame unstyled, which is the flash the design system rules already care about.

The design doc offers `vite-plugin-css-injected-by-js` as the alternative. Do not take it. Injecting CSS from JS puts the design system's cascade order at the mercy of module evaluation order, and this repo has an explicit ordering rule (tokens before components before `styles.css`, stated in `main.tsx` and in `frontend/CLAUDE.md`). A declared `<link>` the shell controls keeps that ordering the shell's business.

**The detection, three layers.**

- **Build time, in the preset's `closeBundle`.** Fail the app's own build if `outDir` contains more than one `.css`, or contains a `.css` that the sibling `workshop.app.json` does not list in `ui.styles`. This catches the mistake at the moment it is made, in the app's repo, without the platform being involved.
- **Install time, platform side, `scripts/check_app_loader.py --app <dir>`.** The same assertion from outside, because an app built on another machine never ran the preset's hook here. `app_ui.py` applies it too and, on failure, returns the app with `ui.error` set, so the Apps page says "widgets declares no stylesheet for the CSS it ships" instead of quietly rendering it bare.
- **Production browser assertion, the definitive one.** The fixture app ships exactly one rule, `.wsfix-probe { --wsfix-loaded: 1 }`, and renders a `.wsfix-probe` element. `scripts/check_app_loader.sh --built` shoots `/apps/widgets` on the **built** origin (`127.0.0.1:8002`, per the handoff's note about minting and revoking a session cookie) and asserts

  ```js
  getComputedStyle(document.querySelector(".wsfix-probe")).getPropertyValue("--wsfix-loaded").trim() === "1"
  ```

  This is green only when the app's own stylesheet actually loaded in the built bundle, which is exactly and only the condition that trap B breaks. It reuses `scripts/shoot.sh`'s browser and session plumbing rather than inventing a second harness.

### 4.3 A third trap, found while measuring, not in design doc section 2

App pages import `react-router-dom` 25 times (measured). Two things break if an app bundles its own copy, and neither is a size problem:

1. React Router v6 builds its route tree by walking children and comparing `element.type` against its own `Route` reference. A `<Route>` created by a second copy of the package is not that reference and is rejected. (Inferred from the library's `createRoutesFromChildren`, not executed here.)
2. `useNavigate`, `useLocation` and `NavLink` read a React context created at module scope. A second copy has a different context object, reads `null`, and throws "may be used only in the context of a `<Router>`".

Two mitigations, both already in the plan above and both load bearing:

- Route contributions are **data** (`{path, export, layout}`), and the shell constructs every `<Route>`. Consequence 1 then cannot happen at all, and this is also what keeps the mount vocabulary closed.
- `react-router-dom` is in `SHARED`, so it is externalized in the app build, mapped in the import map, and covered by the same `guard.ts` as React. Consequence 2 then cannot happen either.

Settle the inference with the fixture app: have it render a `NavLink` and assert the rendered `href`. If the second-copy hazard were real and unmitigated, `--dev` and `--built` both fail loudly.

---

## 5. Failure behaviour: the shell keeps working and says what broke

The rule is the one `frontend/src/lib/lazyPage.tsx` already states for the shell's own chunks: **a page that fails to load must not be a blank page.** The loader extends it to a boundary the shell owns per app.

The reason this is achievable at all is declare-then-activate. Nav, breadcrumbs, the dock tile, the icon, the switcher and the launcher card come from JSON, so a broken app is still **visible** and still **navigable**, and the failure is a message inside one region rather than an absence everywhere.

| what fails | who catches it | what the user sees |
|---|---|---|
| `/api/base-apps/contributions` unreachable | the fetch in `declarations.ts`, which resolves to an empty list and never throws into render | the shell, complete, with no apps. The Apps page shows `StateBlock variant="error"` naming the endpoint, with Retry |
| manifest present, bundle 404 or wrong MIME | `AppRouteHost`, in its effect, not in render | the app's own route area shows `StateBlock variant="error"`: "Widgets failed to load", the URL, and Retry. Shell chrome, nav and chat are untouched |
| bundle loads, `contract` mismatch or missing named export | `loader.ts`, **before** the module is evaluated for contract, immediately after for the export check | same region, message naming what the app declared and what the bundle provides |
| kit version incompatible | `loader.ts`, **before any fetch of the bundle** | same region, naming the required range and the shell's actual kit version (section 6) |
| the app throws during its own render | `RouteBoundary` from `lazyPage.tsx`, reused as is, wrapped around `AppRouteHost` | the existing non-technical route error, with the real error and component stack on the console, which that file already does |
| session provider entry fails or exceeds its 3s budget | the awaited load in `main.tsx` | the shell renders without it. That app's routes render their failed state and name the provider. Every other app is unaffected |
| stale `?v=` after an app rebuild while a tab is open | `loader.ts` | one silent refetch of `/contributions` and one retry with the new hash. Only if that also fails does the failed state appear. This is the app-bundle analogue of `lazyPage`'s one-shot document reload, and unlike a chunk 404 it does not need a page reload, because the hash lives in a URL the shell can re-derive |

The boundary lives in **`frontend/src/apps/AppRouteHost.tsx`**, one instance per declared route, and it is the only place in the shell that knows an app bundle can fail. `AppSuspense` and `RouteBoundary` stay exactly as they are for the shell's own lazy pages; nothing about the existing chunk-staleness repair changes.

A blank page is only possible if a failure escapes into render. So `loadApp` **never throws to a renderer**: it resolves to a discriminated union `{ok: true, bundle} | {ok: false, reason, detail}`, and `AppRouteHost` renders the reason. That is a testable property and section 7 lists the test.

Copy rules apply to every string above: no em-dashes, name the endpoint where naming it helps someone act, and `StateBlock` because the content genuinely is not there (`frontend/CLAUDE.md`, "A region or a line").

---

## 6. Versioning between the shell and an app bundle

Two numbers, and they answer different questions.

**`contract`, an integer the platform owns.** It describes the shape in 2.2: the default export, `exports` as a record of prop-less components, the mount vocabulary, and the `AppHost` context. Bump it only when that shape changes. The shell accepts exactly the contract versions it implements (one, today). An app declaring anything else is refused before its bundle is fetched. React's own major version is folded into this: because React is externalized, an app compiled against 18 would silently receive 19 the day the shell moves, so **bumping React majors bumps `contract`**, which turns a silent runtime hazard into a refusal with a sentence.

**`kit`, a semver range the app declares.** `services/manifests.py` already has a `kit` field with `DEFAULT_KIT = "@workshop/ui@^0.2"`, which is stale against the installed `@workshop/ui@0.6.1` (measured). Reuse that field rather than adding a second one, and fix the default in the same commit.

The shell publishes what it actually is:

```
GET /api/base-apps/runtime  ->  { contract: 1, kit: "0.6.1", react: "18.3.1", router: "6.28.0" }
```

read from the built `frontend/dist/build-info.json`, which `scripts/stamp_build.sh` already writes, so the answer describes the bundle being served rather than what happens to be in `node_modules`.

**What happens when the kit version moves.**

- **Patch or minor.** Nothing. The kit's own contract is that a minor only adds. An app that wants a newly added primitive gets a compile error in its own build, which is where it belongs.
- **Major.** Every app whose declared range excludes the new major stops activating. Its card and its route both say so, naming the range it declared and the version the shell now serves, and pointing at the kit's CHANGELOG migration note. The app is fixed by rebuilding against the new kit and widening its range.

That refusal is the point, and it is not new policy. `packages/ui/CONTRIBUTING.md` already says a removed or renamed export is a MAJOR bump plus a CHANGELOG migration note, "out-of-repo apps consume this package and nothing here compiles them, so the version number and the changelog are the only warning they ever get." Until now that was a convention with no enforcement anywhere. The loader is what turns it into a gate, because it is the first time an out-of-repo consumer actually exists.

One consequence worth stating plainly: **externalizing the kit is what makes "Design System is mandatory" enforceable across code the platform does not compile** (design doc section 2.1). An app physically cannot ship a divergent kit. The price is that a kit major is a fleet-wide event, and the version check is what makes it a visible one instead of a pile of undefined components.

---

## 7. Commit sequence, and the count each step moves

Run `python3 scripts/check_no_app_names.py` before and after every commit and put the three numbers in the commit message (handoff section 5). Baseline today: content 102, imports 20, literals 302 pinned against an actual 305.

| # | commit | content | imports | literals | note |
|---|---|---|---|---|---|
| 0 | fix the failing ratchet: three `"app:budget"` fixture strings in `frontend/src/components/chat/uiCardActions.test.ts` become a fictional id | 102 | 20 | 305 to **302** | Unblocks `reviewer.sh` for everyone. Do this first and alone |
| 1 | shared runtime entries + import map plugin + `guard.ts` + `importmap.test.ts` | 102 | 20 | 302 | No behaviour change. Trap A mitigated and detected before anything can hit it |
| 2 | `packages/app-build` (the preset, `SHARED`, the two `closeBundle` refusals) + the fixture app under `tests/fixtures/app-widgets/` | 102 | 20 | 302 | The fixture is a fictional app outside `PLATFORM_DIRS`, so it moves nothing. Trap B mitigated and detected |
| 3 | backend: the `ui` block via `app_runner._read_manifest_file`, `services/app_ui.py`, `GET /base-apps/contributions`, `GET /base-apps/ui/{slug}/{path}`, `GET /base-apps/runtime`; fix `DEFAULT_KIT` | 102 | 20 | 302 | Nothing consumes it yet, which is fine: it is a read API, not a wired control |
| 4 | frontend: `contract.ts`, `declarations.ts`, `loader.ts`, `AppRouteHost.tsx`, `AppHost` in the kit, `main.tsx` session-provider await. `registry.tsx` still holds all seven apps and is merged into the declaration list | 102 | 20 | 302 | Dual path on purpose. Prove the loader on the fixture app before touching a real one |
| 5 | migrate **notes-app**: build it with the preset into `apps/notes-app/dist/ui`, `git rm --cached` its 5 frontend files, delete its registry entry | **97** | 20 | **301** | The smallest app with no provider, no group, no status and one route |
| 6 | migrate **health** (1 file, 2 literals) and **budget** (3 files, 4 literals) | **93** | 20 | **295** | health is the settings-panel-only case, budget is the plain-page case |
| 7 | migrate **location** (2 files, 5 literals) and **commute** (4 files, 4 literals) | **87** | 20 | **286** | first `group` pair, and the first `status` endpoint |
| 8 | migrate **finance** (3 files, 6 literals) | **84** | 20 | **280** | completes the `money` group |
| 9 | migrate **birds** (44 files, 32 literals, the one `import_site`) | **40** | **19** | **248** | last, largest, and the only `session-provider`. Depends on the residual 4b list in section 1 |
| 10 | move the four legacy redirects out of `App.tsx` into `ui.redirects` | 40 | 19 | **244** | optional, and cheap |
| 11 | delete `frontend/src/apps/registry.tsx` and re-pin the baseline DOWN | 40 | 19 | 244 | the file that has carried this problem since 2026-07-12 |

Every count above is arithmetic on measured per-app numbers (section 1), not an estimate. What remains after step 11 is backend content (40 files) and the ~200 literals in tests, `app_registry.py` and `installed_apps.py`, which are stages 6 and 7, not this one.

Two ordering constraints that are not negotiable:

- **Step 0 before everything.** A failing unconditional ratchet rejects unrelated work, and that has now happened three times (`app-separation-why-it-recurs-2026-08-24.md` section 7.3).
- **An app's legacy class names travel with it, or its migration is not finished.** Per `stage-4b-kit-promotions-2026-08-24.md`, the rules live in `frontend/src/styles.css`, so moving only the `.tsx` files leaves the app looking correct in the shell (the sheet is still loaded) and wrong the moment `frontend/src/styles.css` stops being served to it. Each of steps 5 to 9 carries its app's rules into the app's own stylesheet, and `--built` (section 8) is what proves it.
- **Step 1 before step 2.** The import map is what the externals list is externalizing *to*. Building an app against externals with no map produces a bundle that fails on a bare specifier, which reads like a loader bug and is not one.

Steps 5 through 9 are independently shippable and independently revertable, which is the whole reason for the dual path in step 4. No step wires a dead control.

---

## 8. Verification

Per commit, the repo's standing gate:

```bash
python3 scripts/check_no_app_names.py            # before and after; numbers into the commit message
npm run typecheck                                 # from the repo root, never npx tsc
bash scripts/check_design.sh
cd frontend && npm test                           # includes importmap.test.ts and loader.test.ts
npm test --workspace @workshop/ui                 # if the kit changed, which steps 4 and 9 do
bash scripts/apply.sh                             # last action
```

Specific to this stage:

```bash
python3 scripts/check_app_loader.py --dist                     # exactly one React in frontend/dist  (trap A)
python3 scripts/check_app_loader.py --app tests/fixtures/app-widgets   # CSS declared == CSS emitted (trap B)
bash scripts/check_app_loader.sh --dev                         # vite dev + fixture app, no hook errors (trap A, dev only)
bash scripts/check_app_loader.sh --built                       # built origin :8002, --wsfix-loaded == 1 (trap B, prod only)
bash scripts/shoot.sh /apps/widgets 390 800                    # and READ the png (frontend/CLAUDE.md)
bash scripts/shoot.sh /apps/widgets 1440 900
```

Unit-level properties worth pinning, all pure and all fitting the "no DOM harness" rule:

- `loader.test.ts`: `loadApp` resolves `{ok: false}` for a 404, for a wrong contract, for a missing named export and for a kit-range mismatch, and **never rejects**. That is the blank-page rule as an executable assertion.
- `declarations.test.ts`: a malformed contributions payload yields an empty app list rather than throwing, mirroring `manifests._normalize_entry`'s tolerance.
- `importmap.test.ts`: as described in 4.1.
- `frontend/src/apps/registry.test.tsx` already renders the assembled route table through the real router and asserts every declared path resolves. Keep it, retargeted at declarations, through step 11. It is the existing guard against a registry that typechecks and blanks the screen, and the loader must not lose it.

And the finish line this stage does **not** reach but must not regress: `scripts/check_fresh_clone.sh` (proposed in `app-separation-why-it-recurs-2026-08-24.md` section 4) asserts that the build succeeds with **zero apps installed**. Step 4 is the first moment that becomes true of the frontend, and step 11 is the first moment it is true with no literal specifiers left. If that script exists by then, wire it.

---

## 9. Genuinely the operator's decision

Four, and none of them has an answer in the tree.

1. **How an out-of-repo app gets `@workshop/ui` and `@workshop/app-build` at build time.** Externalizing means the app needs them only for types and config, but it still needs them. Options: publish both to npm under a scope he owns; run a private registry; depend by git URL or tarball. Until this is settled, an app repo can only build on a machine that has the monorepo checked out, which is most of the point of separation. (Constraint on any answer: an account named in this install's `WORKSHOP_BLOCKED_REPO_OWNERS` is out of scope as a host for anything here — see `services/github.py:reject_blocked`.)
2. **Where app UI assets are served from.** This plan proposes the backend at `/api/base-apps/ui/...`, which is authenticated and never fights `apply.sh`, at the cost of a proxy hop per asset. The alternative is copying into `frontend/dist/apps/<slug>/` at install time for edge-static immutable serving, which is faster and couples install to a directory `apply.sh` rebuilds. I recommend the first; the second is a real option and the cost is small either way.
3. **Whether birds' data move happens in step 9 or separately.** The handoff warns that `apps/birds/birdnet.db` and `apps/birds/clips/` live inside the source tree, that `BIRDNET_DB` and `BIRDNET_CLIPS` override it, and that the move "must happen in the same change or the detection database moves with the source". Step 9 moves only frontend files, so it does not force the issue. Deciding to bundle them is his call, not mine.
4. **Whether the standalone per-app build survives.** `frontend/vite.springer.config.ts` builds a birds-only root-mounted app by rewriting `/apps/birds/...` path literals at build time. Once birds owns its own build, that config is either deleted, or moved into the app's repo, or kept in the platform, and only the first two are consistent with "no platform source file may name a specific app".

---

## 10. Where this plan infers rather than measures

Measured, with the command in section 1: every count and per-app breakdown; the three literals over baseline; the absence of `build` and `optimizeDeps` blocks in `frontend/vite.config.ts`; the five CSS files in the current `dist`; react and react-dom being CJS-only at 18.3.1; the kit's `workshop-source` condition and its version; the app pages' external import surface; the residual stage-4b list; `/base-apps` being operator-authed and not edge-blocked; the edge serving `dist` from disk.

Inferred, and each one has a way to settle it:

- **That `optimizeDeps.exclude: ['react','react-dom']` breaks this dev server** rather than merely being unhelpful. The package metadata is measured, the failure mode is reasoning. Settle by adding the exclude to a throwaway config and loading `/` on `:5173`. The plan does not depend on the answer: it does not use `exclude` either way.
- **That React Router rejects a `<Route>` from a second copy of the package** (4.3). Reasoning from `createRoutesFromChildren`, not executed. Settle with the fixture app once step 2 exists. The plan does not depend on it: routes are declared as data regardless, which is better for the mount vocabulary anyway.
- **That one Rollup build with several entries yields one shared React chunk** rather than duplicating it per entry. This is standard Rollup code-splitting behaviour and Vite's default, but it is asserted rather than observed here. `scripts/check_app_loader.py --dist` in step 1 is exactly the assertion that settles it, which is why step 1 ships that check with the change.
- **The 3 second session-provider budget** is a proposal, not a measurement. It should be re-derived from the real load time of the one provider entry that exists once step 9 lands.
- **That `transformIndexHtml` with `order: "post"` sees content-hashed names** in this Vite version (5.4.11). It is the documented behaviour for build-time html transforms receiving the bundle. Settle by reading the generated `frontend/dist/index.html` after the first build in step 1.
