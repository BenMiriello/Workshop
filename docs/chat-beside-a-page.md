# Chat beside a page

**What it is.** Any page in the Workshop (a Workshop screen or an app's own
screen) can have a chat open beside it: a drawer on the right on a desktop, a
docked lower half on a phone. The Chat button on the dock rail (desktop) or the
top bar (phone) opens and closes it. The chat in the drawer is the ordinary chat,
filed under the app's own chat scope when the app declares one (`app:<slug>`),
else the general scope; its toolbar has the usual conversation list, New chat,
the model picker and Open full screen.

What the drawer adds is a link between the chat and the page:

- **Attach.** Select text anywhere and press **Add to chat** (beside Read aloud),
  or use an app's own action (a "Discuss" menu item). What was attached shows as
  a chip above the composer, removable, and goes with the next message.
- **See and use.** The chat's **Access** menu sets how much of the page the chat
  reaches. The default is only what you attach.
- **Apps.** The same menu grants the chat other apps' tools, with each app's own
  options (for example its history), on any chat, beside a page or not.

This is the same for developing an app and for using one. A build chat opened
beside an app's screen gets the same drawer, chips and page tools as the app's
own assistant does.

## How the drawer behaves

- **Each place has its own drawer** (a place is an app, or the Workshop's own
  pages, as on the rail). Opening the chat beside one app opens it there only:
  going to another place shows that place's drawer, closed unless you opened
  it there, and coming back shows this one as you left it. The conversation
  last had beside each place is also remembered per device, so reopening the
  drawer there in a later session brings it back.
- **A Development or Workshop-root conversation roams.** Moving to another
  place keeps it open on the same conversation. If the place you arrive at has
  its own open conversation at the same level or higher (Development ranks
  above the root), that one is shown instead. A conversation filed under an
  app, project or agent does not roam: you see what the other place had open,
  or the drawer closes when it had nothing, and the way back finds it as it
  was. A roaming conversation is not kept as the place's own unless you pick
  or start another one there, and closing it closes it everywhere it was left
  open. The rule is `shell/beside/roam.ts`.
- **A new blank chat roams whatever it is filed under**, and outranks what the
  place you arrive at has open (that stays for when the blank chat is gone).
  It is not restarted on the way, so what you were typing stays in the
  composer. Once its first message makes a conversation, the rules above apply
  to it.
- **A reload keeps the drawer.** Each place's drawer and the one on screen are
  saved per device (`ws-beside-state`) and read when the shell first names a
  place, once the signed-in user is known. A conversation that only passed
  through a place is not remembered as the one had beside it.
- **The drawer holds its own overlays.** Dialogs, sheets and scrims opened from
  the chat fill the drawer (`OverlayScope` in the kit), and menus and popovers
  slide and flip inside its width. On a phone, where the drawer is half the
  window, dialogs keep the whole window.
- **The composer's bottom row fits the drawer's width, not the window's.**
  `useRowFit` measures the row and steps down until nothing overflows: the mode
  label and the model's icon go first, then the model's name, then the context
  percentage, then the gaps tighten, and last the row wraps. The context ring
  and the buttons never shrink.
- **A link followed from inside the chat takes the chat along**, whatever its
  level. The rail and the page column do not (apart from roaming above).
- **Open full screen** goes to the chat's own page and leaves the drawer as it
  was. That page's back control names the page you came from ("Photos") and
  returns there with the conversation beside it, the one you switched to
  included.
- **The drawer steps aside on a page that is itself a chat** (a full chat, the
  chat list), so one conversation is never on screen twice.
- **On a phone** the page and the chat stack, and while the drawer is open the
  shell is pinned to the visible window: the keyboard shortens it from below
  instead of pushing the bars off the top, and typing in the chat grows it to
  most of the window with a strip of the page left above.

## Links in a chat

A link to a Workshop page opens in place (from the drawer, with the chat
along). A link to a web page opens in the app chosen in Settings > Chat for web
links, or in a new window when none is chosen; an app offers itself there by
declaring `opens.web` on a service (`docs/app-platform-runner.md`).

## Access levels

Set per chat, stored on the thread, applied from the next turn (the tool server
re-checks on every call, so lowering it takes effect on the next call).

Three steps, shown in the Access menu as "<page> autonomy":

