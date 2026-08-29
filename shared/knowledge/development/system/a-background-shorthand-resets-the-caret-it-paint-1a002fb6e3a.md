<!-- author: self -->
`.ws-select` draws its chevron with `background-image` + `background-position`. Any state rule that sets the **`background` shorthand** (`:hover`, `:focus`, `[disabled]`) resets every longhand it omits, so the caret's image becomes `none` and its position `0% 0%`. If the transition-property is also the bare shorthand, that reset ANIMATES: `background-position` is interpolable so the caret slides toward the left edge, and `background-image` is a discrete type so it vanishes at the 50% mark. Un-hover replays it backwards.

Operator saw this as: dropdowns "transform to the left and then back to the right in a really weird way and it'll disappear" (2026-08-14).

**Rule: any control that paints part of itself with a background LAYER must use `background-color` in every state rule, and name the longhand in `transition`.** This bug existed in two places at once (`packages/ui/src/styles/components.css` `.ws-select`, and `frontend/src/styles.css` `.num-in` for the birds app), which is what makes it a class rather than an incident.

Pinned by a guard in `frontend/src/styles.test.ts` that parses the real sheet and fails on a `background:` shorthand or a bare `background` transition in any rule whose selector contains `.ws-select` as a whole token. The guard was verified red against the old CSS before being kept.