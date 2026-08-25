# 0029. App-used styling consolidates into the kit, app-specific styling stays with the app

- Status: accepted
- Date: 2026-08-25

## Context

`packages/ui` (`@workshop/ui`) is the shared styling package. It is not private,
it has no runtime dependencies, its `exports` map points only inside its own
root, and `npm pack --dry-run` produces a self-contained 281 kB tarball of
`dist/` alone. `packages/ui/CONTRIBUTING.md` already treats it as a package with
consumers this repo does not compile: "A version bump is not bookkeeping. It is
the only signal a consumer outside this repo ever gets."

A prior session framed the styling debt as a choice between two options:
rewrite the app call sites onto design-system primitives, or move the classes
the apps depend on into the shared kit. The operator rejected the question. His
position: the kit was always intended to be its own package, published to public
npm so people outside Workshop can use it, so classes that apps depend on and
that are not yet in the kit **belong in the kit** by construction. That is what
the kit is for; it is not a new architectural decision. He also noted the
framing was too narrow: it named two classes when the apps depend on a much
broader set that styles the whole surface. His one constraint is that the
architecture must keep the kit publishable standalone. He is not asking for npm
publishing to be set up now.

The measurement behind this record: app pages use **151 distinct classes across
689 call sites**. 39 are already kit classes, 26 come from `frontend/src/styles.css`
(358 uses, of which `.muted` is 214 and `.card` is 56), 84 come from an app's own
stylesheet, and 2 are dead. The kit meanwhile carries a 92-line block
(`components.css` 3680-3771, 13 `.ws-note-*` classes) that exists only because
the notes app wanted it, and no platform page uses any of them.

Two documents already answer "what may enter the kit", and this record cites
them rather than restating them:

- `frontend/CLAUDE.md` §1: "If you need something not covered, add a primitive to
  `packages/ui/src` + a `.ws-` rule to `components.css`, bump the kit's MINOR
  version and re-run `npm run api:snapshot`; don't one-off it inline." And §"the
  shell + token foundation are live": "when you touch a page that still uses
  legacy classes (`.card`, `.muted`, `.pill`, `.btn`, ad-hoc inline styles),
  migrate the part you touch to `ws-*`/primitives rather than extending the old
  idiom. As pages migrate, delete the now-dead rules they leave behind in
  `styles.css`."
- `packages/ui/CONTRIBUTING.md`: "Apps restyle by **setting tokens**, never by
  editing primitives." Plus the 0.x version rules and the `.ws-*` class name
  contract, where renaming a `.ws-*` class is a breaking change.

## Decision

**`packages/ui` is the shared styling package for Workshop, and the architecture
keeps it publishable standalone. Styling that more than one surface can use is
consolidated into it. Styling that only one app can use stays with that app.**

Four rules follow, and none of them is a per-case judgement call:

1. A generic class that apps depend on and the kit does not have moves **into**
   `packages/ui/src/styles/components.css` under the `.ws-` prefix, with its
   hardcoded lengths and colors replaced by tokens on the way in. It leaves
   `frontend/src/styles.css` in the same change.
2. Where the kit **already has** the primitive, nothing moves. The call site
   changes to the kit class or the React primitive. `.muted` is not added to the
   kit; `.ws-muted` is already there with 299 platform uses.
3. Styling only one app can use lives in that app's own stylesheet, never in the
   kit. This runs in both directions: the `.ws-note-*` block leaves the kit for
   the notes app's stylesheet.
4. Nothing in `packages/ui` may name a specific app, in a class name, a selector
   or a section comment. `scripts/check_no_app_names.py` cannot see this today:
   it skips lines starting with `/*` by design and its literal patterns match
   quoted strings and route paths, not CSS class names. Until that gap is
   closed, rule 4 is on the author.

## Consequences

- The kit-versus-call-site question is settled and should not be reopened per
  class. Which of the four rules applies is decided by two facts that are
  already measured: does the kit have this primitive, and can a second surface
  use it.
- Publishability is a pass today and stays a gate, not a goal: no `private`
  field, no dependencies, no import out of the package, no `exports` target
  outside the package root, `npm run build` green. Any change that would make
  the published tarball incomplete is out of bounds, which is what rules 1 and 3
  are protecting.
- The `.ws-` prefix is the price of admission. A class entering the kit is
  renamed, and per `CONTRIBUTING.md` a later rename of a `.ws-*` class is a
  breaking minor bump, so the name is chosen once at the point of entry.
- Volume is known and it is not small: 438 of the 689 app call sites are rule 2,
  the largest single group. That work is a call-site migration, and
  `frontend/CLAUDE.md` already sequences it as "migrate the part you touch",
  with birds migrated last.
- Deferred: npm publishing itself. `CONTRIBUTING.md` §Publishing has the
  commands; this record does not run them.
