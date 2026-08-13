# The agency color system (2026-08-11)

What shipped, why it is shaped this way, and what deliberately differs from the
designer's handoff. The binding rules live in
[`frontend/CLAUDE.md`](../frontend/CLAUDE.md); this is the record behind them.

Source: `design_handoff_agency_colors` (Agency Palette v7), operator-supplied.

## The problem it solves

Workshop's palette answered *is it healthy?* — the status five. It could not
answer the question that actually matters in a system that runs sweeps, crons
and watchdogs: **who did this, me or something moving without me?**

Three tiers, each with a resting and an active (`-up`) tone:

| tier | meaning |
|---|---|
| `you` | your hand on it: typed, clicked, confirmed |
| `system` | machine work inside a conversation you are present for |
| `autonomous` | it moved with nobody watching |

Active means happening right now. Never read/unread, never severity. It is the
only tone allowed to animate.

## The two invariants

1. **Agency is FILLED, status is OUTLINE.** Fill-vs-outline is the primary
   separator between the vocabularies; hue is secondary. That is what makes it
   safe for a scheme's autonomous hue to sit near its danger hue.
2. **One agency color per surface.** Provenance beats mechanism: an autonomous
   agent calling a tool leaves the surface autonomous.

`running` is the one deliberate exception: filled, and colored by the initiating
tier, because a run describes an actor rather than an outcome. Terminal states
never split by tier.

## How it is built

`packages/ui/src/styles/palette.mjs` is canonical, as before. It now carries:

- `SCHEMES` — eight seeds (three agency hues + per-scheme chroma/lightness
  trims + a surface-chroma trim), a verbatim port of the prototype's array.
- `palette()` + `lightDrop()` — the designer's stops, ported verbatim. Given the
  same triple, this function reproduces the handoff's `tokens.css` exactly.
- `DERIVED` + `engineering` derivations — the ~20 tokens the UI needs that the
  design does not define (hover/active ladders, field backgrounds, card rings,
  border-strong, scrims, shadows), expressed as offsets FROM the designer's
  stops so retuning a stop carries them along.

`scripts/gen-tokens.mjs` emits `palettes.css`, `palettes.gen.ts` and
`tokens.generated.js`. `--accent*` is emitted as a `var()` alias of the `you`
family, so the entire existing app moved onto the `you` tier without a rename.

Verification: 144 assertions in `palette.test.ts` across 8 schemes × 2 modes,
plus a one-off diff of all 384 designer tones against the handoff's `tokens.css`
during the port (384/384 matched; see "Deviations").

## Deviations from the handoff, and why

Everything below was a decision, not an accident.

1. **The attributes stayed `data-palette` / `data-theme`** rather than becoming
   `data-scheme` / `data-mode`. The bootstrap, the hooks, the picker, the RN
   token object and the persisted preference all key on the existing names; the
   handoff's naming carried no behavior.

2. **Tokens are unprefixed (`--you`), not `--ws-you`.** The rest of the system
   is unprefixed, and half the handoff's 24 names already existed here under
   Workshop names (`--bg-inset` for `--ws-inset`, and so on). Shipping both
   spellings would have been two vocabularies for one thing.

3. **Light-mode text tones are solved to AA, so a few differ from the
   handoff's.** Workshop renders `warn` and the tier tones as SMALL TEXT on a
   surface, which the prototype did not (its warn is a border, its label stays
   muted). On our backgrounds the designer's light stops land at 4.0-4.4:1 for
   `warn` and 4.26-4.48:1 for `you` in several schemes, under AA. Every colored
   text tone is now walked toward its mode's extreme until it clears 4.5:1 on
   all three surfaces and its own soft fill, then stopped: hue and chroma are
   untouched. **Dark mode is byte-identical to the handoff; in light mode this
   moves `warn` (0.560 → ~0.516 L, where the pre-agency theme's hand-tuned gold
   also sat) and darkens `you` by one step in seven schemes.**

4. **`--cat-*` stays palette-independent.** The handoff suggests the categorical
   suite give up any hue within ~30° of a scheme's agency hues, or be derived
   from the scheme. Eight schemes cover the wheel, so no fixed set can dodge
   them all, and deriving it would break the older rule that a tag color is a
   stable identity for an agent or a source. It is handled the way status is:
   by treatment. A categorical tag is a rounded rect with a dot in sans; an
   agency chip is a pill with no dot in uppercase mono.

5. **The `you` tier has no `-up` tone.** Your own action is not a thing that
   keeps happening while you watch it, and the resting `you` is already the
   loudest thing on the surface. Where the tier needs one (a run you started) it
   uses the resting tone.

6. **The default scheme is `iris`, not the prototype's `lagoon`.** Iris's base
   (320°) is the nearest of the eight to the violet this system shipped for a
   year, so an operator who never opens Settings sees continuity rather than a
   different product.