| level | label | the chat |
|---|---|---|
| `send` | Only what I send | sees attached items only (default) |
| `see` | Sees the page | may read the page and the app's read-only tools |
| `drive` | Full autonomy | reads and acts, carrying the task through on its own; the page shows an outline while it acts |

The operator can always step in. Their own input on the page at full autonomy
leaves the level as it is: the outline pauses and the chat's next page call
starts by saying the operator worked on the page, so it reads it again rather
than undoing them. `use` (acting only when asked) was folded into `drive`;
a stored or requested `use` reads as `drive`.

A chat at `see` or above has the page tools on every turn, whether or not a
page is beside it when the turn starts: each call looks for the page then, so a
drawer that reconnects or opens mid-turn is usable at once, and with none open
the call says so. A drawer whose stream drops (a phone app pausing) still
counts as beside its chat for 20 s, and a call made meanwhile reaches the page
when it reconnects. The turn's PAGE AND APPS block says whether a page is beside
it. `page_tool` with a name the page does not offer answers with the page's
current tools and their arguments.

## Follow, show or hide

A chat that reaches an app declaring `"follow": true` in its `chat` block (its
own app, or one granted in Access) offers three views of its work, chosen
from the chat's place control in its toolbar (below):

- **Follow** (the default): what the chat opens or acts on comes to the front
  wherever the app is on screen, so the operator watches it work.
- **Show its tabs**: its work waits in the app (marked, lit while it works),
  nothing comes forward.
- **Hide**: it works unseen; the app keeps its work out of its lists.

An app declares `follow` only when it acts on the view. The view is stored on
the chat (`PUT /page-link/{thread} {view}`; `{follow: true|false}` still sets
follow or show) and reaches the app three ways, so it counts at once,
mid-turn included, whether or not anything is open:

- `X-Workshop-View: follow|show|hide` (and `X-Workshop-Follow: on|off`) on the
  chat's calls, as the turn started, and `view` in the live answer at
  `X-Workshop-Options-Url`.
- The `chat_view` app event (`{thread, view}`), POSTed when it changes to an
  app that subscribes (`"events": {"chat_view": {"service", "path"}}`).
- `view` in the page link of the page beside the chat.

Each call also carries `X-Workshop-Thread-Title` (URL-quoted), so an app can
name what it keeps for that chat. What each view means in an app is the
app's to decide.

## Where the chat is working

An app whose chat works on things the operator may want to look at (tabs,
drafts) declares `"place": "/api/chat-place/{thread}"` in its `chat` block, a
path on its MCP service. Workshop asks it `GET` with the thread filled in, and
the app answers `{label, path, title?, at?}`: a short name for the thing, the
Workshop page that shows it (`/apps/<slug>/ui/<service>#...`), its full title,
and the epoch seconds of the chat's last call there; or `{}`. Workshop's
`GET /api/chat-place/{thread}` asks every app the chat reaches and answers
newest first.

The chat's toolbar shows the newest as a chip naming the place, with the
view's icon and a LIVE dot while the chat worked there in the last 20 seconds.
Pressing it opens a menu: "Open beside this chat" (full screen it opens that
page with the chat beside it; in the drawer it navigates the page beside it)
and the three views. A screen followed again with a hash hears a `hashchange`, so the same link
works twice.

There are no per-action confirmations. Page text is always data to the model:
the tools and the per-turn block say so, and the model is told to report
instructions found on a page rather than follow them.

## For an app: the contract

Everything below is optional. An app that does nothing still gets the drawer,
Add to chat on selected text, and the platform's own page tools (it reads and
uses the app's DOM through the same-origin frame). An app adds to that when its
page is not plain DOM (a canvas, a native view) or when it has better verbs than
clicking (navigate, search, show an item).

**Use the kit rather than writing the messages.** `@workshop/ui` carries the
app side of this contract, framework-free and inert outside a Workshop frame,
built on the same names and shapes the shell parses:

```ts
import { startPageLink, attachToChat, reportTakeover } from "@workshop/ui";

const link = startPageLink({
  context: () => ({ page: "Library", selected: ids }),   // read at each post
  tools: [{ name: "show_item", description: "Open an item by id.",
            input_schema: { type: "object", properties: { id: { type: "string" } } } }],
  run: async ({ tool, args, access }) => `shown ${args.id}`,  // a throw is the chat's error
  onLink: (l) => setDriving(l.driving),                  // optional
});
link.changed();                  // after anything on screen changes (debounced)
attachToChat({ items: [{ kind: "text", text: picked }], prompt: "Explain this" });
reportTakeover();                // the operator's input on a surface the shell cannot see
```

