# @workshop/ui

The Workshop Design System: a small set of React primitives over a token layer.

It is a **baseline, not a framework**. Use as much of it as you want. Take the
tokens alone and write your own markup, take a few primitives, or build a whole
app out of it. The default is that you don't redesign anything.

## Install

Not published yet.

Inside this repo it resolves as a real npm workspace package: `npm install` at
the repo root links `node_modules/@workshop/ui` to `packages/ui`, and the
`exports` map below decides which subpaths exist. There is deliberately no
bundler alias for it — an alias resolves before the exports map and would let
consumers reach the kit's internals. If imports fail with "cannot find module
@workshop/ui", run `npm install` at the root.

Elsewhere, until it is on npm:

```bash
npm pack --workspace @workshop/ui   # produces workshop-ui-<version>.tgz
npm install /path/to/workshop-ui-0.2.0.tgz
```

## Use

```tsx
import "@workshop/ui/tokens.css";   // the design decisions
import "@workshop/ui/styles.css";   // the component rules
import { Button, Card, StatusPill, PageHeader } from "@workshop/ui";

<PageHeader title="Accounts" />
<Card>
  <StatusPill kind="ok">Synced</StatusPill>
  <Button variant="primary">Refresh</Button>
</Card>
```

Import the two stylesheets once, at your entry point, before your own CSS.

## Entry points

| Import | What | Needs |
|---|---|---|
| `@workshop/ui` | all the primitives | react, react-dom, lucide-react |
| `@workshop/ui/tokens.css` | the whole token layer | nothing |
| `@workshop/ui/styles.css` | the `.ws-*` component rules | nothing |
| `@workshop/ui/tokens.js` | the same tokens as a JS object | nothing |
| `@workshop/ui/map` | the `Map` primitive | maplibre-gl, pmtiles, protomaps-themes-base |
| `@workshop/ui/markdown` | the `Markdown` primitive | react-markdown, remark-gfm |

`map` and `markdown` are separate because they pull heavy libraries. Importing a
`Button` must never drag in a map engine, so those two are optional peer
dependencies behind their own subpaths. If you don't import them, you don't
install them.

`api/ui.api.md` is the generated list of every public export.

## Tokens without React

`@workshop/ui/tokens.css` is plain CSS custom properties and no JavaScript at
all. Any consumer that can load a stylesheet gets the entire Workshop visual
language, in five palettes and two modes, whether or not it uses React:

```css
@import "@workshop/ui/tokens.css";

.my-thing {
  background: var(--card);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  color: var(--fg);
}
```

`@workshop/ui/tokens.js` is the same values as an object, for environments with
no CSS at all (React Native / Expo, canvas, anything computing a color in JS).

## Theming

Two independent axes, both attributes on `<html>`:

| attribute | values | default |
|---|---|---|
| `data-theme` | `light` `dark` `system` | `dark` |
| `data-palette` | `violet` `cobalt` `teal` `plum` `sandstone` | `violet` |

Set them yourself, or use the `useTheme()` / `usePalette()` hooks and the
`PalettePicker` primitive. Set both before first paint to avoid a flash.

`ok`, `warn` and `danger` are byte-identical in all five palettes: status is
meaning, and meaning must not move when someone restyles. Only `running` (the
accent) and `stopped` (the neutral) follow the palette.

## Rules the kit assumes

Consumers get the most out of it by following the same rules it was built to:
no inline hex (use tokens), lucide icons rather than emoji, the five status
kinds, hierarchy from spacing and weight rather than borders. The full statement
is in `frontend/CLAUDE.md` in the Workshop repo.

## Changing it

See [CONTRIBUTING.md](./CONTRIBUTING.md). Short version: the public API is
snapshotted in `api/ui.api.md`, a removed export is a major version, and the
CHANGELOG carries the migration notes.
