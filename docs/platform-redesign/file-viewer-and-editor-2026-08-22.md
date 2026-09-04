# The file tree, viewer and editor

**Status:** decided. Written 2026-08-22 from an operator design instruction, a measured audit of
the live tree, and web research into the available packages.

**What this decides.** Which package we adopt for viewing and editing files, what the difference
between a memory document and a source file actually is, where the editor and the tree live in the
codebase, what the backend serves them, and the order it gets built in.

**What it does not decide.** Where files physically live, which is
[`storage-and-structure-model-2026-08-19.md`](storage-and-structure-model-2026-08-19.md). What the
container around it looks like, which is
[`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md). Where the two touch, this
defers.

**It answers a question those two deliberately left open.** The navigation shell parked
*"the file viewer and editor package. Not written here. Evaluate existing packages"* (§10.5) and
forbade a Files page until a substrate existed (§8.1). The substrate now exists. This is that
evaluation.

---

## 1. The brief

From the operator, 2026-08-22, condensed. These are constraints, not preferences.

1. A viewer and editor for **memories**, which are mostly text documents but may contain code.
2. **Full code editor features belong in the source code viewer/editor**, not in the memory one.
   The operator considered and rejected "features vary by file type": *"i think not actually."*
3. There is a **view/edit mode difference**, and it may be the real distinction:
   *"memories have a view-only mode, and you can edit too. files/source-code doesnt need that."*
4. **Find a package.** Moderately capable, customizable, extensible later. Tiptap if it serves,
   otherwise whatever is best and proven.
5. **Must work on the mobile app and on web.**
6. Follow the design and guidance of the recent app/system redesign.

---

## 2. Two measurements that changed the plan

Both were assumptions until they were checked, and both delete work.

### 2.1 The mobile app is a WebView over the web app

`mobile/app/index.tsx:20-22`, verbatim:

> The whole product surface is the Workshop web app, hosted in this WebView. Native capabilities
> reach the page through `window.WorkshopNative`.

`mobile/` has three screens and its substance is a native bridge, not a native UI. `mobile/
metro.config.js` throws on any `@workshop/ui` import except `tokens.js`, so kit primitives cannot
render there and are not meant to.

**Therefore there is no React Native editor to build.** "Works on mobile" means the web build is
touch-correct and verified on a real device. The standard advice for this problem, which is to run
CodeMirror inside a `react-native-webview` behind an RPC bridge (Joplin and Obsidian both ship
exactly that), describes an architecture this repo *already has for the entire app*. Adopting it
costs nothing because it is already paid for.

Deleted from the plan by this measurement: the RPC bridge, the prebuilt editor asset bundle, the
Expo config plugin that copies it, re-syncing that bundle on every editor upgrade, a second tree
renderer for RN, and a tree library chosen for its React Native support.

### 2.2 The file backend already exists, and there are four of them

`navigation-shell-2026-08-20.md` §8.1 says there is *"no file-browser backend and no per-app source
directory that the UI can address."* **That is now stale** and should be read as history.
`services/app_files.py` and `frontend/src/pages/apps/tabs/AppFilesTab.tsx` landed after it.

What is actually on disk:

| surface | listing | read | write |
|---|---|---|---|
| apps | `apps.py:324`, one level, dirs-first, reports what it filtered | `:340` | `:358` |
| projects | `projects.py:1047`, full recursive walk | `:1073` | `:1088` |
| build agents | `agents.py:755`, full recursive walk | `:781` | `:793` |
| documents | `docs_index.py:95`, flat `rglob` of `docs/` | `:152` any `.md` in the tree | `:169` `docs/` only |

Plus `notes_vault.py` (the Notes app's `$WORKSHOP_NOTES_VAULT`), `knowledge.py` (scoped notes), and `stores.py`.

So the backend work is **consolidation, not greenfield**, and the first rule of this design is that
it must not add a fifth. `app_files.py` is the best-reasoned of the four: it refuses to invent a
second root, it treats path containment as the whole security surface, it filters noisy directories
by name from listings only and reports what it filtered, and it deliberately does not hide dotfiles.
It is the one that gets generalized. The others become callers or get retired.

---

## 3. The package: CodeMirror 6

Adopted. `codemirror@6.0.2` and the `@codemirror/*` packages, all MIT, no licensing condition
anywhere in the tree.

**Why.** It is the only candidate that satisfies all four hard constraints at once: markdown *and*
source code, light *and* dark across eight palettes, web *and* the mobile WebView, and theming that
consumes CSS custom properties rather than fighting them.

- **Mobile is a design goal of the library**, not an afterthought. codemirror.net states it as a
  headline: *"Use the platform's native selection and editing features on phones."* It leans on the
  browser's `contentEditable` rather than reimplementing text editing, which is why IME, virtual
  keyboards and autocorrect work at all. Mobile-specific fixes land roughly monthly through 2026
  (6.43.2, June 2026: Chrome Android select-all, iOS autocapitalize shift handling, Chrome Android
  tap-to-focus scroll).
- **Theming maps onto our tokens.** `EditorView.theme({...})` takes plain CSS strings, so
  `var(--bg-inset)` works directly; the `cm-*` class names are stable and non-generated so external
  CSS can target them; `classHighlighter` emits stable classes for syntax tokens instead of baking
  colors into a JS object; and `Compartment.reconfigure` switches theme at runtime with no teardown.
  This matters more here than anywhere else, because `check_design.sh` fails the build on a raw hex
  in a `.tsx` file.
- **Size.** basicSetup plus one language is ~135 KB gzipped. Monaco measures around 5 MB in
  production.
- **Read-only is two switches, not a fork.** `EditorState.readOnly` refuses modification;
  `EditorView.editable.of(false)` removes `contenteditable` so no virtual keyboard opens on a phone.
  A viewer is the same component as the editor, which is the property the whole design leans on.

### 3.1 Monaco: rejected on the vendor's own answer

The official README FAQ asks *"Is the editor supported in mobile browsers or mobile web app
frameworks?"* and answers, in full: **"No."** Issue #246 (mobile support) has been open since 2016
with no maintainer reply; #1504 since 2019; #4622 (touch selection) was closed unimplemented in
2024. Replit shipped Monaco on desktop and a second editor on mobile until they moved to
CodeMirror. It also costs a hand-written Vite worker configuration we would own forever, and its
React wrapper's last release was February 2025.

### 3.2 Tiptap: rejected, and not for the reason people assume

Licensing is **not** the objection. Tiptap open-sourced ten formerly-Pro extensions under MIT in
June 2025, and everything this project would use is free. The objection is fit, on two counts.

**It is not a code editor, and its maintainers say so.** No line numbers (requested 2019, closed
unimplemented). `code-block-lowlight` re-highlights every code block in the document on every
keystroke because `getDecorations()` scans the whole document. No syntax-aware indent, no bracket
matching, no folding. Tiptap's own issue #547 answers the question directly: *"Just use CodeMirror
(or something else)."*

**And it is the wrong tool for markdown files specifically.** Tiptap is a WYSIWYG editor that
*serializes to* markdown, so the round trip is lossy by construction: `*em*` becomes `_em_`, setext
headings become ATX, list markers and indentation normalize, hard wraps are dropped. Our memory
documents are files on disk under git. **That means a reformatting diff on every save.** For a
corpus that agents read and a human reviews, that is disqualifying on its own.

Milkdown is the strongest WYSIWYG-markdown option and is worth noting for one detail: it solves code
blocks by embedding CodeMirror 6 inside a ProseMirror node view. The competition agrees with the
pick.

### 3.3 What the products we are imitating chose

Obsidian: CodeMirror 6 with the Decoration API. Joplin: CodeMirror 6, shared across desktop, web and
its React Native app. GitHub's code view: an invisible textarea under a virtualized highlighted
overlay. VS Code: Monaco, desktop Electron only. The pattern that has been abandoned everywhere is
the side-by-side split preview; nothing launched after about 2021 leads with it, and we will not
either.

---

## 4. The real distinction: rendered form, not file type

This is the operator's question in §1.3, and his instinct is right. The rule underneath it:

> **A file gets a rendered view when its rendered form differs from its source.
> Otherwise its source is its view.**

- A markdown memory has two forms. `# Title` is a hash and a word as source, and a heading when
  rendered. Both are legitimately "the document", so the file needs **View** and **Edit**.
- `main.py` has one form. There is no rendered `main.py`; syntax highlighting is not a second form,
  it is the source made legible. So there is **no view mode to toggle**, and the operator's
  *"files/source-code doesnt need that"* follows from the rule rather than being a special case.

So it is **one component with two independent axes**, not two components:

| axis | values | who decides |
|---|---|---|
| `form` | `rendered` \| `source` | the user, and only when the file *has* a rendered form |
| `editable` | true \| false | the backend, from whether the path is writable |

`editable` is a fact about the path, not a mode the user selects. A read-only file is not "in view
mode", it is a file you may not write. Conflating the two is what produces the disabled-looking
editor that nobody can explain, and per design rule 10 it must not use a `not-allowed` cursor
either.

### 4.1 "Full code features for source, lighter for memories" is a profile, not a second editor

The operator asked for depth in the source editor and restraint in the memory one. Because the
source form of both is the same component, that difference is **which extensions load**, not which
code runs. One table maps a path to a language and a profile:

| profile | used for | extensions |
|---|---|---|
| `prose` | `.md`, `.txt`, memory bodies | markdown language, soft wrap, history, no line numbers, no folding, no bracket matching |
| `code` | everything else | language from extension, line numbers, folding, bracket matching, indent, search, history |
| `view` | any file, read-only | the profile's highlighting only; `editable: false` so no keyboard opens |

One table, one place, tested as a pure function. Adding a language later is a row, not a design.

---

## 5. Where it lives

### 5.1 The editor is a kit subpath export

`@workshop/ui/editor`, with the CodeMirror packages declared as **optional peer dependencies**.

This is not a new pattern. It is exactly what `@workshop/ui/map` does with `maplibre-gl` and
`@workshop/ui/markdown` does with `react-markdown`: a subpath in the `exports` map, an optional
peer, a heavy dependency that only the consumers who use it pay for. The kit's core entry stays
dependency-free. Per `packages/ui/CONTRIBUTING.md` this is an **additive** change: patch bump,
`npm run api:snapshot`, CHANGELOG entry.

It does not go in `frontend/src` because two surfaces need it (app source and Memory) and a second
copy is exactly the entropy this repo is spending its time removing. It cannot go anywhere mobile
imports it, and it does not need to: §2.1.

### 5.2 Syntax colors are tokens, generated

`packages/ui/src/styles/palette.mjs` says it plainly: *"Nothing else may define a color. Edit here,
then `npm run gen:tokens`."* Syntax highlighting is the first genuinely new use of color in the
system since the agency model, so it goes there and nowhere else, generated across all eight schemes
in both modes.

The role set is deliberately small. Nine, not the thirty a TextMate grammar offers:

`comment`, `string`, `keyword`, `number`, `function`, `type`, `property`, `punctuation`, `invalid`.

Rules: `punctuation` and `comment` are muted, never a hue. `invalid` is the existing danger tone and
is not a tenth color. The remaining six derive from each scheme's own hue geometry so a code file
looks like it belongs in the palette it is being read in, rather than importing somebody else's
theme. Contrast is checked in both modes, like every other token.

### 5.3 The tree is a kit primitive, not a library

**This is the one place this design overrides the research recommendation, so the reasoning is
recorded rather than assumed.** `headless-tree@1.7.0` is the best library in the category and would
be the right answer for a different repo. Here:

- Its decisive advantage over the alternatives is a React Native feature. §2.1 makes that worth
  nothing to us.
- The app already has two divergent tree-ish implementations (`NoteSidebar.tsx` builds one
  client-side from note paths; `AppFilesTab.tsx` is a breadcrumb and a flat list). Adding a third
  shape from a third-party model does not reduce that; replacing both with one primitive does.
- The hard part of this tree is not tree mechanics. It is the interaction the shell doc already
  specifies in §8.3: the Files row rising to become a header, the tree sliding in over the page
  list, a back control that restores the page list **without changing the content**, and a symmetric
  chevron to go back in. A generic tree component gives us the easy half and argues with the half
  that is specified.
- The kit has no tree primitive, and the design system's prescribed route for something new is to
  add one.

So: `FileTree` in `packages/ui`, lazy children through an async adapter, keyboard navigation, the
four required list states, lucide icons, tokens only. **No drag and drop in v1** and no
virtualization until a real directory proves slow, at which point `@tanstack/react-virtual` is the
answer and the primitive's row model is already shaped for it.

The adapter is the important seam. The tree renders *nodes with lazy children*, so a directory and
the memory scope tree are the same component with different adapters. That is
`storage-and-structure-model-2026-08-19.md` §7's own conclusion reused rather than restated: one
continuous tree, and what changes at a boundary is the verb set the node kind already carries.

---

## 6. What the backend serves

One file API, reached through a **workspace root** rather than an app id, built by generalizing
`services/app_files.py`. Its containment rule, its listing filter and its binary and size handling
are already correct and are kept verbatim; what changes is that the root comes from a resolver
instead of being hardwired to an app's linked project.

Held to:

- One level per request, dirs-first then name, reporting what it filtered. Never a recursive walk:
  the two routes that do one are the ones being retired.
- Path containment is the whole security surface. Absolute refused, `..` refused, a symlink
  resolving outside the root refused, and a write can never escape where a read could not.
- Every response carries a real response model, so `schema.gen.ts` describes it and the opaque-field
  ratchet does not regress.
- `writable` is served by the backend per path. The frontend never guesses it, and the
  `path.startsWith("docs/")` rule currently sitting in `Memory.tsx:1172` moves behind this and stops
  being a frontend fact.

Memory keeps its own addressing. A note is a scope and an id, not a directory path, and this design
does not turn it into one. It gets the same viewer, the same editor and the same tree primitive
through a different adapter.

---

## 7. Build order

Each stage is shippable, and none wires a control that does not work.

**P1 — the editor primitive.** `@workshop/ui/editor`, CodeMirror optional peers, the token-driven
theme, the `--syn-*` family in `palette.mjs`, the path-to-language-and-profile table with tests.
Nothing consumes it yet.

**P2 — the tree primitive.** `FileTree` in the kit, async adapter, four states, keyboard nav.

**P3 — one file API.** Generalize `app_files.py` behind a root resolver, typed responses, `writable`
per path.

**P4 — app source, on the primitives.** `AppFilesTab.tsx` rewritten. Deletes the textarea and the
hand-rolled list. This is where the source-code editor becomes real.

**P5 — Memory, on the primitives.** The View/Edit split from §4, the tree in place of the flat card
list, and the shell takeover from `navigation-shell-2026-08-20.md` §3.4/§8.3 which that doc says
Memory needs and which was never built. Memory does not render markdown at all today
(`Memory.tsx:1106` is a `pre-wrap` paragraph), so this fixes that on the way past.

**P6 — mobile verification.** Touch targets, keyboard-avoidance inset inside the WebView, selection
behaviour, on a real device. Small, and it must be performed rather than assumed.

P1 and P3 are independent. P4 needs P1, P2 and P3. P5 needs P1 and P2. P6 needs P4 and P5.

---

## 8. Parked, named so they are not quietly built badly

1. **Obsidian-style hybrid live preview** for markdown. `@codemirror/lang-markdown` ships parsing,
   highlighting and commands and **zero preview decorations**, so every product that has this built
   it themselves. Tables and fenced code blocks are where every decoration-based hybrid leaks. Ship
   plain source with highlighting first; revisit with a real want.
2. **Diff view.** `@codemirror/merge@6.12.2` is official and good, and there is already a
   `GET /api/agents/build/{slug}/diff` with nothing decent rendering it.
3. **Retiring the duplicate file routes.** The two recursive-walk routes and the `documents` pair
   fold into §6's API. Deliberately not in P3, because P3 should not be a migration.
4. **Virtualization**, until a real directory is measurably slow.
5. **Drag and drop** to move files.

---

## 9. Known costs, stated rather than discovered later

- **iOS Safari does not show selection drag handles** in CodeMirror (open upstream issue, February
  2026). Tap to position works; drag a handle to extend does not behave natively. This will be
  visible on the operator's phone and is the most user-facing defect in the pick.
- **Android touch selection has rough edges inside a WebView specifically.** Test on a real Android
  device early rather than at the end. This is the highest-risk unknown and it is why P6 exists as
  its own stage.
- **CodeMirror moved off GitHub to a self-hosted Forgejo** in April 2026; the GitHub repos are
  archived and cannot take new comments. npm is unaffected. Single maintainer, modest bus factor.