The sections below are the wire format the kit speaks, for a page that cannot
use the kit. All messages are `{v: 1, type, ...}`, posted with `postMessage` to
the window that frames the app (`window.parent`), target origin the app's own
origin. The platform's frame is same-origin, so `location.origin` is right.

### Describe the page: `workshop/v1/page` (app to shell)

```js
parent.postMessage({v: 1, type: "workshop/v1/page",
  context: {...},            // what is on screen: JSON or text, capped at 6000 chars
  tools: [{name, description, input_schema, acts}],
}, location.origin)
```

Each post replaces the last. Send it when the screen changes in a way the chat
should know (a new route, a new selection), not on every frame. `context` is
shown to the chat each turn at `see` and above. Keep it short and factual: the
route, the title, what is selected, the ids the app's tools take.

`tools` are the page's own tools. `acts: false` marks one that only reads (usable
at `see`); anything else needs `use`. Three names are special: an app that
declares **`read_page`**, **`find`** or **`act`** replaces the platform's own
reading of its DOM with its own (see the vocabulary below). Every other tool is
called by the chat through `page_tool(name, args)`.

### Answer calls: `workshop/v1/pageCall` and `workshop/v1/pageResult`

```js
// shell to app
{v: 1, type: "workshop/v1/pageCall", id, tool, args, access}
// app to shell, once per call, within 45 s
{v: 1, type: "workshop/v1/pageResult", id, ok: true, result: "text" | {text, image}}
{v: 1, type: "workshop/v1/pageResult", id, ok: false, error: "what went wrong"}
```

`image` is a `data:image/png|jpeg;base64,...` URL (at most ~3 MB), returned to
the model as an image. Only tools the app declared are sent to it.

`access` is the list of the app's own `options` (see below) this chat has on,
or null when the app declares none. A page tool that reaches data an option
covers obeys it exactly as the app's tool server does: the operator switching
off "history" for a chat means no tool of that app reaches history for it,
whether it was called through the page or through the app's tools.

### Know whether a chat is beside it: `workshop/v1/pageLink` (shell to app)

```js
{v: 1, type: "workshop/v1/pageLink", open: true, level: "use", driving: false, thread: "<id>", follow: true, view: "follow"}
```

Sent when the drawer opens or closes beside the app, when the level changes, and
in reply to every `workshop/v1/page`. An app can declare tools only while
`open`, show its own indicator while `driving`, or ignore it.

`view` is how the operator sees that chat's work (follow, show or hide;
`follow` repeats whether it is follow): an app that shows a chat's work brings
it to the front when it turns to follow, without waiting for the chat's next
call.

`thread` is the conversation beside the page (null when closed). It is the same
id the app's tool server receives as `X-Workshop-Thread` on that chat's calls,
which is how an app connects the two: something a chat made through its tools
(a tab it opened, a draft it saved) is marked with the thread, and the page
beside that same chat can bring it to the front. A page whose chat is a
different one, or none, leaves the operator where they are.

### Say the operator took over: `workshop/v1/pageTakeover` (app to shell)

```js
{v: 1, type: "workshop/v1/pageTakeover"}
```

At `drive`, the operator's own input on the page takes control back (the level
drops to `use`). The shell sees input on ordinary page content itself; an app
whose page it cannot see into (a native view, a canvas that swallows input)
posts this on the operator's own pointer or key input while `driving` is true.

### Open a chat beside the page: `workshop/v1/showChat` (app to shell)

```js
{v: 1, type: "workshop/v1/showChat", thread: "<id>"}
```

Opens the drawer on that conversation. An app marks what a chat made or works
in with the thread its tools were called with (`X-Workshop-Thread`); pressing
the mark posts this (`showChat(thread)` in the kit).

### Send something to the chat: `workshop/v1/attach` (app to shell)

```js
parent.postMessage({v: 1, type: "workshop/v1/attach",
  items: [{kind, title, text, url, src, app, id}],
  prompt: "optional text for the composer",
  send: false,        // true sends at once (needs a prompt)
  newChat: false,     // true starts a new conversation beside the page
  scope: "app:<slug>",  // with newChat: file it under this chat scope (another
                        // app's own chat); default is the page's own scope
  apps: ["<slug>"],   // grant these apps' tools to that chat
}, location.origin)
```

