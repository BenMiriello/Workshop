# 0012. Color schemes are generated from one model; status color is fixed across them

- Status: accepted
- Date: 2026-08-07
- Depends on: `frontend/CLAUDE.md` (the Design System law)
- Supersedes: the "ONE brand accent, not a user choice" clause in the previous
  `tokens.css` header, and the earlier idea of multiple simultaneous accent
  colors (requested once, explicitly withdrawn by the operator).

## Context

Workshop shipped one theme: a hand-tuned violet, light and dark, written
directly as ~60 hex literals per mode in `tokens.css`. The operator asked for a
fixed set of five selectable color schemes for personalization, grounded in
color theory rather than taste, with light and dark handled thoughtfully for
every component, and with the current look retained as one of the options.

Three ways to do this:

1. **Hand-write ten themes.** 10 x ~60 values. Every future token addition is
   ten edits, and nine of them are the ones that get forgotten. Contrast becomes
   unverifiable in practice.
2. **Runtime CSS math.** Express tokens as `oklch(var(--l) var(--c) var(--h))`
   and let each palette set three seeds. Elegant, but gamut mapping then differs
   per browser engine, and React Native cannot read CSS variables at all, so the
   mobile tokens would have to be derived some other way anyway.
3. **Generate.** One model in JS; a script emits the CSS and the RN tokens from
   it. Both consumers get provably identical numbers.

## Decision

**(1) Color is generated from `frontend/src/styles/palette.mjs`.** That module
is the only place a color may be defined. `scripts/gen-tokens.mjs` emits
`palettes.css`, `palettes.gen.ts` and `tokens.generated.js`. `tokens.css` keeps
only theme-independent scales and `@import`s the generated CSS. `check_design.sh`
runs the generator in `--check` mode, so a stale artifact fails the gate.

**(2) The model is an OKLCh tonal ramp, shaped like Material 3.** Fixed tones
read off a hue; neutrals are the same hue at near-zero chroma, so surfaces are
tinted rather than dead gray and the tint agrees with the accent. OKLCh instead
of M3's HCT because it is perceptually uniform, ships natively in CSS, and is
where Tailwind v4 and Radix landed. The ramp tones were *measured from the
existing violet theme*, so the current look is an instance of the model, not an
exception to it.

**(3) A palette is five numbers, not a stylesheet.** Brand hue, neutral hue, and
two chroma scales. Adding a sixth scheme is a five-line seed.

**(4) `ok` / `warn` / `danger` are byte-identical in all five palettes.** Status
is meaning, and meaning must not move because someone restyled their tool. They
are solved once against every palette's surfaces so one value is correct
everywhere. Only `running` (the accent) and `stopped` (the neutral) follow the
palette — those two *are* "brand" and "quiet" by definition.

**(5) Readability is solved, not assumed.** Accent and solid tones are found by
walking lightness until they clear their contrast target against `--bg`,
`--card` and `--bg-inset`. This is required, not decorative: at a fixed
lightness a teal is far lighter to the eye than a violet, so a fixed tone gives
one accessible palette and four that quietly fail. `--accent-fg` follows M3's
on-primary rule (whichever end of the ramp contrasts more), which is why it is
near-black in Teal light and white in Violet light.

**(6) The categorical suite stays palette-independent.** A tag color identifies
an agent or a source; an identity that changes when you switch scheme is not an
identity.

## Consequences

- Ten schemes are covered by 53 executable assertions
  (`src/styles/palette.test.ts`), including the AA/1.4.11 floors and the
  "status does not move" invariant. Previously contrast was eyeballed.
- Two colors changed in the default theme, both because they were wrong:
  light-mode `--warn-solid` (a gold dot at 2.63:1 on white) and light `--accent`
  (one step darker; the original violet failed AA on `--bg-inset`). The
  perceptual difference in the accent is nil, and the test pins that it stays
  the same violet.
- `--accent-fg` is no longer always white, so nothing may hardcode `#fff` on an
  accent fill. One rule in `components.css` did (the primary-button spinner);
  fixed.
- Editing `tokens.css` to change a color no longer works. This is a real
  footgun for a future agent, mitigated by header comments in every generated
  file, the `--check` gate, and the theming section of `frontend/CLAUDE.md`.
- Mobile (`tokens.js`) gains `makeTheme(scheme, paletteId)` and the full palette
  table, so the Expo app can offer the same five schemes without a second model.
