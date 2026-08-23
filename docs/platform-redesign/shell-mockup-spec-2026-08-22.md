# The shell mockup, as measurements

**Status:** reference. Extracted 2026-08-22 from
[`navigation-shell-source/workshop-shell-mockup.dc.html`](navigation-shell-source/workshop-shell-mockup.dc.html),
whose README says it "is the artifact the implementation is measured against", plus
`navigation-shell-source/HANDOFF.md` and the three transcripts.

**Why this file exists.** The mockup is 62KB of inline-styled HTML plus a logic class, and it needs a
design tool runtime to render. So it was repeatedly not read, and surfaces got built from the prose
design doc alone, which describes intent rather than geometry. On 2026-08-22 the operator found the
file browser and the mobile menus wrong for exactly that reason. This is the geometry, in one place,
so nobody has to re-derive it.

**Precedence.** [`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md) is the model of
record and WINS on *what to build*. This file wins on *what it looks like and how it moves*. Where
they conflict on content, section 7 below records it. The mockup's colours are NOT a spec (design doc
§7.3: no hex in the mockup appears in `palettes.css`) so every value below is given as a token.

---

## 1. Mobile: bar one

Fixed 52px, `padding: 0 14px`, `gap: 9px`, background `--bg-elevated`, bottom hairline `--border`.
Four items, left to right, and nothing else. No hamburger, no Home button, no back control, no ref.

1. **Place tile.** 38x38, radius 9. Opens the PLACE MENU. House glyph 17px at Home, else the app's
   uppercase letter at 15px/600. It is always accent-soft: it is the current place, not a selection.
2. **Place name + caret**, one tap target, height 38, `padding: 0 8px 0 4px`, radius 9, pressed
   background `--bg-inset`. Name 17px/600, `letter-spacing: -.015em`. Caret is a 13px chevron-down,
   `--faint`, `transition: transform .24s cubic-bezier(.4,0,.2,1)`.
3. **Search**, 38x38, `margin-left: auto`. Only when a search backend exists (design doc §8.4). Until
   then it is not drawn, because a dead control is worse than an absent one.
4. **Avatar**, 32x32, radius pill, two-letter monogram at 11px/600.

### 1.1 The caret is the one deliberately unconventional element

The caret does **not** open a menu. It expands and collapses the PAGE TOOLBAR, and it carries that
state: 0deg (pointing down) is closed, 180deg is open. Design doc §11 accepts this knowingly.

Default is OPEN on every page. A manual toggle holds until you change place or page, then reverts to
open. It force-collapses to 0 while a document is open. **It must never default to closed** (operator
reversal, transcript 2 turns 37 to 38).

## 2. Mobile: the page toolbar

40px plus a 1px hairline (the mockup animates the wrapper to 41px for that reason; implement 40 plus
hairline). `padding: 0 14px`, `gap: 18px`, `align-items: stretch`, horizontal scroll with the
scrollbar hidden. Labels only: no icons, no counts. The wrapper animates
`height .24s cubic-bezier(.4,0,.2,1), opacity .18s ease`.

Its bottom hairline is STRONGER than bar one's, so the two bars read as one block separated from the
content.

**Tab, and these three rules are each an explicit reversal, so do not re-break them:**

| | selected | unselected |
|---|---|---|
| font-weight | 600 | 400 |
| color | `--fg` | `--faint` |
| border-bottom | 2px solid `--accent` | 2px solid transparent |

Every tab is `display:flex; align-items:center; padding-bottom:2px`, and the parent is
`align-items: stretch`, so each tab spans the full 40px and its underline lands on the BAR's own
bottom edge. Only `color` transitions, at .16s.

- **Nothing is raised on selection.** No background, no pill, no radius, no transform, no shadow, no
  margin shift. Design doc §11 lists raising it as rejected.
- **All labels stay on one baseline.**
- The 2px `padding-bottom` is what makes the underline read as attached to the label.

## 3. Mobile: the place menu

It is a **dropdown**, not a drawer and not a sheet. An earlier left slide-over was replaced in
session 2; a `wsDrawerIn` keyframe survives in the mockup as dead code. Do not build a drawer.

- **Scrim** covers `top: 86px` to the bottom (34 status + 52 bar one), so it **never covers bar one**.
  Fade in 160ms. Tapping it closes.
- **Panel** sits 6px below bar one's hairline, `left: 10px`, so it is anchored under the TILE at the
  left edge, not centred and not under the title. Width 300px in a 390px frame. `max-height: 520px`,
  `overflow: auto`, radius 14, `padding: 10px`, row `gap: 2px`, elevated surface, hairline border,
  a deep shadow.
- **Motion:** rise 10px plus fade, 200ms `cubic-bezier(.4,0,.2,1)`. Not a slide from an edge.

**Contents.** A Home row, then a section label, then one row per app.

- Home row: height 48, radius 9, `gap: 11`, leading 32x32 radius-9 tile with a 16px House glyph,
  label 15px. Accent-soft background when you are at Home.
- Section label: mono 12px, `letter-spacing: .08em`, uppercase, `--faint`, `padding: 14px 8px 8px`.
- App row: height 52, radius 9, `gap: 11`, leading 32x32 radius-9 tile at 14px/600 carrying the app's
  letter, name 15px. Accent-soft background on the current app.

**Not in the menu:** no ref or branch pill on any row (design doc §8.2), no Settings row (the avatar
owns settings), and no Agents group (design doc §6.1, agents leave the dock). The mockup draws all
three; all three are superseded.

## 4. Mobile: the file browser

**The tree IS the page content.** No takeover, no column, no overlay. It drills one level at a time.

### 4.1 The Up bar

Its own 40px full-width row, directly below the page toolbar and above the list. Not floating, not in
bar one. Hidden while a document is open. Bottom hairline is the FAINT one, not the standard border.

**The whole bar is the tap target.** Contents: a 13px arrow-up plus the literal word "Up" in
`--accent` mono 12px, then the trail in `--faint` mono 12px, segments joined with " / ". Individual
segments are NOT tappable.

At the root of the directory, Up leaves the directory page entirely.

### 4.2 The rows

**52px, full-bleed, `padding: 0 14px`, `gap: 11px`, ZERO indentation, hairline between rows.** A
phone list does not indent; depth is expressed by drilling, which is the whole point of the pattern.

| | folder | file |
|---|---|---|
| icon | 17px folder, `--muted` | 17px file, `--muted` |
| label | 15px, `--fg`, **sans** | 15px, `--fg`, **mono** |
| meta (right, mono 12px `--faint`) | **child count** | **relative time** |
| chevron | **yes**, 14px, `--faint` | **no** |

Sans-versus-mono is how a folder and a file are told apart. Keep it.

Empty state is centred, `padding: 40px 24px`, 14px `--faint`.

## 5. Desktop: the directory takeover

**It happens entirely INSIDE the 252px page column.** It does not slide over the content column.
There is **no scrim and no shadow** (a shadow was added once and rejected: design doc §3.4 and §11).

- The title row and the page list BOTH `translateX(-100%)` together, so the column travels as one
  piece.
- The tree panel is `position: absolute; inset: 0` and travels `translateX(100% -> 0)`.
- Both at **340ms `cubic-bezier(.32,.72,0,1)`**, in both directions.
- The panel stays mounted after closing so the exit animates.
- There is no z-index anywhere; the tree paints above the list by DOM order.

**Trigger:** tapping the page you are ALREADY on opens its tree. The chevron to go back in appears
only on the selected directory row and only while the tree is hidden. Chevron in, back out, symmetric.

**Tree panel interior:**
- Back header 46px, `gap: 9`, `padding: 0 12px`, bottom hairline, the whole row tappable. 15px
  chevron-left in `--accent`, title 14px/600, and the ref as PLAIN MONO TEXT at the right, not a pill.
- Directory toolbar 34px, `gap: 6`, `padding: 0 10px`: a **New** control (28px, radius 6, bordered,
  12px) and a **Filter** control (same box, no background and no border, `--faint`). This is the
  visible expression of design doc §3.5: a directory supports file operations, a collection does not.
- Scroll region `padding: 6px 6px 12px`.

**Tree row:** 27px, radius 6, `gap: 7`, `padding-right: 8px`, indent `8 + depth * 15` px, 12px icon at
`opacity: .75`, label 13px ellipsized, optional 6px amber dirty dot.

| | folder | file |
|---|---|---|
| font | sans | mono |
| color | `--fg` | `--muted` |

No disclosure triangles anywhere. Active row is accent-soft.

## 6. The file viewer and editor

**The mockup's editor is a PLACEHOLDER and is explicitly not the spec.** Operator, 2026-08-20:
"we are gonna be looking for and finding other packages... We're not gonna build that all custom
ourselves." Design doc §8.1 and §10.5. The component is CodeMirror 6, per
[`file-viewer-and-editor-2026-08-22.md`](file-viewer-and-editor-2026-08-22.md).

**The CHROME around it is still specified, and that part is worth matching:**

- **Desktop header** 46px, `gap: 9`, `padding: 0 20px`, bottom hairline: the path in mono 12px
  `--faint`, the filename in mono 12px `--fg`, a 6px amber dirty dot, then right-aligned **Discard**
  (27px, radius 6, quiet) and **Save** (27px, radius 6, filled accent, 13px/600).
- **Mobile header** 44px, `padding: 0 14px`: a combined back-plus-filename control (13px chevron-left
  plus the name, mono 12px, `--accent`), the dirty dot, then **Save** (28px, radius 6, filled accent).
  **No Discard on mobile.**
- The editor is a full content takeover on mobile: the page toolbar collapses to 0, the Up bar hides,
  and any bottom action bar hides.
- Gutter: 56px desktop, 40px mobile, right-aligned, mono `13px/22px`.

The mockup's own editor has no tabs, no view/edit toggle, no read-only state and no rendered markdown.
Those absences are a consequence of it being a placeholder, NOT decisions. The rendered-versus-source
rule for markdown is decided in `file-viewer-and-editor-2026-08-22.md` §4 and stands.

## 7. Where the sources disagree, and who wins

1. **Menu contents.** Mockup and HANDOFF: Home, Apps, Agents, with ref pills. Design doc §4.3, §6.1,
   §8.2: no Agents group, no ref pills. **Design doc wins.**
2. **Menu anchor.** HANDOFF and design doc say "under the title"; the mockup anchors under the tile,
   which is the actual trigger. **Mockup wins**, the prose is loose.
3. **Bar one.** HANDOFF §3 still describes a Home button, an app chip with a ref, and a drawer. All
   three were reversed later in the same file. **Read HANDOFF's "final form" section, not its §3.**
4. **Files first-tap.** The mockup jumps straight to the tree on a first tap of Files, an artifact of
   Files having no list data in the mock. **The second-tap rule is the spec.**
5. **Motion.** The shipped implementation deliberately uses the kit's `--dur-slow` and `--dur-base`
   rather than the mockup's exact 300/240. Design doc §13.8 records that. **Do not "fix" it back.**
6. **Colour.** The mockup is a layout and behaviour spec, never a colour spec. **Tokens win, always.**

## 8. Drawn in the mockup, and explicitly NOT to be built

Design doc §8 and §11. Do not rebuild any of these from the mockup: a Files page as a dock or page
entry; the custom file editor; the ref or branch pill anywhere; the Files search badge; the search
palette and Cmd+K until a search endpoint exists; the agent as a dock tile; per-app page sets that no
installed app has; "Work" as the noun (it is Tickets); the Activity entry and the Scheduled tab; the
dashboard composer; every hex in the file; and the assumption that dark is the only theme.