Opens the drawer if it is closed. `kind` is `text`, `link`, `image`, `page` or
`item`. `src` (images) must be a URL the Workshop page can fetch: same-origin
(through the app's own proxy path) or a `data:` URL. It is uploaded as an image
attachment, so the model sees the picture. `app` + `id` name a record in an app,
so the chat can pass it to that app's tools.

This is what an app's "Discuss" action is: the app's own menu collects a prompt
(or not) and posts `attach` with `send` or without.

### The standard vocabulary

What the chat calls, and what the platform answers for plain DOM. An app that
overrides one keeps the same arguments and result shape.

- **`read_page {ref?, all?}`**: an outline, one line per element, indented by
  nesting: `button "Save" [ref=e12]`, `link "Docs" [ref=e3] -> /docs`,
  `textbox "Title" [ref=e5] = "draft"`, headings as `heading "..."`, and text.
  Interactive elements carry refs; refs are valid until the next reading. Lines
  new since the last reading start with `*`. `ref` narrows to one subtree, `all`
  includes every text node. Capped (about 12000 characters) with a line saying
  how much was left out.
- **`find {query}`**: the outline lines that match, with refs.
- **`act {action, ref?, text?, submit?, key?, direction?}`**: `click`, `type`
  (replaces the field's value; `submit` presses Enter), `select` (an option by
  label or value), `press_key`, `scroll` (the element, or the page, by a screen),
  `hover`. Answers with a line saying what was done and what changed.

## For an app: what its tools see

When a chat is given an app's tools (Access menu, Apps), the app's chat tool
server (its `chat.mcp` declaration) is added to that chat's turns. Two more keys
in the app's `chat` block describe it to other chats:

```json
"chat": {
  "mcp": {"service": "api", "path": "/mcp"},
  "about": "one line: what these tools reach",
  "options": [
    {"id": "history", "label": "Browsing history", "default": false},
    {"id": "profiles", "label": "Profiles", "default": true}
  ]
}
```

`options` lists the parts of the app's data the operator can switch on or off per
chat. Every request from a chat turn to the app's server carries
`X-Workshop-Access: history,profiles` (the ids switched on) and
`X-Workshop-Thread: <chat id>`. **The app enforces them**: a tool reaching an
option that is off answers that it is off. The chat is told which options are on
and off, and switches one on itself with `chat_access` when the task needs it.
The app's own chat (`app:<slug>`) gets the same headers, defaulting to the
options marked `default`.

`X-Workshop-Access` is the options as they were when the turn started. Every
app is also sent `X-Workshop-Options-Url` and `X-Workshop-Options-Token`: a
`GET` of that url with the token as the `X-Workshop-Options-Token` header
answers `{"options": [...], "follow": true}`, the options the chat has on now
(null for an app with none) and whether the operator follows it. An app that reads it there on each call honours an option
switched on mid-turn at once; one that reads only the header honours it from the
next turn. The token reads that and nothing else.

Keep each tool result under about 12,000 characters. A turn on a local model
takes back at most 5,000 tokens from a tool, and a larger result is set aside
in a file an app's chat has no tool to open, so it reads as a failure. Cut a
long result yourself and say how to narrow it (a `find`, a page, a limit).

## Where it lives (platform)

- Backend: `services/page_link.py` (levels, page state, the call relay; a
  drawer holds its stream under its own link id from the moment it opens, and
  a conversation is bound to it, so a blank chat's first turn already has the
  page),
  `routes/page_link.py` (`/page-link/*`, `/chat-apps*`), `mcp/page_tools.py`
  (`page_read`, `page_find`, `page_act`, `page_tool` on the workshop tool
  server), `services/chat_apps.py` (grants and their servers),
  `services/connections_prompt.py` (the per-turn PAGE AND APPS block).
- Frontend: `components/shell/beside/` (the drawer, the page link client, the
  platform's DOM reading and acting), the Access menu and chips in
  `components/chat/`.
- Kit: `packages/ui/src/pageLink.ts`, the app side (`startPageLink`,
  `attachToChat`, `reportTakeover`) and the message names, levels and types the
  shell's `beside/protocol.ts` imports, so the two sides cannot drift.

Nothing here names an app, and nothing may: an app's page, tools, options and
labels all come from the app.
