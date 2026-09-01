# Declaring an app's surface

**What it decides:** where an app opens, and which of the platform's per-app
tabs it has. Read by `backend/services/app_surface.py`, resolved once per app
onto the launcher card (`GET /api/base-apps/launcher`), and consumed by both the
launcher grid and the shell's dock, so the two can never send you to different
places.

Written 2026-09-01 for ticket `7d8d733a`. Before it, every app got the same
eight rows and opened on the first of them, which was a chat list. An app with
no bundled UI therefore landed on a generic chat list it never chose, beside
tabs that could never hold anything for it. The operator reported it at least
four times across several chats.

## The block

```json
{
  "surface": {
    "home": "tickets",
    "tabs": ["tickets", "runs", "files", "agents", "chats"]
  }
}
```

Both keys are optional, and so is the whole block.

**`tabs`** is the app's own list, in the order it wants them, drawn from
`chats`, `agents`, `memory`, `files`, `tickets`, `runs`, `deploys`. Absent, the
app takes every tab it can have, in the platform's order.

**`home`** is where it opens. A bare tab id (`"tickets"`), a path under the app
(`"/tickets"`), or a full path (`"/apps/<slug>/files/src"`), which is how an app
opens on a screen of its own. Absent, it is the first tab in the resolved list.

## Where it goes

Most specific first. This is the same shape
[`previewing-an-app.md`](previewing-an-app.md) uses for the preview block, for
the same reason: an app that has a manifest declares things there, and an app
that has no manifest still needs somewhere to say them.

1. **`workshop.app.json` in the app's own tree**, alongside `name`, `services`
   and `preview`. This is the normal place.
2. **The app's project registry record**, beside the `title`, `icon` and `dir`
   it already declares there. A working tree the operator made has no manifest
   to carry the block and no catalog entry either, so this is its only place.
   The registry is instance state, which is correct: which surface one
   installation's app opens on is a fact about that installation.
3. **The built-in catalog card's `route`** (`AppSpec.card` in
   `backend/services/app_registry.py`). An app that ships its own screens
   already declares where they are; that route is its home and it needs no
   second declaration. A `surface` block still overrides it, because it is the
   more specific statement.
4. **Nothing.** The app opens on the first tab the platform can actually serve
   it, with every tab it can have.

## Declaring narrows, it never conjures

A tab is offered only if the app could ever fill it. Tickets, runs, deploys and
agents all resolve through the app's linked working tree
(`services/app_links.py`), so an app with no tree can never have one of them;
files needs a root, declared or linked; chats and memory work off the app's slug
alone and are always available. An app that names `deploys` with no working tree
does not get a Deploys tab, because that is exactly the row-that-apologises this
rule exists to remove.

This is **availability, not population**. A linked app with no tickets yet keeps
its Tickets tab: the next one filed lands there, and a nav that comes and goes
with a row count moves under the reader.

The rule itself is one function, `canHaveContent` in
`frontend/src/pages/apps/tabs/appTabState.ts`, shared with `unlinkedReason` (the
sentence a tab reached by deep link uses to say why it is empty) so the two can
never disagree. The backend supplies the facts it reads.

## What must not happen here

**No platform source file may name a specific app**
(`scripts/check_no_app_names.py`, and CLAUDE.md). The whole point of a
declaration is that an app installed tomorrow behaves correctly with no edit to
the platform. If you find yourself adding `if app_id == ...` to decide a landing
or a tab, that is this document's failure, not a case it did not cover.