7. **The egress question is unanswered.** The handoff raises an autonomous agent
   acting OUTSIDE Workshop (a PR pushed, a message sent, money moved) as a
   bigger deal than internal autonomy, and recommends a border treatment inside
   the autonomous tier rather than a fourth hue. Nothing is built for it. When
   it is, it goes inside the autonomous tier.

## Where it is applied

| surface | treatment |
|---|---|
| user bubble, primary buttons, focus ring, toggles, nav active | `you`, entirely via the `--accent` aliases: those surfaces already used the accent and did not change shape. The handoff's 3px leading marker on the active nav item was NOT taken, it is written for a vertical sidebar and reads as decoration on a horizontal toolbar (operator, 2026-08-11) |
| working dots, "show N steps", tool-call chevrons, delegation chips | `system` |
| run reports and sweep questions in a chat | `autonomous`: 2px rail, soft fill, uppercase mono label |
| runs feed rows | identity dot + agency chip from the job's `trigger`; the row scopes `data-agency`, so its `running` chip takes that tier. **A job with no recorded trigger gets no mark at all** (see below) |
| agent cards | 2px top rail in the agent's tier: autonomous only if it can START ITS OWN WORK (`cron`, `event`, `loop`). A `discord`-triggered agent is answering you, so it is `system`. A failed last run drops the rail for a danger hairline |
| error / timeout blocks | outline only: transparent, hairline in `danger` / `warn` |

## Where the autonomous tone is NOT used

The autonomous tier is the loudest claim in the system: it says nobody was
watching. Over-applying it is worse than under-applying it, because a false
alarm on work you did yourself teaches the operator to ignore the color. So:

- **`agencyFor()` returns null, not a tier, when the record has no trigger**,
  and callers render no dot and no chip. On this box 2749 of ~5500 jobs predate
  the field. Calling those `autonomous` would invent an alarm; calling them
  `system` would assert you were present for work that is by definition not a
  chat turn. An absent provenance mark is honest; a guessed one is not.
- **`discord` is `system`, not autonomous.** An agent you message is answering
  you, with you present. The surface the work arrives on is not the tier.
- **The chat uses `system` for everything except messages the platform posted
  with nobody there.** Working dots, tool rows, "show N steps" and delegation
  chips are all system: you are sitting there watching them. Only a run report
  or a sweep's question takes the autonomous rail, gated on `meta.autonomous`
  written by `run_report.py` / `question_report.py`.
- **Nothing decorative takes the tone.** The Settings picker shows the three
  agency tones in its identity strip, where the point IS the palette; its
  preview dots stay the pinned status tones plus the neutral.

The mapping is pinned in `packages/ui/src/agency.test.ts`.

## Amendment, 2026-08-12: color marks CHANGE, not category

The model above shipped and did not survive contact with the chat log. The
operator's read, verbatim: "some things get the user color and others get system
color... it just seems we have ZERO concept of what these colors mean."

He was right, and the cause is nameable. **The first pass painted a taxonomy
rather than a difference.** "Who did this" is nearly constant inside a
conversation, so the system tone landed on ~90% of the log and discriminated
nothing; meanwhile hue was doing five different jobs (identity, interactivity,
liveness, severity, decoration) with no rule about WHICH element carried it. In
one collapsed tool row it was on the chevron and the icon while the name, path
and time were neutral; in a subagent row liveness moved to the meta; in a merged
group it moved to the name.

The amended theory keeps the same tokens and changes what they mean:

| hue | question | duration |
|---|---|---|
| `--you` | is this mine? | steady, never animates |
| `--live` | is this happening? | standing while it runs, transient for one second on arrival |
| `--sys` | whose work is this? | only in a list where origins mix (the runs feed) |
| status | how did it end? | outline, non-default outcomes only |

`--live` is the third scheme hue under a truer name: it is authored per scheme as
the AUTONOMOUS hue and still comes from there, but what it means on screen is
LIVE. Unattended provenance is carried by SHAPE (a permanent rail and label);
the tint is arrival-only, because a cron report from last week is not live and a
bubble still lit a week later teaches the reader to ignore the colour.

Three tests now govern every hue: could the reader still tell things apart
without it; does the thing it marks vary on this screen; is something else
already saying it. And two rules that come from specific mistakes: **a control
is not content** (carets, icons and text buttons are neutral at rest), and **a
name never carries a state** (`Write` is what the step is, `blocked` is how it
went).

Dropped in the amendment: `running` no longer takes the initiating tier's tone
(the identity dot beside it already said who, so the chip repeated it in a tone
too close to read), and agent cards only wear a rail if the agent can start its
own work.

## The failure this shipped with, and the rule it produced

Mid-build, the app went white and grey for the operator. `palettes.css` scoped
its default block to `:root:not([data-palette])`, so a tab still holding
`data-palette="violet"` — an id that no longer existed — matched no block at
all, and every color token in the system resolved to nothing.

The base block is now unconditional (`:root`), with the per-scheme blocks after
it winning by source order. **A color layer must never be able to leave the app
unpainted**, whatever a stale attribute says.
