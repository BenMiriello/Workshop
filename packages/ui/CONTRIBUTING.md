# Changing @workshop/ui

This package is **global scope**. Every app, page and project in Workshop is a
consumer of it; none of them owns it. Changing it changes everything at once,
including out-of-repo consumers that nothing here compiles.

## Who edits this

Platform development only. That is not a permission system, it is a boundary
made of structure, and it holds in three places:

1. **The kit is not inside any app.** It lives at `packages/ui`, a sibling of
   `frontend/`, `mobile/` and `apps/`. Nothing about "I am working on the
   finance page" puts you in this directory.
2. **The reviewer gate enforces `touches`.** An app- or project-scoped ticket
   declares the paths it may write. `packages/ui/**` is not among them, so a
   worker editing the kit from an app ticket fails the gate. Editing the kit
   requires a ticket that names `packages/ui/**` explicitly, which is by
   definition a platform ticket, reviewed as one.
3. **Consumers resolve a package, not a path.** There is no relative import that
   reaches into this directory from an app, and no bundler alias either: the
   web app resolves `@workshop/ui` through the workspace link in
   `node_modules`, so the `exports` map is what decides which subpaths exist.
   `@workshop/ui/controls` does not resolve, and `check_design.sh` fails on a
   relative import back into the kit.

   The mobile app is narrower still: React Native cannot render the react-dom
   primitives, so `mobile/metro.config.js` permits `@workshop/ui/tokens.js` and
   throws on every other `@workshop/ui` import.

Apps restyle by **setting tokens**, never by editing primitives. That is the
supported extension point and it needs no changes here.

## Versioning: this package is 0.x

The major is zero, and npm gives that a specific meaning: `^0.2.0` resolves to
`>=0.2.0 <0.3.0`. **The minor is the breaking position.** So:

| | 1.x-style | here, at 0.x |
|---|---|---|
| breaking change | major | **minor** (0.2.0 → 0.3.0) |
| additive change | minor | **patch** (0.2.0 → 0.2.1) |
| fix | patch | patch |

Consumers pin `^0.2`, which locks them out of the next breaking release exactly
the way `^1` would. When the API is settled enough to promise stability, go to
1.0.0 and the normal rules resume.

## The rules for a change

**Additive change** (a new primitive, a new optional prop, a new token):

1. Make the change.
2. `npm run api:snapshot` and commit `api/ui.api.md`.
3. Bump the PATCH version in `package.json` (0.x rule above).
4. Add a CHANGELOG entry.

**Breaking change** (an export removed or renamed, a required prop added, a prop
whose shape changes, a token deleted, a `.ws-*` class renamed):

1. Make the change.
2. Bump the MINOR version (0.x rule above), and the `kit:` pin in
   `backend/apps.json` plus `DEFAULT_KIT` in `backend/services/manifests.py`.
3. Write a CHANGELOG entry **with migration notes** — what moved, what it is
   called now, and what a consumer has to do. Someone else's build is broken
   until they read this; it is the whole artifact they get.
4. `npm run api:snapshot` and commit `api/ui.api.md`.
5. Update every in-repo call site in the same commit.

`bash scripts/check_design.sh` fails when the public surface moved and
`api/ui.api.md` did not. That check exists because of a real incident: `Menu`
changed from an `items` prop to `MenuItem` children with no version bump.
In-repo call sites were updated, so nothing here failed; out-of-repo apps
resolved the kit as source through a path alias, so nothing compiled them
either. They broke silently and the version still said `^1`.

A version bump is not bookkeeping. It is the only signal a consumer outside this
repo ever gets.

## What counts as breaking

Not obvious in every case, so:

| Change | Breaking? | Bump (0.x) |
|---|---|---|
| Remove or rename an export | yes | minor |
| Add a required prop | yes | minor |
| Change a prop's type (narrow it) | yes | minor |
| Rename a `.ws-*` class | yes (consumers style against these) | minor |
| Remove or rename a CSS token | yes | minor |
| Move an export to a subpath | yes | minor |
| Change a token's VALUE | no (that is the point of tokens) | patch |
| Add an optional prop | no | patch |
| Add a primitive or a token | no | patch |

## Commands

```bash
npm run typecheck    # tsc, no emit
npm test             # the palette contract tests (54 assertions)
npm run build        # dist/: ESM + .d.ts + tokens.css + styles.css
npm run gen:tokens   # regenerate the color layer from palette.mjs
npm run api:snapshot # rewrite api/ui.api.md
npm pack --dry-run   # exactly what would publish
```

Run these from `packages/ui/`, or from the repo root with
`npm run <name> --workspace @workshop/ui`.

## Color

Never hand-edit `palettes.css`, `palettes.gen.ts` or `tokens.generated.js`. They
are generated from `src/styles/palette.mjs`. Edit the model, run
`npm run gen:tokens`, run the tests. `check_design.sh` fails on a stale
generated file.

## Publishing

The package is not on npm yet. When it goes:

```bash
npm run build && npm run api:check && npm test
npm publish --access public
```

`files` limits the tarball to `dist/` plus the changelog and readme, and
`npm pack --dry-run` shows exactly that. Nothing in `exports` points outside the
package root, so the published artifact is self-contained: this was the specific
bug that made the old version pin meaningless, since `exports` mapped
`./tokens.css` to `../styles/tokens.css`, a path that would simply not exist in
a published tarball.
