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

## Hearing what happens in the Workshop: `events`

An app subscribes to things that happen in the Workshop, outside its own
screens, with an `events` block in its `workshop.app.json`:

```json
{
  "events": {
    "copied": {"service": "api", "path": "/hooks/copied"}
  }
}
```

Each key is an event; `service` is one of the app's own services and `path` is
where on it the event is delivered. While that service is running, every such
event is POSTed to `http://127.0.0.1:<the service's port><path>` as JSON:

```json
{"event": "copied", "at": "2026-10-17T12:00:00.000+00:00",
 "text": "what was copied", "source": {"path": "/chat/abc"}, "owner": 1}
```

with an `X-Workshop-Event` header naming it. `owner` is the id of the user
who raised the event, taken from the request that raised it and never from
its body, or `null` when no user was signed in. It is the same id the proxy
sends as `X-Workshop-User` (below), so an app keeps an event's data with the
user who will later read it. Delivery is best-effort: a
two-second timeout, no retry, nothing queued for a service that is stopped, and
the page that raised the event never waits on it. Answer quickly and do the
work after.

The vocabulary is `EVENTS` in `backend/services/app_events.py`, and it is the
only place an event is named; a subscription to anything else is ignored. Today
it holds these:

| event | when | fields |
|---|---|---|
| `copied` | text was copied anywhere in the Workshop: a keyboard shortcut or menu on a page or in a same-origin app frame, or a copy made from code through the kit's `copyText` | `text`, `source.path` (the page it was copied on) |
| `dictated` | a dictation ended (the client's `POST /api/speech/dictations`); also sent once per entry of a device's past dictation history, with `imported: true` | `id`, `text`, `started_at`, `ended_at`, `status` (`done` or `cancelled`), `places` (`kind` field, chat or clipboard; `name`; `path`), `audio` (`url` on loopback, `mime`, `ms`; kept one hour) or `null`, `imported` |

The web UI raises an event with `POST /api/app-events/<event>`, body the
event's fields. An app's own frontend copying from code should use `copyText`
from `@workshop/ui` rather than `navigator.clipboard.writeText`, or its copies
fire nothing the shell can see.

## Who is asking: `X-Workshop-User`

Every request the runner's proxy forwards to an app service
(`/base-apps/run/<slug>/proxy/<service>/...`, HTTP and WebSocket alike) carries
an `X-Workshop-User` header holding the signed-in user's id, as a decimal
string. The proxy sets it itself and drops any copy the request arrived with,
so an app can trust it to scope its data per user. A request reaching the
service's port directly does not pass through the proxy and carries no
header, so treat its absence as signed out: show nothing and write nothing.
An app that also runs on its own, with no Workshop around it (no
`WORKSHOP_APP_SLUG` in its environment), has no users and can keep working
unscoped.

## Dictation in an app's fields: nothing to declare

The Workshop offers dictation in every text field on every page, including the
fields inside an app's frame, with no work from the app. The mic button goes on the
row of the focused field's last line of text, centred on that line, at the
right end, beside any button of the field's own. It never covers or moves the
field's text, and never changes the field: when that line's text already
reaches the button, it takes the empty row below, or sits just outside the
field's right edge. In a long field whose last line is out of sight, the
line the caret is on stands in for it. Words dictated into a field keep its caret scrolled into
view, as typing does. When the focused element is a
hidden helper (a terminal or code editor taking keys through an invisible
textarea under its cursor), the button goes in the corner of the nearest
enclosing area big enough to be what is on screen instead. Fields that hold a
number (`type="number"`, or `inputmode` numeric, decimal, tel or none) get no
button.

Words arrive as they are said, the way phone and desktop dictation do it: the
words of what is still being said are written into the field and rewritten in
place as they are revised, each time with an `input` event
(`inputType: "insertReplacementText"`), so an app's controlled inputs see
dictation as they see typing. Typing in the field, moving its caret, or
clearing it hands those words to the operator: they stay as they are and new
words start after them. A field the operator cannot see (a hidden helper) is
sent only words that have stopped changing. `data-dictation="off"` also stops
the speech shortcuts being taken from inside that element, so an editor that
binds the same keys keeps them.

Two optional attributes, for when the default is wrong for an app:

- `data-dictation="off"` on a field or any ancestor: no dictation there.
- `data-dictation-anchor` on an element: the button goes in that element's
  corner when focus is anywhere inside it.

## Opening over any page: `overlay`

An app can open as a window over whatever page the operator is on, and put
text into the field the operator was in, with an `overlay` block in its
`workshop.app.json`:

```json
{
  "overlay": {"service": "app", "path": "/", "title": "Picker", "shortcut": "Mod+Alt+V"}
}
```

`service` is one of the app's own services and is required; without it the
block declares nothing. `path` is where on that service the window opens
(default `/`), `title` heads the window (default: the manifest's `name`, else
the slug). `shortcut` is optional: modifiers then one key, joined by `+`, where
`Mod` is Cmd on a Mac and Ctrl elsewhere. It must hold Mod, Ctrl, Alt or Cmd,
or be a function key, because anything else would fire while the operator
types; a shortcut that breaks this is dropped and the block keeps the rest.
Read by `backend/services/overlay_apps.py`.

While the service is running the app is listed by `GET /api/apps/overlays`
(`{overlays: [{slug, title, shortcut, url, events, settings}]}`), and the shell opens it:

- from its shortcut, pressed on any page or inside any same-origin app frame,
  pressed again (or Esc) to close it. The operator can change the shortcut per
  device in Settings > Apps, under "Open over any page".
- from the account menu, below Find in page.

It opens in a centred modal over the page, framed through the runner's proxy
(`/api/base-apps/run/<slug>/proxy/<service>/<path>`). Before it opens, the shell
remembers the field the operator was in: the one focused, else the last one
focused (a menu click takes focus from it). A field inside the overlay itself
is never remembered.

Messages, the same `{v: 1, type}` shape as every shell message, exchanged with
the overlay's own frame only:

| Direction | Type | Body |
|---|---|---|
| app -> shell | `workshop/v1/hello` | none |
| shell -> app | `workshop/v1/context` | `{overlay: true, field: {name} \| null}` |
| app -> shell | `workshop/v1/insertText` | `{text}`: the overlay closes, the field gets its focus back and the text goes in at its caret, as typing would |
| app -> shell | `workshop/v1/closeOverlay` | none |
| shell -> app | `workshop/v1/openSettings` | none: the settings button in the overlay's title row was pressed |

Send `hello` once loaded; `field` is the remembered field's name, or `null`
when there is none, in which case `insertText` only closes the overlay, so
offer something else (a copy button) instead. An app opened on its own, not
as an overlay, gets no `context` answer and should behave as a normal page.

An app with settings of its own declares `"settings": true` in the block. The
overlay's title row then carries a settings button on the right, and pressing
it sends the app `openSettings`, so the app's own settings open from the same
place as the title rather than from a second gear inside the app.
