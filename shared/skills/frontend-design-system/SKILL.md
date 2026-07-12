---
name: Frontend Design System
description: "How to build Workshop UI: use src/ui primitives, tokens not hex, lucide not emoji, five status kinds, edit-mode for writes. Use before any change under frontend/."
---

# Workshop Frontend Design System

Binding for every change under `frontend/` (and `mobile/`). Full law: `frontend/CLAUDE.md`.

## The rules that fail the build

1. **Use the primitives** from `src/ui` (`Button, Card, Field, Input, Select, Textarea,
   PageHeader, SectionLabel, Stack, Row, Badge, StatusPill, StatusDot, Tag, Tabs, Toggle,
   Segmented, Modal, ConfirmDialog, Table/TableScroll, StateBlock, Skeleton, ActionBar, Stat`).
   Need something new? Add a primitive to `src/ui` + a `.ws-` rule to `components.css`; never one-off inline.
2. **No inline hex colors, ever.** Color comes from tokens (`var(--accent)`, `var(--muted)`) or a
   primitive. `scripts/check_design.sh` fails on raw hex in `.tsx`.
3. **Status = one of five kinds** (`ok | running | stopped | warn | danger`) via `StatusPill` /
   `StatusDot`. `running` is the only thing that animates. Never invent a status color.
4. **Categorical (agents, sources, series) uses `Tag` + `catFor(key)`**, not status colors.
5. **lucide icons only, never emoji** (14px in labels/meta, 16px in buttons). `check_design.sh` fails on emoji.
6. **Hierarchy from position/scale/weight/color, not borders.** One `PageHeader` per screen; group under
   `SectionLabel`. 4px spacing grid (`--space-1..8`) — never invent `13px`.
7. **Edit-mode for writes.** Config/money commit on an explicit Save via `useEditMode` + a sticky
   `ActionBar`. View prefs (theme, filters, source `<select>`, segmented view switch) commit on change.
8. **Destructive actions get `ConfirmDialog`** naming the consequence. Never bare red text or one-tap delete.
9. **Every list/page ships four states**: loading (`Skeleton`), empty (`StateBlock` with an instructional
   hint), error (`StateBlock variant="error"` + failing endpoint + retry), overflow (`TableScroll`/paginate).
10. **Default theme is dark**; both themes must hit WCAG AA. Cursors are `default`/`pointer`/`text` only.

## Before you claim a UI task done

```
bash scripts/check_design.sh      # from repo root: flags hex, emoji, not-allowed cursors
cd frontend && npx tsc --noEmit   # types must pass
```

Both are part of the completion gate. A UI change that fails either is not done. Design new screens to
the system from the start; there is no "systematize it later" pass.
