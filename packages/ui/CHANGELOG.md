# Changelog — @workshop/ui

The Workshop Design System kit. This changelog is the grounded record that the
AI-assisted kit migration reads (docs/workshop-v1/03-ui-kit-and-marketplace.md
section 3): each major records what moved, was renamed, or was removed, so a
breaking upgrade is migrated against real notes, not guesswork.

Format: per-version sections, newest first from 0.2.0 on; the 1.x sections below
remain in the order they were written.

**Versioning is 0.x.** While the major is zero, npm treats the MINOR as the
breaking position: `^0.2.0` means `>=0.2.0 <0.3.0`. So a breaking change bumps
the minor (0.2 → 0.3) and a compatible one bumps the patch. See CONTRIBUTING.md.

## 0.8.1 - 2026-11-05 - A menu under a sticky bar stays inside the scroller on iOS

**Fixed.** A `Menu` or `Popover` whose trigger sits in a `position: sticky`
element (a card's sticky footer, a sticky toolbar) is now kept inside the
scroller that element sticks in, and every clipping box above it. iOS WebKit
(measured on 18.6) paints a fixed surface there cut at the scroller's edge,
while reporting it where it was placed, so the menu lost its right edge in a
phone sheet. Desktop engines keep showing it whole, now inside that box. No
API change.

## 0.8.0 - 2026-11-05 - page views, read aloud, spoken actions, floating menus

**Minor, with migrations.** Everything built on 0.7.10 between 2026-09-23 and
2026-11-04, one subsection per change, newest first. Read each one's Removed and
Changed notes before upgrading; under 0.x the minor is the breaking position.

### Page states say when a page is held and why it closed (2026-11-04)


**Added.** `PageState.held` is true while the shell holds a page's media
because the page is not on screen, and a closing state may carry
`closedBy: "released"`: the shell let go of a page that kept playing while it
was held. The app keeps that tab and opens the page again when it is shown.
Both are left out by shells from before them.

### Menus and popovers float over their container (2026-11-03)


**Changed.** `Menu` and `Popover` open as `position: fixed` surfaces placed
from their trigger, so they overlay whatever scrolls or clips around them
instead of opening inside it. A menu on a row near the end of a short scrolling
list used to grow the list and scroll it up under itself; it now opens over the
list, and when neither side of the trigger has room it slides onto the screen
over the trigger. The surface keeps its place in the DOM, so descendant rules
written against `.ws-menu__list` and `.ws-popover__surface` still match. The
`--top`/`--bottom` side classes no longer set `top` or `bottom`.

**Migration.** A rule that positioned a surface with `top`, `bottom`, `left`
or `right` now fights the inline position; shift it with `translate` instead.

### A failed page load is shown where it was going (2026-11-03)


**Added.** `PageErrorEvent.shownInPage`: the page view showed the failure
itself. A main-frame load that fails now moves the page view to the address
that failed, with a page saying why and Try again, so Back returns to the
page that was left; before, the page being left stayed and its address came
back. An app that shows its own notice for a page error leaves it out for
these.

**Changed.** A page view pauses every sound and video in a page the moment
it is left: a load in its main frame starts, or the page moves through its
own history (a pushed or popped entry). An address rewritten in place and a
jump within the page do not count.

**Migration.** None.

### Apps declare their own spoken actions (2026-11-02)


**Added.** `useSpokenActions(actions)`, `useSpokenAction(action)` and
`registerSpokenActions(actions)` (which returns the unregister): a page or an
app in a shell frame declares what it can be told to do by voice, in the shape
of WebMCP's `registerTool`: `id`, `title`, `description`, `phrases`,
`examples`, an optional `input` (one free text, or `choices`), `annotations`
(`readOnlyHint`, and `consequentialHint`, which makes the shell ask first) and
`run(arg?)`. A phrase may hold one slot for the input (`"add to {group}"`). The shell hears an
action's phrases while its document is on screen, understands looser wordings
from its description and examples, and lists it in Commands under the app's
name. Outside a Workshop shell nothing is registered. `provideSpokenActions`
and `spokenActionsHost` are the shell's side of the same contract.

**Migration.** None.

### A popover over a page view takes its clicks (2026-11-01)


**Fixed.** `drawnOverPage` (through its paint-order rule) placed a box with a
z-index inside a positioned wrapper with z-index auto, such as a `Popover`'s
surface, under the wrapper's own place in the tree, so an open popover over
a page view was never counted: its clicks went to the page under it. A
positioned box with z-index auto starts no stacking context, and the rule now
orders what it holds in the context around it, as the browser does.

**Migration.** None.

### Page views are drawn under the web UI (2026-11-01)


**Changed.** A native shell now draws page views under its see-through web
view, so everything the web UI draws over a page (menus, bars, dialogs, their
shadows and round corners) shows as the engine renders it, with no hole cut
in the page. `drawnOverPage` and `pages.setCutouts` now decide only where the
web UI takes clicks and touches from the page; a dimmed scrim counts, so a
click on it closes its dialog instead of reaching the page.

**Added.** `usePageSlot(slot, shown)`: while `shown`, the element a page view
is placed over and every box holding it draw no background, and an app in a
frame tells the shell (`PAGE_SLOT_MSG`), which does the same for the frame.
`seeThrough(el)` is the same for one element and its holders.

**Migration.** An app that places a page view calls `usePageSlot` on the
element it places it over. Without it, the slot's background covers the page
on a shell that draws pages under the web UI.

### Read aloud knows where the text is (2026-10-31)


**Added.** `ReadAloudHost.read` takes an optional third argument, `place`:
the element or selected range the text shows in, so the shell's reader can
mark the sentence being read on the page and follow it. An optional
`ReadAloudHost.place(el, source, text)` declares that an element reads as a
text, so a tap in it while something is being read reads it from the tapped
sentence. `useReadAloud` passes both through (`read(text, place?)`, `place`).
`ReadAloudButton` uses them: given `within`, its document is highlighted while
read and can be tapped into.

**Migration.** None. Both are optional; a host without them reads as before.

### Portalled surfaces carry the app's type and ink (2026-10-30)


**Fixed.** A `Modal` (centered or `sheet`), a `Sheet` drawer and the toast
stack portalled to `<body>`, outside `.ws-root`, now take the same
`--font-sans`, `--fs-base`, `--lh-base` and `--fg` as `.ws-root` instead of
the browser's serif font and black text. Nothing inside `.ws-root` changes:
the rule applies only to a `.ws-overlay` or `.ws-toasts` with no `.ws-root`
ancestor, at zero specificity.

**Migration.** None. An app that restated the font on `.ws-overlay` or
`.ws-modal` for this can drop that rule.

### What the web UI draws over a page view (2026-10-30)


**Added.** `drawnOverPage(host, area)` and `DrawnOver`: what a document
draws over a native page view, read from the document by one rule (every box
that shows something over `area` and paints above `host`, by CSS stacking),
never from a list of roles. `pages.setCutouts(id, rects, { modal })` and
`PageCutoutOptions`: `modal: false` lets the page keep every click outside
the rects. `ShellApi.overlays(rects, modal)`: the web UI's own layers, which
a native shell now cuts out of every app's page views by itself.

**Migration.** None. `setCutouts` without options behaves as before. An app
that measured Workshop's own document to cut its layers out can stop when
`shell.overlays` is offered.

### A page view plays only on screen (2026-10-29)


**Added.** `pages.hide(id, { keepPlaying })` and `PageHideOptions`. A hidden
page view's sound and video are now paused and cannot start until it is shown
again (what was playing stays paused); `keepPlaying` lets a hidden page play on
while its app is in front. The shell holds every page view of an app whose
frame the web UI is not showing (`shell.foreground`, now taken by the iOS
shell too) and every page view while the shell is in the background,
minimized or covered, whatever the app asked.

**Migration.** None. An app that let hidden pages play now passes
`keepPlaying` to keep doing so while it is in front.

### Menu rows with buttons of their own (2026-10-29)


**Added.** `MenuRow`: a row that is not itself chosen, with buttons at its end
(`actions`) and `muted` for an entry that no longer stands. `MenuAction`: an
icon button for a row or a heading that does not close the menu and is reached
by the arrow keys. `MenuLabel` takes `actions` for buttons acting on its group.

**Migration.** None.

### Page cutouts (2026-10-28)


**Added.** `pages.setCutouts(id, rects)`: rects in `setRect`'s CSS pixels where
the web UI shows through a page view. A menu or dialog over a page no longer
needs the page moved away and replaced by a still of itself; the page stays in
place and keeps playing around it. While any rect is set the web UI takes every
click and touch, so a click on the page closes the menu as it would anywhere
else. An empty list makes the page whole again.

**Migration.** None. The Mac and iPhone apps need a new build for it;
`typeof pages.setCutouts` tells an older build apart.

### Opening a document never overwrites newer writing; an empty one is a blank page (2026-10-28)


**Fixed.** A `VersionedDocument` that finds unsaved typing kept on this device
merges it on open with the source's text winning wherever the two differ: that
copy was typed in an earlier session, so it no longer overwrites what was saved
since. A stale empty copy had replaced a whole note the moment it was glanced
at in another app. A kept copy without the text it was typed on is applied
only onto that same version. Typing in an open document still keeps yours in a
conflict (`mergeDrafts`/`merge3`/`mergeField` take an optional `prefer`).

**Changed.** An empty document shows a blank page instead of "Nothing written
yet" and a Start writing button; a click anywhere on it starts writing. The
default `placeholder` is now empty.

### Text fields never wear the focus ring (2026-10-28)


**Changed.** The global `:focus-visible` ring no longer matches text-entry
elements (`input` other than checkbox, radio, range and button types,
`textarea`, `select`, `contenteditable`). Browsers match `:focus-visible` on a
field however it was focused, so the ring showed on every click. A field's focus
cue is its own treatment: `.ws-input`, `.ws-select`, `.ws-textarea`,
`.ws-searchfield` and `.ws-stepper` show one accent hairline. The ring left on
buttons, tabs and links is 2px instead of 3px.

**Migration.** None. A consumer rule that sets `box-shadow: none` on a field to
cancel the ring can be deleted.

### One focus ring on a search field (2026-10-28)


**Fixed.** A focused `.ws-searchfield` drew the global focus ring on its input
as well as its own ring on the field, so a second box showed inside the field.
The input inside a search field no longer takes the global ring.

**Migration.** None.

### Page sound (2026-10-27)


**Added.** `pages.setMuted(id, muted)`, and `playingAudio` and `muted` on the
"pages:state" event (type `PageState`). A page view's sound can be silenced
without stopping the page (WebKit's page mute, the call Safari's tab speaker
uses), and the shell reports whether the page has sound playing, muted or not.
A page stays muted across its navigations; a page view opened again starts
unmuted. The two state fields are left out where the shell cannot tell, and
`setMuted` rejects there.

**Migration.** None. The Mac and iPhone apps need a new build for it;
`typeof pages.setMuted` tells an older build apart.

### OverlayScope: a column beside the content gets its own overlays (2026-10-27)


**Added.** `OverlayScope` (props `active`, `className`) and the `.ws-overlay-scope`
rule. It renders a host that fills its own box and routes `Modal`, `Sheet` and the
other portalled overlays inside it there, so a column that sits beside the shell's
content region (the chat beside a page) no longer sends its dialogs to `<body>`
at the size of the whole window. `active={false}` leaves overlays with the host
above. Nothing else changes.

### Phone fixes for documents: no sideways text, words kept as typed, wrapping titles (2026-10-25)


**Fixed.** Wrapped editor text fits its column in WebKit: one long unbroken
run (a URL) no longer widens a read-only document past the screen.

**Changed.** The page look keeps words as typed: autocorrect and inline
predictions are off (the keyboard still underlines misspellings and offers
fixes to tap). A `DocumentBar` title wraps, read or edited, in one box that
grows with its text instead of a one-line field that scrolled sideways, and
takes the 16px floor on a phone.

**Added.** `DocumentBar` `nameShown`: how the name reads while its field is not
focused. `VersionedDocument` passes `titleDisplay` through it, so a hidden word
in a title shows as written only while the title itself is being typed in.

### A document is read and written in one surface (2026-10-25)


**Changed.** `VersionedDocument` no longer swaps a reader for an editor. Its
text is always the page-look editor: read-only while it is read, writable from
the tap that starts writing, with the caret where the tap landed and nothing
else on screen changing. History previews and another writer's version are the
same editor, read-only (`DocumentText`). The page look now draws everything the
reader drew: images and task boxes in place, and tables, code blocks (diagrams
included) and rules through the one markdown renderer. Inline markup, blocks and
a host's replacement text show what is written only where the caret has been
moved to write. Links are followed by a tap while reading, Ctrl or Cmd and a
click while writing. `.ws-vdoc__read` is no longer drawn.

**Added.** On `Editor`: `marks` (a host's additions to how text is drawn:
replacement text, a class, a link; `EditorMark`, `EditorMarks`), `onLink`,
`onStartEditing` (a read-only editor a tap makes writable) and `scrollTo`. On
`VersionedDocument`: `marks`, `onLink` and `scrollTo`. `DocumentText`.

**Deprecated.** `VersionedDocument`'s `renderBody` is no longer drawn.
Migration: move what it drew into `marks` (mentions, replaced words, marked
ranges) and its link handling into `onLink`, which gets a URL or
`wikilink:<title>`. The prop is removed in the next breaking release.

### Wider chat cards (2026-10-25)


**Changed.** `.ws-chat-grid` columns are 18rem cards rather than 15rem, so the
page measure holds three and a chat's title reads before it ellipsizes.

### Editing a document leaves its text where it was (2026-10-25)


**Changed.** The editor's page look (`look="page"`) now draws a document
exactly where the reading form does, so tapping into one moves nothing:
numbered items keep their numbers hanging in the margin (counted as the reading
form counts them), nested lists step in by the reading form's indent with its
disc, circle and square bullets, loose lists keep their paragraph spacing, and
headings and quotes keep the reading form's spacing. The `#` of a heading, a
quote's `>` and a list's marker no longer turn back into source on the line
being edited; Backspace at the start of one removes it. Inline markup (`**`, a
link's target) shows only around the text the caret has been moved into, never
from the tap that opened the editor. Brackets that are only text keep the
text's color. Inline code in a document's reading form wraps between words,
as the editor wraps it.

### The chat list as a grid of cards (2026-10-25)


**Added.** `.ws-chat-grid` and `.ws-chat-card` (with `__menu`, `__preview`
and `__foot`): the chat list's grid layout, as many 15rem cards per row as
fit, each holding a title line, three lines of preview and a foot on the
bottom edge, every card in a row the same height.

### A text box that opens while you write (2026-10-25)


**Added.** `Textarea` takes `grow`: while focused it fits all of its text, and
on blur it returns to its own size.

**Changed.** A dialog's action row wraps when its buttons do not fit on one
line, instead of running past the edge.

### Number fields keep what is typed (2026-10-25)


**Fixed.** `Input type="number"` shows the typed text while it has focus and
the caller's `value` again on blur. Clearing a field to type a new number no
longer redraws a 0 (or a caller's clamped minimum) under the caret. Every
change is still reported to `onChange` as before.

### A form dialog can use a phone's height (2026-10-25)


**Added.** `Modal` takes `tall`: on a phone the dialog grows to the height of
its region before its body scrolls, instead of the 32rem cap other dialogs keep
there. For dialogs that are mostly form fields.

### A host can keep text out of sight (2026-10-25)


**Added.** `EditorProps.conceal` (`EditorConceal`): ranges the host names are
drawn blurred while editing, except within `reveal` characters of the caret,
kept current as the text and the caret move. `VersionedDocumentProps.conceal`
passes it through, and `VersionedDocumentProps.titleDisplay` says how a title
reads wherever it is not being edited.

**Changed.** The history sheet's previews and another writer's text are drawn
with the host's `renderBody` and `titleDisplay`, as the document itself is.

### Date fields fit on a phone (2026-10-25)


**Fixed.** `Input` with `type` date, time, datetime-local, month or week drops
the native appearance, so on iOS WebKit it takes the field's width instead of a
native minimum that ran past the right edge of a dialog. The native picker
still opens on tap. CSS only; no API change.

### Documents save as they are typed (2026-10-24)


**Changed.** `VersionedDocument` saves the text itself: after a pause in
typing, on Done, on Cmd/Ctrl+S, and when the page is hidden or left. There is no
draft and no Save or Discard. A save that finds a newer version merges the two
word by word (`documentMerge.ts`) and saves the merge; only where both sides
changed the same words is mine kept, with a notice offering theirs. An open
document re-reads its source when shown again and every `pollMs` (10s), and
takes newer text in place; the editor applies outside text as edits, so the
caret stays put and Undo skips them. Typing not yet confirmed is kept on the
device with the text it was typed on, and merged on the next open. The history
sheet restores through the same save loop and drops the version-number column
(`.ws-vdoc__vnum` is gone).

**Changed, compatible.** `DocumentSource` no longer has `saveDraft` or
`discardDraft`, and `load()` returns `{ state }`; a source that still has them
is not called on them. `save` and `restore` take an optional
`DocumentWriteMeta` naming the device, so a source can group one device's saves
into one history entry. New export: `DocumentWriteMeta`.

### Attach and send (2026-10-24)


**Added.** `ChatAttach.sendOnly`: the items go to the chat beside the page as a
message of their own, sent at once, and the composer keeps its draft and
anything else attached.

### A tapped line stays put (2026-10-24)


**Added.** `EditorProps.initialTop`: the viewport y the starting caret's text
should sit at. `VersionedDocument` passes where a reading form was tapped, and
the editor holds that line there while it measures what it had estimated, so
starting to edit a long document no longer moves the text twice. Markdown is in
place from the editor's first frame and is parsed past the caret at mount.

**Changed.** `DocumentBar` in the `title` variant: the title takes the bar's
free width read or edited, and back is the chevron alone.

### How a chat's work is seen (2026-10-18)


**Added.** `CHAT_VIEWS`, `ChatView` and `PageLinkState.view`: follow, show or
hide, how the operator sees the chat beside the page at work in an app.
`readShellMessage` reads it, from `follow` when the shell does not send it;
`follow` stays, true only for `follow`.

### Lines break as they read (2026-10-18)


**Fixed.** The page look of `Editor` breaks lines where the reading form does
(pre-wrap rather than CodeMirror's break-spaces, and no WebKit
after-white-space), so in Safari and the Workshop apps a word near the edge no
longer moves to the next line when editing begins.

### Bullet height (2026-10-18)


**Fixed.** The page look's list bullet sits a pixel higher, level with the
reading form's.

### Bullet matches the reading form's (2026-10-18)


**Fixed.** The page look's list bullet is the reading form's size and sits where
its disc marker sits.

### No ring around a page being edited (2026-10-18)


**Fixed.** The page look of `Editor` draws no focus ring around its text; the
caret is the only sign it is being edited (the ring had been clipped out of
sight by the editor's own scroll box until 0.7.10.41).

### A document scrolls as one while edited (2026-10-18)


**Fixed.** The page look of `Editor` inside a document body grows to its text
and the body scrolls it, as while reading, instead of fitting the window and
scrolling inside itself. Starting to edit no longer cuts the visible text short
at the bottom or jumps a scrolled document back to the top.

### Lists edit the way they read (2026-10-18)


**Fixed.** The page look of `Editor` (`look="page"`) spaces list items like the
reading form (`.ws-md li`) and draws the bullet as the same round dot, so
starting to edit a document with a list no longer moves the text below it.

### Title field as wide as its title (2026-10-18)


**Fixed.** `DocumentBar` with `onNameChange`: the title field is exactly as
wide as its text (a hidden copy of the text sets the width) instead of a
character count with an 8-character floor, so starting to edit a document no
longer pushes the meta line and actions to the right. No API change.

### Show a chat from the page (2026-10-18)


**Added.** `showChat(thread)` and `PAGE_LINK_MESSAGES.showChat`: an app asks the
shell to open the chat beside the page on a given conversation, so the mark on
something a chat made or works in opens that chat.

### Follow in the page link (2026-10-18)


**Added.** `PageLinkState.follow` (optional): whether the operator follows the
chat beside the page (its eye button), so an app that shows a chat's work can
bring it to the front when it is switched on. `readShellMessage` reads it,
true when the shell does not send it.

### Read aloud button (2026-10-18)


**Added.** `ReadAloudButton({ source, text, within?, selected? })`: the bar
control that reads the selection inside a document, else the whole text, and
stops its own reading. `VersionedDocument` uses it; any document bar can.

### Read aloud, and editing that looks like reading (2026-10-18)


**Added.** `useReadAloud(source)`, `readAloudHost()` and `provideReadAloud(host)`:
a page asks the Workshop shell's one reader to read text (`read`, `stop`, and
whether it is reading for that source). The shell installs the host; outside a
shell `available` is false and a Read aloud control should not show.

**Added.** `Editor` `look="page"`: no box, the reading form's face, size and
paragraph gap, prose emphasis instead of code colors, and markdown drawn as it
reads (marks hidden away from the selection's lines). `look="field"` is the
default and unchanged.

**Added.** `VersionedDocument` `menuItems`: the host's `MenuItem`s in the bar's
More menu.

**Changed.** `VersionedDocument` edits in the page look; Edit is a pencil and
finishing is a check (saves when there are changes, then shows the reading
form); Read aloud sits in the bar (the selection inside the document, else all
of it); Tags moved into More. `DocumentBar` with both `onBack` and
`onNameChange` draws the chevron alone ahead of the name field.

### Page link names the chat (2026-10-17)


**Changed.** `PageLinkState` gains `thread`, the conversation beside the page
(null when none), read from the shell's `workshop/v1/pageLink`. It is the id
the app's tool server gets as `X-Workshop-Thread`, so an app can mark what a
chat made and bring it to the front beside that chat.

**Migration.** Code that builds a `PageLinkState` itself (an initial value)
adds `thread: null`.

### Page link (2026-10-17)


**Added.** The app side of a chat beside a page, so an app framed by the shell
uses the shell's own message names and shapes instead of writing them again:
`startPageLink({context, tools, run, onLink})` keeps the shell told what is on
screen and answers the chat's calls to the app's tools (an undeclared tool or a
throw becomes the chat's error); `attachToChat(...)` hands items or a prompt to
the chat; `reportTakeover()` gives a chat in control the page back. Types
`PageTool`, `PageCall` (with `access`, the app's chat options this chat has on),
`PageResult`, `PageLinkState`, `ChatAttach`; constants `PAGE_LINK_MESSAGES`,
`PAGE_LEVELS`, `PAGE_CONTEXT_MAX`. Framework-free; inert outside a frame.

**Migration.** None.

### Notice body on a phone (2026-10-17)


**Fixed.** Below 640px a `Notice` whose sentence ran longer than one line put
the sentence on the line BELOW its icon, leaving the icon alone on the first.
The body now takes the rest of the icon's line and wraps inside it; an action
still drops to its own line.

**Migration.** None.

### Mic capability (2026-10-17)


**Added.** The native bridge types gain the `mic` capability (`MicApi`):
`status()`, `request()` (the system prompt while undetermined) and
`openSettings()`, mirroring `shared/native-bridge.ts`. Offered by the Mac and
iPhone shells from their next build; check `has("mic")` before calling.

**Migration.** None.

### UseTrackingStatus without location (2026-10-17)


**Fixed.** `useTrackingStatus` called `location.trackingStatus()` on any native
shell, so a shell that does not offer the `location` capability threw while
rendering and took the Settings Account tab down with it. It now does nothing
unless the shell has `location`.

**Migration.** None.

### CopyText (2026-10-17)


**Added.** `copyText(text)`: writes the clipboard and resolves true once the
text is there. A copy made from code fires no DOM `copy` event, so after a copy
lands it dispatches `COPIED_EVENT` (`"workshop:copied"`, detail `{ text }`) on
its window; the Workshop shell listens for it on its own window and on
same-origin app frames, and tells apps subscribed to the "copied" event.
`CopyButton` now copies through it.

**Migration.** None. An app copying from code should call `copyText` rather
than `navigator.clipboard.writeText`, so its copies are seen.

### Page zoom (2026-10-12)


**Added.** `pages.setZoom(id, factor)`. Draws the page view at `factor` times
its size, the way a browser's zoom does (WKWebView `pageZoom`): 1 is normal,
the layout width changes with it, and the factor is held to 0.3 through 3. It
stays with the page view across its navigations; a page view opened again
starts at 1.

`hidden?` on `PageCommandItem`: the Mac takes the command's key and lists no
menu item for it, for a second key of a command already listed (Cmd-= beside
Cmd-+). An older Mac build ignores the flag and lists the item.

**Migration.** None. The Mac and iPhone apps need a new build for it;
`typeof pages.setZoom` tells an older build apart.

### Page downloads handed to the app (2026-10-12)


**Added.** `downloadsToApp` in `PageNavigationRules`, and the "pages:download"
event (type `PageDownloadEvent`). With it set, a load of a page's main frame
whose response is an attachment (a Content-Disposition starting with
"attachment") or a type the page view cannot show is cancelled, the page stays
where it was, and the file is reported with `{ id, app?, url, from, filename?,
mime?, bytes?, reason: "attachment" | "unshowable" }`. Only http and https
addresses are reported, and frames other than the main frame are left alone.
The shell downloads nothing; the file is the app's to fetch. Off unless the app
sets it, and kept with the rest of the app's rules.

**Migration.** None; an app that does not set it sees no change. The Mac and
iPhone apps need a new build for it. An older build refuses rules carrying the
new key ("Not a navigation rule: downloadsToApp") and keeps the old ones, so an
app that sets it retries without it when `set` rejects.

### Navigation rules for page views (2026-10-12)


**Added.** `pages.navigationRules.set(rules)` and `.remove()`, and the
"pages:navigationBlocked" event (types `PageNavigationRules`, `PageHopRule`,
`PageStripRule`, `PageNavigationBlockedEvent`). An app declares where its
page views may go, and the shell applies it to every navigation of its pages,
pop-ups included, and keeps it across launches:

- `popupsNeedTap`: a pop-up opens only from a tap, click or key.
- `blockUntappedJumps`: the whole page jumping to another site is cancelled
  when the page's script, or a frame from another site, started it without a
  tap within `tapWindowMs` (default 5000); each is reported with where it
  would have gone. Loads the app starts, server redirects, form posts, back,
  forward and reloads are never judged.
- `publicSuffixes`: the public suffix list, for telling sites apart.
- `hops`: tracking hops skipped, the destination taken from a query
  parameter (plain or base64) or the path.
- `stripParams`: tracking query parameters taken out.

Every rule is off unless the app sets it. Taps, clicks and keys are noted by
the shell, natively, so a page can neither fake nor hide them.

**Migration.** None; an app that sets no rules sees no change. The Mac and
iPhone apps need a new build for it; `typeof pages.navigationRules?.set` tells
an older build apart.

### Page commands reach the app in front with no page (2026-10-11)


**Added.** `WorkshopNative.shell.foreground(app | null)` (capability
`"shell"`, type `ShellApi`): Workshop's web UI reports which app's frame it
shows. On the Mac, with no page view visible, a registered page command fires
for that app, and "pages:command" arrives with a null `id` (no page). The iOS
shell takes the call and keeps nothing.

**Changed.** `PageCommandEvent.id` is `string | null`.

**Migration.** A handler that reads `e.id` as a page id checks for null
first; acting on a null id for the app's current view (its active tab, say)
is what makes Cmd-W and the like work with no page on screen. An app that
ignores a null id keeps today's behaviour, except that the shell's own item no
longer fires for a key the app registered.

### Page commands answer while the app is in front (2026-10-11)


**Changed.** On the Mac a registered page command also fires when keyboard
focus is outside every page view (Workshop's web UI, the app's own controls in
its frame): the app whose page view is the topmost visible one answers, and
"pages:command" names that page, as it would the focused one. Only that app is
asked; when it has no command for the key, or no page view is visible, the
shell's own item applies (Cmd-W closes the window). Menu items are enabled only
while their command would answer. The types are unchanged; their comments
state the new rule.

**Migration.** None. An app that acts on "pages:command" for its own pages
already handles the event; it now also arrives while focus is in its controls.

### One Live Activity for every app (2026-10-11)


**Breaking.** `WorkshopNative.liveActivity` takes a generic
`LiveActivityState` instead of the single-purpose state it had, whose
`direction`, `leaveByLocal` and `targetArriveLocal` fields are gone and whose
`countdownTo` is now optional. `ActivityStatus` is now the kit's five status
kinds (`ok | running | stopped | warn | danger`, the same as `StatusKind`)
instead of `ok | info | caution | urgent`. The old state type is removed.

**Added.** `liveActivities()`, the Live Activity API acting for the app the
frame serves (`undefined` outside an app's served pages, in a plain browser,
and in a shell from before activities were per app), and the types
`LiveActivityState`, `LiveActivityHandle`, `LiveActivityApi` and
`ActivityStatus`. A state is a `headline`, an optional `subtitle` and
`detail`, an optional `countdownTo` (ISO 8601; the shell counts down
natively), an optional `progress` (0 to 1, clamped), a `status` and an SF
Symbol name in `symbol`. The shell lays out the lock screen and the Dynamic
Island's compact, expanded and minimal views from those fields alone.

**Changed.** An activity belongs to the app that started it. `start` is
refused without an app; `update` and `end` reject an activity another app
started, or one that is gone; a tap on the activity opens the app's page in
Workshop. A state with an unknown status, no headline, no symbol or an
unreadable `countdownTo` is rejected.

**Migration.** The calls keep their names and arguments; what changes is
where the API comes from and the state passed to it.

- Get the API with `const la = liveActivities()` instead of
  `native().liveActivity`. It is `undefined` where an activity cannot belong
  to an app, so check it and `await la.isSupported()` before starting.
- `start(state)` and `update(id, state)`: build the new state from the old.
  `headline` and `subtitle` carry over as they were. `countdownTo` carries
  over and may now be left out. `direction` has no field of its own; fold it
  into `headline` or `subtitle` if the activity should say it. The two
  preformatted time strings, if still wanted, go into `detail` as one line.
  Set `symbol` to the SF Symbol the activity should show; the old layout
  drew a fixed one. Map the status: `ok` to `ok`, `info` to `running`,
  `caution` to `warn`, `urgent` to `danger`.
- `end(id)` is unchanged.
- An `update` or `end` that rejects means the activity is gone (dismissed,
  timed out, or started by an older shell). Start a new one. Activities an
  older shell started are not carried over.

### Page views are each app's own (2026-10-11)


**Changed.** `pagesBridge()` returns the bridge acting for the app its frame
serves (the slug in `/base-apps/run/<slug>/proxy/...`, under `/api` too) when
the shell offers `forApp`. The shell keeps page ids, profiles and their data
stores (cookies included), content rule lists, user scripts and page
commands per app: an app lists, reads and removes only its own, and
`closeAll` (also called when the frame goes away) closes only its pages. A
shell without `forApp` behaves as before.

**Added.** `WorkshopNative.forApp?(app)`, and `app?` on `PageState`,
`PageErrorEvent`, `PageNewWindowEvent`, `PageCommandEvent` and
`PageMessageEvent`. The bridge from `forApp` passes `on` only its app's
`"pages:*"` events.

**Migration.** Rebuild the app with this kit: a shell with per-app page views
refuses page requests that carry no app. Nothing else changes for an app that
reaches page views through `pagesBridge()`. Stores made before the change keep
their identifiers and cookies: the first app to reach its profiles takes them
over. Rule lists and scripts stored before it are dropped once; set them again
when `contentRules.list()` no longer has them.

### Page commands are the app's to register (2026-10-11)


**Breaking.** `PageCommand` (a fixed union of six command names) is
removed, and `PageCommandEvent.command` is a `string`: the id of a command
the app registered. The Mac shell no longer has page items of its own (File's
Cmd-T and Cmd-L, View's Cmd-R, a History menu with Cmd-[ and Cmd-]), and
Cmd-W in a page view closes the window unless an app registered it.

**Added.** `PagesApi.commands.set(items)` with `PageCommandItem` (`id`,
`title`, `key`, `modifiers?` of `PageKeyModifier` `"command" | "shift" |
"option" | "control"`, default command; `menu?`, the top-level menu, default
"File"; `focusShell?`). The Mac lists them in its menu bar, takes their keys
while a page view has focus, and sends `"pages:command"` with the item's id.
The iOS shell takes the list and never fires; it no longer declares
`"pages:command"`.

**Migration.** Register each command you handled with `commands.set`: the id
your handler map is keyed by, a title, the key, and the menu it was in
(`focusShell: true` for one that moves focus into your UI, as an address
field does). Key the handler map by `string` rather than `PageCommand`, and
call `commands.set([])` when the screen goes away.

### The shell's web engine (2026-10-11)


**Added.** `WorkshopNative.engine` (optional) with `NativeEngine`
(`webkitVersion?`, `safariVersion?`): the Mac reports the installed Safari's
version and WebKit's build, iOS its OS release as `safariVersion`. Shell
builds from before it leave it out. The Mac shell's own user agent no longer
carries "Version/… Safari/…"; web code that wants a Safari agent for page
views builds one from `engine.safariVersion`. Nothing is removed.

### Page views: menu, press and pick removed (2026-10-11)


**Removed.** Everything 0.7.10.18 added: `PagesApi.menu.set` and
`.menu.answer` with `PageMenuItem`, bridge topics `"pages:menuRequest"`
(`PageMenuRequestEvent`) and `"pages:menu"` (`PageMenuEvent`),
`PagesApi.pick(id, on)`, and topic `"pages:press"` (`PagePressEvent`).
A page's context menu on a right-click or long-press is WebKit's own again,
with nothing waiting on the app and no long-press recognizer of the
platform's. An app that wants its own menu on a page draws it in the page
with a user script and reports the choice over `"pages:message"`.

### Page views: the app's items in page menus, long-press and pick (2026-10-11)


**Added.** `PagesApi.menu.set(items)` and `.answer(token, items)` with
`PageMenuItem` (`id`, `title`, `enabled?`; a disabled item is a header).
Bridge topic `"pages:menuRequest"` with `PageMenuRequestEvent` (`id`,
`token`, `x`, `y`, `link?`, `image?`, `media?`): a page's own context menu
(a right-click on the Mac, WebKit's long-press menu on the iPhone) waits up
to 400 ms for the answer, then takes the `set` items. Topic `"pages:menu"`
with `PageMenuEvent` (`id`, `item`, `token?`, `x`, `y`) when one is chosen.
`PagesApi.pick(id, on)` holds the next tap on a page. Topic `"pages:press"`
with `PagePressEvent` (`id`, `x`, `y`, `pick`): a one-finger long-press
WebKit's menu did not take (iPhone), or the picked tap. Nothing is removed.

### Page views: user scripts and page messages (2026-10-11)


**Added.** `PagesApi.userScripts.set(identifier, script)` and
`.remove(identifier)` with `PageUserScript` (`source`; `world` `"page"` or
`"isolated"`, default isolated; `frames` `"all"` or `"main"`, default all;
`at` `"start"` or `"end"`, default start): a script installed in every page
view, pop-ups included, from each page's next document, and kept on the
device for the next launch. Bridge topic `"pages:message"` with
`PageMessageEvent` (`id`, `world`, `frameMain`, `frameUrl`, `body`,
`dropped?`): what a user script posts with
`window.webkit.messageHandlers.workshop.postMessage(value)`, bodies over
256 KB dropped and counted. `PagesApi.run` takes an optional third argument,
`PageRunOptions` (`world`), to run in the isolated world. `PageWorld` names
the two worlds. The iOS shell now also forwards `"pages:command"`. Nothing is
removed.

### Page views: the size a pop-up asked for (2026-10-11)


**Added.** `PageNewWindowEvent.features` (optional) with `PageWindowFeatures`
(`width`, `height`, `x`, `y`, each optional): set with `popupId` when the page
asked for a size for its new window, as `window.open(url, name,
"width=500,height=600")` does, so the web can show it as a floating window
instead of a tab. Nothing is removed.

### Page views: browser shortcuts and crashed pages (2026-10-11)


**Added.** Bridge topic `"pages:command"` with `PageCommandEvent` (`id`,
`command`) and `PageCommand` (`location`, `newTab`, `closeTab`, `reload`,
`back`, `forward`): the Mac shell sends one for Cmd-L, Cmd-T, Cmd-W, Cmd-R,
Cmd-[ and Cmd-] typed while a page view has focus. `PageState.crashed`
(optional): the page's web process ended, and it runs no script until a
reload. Nothing is removed.

### Page views: pop-ups that keep their opener (2026-10-11)


**Added.** `PageNewWindowEvent.popupId`: the shell has already opened a
hidden page view for the new window, linked to the page that opened it, so a
sign-in pop-up can hand its result back. `PagesApi.adopt(popupId, id)` gives
that view the id of the tab made for it. A pop-up that closes itself pushes
its `closed` state like any page. Nothing is removed.

### The native bridge names a Mac shell (2026-10-11)


**Changed.** `WorkshopNative.platform` is `"ios" | "macos"`: the AppKit shell
in `macos/` installs the same bridge and says `"macos"`. It offers only the
capabilities it implements, so `has()` stays the way to ask what is there.
Nothing is removed.

### Page views: content mode and content rules (2026-10-11)


**Added.** `PageOpenOptions` gains `contentMode` (`"mobile"` or `"desktop"`,
the new type `PageContentMode`), and `PagesApi` gains
`setContentMode(id, mode, userAgent?)`, which applies the mode (and the user
agent, when given) and reloads the page. `PagesApi.contentRules` sets, removes
and lists WebKit content rule lists by identifier; a set list applies to every
page view, open or opened later, and stays in force across launches of the
phone app. Nothing is removed.

### Page views close with the app that opened them (2026-10-10)


**Fixed.** When `pagesBridge()` finds the shell's bridge through a parent
frame, it closes every page view once that frame goes away (`pagehide` or
`unload`). Page views sit above the whole shell, so before this, leaving a
framed app left its pages on screen over the next one. Nothing else changes.

### Native page views from the phone app (2026-10-10)


**Added.** The native bridge's `WorkshopNative` type gains `pages`, the mobile
shell's native page views (real WebKit pages placed above the web UI, one
iOS data store per profile), with its event topics `pages:state`,
`pages:error` and `pages:newWindow` and the types `PagesApi`,
`PageOpenOptions`, `PageRect`, `PageProxy`, `PageState`, `PageErrorEvent`,
`PageNewWindowEvent`, `PageRunResult` and `PageCookie`. `pagesBridge()`
returns the bridge that can drive them, looking through same-origin parent
frames so a framed app finds the shell's bridge; `topViewportRect(el)` gives
an element's rect in the top document's viewport, the space page rects use.
Nothing is removed.

### A document's bar stays still, and reading text opens it for editing (2026-10-10)


**Changed.** `VersionedDocument` no longer shows the "unsaved changes were
kept" banner, its "Discard draft" button, or the bar's "Discard". Drafts are
still written as before; the bar says so with one dot in a fixed slot (accent,
labelled "Unsaved changes"), so it appearing moves nothing. While there are
changes the bar's last button is Save, otherwise Done. History and the
Preview toggle moved into a "More" menu, which also holds "Discard unsaved
changes" (with a confirmation) while there are changes. Tags: a tag button in the bar opens the add field; existing
tags always show. The editor's minimum height inside a `fit` document is 8rem
(was 12rem), so it fits a short phone viewport without the page scrolling.

**Added.** Tapping or clicking the text of a document that is being read opens
the editor with the caret at that spot. `DocumentBar` takes `quietDirty`
(reserved slot, accent dot), and `Editor` takes `autoFocus` and
`initialSelection` (read at mount). All optional; nothing is removed.

### List text in the editor keeps the normal text color (2026-10-09)


**Fix.** The markdown highlighting painted the whole list, text included, in the
punctuation tone, so a numbered or bulleted line went grey as soon as it was a
list item. Only the marker ("1.", "-") takes that tone now; the item's text is
the editor's text color.

### The rail shows only the app tiles that fit (2026-10-07)


**Fix.** With more installed apps than the rail had room for, the tiles pushed
the "+" and the avatar off the bottom of the screen. App tiles now sit in
`.ws-rail__apps`, the only part of the rail that shrinks: it shows as many whole
tiles as fit and clips the rest, so the controls below it are always visible.
A consumer that renders its own rail wraps its app tiles in that class.

### A VersionedDocument can fit a sized box, and is edited in its reading face (2026-10-04)


**Local to this installation, additive.** `VersionedDocument` takes an optional
`fit`: the frame fills a box its host has already sized, its bar sits above the
body instead of sticking inside the host's scroller, and the body scrolls under
it. In the source form the editor fills what is left and scrolls inside itself.
Sticky inside a padded scroller let the text show through the strip above the
bar. Separately, the editor inside a `VersionedDocument` is drawn in the sans
face, size and leading of the reading form rather than the code editor's mono,
never under the `--fs-field` no-zoom floor.

### A missed deadline is a transient failure (2026-10-04)


**Local to this installation, a fix; no API change.** `isTransientNetworkError`
now also answers true for an error named `TimeoutError`, which is what
`AbortSignal.timeout` rejects with and what the shell's fetch deadline throws.
A request a stalled server never answered is the same fault as a 502 from a
restarting one, so `withTransientRetry` and every caller that keys retries on
this function ride it out the same way. An `AbortError` (a caller cancelling
its own request) is still not transient.

### A VersionedDocument with a Back can be titled (2026-10-02)


**Local to this installation, a fix; no API change.** `DocumentBar` folds its
name into the back button whenever `onBack` is given, so a `VersionedDocument`
given `onBack` drew no title field and a note could not be titled. It now holds
Back while editing and gives it back on Done. The combined back button reads the
`namePlaceholder` for an empty name instead of nothing.

### VersionedDocument, editor suggestions and highlights (2026-10-02)


**Local to this installation, additive.** New exports on the `editor` subpath and
three optional `Editor` props; nothing removed or renamed. Queued for upstream as
a platform change (docs/upstream-queue.md).

#### Added

- **`Editor` props `completions`, `highlights`, `onSelectionChange`.** Plain data
  rather than CodeMirror extensions, because an app bundle cannot hand the shell's
  editor an extension built against a second copy of `@codemirror/state`. A
  completion source is a trigger (`"@"`, `"[["`), an optional close (`"]]"`) and a
  function from the typed query to suggestions; highlights are ranges with a tone
  and a tooltip that follow the text until replaced. `matchTrigger` is the pure
  matcher, exported for tests. New optional peer: `@codemirror/autocomplete`.
- **`VersionedDocument`** (editor subpath): one markdown document with a title,
  tags, drafts autosaved to the host and to the browser, a version on every Save,
  a history sheet with restore, and a save that names its base version so a newer
  save made elsewhere is offered rather than overwritten. Storage is the host's,
  through `DocumentSource`; `.ws-vdoc__*` styles in components.css.

#### Changed

- The editor's implementation moved to `src/EditorCore.tsx`; `src/Editor.tsx` is
  now the subpath entry that re-exports it beside the document frame. The import
  path `@workshop/ui/editor` is unchanged.

### Sheet (2026-10-01)


**Local to this installation, additive.** One new export, `Sheet`, and nothing
removed or renamed. Queued for upstream as a platform change
(docs/upstream-queue.md).

#### Added

- **`Sheet`**: a panel you work in beside the work. A side panel on the right
  edge from 900px up, a bottom sheet below it. The head holds the title and a
  close button, the body scrolls, and `actions` sit outside the scroll. Esc, the
  scrim and the close button all call `onClose`; focus is trapped and restored
  as in `Modal`. Its overlay is `.ws-overlay--side`, a sixth sanctioned overlay
  modifier. Use it for settings and pickers; a decision you are being asked to
  make stays a `Modal`.

#### Changed

- `Modal`'s focus handling moved into an internal hook shared with `Sheet`.
  Behaviour is unchanged.

### Toast and NumberField (2026-09-23)

**Additive.** `ToastHost`, `useToast`, `type ToastSpec` (the Toast primitive)
and `NumberField`. Nothing removed or renamed.

## 0.7.10 - 2026-09-16 - a section owns its spacing, and stretching is opt-in

**Patch, additive.** One new export (`Section`), one new optional prop
(`Segmented block`), and two layout defaults corrected. Nothing removed or
renamed. A consumer that wanted a full-width Button or Segmented inside a Field
was getting it by accident and now says `block`.

### Added

- **`Section`** — one group under one header. `label`, `icon` and `actions`
  render the header row every page was hand-rolling as
  `<section style={{marginBottom}}><Row between><SectionLabel/>…`, and the
  section owns the gap beneath it (`--space-6`) and beneath its header
  (`--space-3`).

  The spacing between two sections was previously the SectionLabel's own
  asymmetric margin, and `.ws-section-label:first-child` zeroes the top half of
  that in any wrapper opening with a label, which is all of them. So a header
  sat 0px under the block above it and 8px over its own, and pages made up the
  difference with an inline `marginBottom` that some carried and some did not.
  Reported on an app's Overview tab, 2026-09-16: "the spacing above that title
  is less than the spacing below, which looks really weird", "that spacing
  below it is irregular".

- **`Segmented block`** — fill the container and divide it between the options.
  Off by default (see below).

### Changed

- **A label in a `Row` no longer carries its bottom margin.**
  `.ws-row > .ws-section-label { margin-block: 0 }`. `align-items: center`
  centres each item's MARGIN box, so a label with 8px under it sat ~4px above
  the buttons beside it on every header that carried actions.

- **A `Button` or a `Segmented` inside a `Field` takes its content width.** A
  Field is a column flex box, so `align-items: stretch` made every child full
  width; an Input wants that and a button does not. A Field carrying nothing
  but a `help` string was turning a primary button into a page-wide bar, and a
  two-pill toggle spanned its card with the pills in the corner of it.

  `<Button block>` has always been the opt-in for the case that does want it,
  and `Segmented` now has the same prop. `.ws-field--inline` is unaffected: it
  is a grid, where `align-self` means something else.

## 0.7.9 - 2026-09-04 - the tab bar's fade is wide enough to see

**Patch, fix.** CSS only: no export added, removed or altered.

### Changed

- **`.ws-tabbar`'s right-edge fade is 48px, not 28px**, and holds its full
  width to 90% of the scroll travel rather than 80%.

  0.7.8 added the fade and never looked at it on a phone. At 28px it faded the
  last glyph of a label, which reads as antialiasing: re-shot at 390px on
  2026-09-04, the Settings strip still looked plainly cut off at "Provide" and
  the reported defect reproduced. 48px is about two characters at the bar's
  type size, the smallest fade that reads as a fade.

## 0.7.8 - 2026-09-01 - a scrolling tab bar says it scrolls

**Patch, additive.** CSS only: no export added, removed or altered.

### Changed

- **`.ws-tabbar` fades its right edge while there is more to the right.** The
  bar hides its scrollbar on purpose, and that hid the only sign that a phone
  was showing six of ten tabs.

  The fade's width is driven by the bar's own scroll position, so a bar that
  does not scroll never wears one, and it releases as the last tab arrives. It
  is behind `@supports (animation-timeline: scroll(self inline))` and a
  registered `--ws-tabbar-fade` property; a browser without scroll timelines
  renders the bar exactly as before.

## 0.7.7 - 2026-09-01 - the scheme's description sits under the scheme

**Patch, additive.** No API change: no prop added, removed or altered.

### Changed

- **`PalettePicker` now places the selected scheme's blurb itself**, in the
  group that holds the selection, as `.ws-palette-group__blurb`.

  Consumers passed the blurb in as the surrounding field's hint, which renders
  below the whole control. With the schemes split into two groups, an
  Expressive scheme's description captioned the Quiet row, so the sentence read
  as a caption for the wrong swatches. The picker is the only thing that knows
  which tile is selected, so it is the only thing that can place the sentence.

  A consumer still passing a blurb as a field hint will now show it twice.
  Drop the hint.

## 0.7.6 - 2026-08-31 - a page can say whose it is

**Patch, additive.** One new optional prop.

### Added

- **`PageHeader` takes `eyebrow`** — whose page this is, above the title,
  uppercase and muted. For a page whose title names the CONTENT ("Tickets",
  "Memory") while the thing that owns it is somewhere else.

  The Tickets page had already hand-rolled exactly this as a private
  `.ws-tickets__eyebrow` to print its scope over the title. The per-app tabs
  then needed the same thing for a different reason: an app's tab is headed
  "Chats" or "Tickets", the app's own page column is collapsed by default, and
  on an app with no bundled crumbs that left nothing on screen naming the app.
  Reported 2026-08-31: "development STILL opens to the OLD chats page".

  A second private copy is how one idiom becomes two, so the slot is in the kit
  and both spellings share one CSS rule. Not `back`: an eyebrow is a label, not
  a way out.

## 0.7.5 - 2026-08-28 - a name field you can actually empty

**Patch, additive.** One new optional prop.

### Added

- **`DocumentBar` takes `namePlaceholder`.** What an empty name reads as. It is
  the input's `placeholder` and the span's text when there is no name yet, and
  never the input's VALUE. A caller passing `name={title || "Untitled note"}`
  has built a field that refills itself the moment you clear it: every
  keystroke that empties it puts the fallback back, so the name cannot be
  emptied at all. Reported 2026-08-28 against a new Memory note.

## 0.7.4 - 2026-08-28 - a name in the document bar can be committed

**Patch, additive.** One new optional prop.

### Added

- **`DocumentBar` takes `onNameCommit`.** `onNameChange` alone is right for a
  name that is part of a draft its own Save will write (a note's title), and
  wrong for one whose write is a real operation: renaming a FILE moves bytes on
  disk and repoints every ref that named the old path, so per-keystroke would be
  one move and one ref rewrite per letter. `onNameCommit` fires on blur and on
  Enter; Escape blurs without committing, so a half-typed name is given back
  rather than written. The editable name also gets an accurate accessible name
  now, "Filename" or "Title" by variant, instead of "Title" for both.

## 0.7.3 - 2026-08-28 - an empty editor says what to write in it

**Patch, additive.** One new optional prop; nothing else changed.

### Added

- **`Editor` takes a `placeholder`.** An empty editor inside a full-width
  document has no border on its long edges and a background one shade off the
  page's, so it reads as blank page rather than as somewhere to type. Measured
  2026-08-28 on a new Memory note: an editor 1128x513 that nobody could see.
  The placeholder answers the more useful question at the same time, which is
  what to write there. The `view` profile ignores it: a reader is not about to
  type. The `.cm-placeholder` theme rule this styles has existed since the
  editor shipped and had nothing wired to it.

### Fixed

- **`.ws-doc--full` sizes its editor by layout, not by arithmetic.** The height
  was `100dvh` minus bar one, minus the document bar, minus the body's padding,
  which is only correct when the editor is the FIRST thing in the body. A new
  note has a scope picker, a type, a hook and two help lines above it, and the
  editor's box ran 165px below the fold. It is a bounded flex column now: the
  body takes what the bar leaves, the editor takes what the fields leave, and
  nothing has to be told what its siblings are. The editor keeps a 12rem floor
  and the body is the scroller, so a document is still one scroller either way.

## 0.7.2 - 2026-08-28 - shell chrome beside <main> gets the page's measure

**Fix, CSS only.** No export moved and `api/ui.api.md` is unchanged.

The shell renders the drift banner as a SIBLING of `<main>`, and `<main>` is
where the content cap and the edge inset live (`.ws-app__body > main`). So the
banner started at the viewport edge and ended past the other one: on a 390px
phone its "Open Branches" link sat off the right of the screen, which made the
one control on the one banner that interrupts every screen unreachable on the
device it is read on. `.ws-trail` already solved this for itself; this
generalises it.

### Added

- **`.ws-shellnotice`** — a `Notice` drawn by the shell rather than by a page.
  Takes the same `max(--page-pad-x, (100% - --page-max) / 2)` measure as
  `.ws-trail` and `<main>`, and drops the cap under `.ws-app--appfull` the same
  way, so the three pieces of chrome line up at every width.

### Changed

- **`.ws-notice` wraps its action below 640px.** The body is `min-width: 0` and
  the action is `flex: 0 0 auto`, so on a narrow screen the sentence collapsed
  toward one word per line while the control held its size and pushed out of the
  box. The body now takes a `16ch` floor there and the row wraps, so the action
  drops to its own line, indented to the text column. Nothing changes at or
  above 640px.

Pinned in `frontend/src/styles.test.ts`.

## 0.7.1 - 2026-08-27 - a diagram library a consumer never installed is not an error

**Fix.** No export moved and nothing changed for a consumer that installs
mermaid. 0.6.4 said a consumer that does not install it "pays nothing and a
fence merely stays a code block". That was true of this package — mermaid is an
optional peer and the kit's own build keeps it external — and NOT true of the
app bundling it: a bundler resolves `import("mermaid")` at BUILD time, so a
consumer that left the package out got a failed build rather than a code block.

### Added

- `src/mermaidOptional.ts`: the stand-in a consumer's bundler resolves `mermaid`
  to when the package is not installed (a resolve alias; `frontend/vite.config.ts`
  in this repo is the worked example). It rejects with `MermaidUnavailableError`,
  which lands in the catch `MermaidDiagram` already had, so the fence renders as
  the code block it was before diagrams existed — and, unlike a diagram that
  will not parse, with no `Notice` beside it: an install this reader does not
  control says nothing about what they wrote. Neither name is exported from the
  package: the module is addressed by the build, not imported by app code.
- `src/mermaid.d.ts`: an ambient declaration of the slice of mermaid this kit
  calls (`initialize`, `render`, and the config keys it sets). An ambient
  declaration is matched before node_modules, so the kit typechecks identically
  whether or not the package is installed — without it, `tsc` fails with TS2307
  for exactly the consumers who opted out, and their build never reaches the
  bundler. Widen it in the same change as the call that needs it.

## 0.7.0 - 2026-08-27 - a tree you can navigate, and one header for a note and a file

**Minor, because `FileTreeRow` gained a variant.** A consumer that switches
exhaustively on `row.kind` will not compile until it handles `"group"`. Nothing
was removed and no existing prop changed meaning; every other item here is
additive and every existing caller renders exactly as before.

Driven by the operator's walkthrough of the Memory page on 2026-08-27, written
up in `docs/platform-redesign/memory-page-and-file-viewer-2026-08-27.md`.

### Added

- **`FileTree` draws a caret.** A chevron leads every branch row, right when
  closed and down when open; a leaf gets a spacer of the same width so labels
  line up. This REVERSES the "NO DISCLOSURE TRIANGLES" rule this component
  shipped with (`shell-mockup-spec-2026-08-22.md` section 5): the operator
  overruled it directly, on the grounds that a tree without one is missing the
  essentials. The caret is decoration, not a second hit target: the row still
  opens the branch and `treeKeyIntent` is unchanged.
- **`FileTree` marks the folder you are in.** Every branch enclosing the
  selection carries `is-onpath`. Derived inside the component from the flattened
  rows by the new exported `activePathIds(rows, selectedId)`, so no caller has
  to compute an ancestry the tree already knows and the mark cannot disagree
  with the selection.
- **`FileTreeNode.group?: string`.** When one level holds more than one kind of
  thing, `flattenTree` emits a `{ kind: "group", label }` row above each run.
  A level whose nodes carry no group draws no headers, so nothing existing
  changes. `FileDrill` follows the same rule on a phone. Group rows are
  `role="presentation"`: not selectable, not focusable, skipped by the keymap.
- **`DocumentBar` gained `variant` (`"file" | "title"`) and `onNameChange`.**
  The `title` variant draws the name in the sans face at prose weight so a
  record's title reads as a title rather than as a filename, and
  `onNameChange` turns it into an in-place input. This is what lets a note and
  a file share one header, superseding
  `file-viewer-and-editor-2026-08-22.md` section 4's split between a note (a
  record with an `ActionBar`) and a document (a file with this bar). The old
  argument against sharing was that it would print a title twice in mono; that
  was an argument against the mono face, which the variant answers.

### Changed

- **`.ws-docbar` is sticky.** The header stays on screen while the document
  under it scrolls. It carries Save, the form toggle and the dirty dot, so
  scrolling it away took the document's controls with it.
- **`.ws-doc--full` applies on a desktop too**, via a `--doc-editor-h` custom
  property the desktop media query overrides. It was phone-only, which left a
  desktop document as a page scrolling around a `60vh` editor scrolling inside
  it. Consumers that already pass `takeover` are unaffected; a consumer that
  wants the desktop shape now applies `ws-doc--full` on both.

## 0.6.4 - 2026-08-27 - a mermaid fence renders as a diagram

**Additive.** No export moved and no prop changed. `Markdown` now draws a
fenced block tagged `mermaid` as a diagram instead of as a code block, on every
surface that renders markdown, with no per-surface configuration and no new
prop. A caller that passes its own `components` still wins: the kit's overrides
are merged UNDER the caller's, so a consumer that already replaces `pre` keeps
its own behaviour.

### Added

- A `mermaid` fence renders as an SVG diagram. `mermaid` is a new OPTIONAL peer
  dependency (`>=11`): it is reached through a dynamic `import()` and never
  through a top-level one, so a consumer that does not install it pays nothing
  and a fence merely stays a code block. It is around a megabyte gzipped, which
  is why it is loaded on first sight of a diagram and lands in its own chunk.
- The diagram is painted from the kit's own tokens (`--fg`, `--bg-inset`,
  `--border-strong`, `--muted`, ...) rather than from mermaid's palette, and it
  is redrawn when `data-theme` or `data-palette` changes on `<html>`. Restyling
  by setting tokens keeps working, including here.
- `.ws-mermaid` in `styles.css`: the surface a drawn diagram sits on.

### Notes

- Security is unchanged. `Markdown` still does not render raw HTML from the
  document (there is no rehype-raw and there will not be one). mermaid runs with
  `securityLevel: "strict"` and `htmlLabels: false`, which keeps its DOMPurify
  pass on and its click handlers off; the SVG it returns is the only markup
  injected, and it is mermaid's output, not the author's.
- A diagram that does not parse renders as the code block it would have been,
  with the parse error on a `Notice` beside it. That is the common case while a
  reply is still streaming, not an edge case, so it never blanks or throws.

## 0.6.3 - 2026-08-25 - the transient backoff schedule is a named export

**Additive.** One new export, `TRANSIENT_BACKOFF_MS`. Nothing existing moved:
`withTransientRetry` now defaults its `backoffMs` parameter to it instead of to
an inline `[400, 1200, 2500]`, so every existing call behaves byte-identically.
The parameter type widened from `number[]` to `readonly number[]`, which accepts
strictly more.

### Added

- `TRANSIENT_BACKOFF_MS` - the three waits (400ms, 1200ms, 2500ms; 4.1s total)
  a transient network failure is ridden out on. It was a default parameter,
  which meant a consumer that does its own retrying (a query client's `retry`
  plus `retryDelay`) could only restate it, and did: one consumer restated the
  count and took an exponential default, so two mechanisms waited 4.1s and 7.0s
  for the same backend restart. Derive both the count
  (`TRANSIENT_BACKOFF_MS.length`) and the delays from this array.

## 0.6.2

### Fixed

- `useEditMode.save()` no longer leaves a rejecting `onSave` as an unhandled
  promise rejection. It catches, exposes the message as `saveError`, and stays
  in edit mode so the draft is not lost. `begin()` and `cancel()` clear it.
  Additive: `save()` never rejected usefully before, so existing call sites that
  wrapped it in `.catch(() => {})` remain correct and the guard is now dead code
  they may drop at their leisure.

## 0.6.1 - 2026-08-23 - an overlay can start below chrome the shell does not own

**Additive.** One new modifier. Nothing existing moved: `--bare`, `--dropdown`,
`--under-bar1` and `--sheet` are byte-identical, so the place menu, an app's
bottom sheet, `Modal` and `ConfirmDialog` are all unaffected.

- **New: `.ws-overlay--under-chrome`.** `top: var(--ws-overlay-top, 0px)`. For a
  scrim that must start below a strip of chrome the SHELL does not own, whose
  height is therefore content rather than a token. The caller measures the strip
  and publishes the offset as `--ws-overlay-top`; the default keeps an
  unmeasured scrim filling its whole region rather than collapsing it.

  It is `--under-bar1` at a different altitude, and the split is deliberate. Bar
  one belongs to the shell, so the shell knows its height (`--bar1-h`) and can
  say so in CSS. A chat toolbar or an app's own header is drawn by a page, so
  the only honest source is a measurement at runtime.

  First consumer: the chat's ticket sheet, whose scrim has now been wrong in
  both directions. It dimmed only the chat column and left the page bright to
  either side; the correction handed it the unmodified overlay, which covered
  the entire viewport including the top nav and the chat toolbar. Combined with
  the shell's overlay host (content region, so the dock and the phone's bars are
  outside it already), this modifier is the remaining half: full page width,
  starting under the toolbar, down over the composer.

## 0.6.0 - 2026-08-23 - one overlay backdrop, not four

**Breaking.** `.ws-placemenu__scrim` is REMOVED. There is now exactly one
dimming surface in the system, `.ws-overlay`, plus four sanctioned modifiers,
and anything that puts a surface over the app composes them.

Reported by the operator on 2026-08-23, having counted the copies: the chat work
sheet dimmed only the chat column and left the rest of the screen bright, the
phone place menu had a private copy with no blur, and an app's More sheet had a
third with a raw `rgba()` fill and a raw z-index. "why do we have multiple
randomly implemented poorly implemented different overlay backdrops... the blur
one is better than this. we shouldnt have multiple implementations of the same
thing done different like this its a terrible way to make an app." The blur one
is `.ws-overlay`, so the others became it.

- **Removed: `.ws-placemenu__scrim`.** It was `.ws-overlay` with a different top
  edge, a lower layer and no blur.
  **Migration:** render
  `class="ws-overlay ws-overlay--bare ws-overlay--under-bar1 ws-overlay--dropdown"`.
  That composition is byte-for-byte the old geometry plus the blur.
- **New: `.ws-overlay--bare`.** `display: block; padding: 0`. For a scrim that
  holds nothing: a dismiss surface, where the panel positions itself. The base
  rule centres a `.ws-modal` and pads around it, which a bare scrim does not
  want.
- **New: `.ws-overlay--dropdown`.** `z-index: var(--z-dropdown)`. For a scrim
  that belongs under a panel on the dropdown layer rather than over the whole
  app on the modal layer.
- **New: `.ws-overlay--under-bar1`.** Starts the scrim at bar one's hairline
  instead of the top of the viewport, so the tile that opened the panel stays
  visible and stays live.
- **Fixed: the phone media query no longer re-applies padding to
  `.ws-overlay--sheet`.** `@media (max-width: 560px) { .ws-overlay { padding: ... } }`
  sat below `.ws-overlay--sheet { padding: 0 }` at equal specificity and undid
  it, so a bottom sheet was inset 16px from the screen edges on exactly the
  device it exists for. The query now excludes `--sheet` and `--bare`. If you
  were compensating for that inset in an app, drop the compensation.
- **Fixed: `prefers-reduced-motion` now stills every overlay**, not only the
  place menu's, because there is one rule to still.

No TypeScript surface changed; `api/ui.api.md` is unchanged by this release.

## 0.5.25 - 2026-08-22 - the chrome around an open file

**Additive.** The file viewer and editor had two near-identical headers (the
Files tab's and Memory's) and neither matched the mockup it is judged by. This
is that header, once, as a primitive:
`docs/platform-redesign/shell-mockup-spec-2026-08-22.md` section 6.

- **New: `DocumentBar`.** The header a file wears while it is open. Desktop 46px
  with the path in mono `--faint`, the filename in mono `--fg`, a 6px `--warn`
  dirty dot, then right-aligned actions; a phone gets 44px and a COMBINED
  back-plus-filename control (a 13px chevron immediately followed by the name,
  both `--accent`, one tap target) in place of the path. The `path` and `meta`
  slots are desktop only; on a phone the name has the width.
- **New: `documentBar(input)` plus `DocumentBarInput` / `DocumentBarModel` /
  `DocumentForm`.** The pure rule for what the header shows, from four booleans
  (`writable`, `hasRenderedForm`, `form`, `dirty`). It is what keeps a markdown
  document and a source file consistent: same header, toggle on or off. Tested
  in `documentBar.test.ts`.
- **THE DIRTY DOT IS CONDITIONAL.** The mockup draws it unconditionally because
  its editor has exactly one state, "editing with unsaved changes". Here `dirty`
  is a real boolean from `useEditMode`, so the dot renders only when the document
  is dirty, and it REPLACES the sticky ActionBar's "unsaved" pill on this
  surface rather than sitting beside it.
- **New CSS: `.ws-doc`, `.ws-doc--full`, `.ws-doc__body`, `.ws-docbar*`.** The
  document surface. `--doc-gutter-w` is 40px on a phone and 56px on a desktop
  and is applied to `.cm-gutters` from the stylesheet, so `Editor` itself is
  unchanged. `.ws-doc--full` is the phone takeover: the document escapes
  `<main>`'s page inset, the editor loses its side borders and fills the
  viewport below the bars.
- No export was removed or renamed, and nothing changed shape.

## 0.5.24 - 2026-08-22 - the file browser, measured against the mockup

**Additive, plus two corrections.** `FileDrill` and `FileTree` shipped earlier
the same day, built from the prose design doc; neither was ever measured against
the mockup they are judged by. This is the geometry, from
`docs/platform-redesign/shell-mockup-spec-2026-08-22.md` sections 4 and 5.

- **New: `drillUpIntent(trail)` and the `FileDrillUpIntent` type.** Up at the
  ROOT of a directory leaves the directory page entirely (section 4.1) rather
  than doing nothing, and the kit cannot know what leaving means for the page it
  is drawn on. So the rule is decided in `fileTree.ts` and the destination is the
  caller's, through the new optional **`onExit`** prop on `FileDrill`. Without
  `onExit` the Up bar is not drawn at the root at all: a control that does
  nothing is worse than an absent one. `drillUp` is unchanged and still exported.
- **New: `drillTrailText(trail)`.** The Up bar's label: every trail label, root
  first, joined with " / ".
- **New: `FileTreeNode.dirty`.** Optional. Draws section 5's 6px amber dot at the
  right of a desktop tree row.
- **`FileDrill` redrawn.** The Up control is now its OWN 40px full-width row
  above the list and the WHOLE BAR is the tap target (a 13px arrow plus the word
  "Up" in `--accent`, then the trail in `--faint`, both mono 12px; no trail
  segment is separately tappable). Rows are 52px, FULL BLEED (they pull back
  through `--page-pad-x`), `padding: 0 14px`, `gap: 11px`, a faint hairline
  between them, and no indentation at any depth: a phone list does not indent,
  depth is expressed by drilling. A folder is the SANS face with a child count
  and a chevron; a file is the MONO face with a relative time and no chevron.
  New classes: `.ws-drill__up`, `__up-go`, `__up-trail`, `__icon`, `__meta`,
  `__block`, `.ws-drill__row--folder`, `--file`, `--pending`. **Removed:**
  `.ws-drill__trail`, `.ws-drill__cursor`, `.ws-drill__detail` (now `__meta`).
- **`FileTree` redrawn.** 27px rows, radius 6, gap 7, `padding-right: 8`, indent
  `8 + depth * 15`, a 12px icon at `opacity .75`, a 13px ellipsized label, and an
  optional dirty dot. **No disclosure triangles anywhere** (section 5 has none):
  the row opens its own branch, the arrow keys still do, and a folder is told
  from a file by the FACE, sans versus mono, exactly as on the phone. The row no
  longer draws `node.detail` either: the 252px column has no width for a meta
  column and section 5 does not have one. New classes:
  `.ws-tree__row--folder`, `--file`, `.ws-tree__dirty`. **Removed:**
  `.ws-tree__twist`, `.ws-tree__twist--empty`, `.ws-tree__detail`.
- **No new colour.** The faint hairline both surfaces use is
  `color-mix(in oklab, var(--border), transparent 45%)`, derived from the token
  rather than added to the palette, so it follows all eight palettes in light and
  dark. The dirty dot is `--warn`.

## 0.5.23 - 2026-08-22 - the phone's place menu, and the page toolbar's baseline

**Additive, plus one correction and one deletion.** No export changed, so
`api/ui.api.md` is unmoved. The shell's phone surfaces were built from the prose
design doc without the mockup being opened; this is the geometry, from
`docs/platform-redesign/shell-mockup-spec-2026-08-22.md` sections 2 and 3.

- **New: `.ws-placemenu`, `.ws-placemenu__scrim`, `__row`, `__row--platform`,
  `__tile`, `__name`, `__group`, and the `ws-rise` keyframe.** The phone's place
  menu is a DROPDOWN anchored under bar one's tile: a 300px panel that rises
  10px and fades, over a scrim that starts at bar one's hairline and never
  covers the bar. It is not a drawer and not a sheet, and the kit's generic
  `Menu` does not produce it (no scrim, a 16rem cap, toolbar-sized rows). Markup
  and focus behaviour live in the consumer, as the rest of the shell's do.
- **New shell-local sizes on `.ws-shell`:** `--placemenu-w`, `--placemenu-max-h`,
  `--placemenu-row-h`, `--placemenu-row-h-sm`, `--placemenu-tile`. Same kind of
  value as `--bar1-h`: the panel's own box, not spacing.
- **Fixed: `.ws-tabbar__tab` is `align-items: center`, not `flex-end`.** The
  parent is `align-items: stretch`, so a centred tab is the bar's full height
  and its 2px underline lands on the BAR's bottom edge while every label stays
  on one baseline. `flex-end` drew the same underline and rode the labels low.
- **Removed: `.ws-bar1__menuletter`.** It styled the rows of the generic `Menu`
  that the phone's place menu used to be, and nothing emits it now. Recorded
  here rather than treated as a breaking class removal: it was only ever written
  by the Workshop shell's own mobile bar, and its one declaration (`font-size`)
  never applied, because the glyph it wrapped sets its font size inline.

## 0.5.22 - 2026-08-22 - two classes that were referenced and never defined

**Additive only.** Two new rules, one dead className removed from `Map`. No
export moved and no class was renamed.

A `className` that no rule matches renders UNSTYLED, and nothing caught it:
`tsc` sees a string, the design gate looks for inline hex and emoji, and the
tests cover pure modules with no DOM. `scripts/check_undefined_css.py` now gates
this repo-wide at zero.

- `.ws-chat-card` now sets `min-width: 0`. `Chats.tsx` had always passed the
  class WITH a comment explaining that it carries exactly this, to stop a long
  snippet setting the card's min-content width and giving the page a horizontal
  scroll on a phone. The rule was never written, so the bug its own comment
  described was live the whole time.
- `.ws-bc__link` joins the existing `.ws-link` rule instead of being a BEM child
  with no rule and no `.ws-bc` parent. Four pages use it for a quiet "view all"
  link beside a section heading; it had been rendering as a default browser link.
- `Map` no longer passes `ws-meta`, which was defined nowhere. Its inline tokens
  were already doing the styling.

## 0.5.21 - 2026-08-22 - text fields stop zooming an iOS page in

**Additive only.** Two new tokens and one coarse-pointer block. No export moved,
no class was renamed, and fine-pointer rendering is unchanged.

Reported on a phone, 2026-08-22: "focusing any text input, textarea, or the code
editor zooms the page in." iOS Safari zooms the viewport whenever a focused text
field computes below 16px and does not zoom back out. Every size in this kit's
type scale is below 16px, so every editable surface did it.

Added:

- `--fs-field` (`var(--fs-base)`, 14px) and `--fs-field-code`
  (`var(--fs-sm)`, 13px) in `tokens.css`. `.ws-input`, `.ws-select`,
  `.ws-textarea`, `.ws-readonly`, `.ws-searchfield input` and
  `.ws-stepper__input` now name `--fs-field`; the `Editor` theme names
  `--fs-field-code`. The values ARE the scale values, so nothing moves on a
  desktop.
- A `@media (pointer: coarse)` block in `components.css` ("THE 16px FLOOR")
  that raises both tokens to `1rem` on `.ws-root`, floors
  `.ws-searchfield--lg input`, `.ws-titleedit__form .ws-input` and
  `.ws-editor .cm-textfield`, and widens `.ws-stepper__input` to `3.25rem` so a
  four-digit value still fits.

`.cm-textfield` is the code editor's search panel, which `Editor` turns on. It
is the one editable surface this kit does not author: CodeMirror's base theme
sizes it at 70%, about 11px, the smallest focusable field in the app. It is
floored in the coarse block rather than in the `Editor` theme so the desktop
panel stays exactly as CodeMirror draws it.

**A pointer query, not a breakpoint.** An iPhone Pro Max in landscape is 932px
wide, so a max-width media query misses the exact device with the problem.
`(pointer: coarse)` asks what the primary input device is, which is the real
question, and it matches the focus-ring block already in this sheet.

**Not the viewport meta tag.** `user-scalable=no` and `maximum-scale=1` stop the
zoom by taking pinch-zoom from everyone, which fails WCAG 1.4.4, and iOS has
ignored both since iOS 10.

**Control heights do not move**, which was measured rather than assumed. The
only fixed height any editable control has is `--control-h` (32px); less the 1px
borders that is a 30px content box, and 16px at the UA `normal` line-height is a
~20px line box, so it centres with room either side. `--control-h-sm` and
`--control-h-lg` carry no editable control at all. Raising the height tokens
would have resized every button, chip and pager cell on the phone for nothing.

**The editor is sized on `&`, not on `.cm-content`.** `&` is the one ancestor
both `.cm-content` and `.cm-gutters` inherit from, and `.cm-scroller` sets the
line-height for both, so one declaration scales code and line numbers by the
same factor and the gutter cannot drift out of alignment.

## 0.5.20 - 2026-08-22 - `FileDrill`: the same tree, one level at a time

**Additive only.** The phone shape of `FileTree`, from
`docs/platform-redesign/navigation-shell-2026-08-20.md` section 8.6: "the file
tree is the page content and drills in place, with Up walking the tree".
`AppFilesTab.tsx` is the first consumer and branches on `useIsWide()`: the rail
above 900px, the drill below it.

Added, on the **core entry**:

- `FileDrill`, `type FileDrillProps`: one level on screen, a folder tap
  descends, an Up control walks back out. Props: `adapter`, `trail`, `onTrail`,
  `selectedId`, `onSelect`, `maxRows`, `emptyTitle`, `emptyHint`, `label`,
  `className`.
- `drillTrail`, `drillCursor`, `drillParent`, `drillInto`, `drillUp`,
  `type FileDrillTrail`: the pure trail rules (`src/fileTree.ts`, tested in
  `fileTree.test.ts`).
- `.ws-drill`, `.ws-drill__trail`, `.ws-drill__cursor`, `.ws-drill__level`,
  `.ws-drill__row`, `.ws-drill__label`, `.ws-drill__detail`, `.ws-drill__into`,
  `.ws-drill__aside` in `components.css`.

**It takes the SAME `FileTreeAdapter`.** A surface that shows a rail on a
desktop and drills on a phone writes one adapter and branches on width; nothing
about the data is said twice, and the level store is the same three transitions
(`levelLoading` / `levelReady` / `levelFailed`), so there is no second loader.

**UP IS STRUCTURAL, NOT BACK** (section 8.1). The way out of a level cannot be
derived from a node, which knows its own id and nothing about what contains it,
and it is not browser history either. It is RECORDED, as the trail of branches
the reader descended through, and the trail lives in the CALLER so that opening
a file and pressing Up returns to the level that file is in. At the root, Up is
a defined no-op that returns the same trail.

Four list states as usual: skeleton rows while a level loads, an empty
`StateBlock` naming the folder with an instructional hint, an error `StateBlock`
naming the folder that failed with Retry (the level IS the page here, so a
failed level is a region rather than a line) with the trail still above it so Up
works out of a folder that will not load, and a capped level reporting the
remainder. Rows are full-width 44px touch targets.

## 0.5.19 - 2026-08-22 - `FileTree`: one tree navigator, lazy per level

**Additive only.** P2 of
`docs/platform-redesign/file-viewer-and-editor-2026-08-22.md`. Nothing consumes
it yet: `AppFilesTab.tsx` and `NoteSidebar.tsx`, the two hand-rolled trees it
replaces, migrate in P4 and P5.

Added, on the **core entry** (no new dependency, so it is a normal primitive
rather than a subpath like `./editor`):

- `FileTree`, `type FileTreeProps`: a tree that renders a node hierarchy and
  fetches each level only when it is opened. Props: `adapter`, `selectedId`,
  `onSelect`, `defaultOpenIds`, `maxRowsPerLevel`, `emptyTitle`, `emptyHint`,
  `label`, `className`.
- `type FileTreeAdapter`, `type FileTreeNode`, `type FileTreeNodeKind`,
  `type FileTreeIconHint`, `type FileTreeLevel`: the data seam.
- `flattenTree`, `treeKeyIntent`, `toggleBranch`, `nodesById`,
  `FILE_TREE_MAX_ROWS_PER_LEVEL`, `type FileTreeLevels`,
  `type FileTreeLevelState`, `type FileTreeRow`, `type FileTreeIntent`: the pure
  row model and keymap (`src/fileTree.ts`, tested in `fileTree.test.ts`), for a
  caller that draws its own rows and should not invent a second one.
- `.ws-tree`, `.ws-tree__row`, `.ws-tree__twist`, `.ws-tree__icon`,
  `.ws-tree__label`, `.ws-tree__detail`, `.ws-tree__aside` in `components.css`.

**It takes an ADAPTER, not a URL and not a fixed data shape.** The adapter is a
root node plus `children(node)` returning that node's children, one level per
request. So an app's source directory and the Memory scope tree are this same
component with two different adapters, and nothing about HTTP, paths or a
response shape is in the primitive. A node carries `id`, `label`,
`kind: "branch" | "leaf"` and an optional `icon` hint; a level may also report
`filtered`, which the tree shows as a line rather than hiding silently. Section
5.3 of the design doc records why this is a kit primitive and not headless-tree,
react-arborist, react-complex-tree or MUI X Tree View.

**A level that fails does not destroy the levels already open.** The level store
is keyed by parent id and a failure replaces exactly one key, so the failure is
a `Notice` at that branch's indent with its own Retry and every other branch
stays on screen. That is the region-versus-line rule: the space would not have
been empty if the failure had not happened. The ROOT level is the other case,
and it gets all four list states as regions, because there the space would be
empty: `Skeleton` rows while loading, an empty `StateBlock` with an
instructional hint, an error `StateBlock` naming the root and offering retry,
and a per-level cap (`FILE_TREE_MAX_ROWS_PER_LEVEL`, 500) whose remainder is
reported as one overflow line.

Keyboard: up and down move, right opens a branch and then steps into it, left
closes it and then steps out to the parent, Enter selects, Home and End jump.
One row is in the tab order at a time (roving tabindex).

Not built, and deliberately (design doc section 8): **no drag and drop** and
**no virtualization**. The row model is already a flat list of keyed rows each
carrying a depth, so `@tanstack/react-virtual` drops in later without a new row
shape, once a real directory is measurably slow.

## 0.5.18 - 2026-08-22 - The editor: `@workshop/ui/editor`, and the `--syn-*` color layer

**Additive only.** P1 of
`docs/platform-redesign/file-viewer-and-editor-2026-08-22.md`: one text and code
editor for the whole system, on CodeMirror 6. Nothing consumes it yet; the file
tree (P2) and the file API (P3) are separate.

Added, at the new **`@workshop/ui/editor`** subpath:

- `Editor`, `type EditorProps`: a CodeMirror 6 editor themed entirely from
  tokens. Props: `value`, `onChange`, `path`, `profile`, `editable`, `height`,
  `className`, `ariaLabel`. `editable={false}` removes `contenteditable`
  (`EditorView.editable`) as well as refusing modification
  (`EditorState.readOnly`), so **no virtual keyboard opens on a phone** for a
  file you may not write. It is not drawn as a disabled control and it never
  uses the refusal cursor: `editable` is a fact about the path, not a mode.
- `languageForPath`, `type EditorProfile`, `type PathProfile`,
  `type PathLanguage`: the path to language and profile table, as a pure
  function (`src/editorProfile.ts`, tested in `editorProfile.test.ts`). Three
  profiles and no fourth: `prose` (markdown, soft wrap, history, no line
  numbers, no folding), `code` (language by extension, line numbers, folding,
  bracket matching, indent, search, history) and `view` (highlighting only).
  Adding a language later is a row in that table.

The CodeMirror packages are **optional peer dependencies**, exactly as
`maplibre-gl` is for `./map` and `react-markdown` is for `./markdown`:
`@codemirror/state`, `@codemirror/view`, `@codemirror/language`,
`@codemirror/commands`, `@codemirror/search`, `@codemirror/lang-markdown`,
`@codemirror/language-data` and `@lezer/highlight`. **The core entry gains no
dependency**, and a consumer that never opens a file installs none of them.

Added tokens, generated across all eight schemes in both modes:

- `--syn-comment`, `--syn-string`, `--syn-keyword`, `--syn-number`,
  `--syn-function`, `--syn-type`, `--syn-property`, `--syn-punctuation`,
  `--syn-invalid`.

Nine roles, not the thirty a TextMate grammar offers. `--syn-comment` and
`--syn-punctuation` are muted neutrals rather than a hue; `--syn-invalid` is
emitted as `var(--danger)` and is not a tenth color. The six that carry a hue
sit on a ring **anchored at the scheme's own base hue** and take the scheme's
own `you` chroma trim, so a file read in Fern is green-led where the same file
in Clay is terracotta-led, and a quiet scheme reads code as quietly as it reads
everything else. Every one of them is solved to 4.5:1 against `--bg`, `--card`
and `--bg-inset` by the same solver every other text tone uses, in all sixteen
scheme x modes, and pinned in `palette.test.ts`.

Added `.ws-editor` to `components.css`: the container only (a flex column, so
`height` on the wrapper reaches the editor and its absence lets the editor grow
with its content). The editor surface itself is themed in `Editor.tsx`, because
`EditorView.theme()` takes plain CSS strings and CodeMirror injects its own
stylesheet. Switching palette or theme therefore needs **no editor
reconfiguration at all**; where reconfiguration is genuinely needed (profile,
language, editability) it goes through a `Compartment`, never a teardown, so a
change never costs the selection, the scroll position or the undo history.

## 0.5.17 - 2026-08-20 - App-facing capabilities: polling, time, bytes, HTTP errors, storage, breadcrumbs, native bridge

**Additive only.** Stage 4b of the app-separation plan
(`docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md`):
`frontend/src/pages/apps/**` was reaching into platform-internal frontend
modules, which is the frontend mirror of the backend coupling Stage 4 removed.
Everything below moved here from `frontend/src/lib/*` and `frontend/src/native/*`
verbatim (their own headers explain the "why", unchanged); every in-repo call
site (platform pages too, not just app pages) was updated in the same change so
there is exactly one copy of each.

Added:

- `usePoll`, `pollShouldRun`, `type WhenHidden`: the one polling hook, pauses
  while the tab is hidden and catches up on return.
- `timeAgo`, `timeAbsolute`, `timeAbsoluteShort`, `formatDuration`,
  `timeAgoSeconds`: the one set of date/time formatters.
- `formatBytes`, `formatVramBytes`, `formatMiB`, `VRAM_UNKNOWN`: byte-size
  formatting.
- `HttpError`, `ERROR_BODY_CAP`, `isTransientNetworkError`,
  `withTransientRetry`, `shortHttpError`: shaping a failed request into what
  is safe to put on screen.
- `userStorage`, `setCurrentUser`, `registerDeviceScoped`, `isDeviceScoped`,
  `namespacedKey`, `isNamespacedKey`, `planMigration`,
  `_resetCurrentUserForTest`, `_resetDeviceScopedForTest`, `type UserId`,
  `type StorageExemption`: the namespaced-storage engine (active-user
  namespacing, the one-time legacy-key migration). Ships with ZERO built-in
  exemptions; a host registers its own device-scoped keys via
  `registerDeviceScoped`, which is the generic half of what was Workshop's
  `lib/storage.ts` (that file now instantiates this engine with Workshop's own
  exemptions, and stays where it was).
- `BreadcrumbProvider`, `useCrumbLabel`, `useCrumbTrail`,
  `useBreadcrumbOverride`, `type Crumb`: a page telling its host what its
  current entity/trail is. The RESOLUTION half (mapping a route to its default
  trail) is host-specific and stays in Workshop's `components/appNav.tsx`.
- `AppSettings`, `SettingsSection`, `SettingsRow`: the collapsible app-settings
  slot, moved as-is: it was already built entirely from kit primitives.
- `native`, `isNativeShell`, `hasCapability`, `isPaidTier`, `onNativeEvent`,
  `type Capability`, `type WorkshopNative`, `type BridgeEventTopic`,
  `getCurrentFix`, `startDeviceTracking`, `stopDeviceTracking`,
  `backgroundTrackingSupported`, `useNative`, `useNativeEvent`,
  `useTrackingStatus`, `type NativeInfo`: the Workshop native-bridge
  accessors. Their wire types are a LOCAL mirror of `shared/native-bridge.ts`
  (`packages/ui/src/native/types.ts`), not an import of it: the monorepo-only
  `shared/` alias broke the kit's declaration build (files landed at
  `dist/packages/ui/src/*` instead of `dist/*`) because `tsc
  --emitDeclarationOnly` tried to also emit a declaration for the resolved
  file. See that file's header.
- `type AppChatDockRenderer`, `type AppChatDockOptions`: type-only contract
  for a shell-provided "render my assistant dock" prop. `AppChatDock` itself
  (wired to Workshop's chat threads and its manifest endpoint) is NOT promoted:
  it is platform furniture, not a design-system capability. An app page now
  receives a `renderChatDock` function prop from the shell
  (`frontend/src/apps/registry.tsx`) instead of importing the component.

Not promoted, deliberately: `components/appNav.tsx`'s `AppGroupNav` (needs the
platform's app registry) and `frontend/src/api/schema.gen.ts` (a generated
backend wire-contract type, not platform UI behaviour; `Location.tsx` keeps
this one cross-boundary import with a comment explaining why).

## 0.5.16 - 2026-08-20 - The navigation shell: dock, page column, phone toolbars

**Additive plus one removal, CSS only (no export, prop or token changed).** The
top toolbar is gone and a three-column shell replaced it. Design record:
`docs/platform-redesign/navigation-shell-2026-08-20.md`.

Added, all under `THE SHELL` in `components.css`:

- `.ws-shell`, `.ws-shell__stage`, `.ws-shell__cols` — the frame. A row at
  900px and up, a column below it.
- `.ws-rail`, `.ws-rail__tile`, `.ws-rail__rule`, `.ws-rail__spacer`,
  `.ws-rail__avatar` — the 60px primary rail (desktop). **It is called `rail`
  and not `dock` because `.ws-dock` is already the floating chat dock panel.**
  The concept is still "the dock" in the design doc and in the code.
- `.ws-pagecol`, `.ws-pagecol__inner`, `.ws-pagecol__title`,
  `.ws-pagecol__list`, `.ws-pagerow`, `.ws-pagerow__label` — the 252px
  secondary column. It collapses by clipping to zero width AND translating its
  panel fully left, which is what slides it out from under the rail with the
  content arriving behind it rather than covering it over.
- `.ws-bar1__wrap`, `.ws-bar1`, `.ws-bar1__tile`, `.ws-bar1__titlebtn`,
  `.ws-bar1__title`, `.ws-bar1__caret`, `.ws-bar1__avatar`,
  `.ws-bar1__menuletter` — the phone's first bar.
- `.ws-tabbar__slot`, `.ws-tabbar`, `.ws-tabbar__tab` — the phone's page
  toolbar. **`__tab`, not `.ws-tab`,** which is the Tabs primitive.
- `.ws-trail`, `.ws-trail__item`, `.ws-trail__sep`, `.ws-trail__link`,
  `.ws-trail__cur` — a trail at the head of the content column, rendered only
  where the path goes deeper than the page column already says.

Removed: `.ws-topnav`, `.ws-brand`, and (from the app's own `styles.css`)
`.ws-breadcrumbs`, `.ws-brand__home`, `.ws-bc__*`, `.ws-sectionnav-*`,
`.ws-iconbtn`. Nothing renders them: `BreadcrumbBar` and `SectionNav` were
deleted with the toolbar. An out-of-repo consumer that hand-wrote a
`.ws-topnav` band has to supply its own rule now.

`.ws-app--chatfull`'s selectors changed from child to descendant, because the
shell puts two boxes between `.ws-app` and `.ws-app__body`.

## 0.5.15 - 2026-08-19 - Popover/Menu clamp position instead of picking an edge

**Fix, internal only (no public prop or export changed).** `useAnchoredPlacement`
used to pick between exactly two horizontal positions: flush with the trigger's
left edge, or flush with its right edge, whichever overflowed less. That is only
correct when the surface is comfortably narrower than the clip box (a toolbar
menu near a screen edge). Once the surface approaches the clip box's own width
(Popover's panel is 18-24rem, a real fraction of a phone's viewport), a trigger
in the MIDDLE of the clip box can overflow past both edges no matter which one
it is pinned to. Reported 2026-08-19: a chat ref chip's dropdown (which, unlike
a toolbar button, can land anywhere across a wrapped line of text) opened
"slightly hanging off the screen to the side" on mobile.

Replaced the binary `align: "start" | "end"` with a clamped pixel offset
(`insetStart`), applied as an inline `left` style rather than a `--start`/`--end`
modifier class (now removed from `.ws-popover__surface` and `.ws-menu__list`).
The surface slides to stay fully inside the clip box at any trigger position,
and only anchors to the clip's own edge (rather than the trigger's) in the
genuinely-unfittable case where the surface is wider than the box has room for
at all. `menuPlacement.ts` and `useAnchoredPlacement.ts` are internal, not
re-exported from the package entry point, so no consumer-facing API moved.

## 0.5.14 - 2026-08-18 - Markdown forwards `components` and `urlTransform`

**Compatible (additive, no name moved).** `Markdown` gains two optional props,
`components` and `urlTransform`, passed straight through to react-markdown.
Default rendering is unchanged when they are absent. They let a consumer render
an in-app link (Workshop's `workshop:<kind>/...` refs) as a chip instead of a
bare `<a>`, and keep a custom URL scheme from being stripped. Pass hoisted
(constant) objects, not per-render literals, or the parse memo is defeated.

## 0.5.13 - 2026-08-18 - the segmented control's selected option gets a ring

**Compatible (no name added or moved).** A CSS-only release: `.ws-segmented`.

The sliding thumb's fill (`--card`) and the track underneath it (`--field-bg`)
sit only about 4% of lightness apart in both modes, and `--shadow-card` is
literally `none` in dark, so the selected option was barely distinguishable
from the rest of the control. Operator, 2026-08-18: "the background color for
the selected option... is just too close to the background of the container."

The thumb (and the two non-JS fallback selected states it has) now also carries
`0 0 0 1px var(--card-ring)`, the same faint ring a card gets in dark mode per
the design system's own rule (float on shadow in light, a ring in dark).
`--card-ring` is `transparent` in every light scheme, so light mode is
unchanged and keeps doing the job with the shadow alone.

## 0.5.12 - 2026-08-15 - a link is underlined, not tinted, and now actually is

**Compatible (no name added or moved).** A CSS-only release: `.ws-link`.

The design system has specified this treatment in detail for months and nothing
implemented it. There is no base `a` rule in `tokens.css`, `components.css` or
the app's `styles.css`, so every link was dressed by whatever component it sat
in, and a link that no component dressed rendered in **the user agent's blue**:
a hue from no palette, on a dark surface, failing contrast and meaning nothing.
Found on TicketDetail's "All runs", whose `.ws-sectionhead__link` set a margin
and a font size and no colour at all.

At rest a link inherits its ink and is marked by an underline at 45% of the
current colour, so the hard case is free: a link inside a `--you`-filled bubble
is legible with no special case, because it is written in whatever ink that
surface already uses. On hover it takes `--you`, since the act of interacting is
the part that is genuinely yours.

`.ws-sectionhead__link` is covered by the same rule rather than left to be fixed
per caller.

## 0.5.11 — 2026-08-15 — a panel is not a menu

**Compatible (one name added).** `Popover`: a panel anchored to a trigger.

```tsx
<Popover trigger={<Button icon={<Ticket />} />} label="Work" side="bottom">
  ...chips, rows, an answer field, a Send button...
</Popover>
```

`role="dialog"`, `aria-label={label}`, and both controlled (`open` +
`onOpenChange`) and uncontrolled. Closes on Escape and on an outside
pointerdown, and on nothing else. Escape returns focus to the trigger; an
outside click deliberately does not, because a programmatic `.focus()` after a
mouse dismissal re-triggers the global `:focus-visible` ring and reads as a
border the app stuck on the control for no reason.

**Why `Menu` could not be used.** Menu is a list of choices, and two of its
behaviours are load-bearing there and wrong here. It owns arrow-key roving focus
over `[role="menuitem"]`, so it would take Up/Down away from a text field inside
the panel; and it closes on any click landing in a menuitem, because picking one
of a set of actions ends that interaction, whereas a click in a panel is work
being done. It is also `role="menu"`, which tells a screen reader the arrow keys
walk the contents. The first consumer is the chat's Work panel: filter chips,
ticket rows, an answer field and a Send button.

**Why not `Modal`.** A modal traps focus, dims the page and sits in the middle
of the viewport. This is attached to the button that opened it and has to flip
and re-align against every clipping ancestor, which is what `menuPlacement`
already solves. It does not trap focus and it autofocuses nothing: opening a
panel must not raise a phone keyboard or take the caret off the composer.

**Internal refactor, no surface change.** Menu's measuring layout effect and its
`clipBox` walk moved into `useAnchoredPlacement` (not exported), which both
primitives now call, so the flip rule has one implementation rather than two
copies of the arithmetic that has already been got wrong once. The pure half of
`clipBox` came out with it as `narrowClip` in `menuPlacement.ts`, with tests
beside `placeMenu`'s: it is the per-axis narrowing, which is the part that
matters (`overflow-x: hidden` with a visible y is the chat log exactly, and
collapsing the two axes into one test is the bug).

New CSS: `.ws-popover`, `.ws-popover__surface` and its four placement
modifiers, beside the `.ws-menu` rules. Card surface one elevation step below a
modal, roomier padding than a menu row, and the same clamped max-height with an
internal scroll, which the placement rule depends on: the surface is measured
after its own CSS has capped it.

## 0.5.10 — 2026-08-15 — a control is not a flex sibling of the card it decorates

**Compatible (fix, no exports changed).** Three `.ws-notes-grid` / `.ws-note-card`
CSS fixes, all found auditing a regression on the Memory page's Library list:

- `.ws-note-card__link` (the chat card's wrapping `<a>`) had no rule at all, so
  the browser's default blue-underlined anchor painted every descendant inside
  it, including plain-text timestamps. Now `color: inherit; text-decoration:
  none`.
- `.ws-note-card__footer .ws-tag` gets `white-space: nowrap`: a scope tag was
  wrapping onto a second line mid-word inside its own pill at card widths its
  label didn't fit. `Row wrap` on the footer already moves the whole tag to
  the next line as a unit when needed; this only stops it breaking internally.
- New `.ws-note-card__share`: an absolutely-positioned corner slot for a
  per-item control (Memory's chat-sharing badge) that must sit OUTSIDE a
  card's `Link` — a control nested inside the anchor, or added as a second
  flex child of `.ws-notes-grid > li`, either breaks operability (a button
  inside an `<a>`) or breaks layout (two flex children fighting the grid
  track's width, which is what actually shipped and squeezed every chat card
  to a third of its column with the control spilling into the next one).
  Absolutely-positioned children of a flex container are not flex items, so
  this costs the grid's single-child stretch rule nothing.

## 0.5.9 — 2026-08-14 — a bottom bar that is actually at the bottom

**Compatible (one name added).** `PageActionBar`: a page's primary action,
pinned to the bottom of the shell's content region.

It renders into the `OverlayHost` the shell already provides, so
`position: absolute; bottom: 0` is the bottom of that region BY CONSTRUCTION.
That is the point. The two attempts before it were sticky bars inside `<main>`,
which is the scroller, so where they landed depended on the scrollport's exact
box and the scroller's own padding; both left the bar hovering above the bottom
with the list scrolling through the strip underneath, and the second added a
page-level negative margin to compensate, which is the shape of a fix that is
guessing. There is nothing left to compensate for.

`--ws-pagebar-h` is a token because a bar outside the scroller cannot lengthen
the page, so the PAGE has to reserve exactly that much room at the end of its
content. One number, two readers.

## 0.5.8 — 2026-08-14 — a filter row, a board, a quiet tag, and a caret that stops running away

**Compatible (six names added, one optional prop, one CSS fix).**

New: `FilterBar` + `FilterChip` (search, one removable chip per active filter,
an add affordance, actions pinned right), and `Board` + `BoardColumn` +
`BoardCard` (fixed-width lanes in a horizontal scroller). Also `MenuCheckItem`,
a `role="menuitemcheckbox"` row: a menu closes itself when a `[role="menuitem"]`
is activated, because choosing one of a set of actions ends that interaction,
and ticking one of six statuses does not. Arrow keys reach both roles. Both come from the
tickets redesign, and both are built generic because the shapes recur: the
filter row is meant to replace the ad-hoc `Select` rows in Work, Memory and the
runs feed, and a board is a board.

`FilterBar` is a LAYOUT primitive, not a filter engine. What is shared between
those three pages is the row (how it wraps, which child absorbs slack, what a
chip looks like); what is not shared is where the options come from and what
picking one does. Owning that here would mean the kit learning what a scope is.

`Tag` takes `variant="neutral"`: hue in the dot only, pill neutral. For rows of
tags, where six tinted pills read as six competing signals while saying no more
than six dots do. The default is unchanged, so no caller moves.

**The fix.** `.ws-select` paints its chevron with `background-image` +
`background-position`, and every state rule set the `background` SHORTHAND,
which resets the longhands it omits. `:hover` therefore set
`background-image: none; background-position: 0% 0%` — and the transition
named the shorthand too, so the reset ANIMATED: the caret slid from the right
edge toward the left, vanished halfway (an image is a discrete animation type,
so it flips at 50%), and slid back on un-hover. `:focus` did the same, which is
why the caret stayed gone the whole time a select was open, and `[disabled]`
deleted it outright.

Reported 2026-08-14: "any time there's a dropdown it'll transform to the left
and then back to the right in a really weird way and it'll disappear." Every
state rule now sets `background-color` and the transition names the longhand.
Pinned in `frontend/src/styles.test.ts`, which parses the real sheet and fails
on a `background:` shorthand or a bare `background` transition in any rule whose
selector mentions `.ws-select`.

## 0.5.7 — 2026-08-14 — two surfaces, because one token had two opposite jobs

**Compatible (one token added, one token's value changed).** New `--bg-sunken`,
a recessed panel for content holding a separate context. `--bg-block` goes back
to a whisper: light 0.855 to 0.965 L, dark 0.365 to 0.27.

This is the fourth pass on these colours and the first that changes the SHAPE of
the problem rather than the number. Workshop's chat needs a grouped block of tool
calls to be soft (it sits inside a message and must not shout over it) and a
delegation to be unmistakable (it holds a whole separate context). Both demands
were being made of `--bg-block` alone, and in light mode they are unsatisfiable
together: the card is 0.995 and `--bg-inset` was 0.945, so soft-enough-for-the-card
landed ON the delegation, and clear-of-the-delegation was loud against the card.
The delegation was sitting in the only place the block could go.

Three passes moved the block further and further out, fixed the collision, and
produced the opposite complaint. Splitting the surfaces resolves it: the block is
a whisper (~1.09 against its card) and the panel is the deep step (~1.22 against
the block), which also matches what the two things ARE. A group is this turn's
own steps behind a count; a delegation is a separate context, and it carries a
border as well. Fill against frame, soft against deep.

`--bg-inset` is unchanged and still the page-level inset. The lesson worth
keeping: it is a PAGE surface, and reaching for it inside a card is what put two
unrelated things on the same colour.

Bounds are asserted for all sixteen scheme x modes: the block stays under 1.18
against its card, the panel clears 1.15 against the block, and `--muted` and
`--fg` clear AA on both.

## 0.5.6 — 2026-08-14 — `--bg-block` moves as far as the ink on it allows

**Compatible (one token's value changed).** Light 0.90 to 0.855 L, dark 0.30 to
0.365. Separation from `--bg-inset` goes from 1.14 to 1.32 in light and 1.31 to
1.68 in dark.

0.5.5 fixed a genuine collision (3/255 apart in every light scheme) and the
result still read as the same colour to the operator. The lesson is in the test
rather than the value: the old assertion was `>= 1.1`, which proves two surfaces
are not identical and proves nothing about whether anyone can tell them apart. A
measurement that disagrees with the eye is the measurement being asked the wrong
question. The bound is 1.25 now.

The new value is set by the INK, not by taste. A block carries text (`--muted`
rows), and `--bg-block` is deliberately not in `surfacesOf`, so text tones are
not solved against it; pushing the two surfaces further apart to satisfy the eye
would otherwise walk that text under AA. `--muted on --bg-block` is now asserted
at 4.5:1 for all sixteen scheme x modes and sits at ~4.6 worst case, so this is
close to as far as the surface can travel without the rows on it getting harder
to read.

## 0.5.5 — 2026-08-14 — `--bg-block`, a surface that is not on the same side in both modes

**Compatible (one token added).** New generated surface `--bg-block`: a block of
grouped content sitting on a card, guaranteed distinct from BOTH `--card` and
`--bg-inset` in every scheme and mode.

It exists because "distinct" cannot be expressed as a relative fill here.
Elevation is not symmetric across the two modes: `--bg-inset` is the lowest
surface in both (dark 0.205 L against a 0.235 card, light 0.945 against 0.995),
so a block drawn as "the card lifted toward `--fg`" moves away from `--bg-inset`
in dark and lands on top of it in light. The Workshop chat log did exactly that,
and its grouped-tool-call fill came out within 3/255 of the delegation fill in
all eight light schemes while sitting a comfortable 26/255 clear in all eight
dark ones. Correct everywhere it was checked, wrong in half the app.

`--bg-block` is authored per mode in `palette.mjs` (dark 0.30 L, above the card;
light 0.90 L, one clear step BELOW `--bg-inset` rather than between it and the
card) and `palette.test.ts` now asserts the separation from both neighbours for
all sixteen scheme x modes, so it cannot silently close again.

Use it for a run of related rows gathered into one block inside a card. It is not
a hover (`--card-hover`) and not a recessed panel (`--bg-inset`).

## 0.5.4 — 2026-08-14 — a menu stays inside the box that clips it

**Compatible (behaviour fix, no surface change).** `Menu` measured its popover
against the VIEWPORT and nothing else, so a menu inside a clipping scroller
opened outward and was sliced off at the scroller's edge while the viewport
still had room to spare.

The chat is exactly that geometry: `.chat-log` is `overflow-x: hidden` and the
column is capped at `--page-max` and centred, so on a desktop a menu opening
from a message bubble had hundreds of pixels of viewport to its right, never
flipped, and lost half of itself at the log's edge. Reported 2026-08-14.

- **The clip box is the viewport narrowed by every ancestor that clips**, asked
  per axis (`overflow-x: hidden` with a visible y is a real combination, and
  clipping one axis says nothing about the other).
- **`side` is now a preference, not a mandate.** It flips top/bottom on the same
  rule `align` already used: flip only when the preference is genuinely clipped
  and the other way fits. A menu opening upward from the first message in a
  scrolled-to-top list used to open into nothing.
- **Placement is projected, not read back.** It used to measure where the list
  currently sat, so a menu that had flipped measured its flipped position, saw
  no overflow, and stayed flipped even after the window grew back.
- **Re-measures on ancestor scroll**, not only on resize. Scrolling does not
  close a menu, so the side that fitted when it opened may not fit a moment
  later.

The arithmetic moved to `src/menuPlacement.ts` (internal, not exported) so it
can be executed by a test; the kit has no render harness, and this was a bug in
one comparison that nothing could reach.

## 0.5.3 — 2026-08-13 — a link is underlined, not tinted

**Compatible (styles only, no surface change).** Four colour corrections, all of
them a token that was saying the wrong thing:

- **`.ws-md a` no longer takes `--accent`.** `--accent` is an alias of `--you`,
  which fills the operator's own message bubble, so every link in every rendered
  markdown body was painted "this is yours". Links are now `color: inherit` with
  the underline carrying the affordance, and `--you` on hover, where the MINE
  tone actually belongs. Reported against the chat, 2026-08-13. The rationale is
  in `frontend/CLAUDE.md` under "A link is underlined, not tinted".
- **`.chat-voice-orb.is-speaking`** moves `--ok` → `--running`. Speaking is
  liveness, not an outcome, and it now matches its `is-listening` sibling.
- **`.chat-voice-orb.is-idle`** moves `--danger` → `--stopped`. Idle is not a
  failure; `--stopped` exists for exactly this and was unused.
- **`.chat-voice-now`** moves `--accent` → `--live`. The words being spoken right
  now are live, not owned.

Consumers who relied on links being accent-coloured should set `.ws-md a { color:
var(--you) }` locally, but read the CLAUDE.md entry first: the underline was
always doing the work.

## 0.5.2 — 2026-08-12 — a record page is not a list page

**Additive.** `PageHeader` gains `back`, `meta` and `variant`; new primitives
`DetailLayout`, `Props`/`Prop` and `Clamp`, with `.ws-page-header--entity`,
`.ws-page-header__back`, `.ws-page-header__meta`, `.ws-detail*`, `.ws-props*`
and `.ws-clamp*` in `components.css`. Nothing removed, nothing renamed: the
existing `PageHeader({title, sub, actions})` call is unchanged and stays the
default (`variant="page"`).

Every detail page in the app had hand-rolled the same header inside the `title`
slot, and it was wrong the same way four times:

- **A bare lucide arrow inside the `h1`.** An icon with no size rule inherits
  the h1's `font-size`, so the arrow rendered at 24px, and a router `Link`
  takes the MINE hue, so it was also the only coloured thing up there. The way
  OUT of a page outweighed the record on it. `back` renders a named link above
  the title in muted ink, per "a control is not content".
- **A page-name title carrying content.** `--fs-xl` (24px) is right for
  "Tickets" and wrong for one ticket's sentence, which wraps to three lines and
  becomes a banner. `variant="entity"` sets the title at reading size and lets
  it wrap.
- **A decorative entity icon.** A ticket glyph on the ticket route marks
  nothing: it is constant, and a constant is not a signal (same rule as hue).
- **`sub` as a chip rack.** Four `Badge`/`Tag`/`StatusPill` objects in a row,
  none of which the reader scans for, because there is exactly one record on
  the page. `meta` is one muted line of plain text with the status word keeping
  its tone.

`Props`/`Prop` exist because the rails were built out of `Field`, whose label is
13px `--fg` medium — correct beside an input, wrong beside a value that is only
read, so every rail looked like a disabled form. Its columns auto-fill: one
column in a 260px rail, two or three across a phone, with no media query at the
call site.

`DetailLayout` puts the rail FIRST in the DOM and moves it right at 900px, which
is what makes the narrow layout right. Stacking a rail after the main column
buried the properties under the comments, the runs and the activity feed;
reported 2026-08-12 as "you've HIDDEN properties down at the bottom".

`Clamp` cuts long body text to a height and reveals the toggle only when the
content actually overflows (it measures; a line count says nothing about how
tall a code block or an image is).

## 0.5.1 — 2026-08-12 — a failure beside content is a line, not a region

**Additive.** New primitive `Notice`, plus `.ws-notice*` in `components.css`.
Nothing removed, nothing renamed.

`StateBlock` is a REGION: centred, tall, dashed, sized to stand in for a list
that could not load. It was the kit's only error shape, so it also got used for
every failure that happened NEXT to content that was fine, and a one-sentence
problem rendered as a 120px panel in the middle of a working page. In the chat
that meant a dropped poll put a dashed slab over the composer; the operator's
words on 2026-08-12 were "huge and blocky", and roughly twenty call sites across
the app had the same shape.

`Notice` is the other half of rule 9: one line, an icon, an optional cause, an
optional endpoint, an optional action. No border, no fill, no centring. Use it
whenever the screen still has its content; keep `StateBlock` for the empty
region.

## 0.5.0 — 2026-08-12 — one list row, and status stops being a labelled chip

**Breaking (CSS).** `.ws-tickets` and `.ws-ticket-row*` are renamed
`.ws-list` and `.ws-listrow*`: the shape is used by the ticket list AND the run
feed now, so it is not a ticket thing. Removed `.ws-job-card*` and
`.ws-jobs-grid` with the card layout they styled. Added `.ws-listrow__mark`,
`.ws-listrow__scope` and `.ws-list__children`. No exported component changed.

**Why the chips went.** Operator, 2026-08-12: "why does all info have to be a
pill and all has a color dot". A ticket row carried a `StatusDot` with its label
and a coloured `Tag` for scope; a run card carried a `StatusPill`, an
`AgencyChip` repeating the trigger its `AgencyDot` had already marked, and a
child-runs button, over four bands. Two or three chip-shaped objects per row,
which is exactly how you stop any of them being scannable.

The rule now: **a chip is for something you scan a list FOR; plain text is for
something you read once you are already looking at the row.** So status is a
bare dot with no label (five states, learned once, and the accessible name is on
the wrapper), scope and trigger and duration are muted text, and the ticket
list drops the scope entirely when it is already filtered to one scope. The
status vocabulary is unchanged: same five kinds, same colours, same pulse on
`running`. Only the mark got smaller.

`.ws-listrow` is a two-column grid (mark, then content) so the same markup is
two lines on a phone and one line from 720px.

## 0.4.0 — 2026-08-12 — the shell can no longer scroll its own toolbar away

**Breaking (CSS).** Removed `.ws-board*`, `.ws-ticket-card*` and
`.ws-ticket-grid`: the board and the card grid they styled were deleted from the
tickets surface. Added `.ws-tickets`, `.ws-ticket-row*` and `.ws-tickets-bar*`.
No exported component changed, so the API snapshot is unmoved.

**The fix that matters.** `.ws-root` and `.ws-app` are the same element in
`App.tsx`, and the box-sizing reset read `.ws-root *` — descendants only. So the
shell itself was `content-box`, and `.ws-app`'s `height: 100dvh` plus
`padding-top: env(safe-area-inset-top)` resolved TALLER than the viewport
wherever the notch inset is non-zero. The document scrolled, and the global
toolbar scrolled off the top with it. It reproduced on phones and installed
PWAs only, so it read as a mobile layout bug rather than a broken invariant.

The reset now covers the root itself, and `.ws-app` adds `max-height: 100dvh`
and `overflow: hidden` so nothing inside can push the shell at all. `<main>`
remains the single scroller. `src/styles/shell.test.ts` pins all of it, and the
consuming app pins the matching `html, body { height: 100%; overflow: hidden }`.

**The ticket row.** The list was a `.ws-table` with `min-width: 520px` inside a
horizontal scroller, which on a phone is a narrow window onto a wide grid: you
scrolled sideways to read a title, and the columns that did fit were too tight
and wrapped. `.ws-ticket-row` is one row per ticket that never scrolls sideways
at any width: two lines below 720px, one line above it, title and meta both
clipped rather than wrapped.

## 0.3.2 — 2026-08-12 — two chevrons that never rotated

Patch. No API change; the snapshot is unmoved.

`.ctx-expand__chev` and `.mcp-name__chev` declared
`transition: transform var(--motion-fast) ease`. There is no `--motion-fast`
token in this system and there never has been. With no fallback the `var()`
is invalid at computed-value time, which invalidates the whole `transition`
declaration, so both chevrons snapped rather than rotating. Both now use
`--dur-fast` and `--ease-in-out`, the real tokens, which also means they are
finally zeroed by the reduced-motion block that rewrites `--dur-*`.

## 0.3.1 — 2026-08-12 — the focus ring is a shadow, so stop suppressing an outline

**Fix.** An open `Menu` popover sometimes drew a hairline highlight where its
elevation shadow should have been, and whether it did depended on whether the
last thing you touched was the keyboard or the mouse. Same click, same control,
different result.

`.ws-root :focus-visible` indicates focus with `box-shadow`, deliberately, since
an outline cannot follow a control's own radius. Two places tried to opt out of
it with `outline: none`, which does nothing to a box-shadow: the ring stayed,
and because `box-shadow` REPLACES rather than adds, the element also lost the
shadow it already had. That is why it read as a border rather than a highlight.

The ring is now scoped to `:focus-visible:not([tabindex="-1"])`. An element you
cannot tab to was never navigated to by keyboard, so a keyboard-navigation cue
on it is a contradiction; those elements (the `Menu` popover, `Stepper`'s
buttons) are focused programmatically only so arrow keys have a starting point,
and it is that programmatic focus whose `:focus-visible` match varies by input
modality. Outline is still reset for everything.

No API change. Consumers get the fix by upgrading; anything relying on a ring
around a `tabindex="-1"` element will lose it, which is the intent.

Also: `.ws-palette` now clears the ring with `box-shadow: none` rather than the
no-op outline, so keyboard-focusing a scheme no longer draws a rectangle around
the whole button in addition to the ring on its preview.

## 0.3.0 — 2026-08-11 — the agency axis: who did this, not just how it went

**Breaking.** The palette answered one question, *is it healthy* (the status
five). It now answers a second, which for a system that runs sweeps and crons is
the more important one: *was that me, or something moving without me.*

New axis, three tiers, each with a resting and an active (`-up`) tone:

| tier | meaning |
|---|---|
| `you` | your hand on it: typed, clicked, confirmed |
| `system` | machine work inside a conversation you are present for |
| `autonomous` | it moved with nobody watching |

Two rules the kit now enforces:

1. **Agency is FILLED, status is OUTLINE.** `StatusPill` lost its soft fill and
   took a hairline in its own tone; the new `AgencyChip` is a filled pill with
   no border. That pairing, not hue, is what separates the two vocabularies, so
   a hue coincidence in one scheme is survivable.
2. **One agency color per surface.** `data-agency` on any element binds the tier
   for everything inside it. Provenance beats mechanism: an autonomous agent
   calling a tool leaves the surface autonomous.

`agencyFor()` returns `AgencyTier | null`: **null when the record carries no
trigger**, so a caller renders no provenance mark rather than guessing one. The
autonomous tier says nobody was watching, and a false alarm on work the operator
did himself is worse than an absent mark. `discord` maps to `system`, not
autonomous: an agent you message is answering you, with you present.

New exports: `AgencyChip`, `AgencyDot`, `agencyFor`, `agencyProps`,
`AGENCY_LABEL`, `AgencyTier`, `resolvePaletteId`, `LEGACY_PALETTES`,
`PaletteGroup`. New CSS: `.ws-agency`, `.ws-agency-dot`, `.ws-agency-rail`,
`[data-agency]`, `.ws-palette-group`, `.ws-nav__count`, `@keyframes ws-ring`.

New tokens per scheme: `--you`, `--you-fg`, `--you-soft`, `--sys`, `--sys-soft`,
`--sys-up`, `--auto`, `--auto-soft`, `--auto-up`. `--accent`, `--accent-fg` and
`--accent-soft` are now aliases of the `you` family, so nothing that used them
had to change; the brand accent and "your hand on it" were always the same idea.

**`--running` is no longer one fixed color.** A run you started, a run inside
your chat and a run nothing asked for are three different facts, so `running`
resolves to the initiating tier's active tone (default `system`, re-pointed by
`data-agency`). It is also the one status that stays filled: `running` describes
an actor, not an outcome. Terminal states never split by tier.

### Migration

- **The five palettes are gone; there are eight schemes**, in two groups.
  Expressive (`lagoon`, `iris`, `cobalt`, `fern`, `clay`) spends three hues;
  quiet (`slate`, `linen`, `graphite`) spends one and separates the tiers by
  chroma. `PaletteId` changed values accordingly and the default is now `iris`.
  A stored id from before resolves through `resolvePaletteId()`
  (`violet`/`plum` → `iris`, `teal` → `lagoon`, `sandstone` → `linen`,
  `cobalt` kept its id and its 258 hue), so no saved preference is lost.
- **`PaletteMeta.swatch` is now `{ you, sys, auto }`**, not a single string: what
  a scheme IS is the relationship between its three tiers, so the picker shows
  all three. `PaletteMeta.group` is new.
- **Consumers styling a status chip against its old fill must re-check
  contrast** — the fill is gone, and the chip is now hairline-on-transparent.
- The brand accent is user-selectable now, reversing the old "one brand accent,
  not user-selectable" rule. Identity moved from the violet to the relationship
  between the tiers, which is the same in all eight schemes.

Color model rewritten around the handoff's generator (`palette.mjs`): eight
scheme seeds, the designer's stops, and per-scheme chroma/lightness trims. Dark
mode is byte-identical to the handoff; in light mode every colored TEXT tone is
walked to 4.5:1 against the surfaces and its own soft fill, which moves `warn`
and darkens `you` a step in several schemes. 144 contract assertions in
`palette.test.ts`.

## 0.2.5 — 2026-08-11 — ticket link rows, comments, board lanes, phone fixes

Compatible. CSS only, no export or prop change.

New rules: `.ws-ticket-link` (a linked ticket as a whole clickable row),
`.ws-comment` (one entry in a discussion thread), and `.ws-board` +
`.ws-board__lane/head/count/items/empty` (fixed-lane board that scrolls
horizontally rather than crushing its columns).

Two phone fixes at `max-width: 640px`. `.ws-ticket-card__pills` now wraps
instead of clipping: the `nowrap; overflow: hidden` that keeps a grid of cards
one height was chopping scope tags mid-word on a phone, where there is only one
card per row and the height argument does not apply. `.ws-segmented` now scrolls
horizontally instead of overflowing the viewport, and hides its sliding thumb
while scrolled (the thumb is absolutely positioned and stops tracking the
visible row), with the pressed state carrying the background itself.

## 0.2.4 — 2026-08-11 — MenuItem disabled

Compatible. Adds an optional `disabled` prop to `MenuItem`: renders a native
`disabled` button (no `onSelect`, no menu-close, cursor stays `default` per
rule 10) so a present-but-not-choosable row can still carry a `title` tooltip
explaining why. No export removed or renamed.

## 0.2.3 — 2026-08-11 — full-width shell, content at an intermediate measure, apps own their width

Compatible. No export or prop change. `--page-max` changes value, `--chat-max`
now tracks it, and two classes are added (`.ws-app--appfull`, `.ws-page`); no
token or class is removed or renamed.

What changed and why it matters to a consumer:

- `--page-max` is now `1200px` (was `1680px`), and it caps the CONTENT, not the
  shell. `.ws-app` and the `.ws-topnav` inside it span the full viewport; the cap
  moved onto `.ws-app__body > main`, centred via a `max(base, (100% - --page-max)/2)`
  inline padding. Net effect: the global toolbar is full-bleed, regular pages read
  in a centred column at the new measure.
- `--chat-max` is now `var(--page-max)` (was `900px`): the focused chat reads at
  the standard page width, and its shell is full-width like every other route
  (the chat's own toolbar+log+composer stay capped together on `.chat-panel--full`).
- `.ws-app--appfull` (new): put on the shell for an app route so its `<main>`
  drops the centred cap and the app fills the viewport. An app owns its own width.
- `.ws-page` (new): the opt-in container an app built on the kit wraps its content
  in to match the standard page measure, rather than the width being imposed.

Operator directive, 2026-08-11: one intermediate width for pages and chat, apps
uncapped and self-managed, the top toolbar full-bleed.

## 0.2.2 — 2026-08-11 — focus ring is keyboard-only on touch devices

Compatible. No export, prop, token, or class change; a CSS behaviour fix.

The `:focus-visible` accent ring is now suppressed where the primary pointer is
coarse (touch), via `@media (pointer: coarse) { .ws-root { --focus-ring: none } }`
next to the universal focus rule in `components.css`.

Why it matters to a consumer: iOS Safari matches `:focus-visible` after a plain
tap on a `<button>`, so the ring meant for keyboard navigation stuck on the last
control tapped (a menu trigger, most visibly) until focus moved, and did not on
Chrome/Android, which read as an inconsistent stuck "active" border on mobile.
Neutralising the `--focus-ring` token for the coarse-pointer subtree clears every
`:focus-visible` / `:focus-within` consumer at once. Desktop (fine pointer) keeps
the keyboard ring; text inputs are unaffected because their indicator uses
`:focus`, not this token.

## 0.2.1 — 2026-08-11 — `Markdown` is memoized

Compatible. Same export, same props, no call-site changes required.

`Markdown` is now wrapped in `React.memo`, and its `remarkPlugins` array is
hoisted to a module constant instead of being allocated inline on every render.

Why it matters to a consumer: markdown parsing is the most expensive thing this
kit does, and the component is typically rendered once per item in a list that
re-renders for reasons that have nothing to do with the markdown. In the
Workshop chat the composer's draft text is state on the component that also owns
the transcript, so every keystroke re-rendered every message. Measured against a
real 67-message / 310 KB thread, one pass of remark parse + mdast->hast is about
150 ms on a desktop core, which is 600-900 ms on a phone, per keystroke, scaling
linearly with thread length. Typing was unusable on mobile.

Both props are strings, and a given message's text is stable between renders, so
the shallow compare skips the parse entirely. The inline `[remarkGfm]` literal
mattered too: a new array identity every render defeats both this memo and
react-markdown's own internal caching.

If you were relying on `Markdown` being a plain function component (calling it
directly rather than rendering it, or attaching properties to it), it is now a
`MemoExoticComponent`. Rendering it as JSX is unchanged.

## 0.2.0 — 2026-08-10 — the kit becomes a real package

**Renumbered from 1.x to 0.x.** The 1.0.0 / 1.1.0 / 1.1.1 sections below describe
a package that was never built and never published, and whose version field said
`1.0.0` the whole time the changelog was moving. A 1.x line advertises a
stability guarantee this kit has not earned yet. It restarts at 0.2.0, which is
lower than the numbers below on purpose: nothing ever consumed those, so there is
no upgrade path being broken, and 0.x tells a future consumer the truth about how
settled the API is. The 1.x history stays in this file because the migration
notes in it are real.

Until now `@workshop/ui` was a `package.json` sitting in `frontend/src/ui` with
no build, no artifact and no consumer that resolved it by name. This release
makes it an actual package. **The API of the primitives themselves is unchanged
except for `Markdown` (below); the breaking part is how you get at them.**

### Why this is a breaking release

Three things were true at once, and together they meant the version number said
nothing:

- `main` pointed at `index.ts`. Raw TypeScript, never built, never packed.
- `exports` mapped `./tokens.css` to `../styles/tokens.css`, a path **above the
  package root**, which `npm pack` silently drops. The package could not have
  worked if published.
- Every consumer, in-repo and out, resolved the kit through a bundler path alias
  into the source directory. Nothing compiled out-of-repo consumers, so a
  breaking change to a primitive produced no error anywhere.

That last one already bit. `Menu` changed from an `items` prop to `MenuItem`
children during 1.x with no version bump. In-repo call sites were updated in the
same commit so nothing here failed. Out-of-repo apps aliasing into the source
broke on their next build, against a pin that still read `^1`. If you are on a
1.x pin and using `Menu`, see the migration note below.

### Breaking: the package moved

`frontend/src/ui` and `frontend/src/styles` are now `packages/ui/src` and
`packages/ui/src/styles`. The web app is a consumer of the kit, not its owner.

Any relative import into the old location must become a package import:

```diff
-import { Button, Card } from "../../ui";
+import { Button, Card } from "@workshop/ui";

-import "./styles/tokens.css";
-import "./styles/components.css";
+import "@workshop/ui/tokens.css";
+import "@workshop/ui/styles.css";
```

Note `components.css` is exported as **`@workshop/ui/styles.css`**, not
`components.css`.

### Breaking: `Markdown` moved to its own subpath

```diff
-import { Markdown } from "@workshop/ui";
+import { Markdown } from "@workshop/ui/markdown";
```

`Markdown` pulls `react-markdown` and `remark-gfm`. Re-exporting it from the
core entry meant importing a `Button` dragged a markdown parser into the bundle.
Both libraries are now optional peer dependencies; if you don't import this
subpath you don't need them installed.

### Breaking: `Map` is a subpath, not a deep import

`Map` was never exported from the barrel; consumers reached in at
`.../ui/Map`. That deep path is gone. It is now a declared entry point:

```diff
-import { Map, type LngLat } from "../../ui/Map";
+import { Map, type LngLat } from "@workshop/ui/map";
```

`maplibre-gl`, `pmtiles` and `protomaps-themes-base` are optional peers for the
same reason as above.

### Breaking: internals are no longer reachable

The old alias mapped the package name onto a directory, so
`@workshop/ui/<any-internal-file>` resolved. The exports map now lists exactly
six entry points and nothing else resolves. If you were importing an internal
module, use the public export or open a ticket to promote it.

### Migration note carried forward from 1.x: `Menu`

`Menu` takes children, not an `items` array:

```diff
-<Menu items={[{ label: "Rename", onSelect: rename }]} />
+<Menu>
+  <MenuItem onSelect={rename}>Rename</MenuItem>
+</Menu>
```

`MenuItem`, `MenuSeparator` and `MenuLabel` are exported alongside `Menu`. This
change actually shipped in 1.x without a version bump; it is recorded here
because this is the first release where a consumer can act on it.

### Added: it is now publishable

- Real build. `npm run build` emits `dist/` with ESM, `.d.ts` declarations,
  source maps, and two self-contained stylesheets. `@import`s in `tokens.css`
  are inlined, so the shipped file resolves against nothing.
- `react`, `react-dom` and `lucide-react` are peer dependencies. The kit no
  longer bundles a second copy of React into a consumer.
- `files` limits the tarball to `dist/` plus the docs. `npm pack --dry-run`
  shows exactly what publishes.
- The whole package is self-contained: nothing in `exports` or in the source
  points outside the package root.

### Added: the API is snapshotted

`api/ui.api.md` records every public export of every entry point, generated by
`npm run api:snapshot`. `scripts/check_design.sh` fails when the surface changed
and the snapshot did not, so the silent-break failure mode above cannot repeat.
See CONTRIBUTING.md.

### Fixed: the version was wrong

`package.json` said `1.0.0` while this changelog had already reached `1.1.1`.
Worse, **1.1.0 was itself a breaking change shipped as a minor**: it moved the
canonical color source out of `tokens.css` into the generated `palettes.css`,
so anything importing `tokens.css` and expecting color declarations in it got
none. If you are pinned anywhere in 1.x, treat 1.1.0 as the real boundary.

### Unchanged

Token values, palette model, status semantics, the `.ws-*` class names, and the
props of every primitive other than the two relocations above. This is a
packaging release; it is not a redesign.

## 1.0.0 — 2026-06-14

Initial versioned baseline. Formalizes the existing `src/ui` + `src/styles`
foundation as a versioned package without changing any runtime behavior or token
values.

- Declared `@workshop/ui` as version 1.0.0. App manifests pin it as
  `kit: "@workshop/ui@^1"`. (Now `@workshop/ui@^0.2` — see 0.2.0 above.)
- Export surface is `src/ui/index.ts` (unchanged): the primitives plus `cx`,
  theme helpers, status/category helpers, chart helpers, and `useEditMode`. See
  that file for the full list.
- Tokens: `src/styles/tokens.css` is the single canonical source of truth for
  the web (CSS custom properties). The mobile token object is derived from it.
- Token values are unchanged from the pre-versioning state. This release is a
  metadata/packaging formalization only.

### Single-source tokens

`tokens.css` is canonical. The mobile token object is generated from it, so the
two stop drifting (they were hand-synced before).

- Generator: `scripts/gen-tokens.mjs` (run via `npm run gen:tokens` from
  `frontend/`). It parses the CSS custom properties out of `tokens.css` and
  emits `src/styles/tokens.generated.js` (the same shape as the hand-maintained
  `tokens.js`).
- The generator is idempotent and never modifies `tokens.css`.
- Flow to refresh mobile after a token change: edit `tokens.css`, run
  `gen:tokens`, then reconcile `tokens.generated.js` into `tokens.js` (the
  Expo-consumed module that also carries the usage docs + `makeTheme` helper).
  The generated file is the diff oracle; review it before promoting changes.

## 1.1.0 — configurable color schemes

Five palettes, each in light and dark. Appearance is now two independent axes:
`data-theme` (light/dark/system) × `data-palette` (violet/cobalt/teal/plum/
sandstone), both on `<html>`, both picked in Settings → Appearance.

**The canonical color source moved.** `tokens.css` no longer defines any color;
it owns the theme-independent scales and `@import`s the generated `palettes.css`.
The model is `src/styles/palette.mjs`:

- An OKLCh tonal ramp (M3-shaped: fixed tones read off a hue, neutrals from the
  same hue at near-zero chroma). The ramp tones were measured FROM the previous
  hand-tuned violet theme, so that look is now one instance of the model rather
  than a special case — `Violet` is still the default and its surface anchors are
  byte-identical.
- Five palette seeds (brand hue, neutral hue, two chroma scales). Four cool hues
  spaced across the arc that cannot be misread as status (197/258/290/334), plus
  Sandstone: warm neutrals with a cool indigo accent.
- A pinned status tone table. `ok`/`warn`/`danger` are identical in every
  palette, solved once against every scheme's surfaces.

Generator: `scripts/gen-tokens.mjs` now emits three files — `palettes.css`,
`palettes.gen.ts` (typed menu for the picker), `tokens.generated.js` (Expo). It
no longer parses CSS; it imports the model. `--check` mode runs inside
`scripts/check_design.sh`, so a stale generated file fails the gate.

New/changed API:

- `usePalette()`, `applyPalette()`, `getStoredPalette()`, `useResolvedMode()`,
  `type PaletteId` — from `ui/theme`.
- `PalettePicker` — the five-swatch radiogroup. Each swatch is a live preview
  (it carries `data-palette`/`data-theme`, so it renders that scheme's real
  tokens).
- `PALETTE_META`, `DEFAULT_PALETTE` re-exported from the generated list.
- `makeTheme(scheme, paletteId)` in `tokens.js` takes a second argument;
  `palettes`, `paletteMeta`, `defaultPalette` are exported. The old
  `palette.light/.dark` still resolves, to the default palette.
- `SettingsRow` accepts `hint`.
- New token `--scrim` (modal overlay; was a hardcoded rgba).

Contrast: every text token now clears AA on `--bg`, `--card` and `--bg-inset` in
all ten schemes, and every solid mark clears 3:1. Two values moved as a result —
the light-mode gold `--warn-solid` (was 2.63:1 on white) and the light `--accent`
(one step darker; the original failed AA on `--bg-inset`). Pinned in
`src/styles/palette.test.ts`.

`--accent-fg` is no longer always white. `.ws-btn--primary` spinners use it.

## 1.1.1 — color scheme refinement pass

Two fixes from the first round of real feedback:

- **Accent chroma pulled back, system-wide and per-hue.** The accent ran hot
  against the rest of the muted UI, most visibly filling the chat bubble
  (`.chat-msg.user .chat-bubble`, `styles.css`) at full strength. Base chroma
  dropped 0.199 → 0.175 (light) / 0.160 → 0.140 (dark) across all five
  palettes. On top of that, `Cobalt` and `Sandstone` (blue-family hues,
  250-270°) and `Plum` (magenta, 334°) got an additional per-palette
  `brandChroma` pull-back (0.74 / 0.78 / 0.86) — OKLCh is not perceptually
  uniform across hue, and a blue at the same numeric chroma as violet reads
  distinctly more vivid. `Teal` was untouched: sRGB's gamut is narrow enough
  there that a full-chroma request was already clipping to about half.
- **The Sandstone swatch didn't read as sandstone.** The picker's preview is a
  live render of the scheme's real (very low-chroma) surfaces, which is
  correct but made Sandstone indistinguishable from the cool schemes at chip
  size — the one bright element in the chip, the accent bar, is deliberately
  a cool brand hue even in a warm scheme. `identitySwatch()` in `palette.mjs`
  adds a second, moderate-chroma tone at the palette's NEUTRAL hue, rendered
  as a strip above the preview; used only in the picker, never in app UI.

`PaletteMeta` gained a `swatch: string` field. `palette.test.ts` gained a
seed-level assertion that the blue-family palettes carry a lower `brandChroma`
than Violet, so this can't silently regress.
