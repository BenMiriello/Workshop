# Dictation, spoken commands and the clipboard

The design of record for where dictated words go, the dictation bar, spoken
commands, and how dictation and copying reach a clipboard app. Settled with the
operator on 2026-10-28. The lower layers it builds on are in
[`declaring-an-app-surface.md`](declaring-an-app-surface.md) (the `events`
block and dictation in an app's fields).

## What is platform and what is an app

| Platform (core) | Default app (its own repository, can be turned off) |
|---|---|
| Speech engine, live dictation into fields, the dictation bar, spoken commands and their manager in Settings > Speech | Clipboard: history of copies and dictations, stars, audio, limits |
| Events an app can subscribe to: `copied`, `dictated` | Subscribes to them |
| An app may open as a window over any page (`overlay`), and put text into the field the operator was in | Clipboard opens that way |
| The app catalog: install an app from its repository; default apps install on first run and can be turned off | The catalog lists Clipboard as a default |

Dictation is an input method, like the keyboard, so it stays in the core with
its on/off switch. Everything that keeps history is an app.

## Where dictated words go

**The destination is chosen once and only changes when the operator changes it.**

1. Starting dictation in a field makes that field the destination. Starting with
   no field focused makes the **clipboard** the destination.
2. Focusing, clicking or typing in another field never moves the destination.
3. Inside the destination field the caret still decides where the next words go
   (every platform does this; it is what lets typing and dictating mix). Typing
   or moving the caret freezes the words shown so far, as before.
4. While dictating, the destination field carries a ring drawn by the speech
   layer over the field (an overlay; the field itself never changes size or
   layout). It is drawn in the field's own layer of the page (the z-index of
   its outermost stacking-context ancestor, `stackLayer.ts`), so a menu,
   dialog or sticky header over the field covers the ring too.
5. The destination outlives navigation:
   - **A chat** (a composer marked as one, see below) is the destination as a
     chat, not as an element: when its composer is on screen words are written
     into it live; when it is not, the words go into that chat's draft
     (`draftWire`) as they are said, the newest rewritten there as they are
     revised (`draftTail.ts`), and are there when it is opened.
   - **A field in an app frame the shell keeps loaded but hidden** is still
     connected: words keep going into it.
   - **A field that left the page** waits: settled words are held for it, the
     bar shows them with a link to its page, and they go in at the end of the
     field once a field with the same identity is on screen again AND the
     operator has pressed in it (`fieldReturn.ts`). Being on screen is not
     enough: a page may focus a field by itself, and an editor that is still
     loading, or read-only until pressed (a notes editor), writes over or drops text put
     in it. An editor (not a plain text box) that came back as a new element
     takes nothing until then, the words still being said included. Words a
     field never took are copied at Stop. A field's identity is its document
     (shell or app frame by slug), the page path, and its first stable key
     (id, name, aria-label, placeholder) plus its index among fields with that
     key. A field with no stable key cannot wait: its words go to the
     clipboard and the bar says so.
6. Changing the destination (the bar's menu, "Dictate here" on the mic chip
   of another focused field, or a second press in that field once nothing has
   been said for 5 s, `fieldSwitch.ts`) affects only words said after it. Words already
   placed stay; the open words are frozen where they are first.
7. Every dictation is also published as a `dictated` event when it ends, so a
   clipboard app keeps it. The system clipboard is written only when the
   destination is the clipboard, on Stop pressed by click or key (a user
   gesture; `ClipboardItem` with a Promise so the last words can still arrive),
   or through the native bridge in the Mac and iPhone apps. Dictating into a
   field never replaces what the operator last copied.

### Where words went: pieces

**Every dictation keeps its own transcript, cut into pieces, and each piece
went to exactly one place.** The transcript is the record; what a field or chat
holds is a copy of it. A piece is a run of words that went to one place
(`lib/speech/pieces.ts`). A new piece starts when the place changes, when a
message is sent, or when the operator types in the place; pauses and time
never start one. So a long stretch into one field is one piece, and in a chat
each message is its own.

- A piece is **placed** (in its field or chat), **waiting** (for a field that
  left the page), **clipboard**, **unplaced** (its field was given up on), or
  **sent**. Unplaced and waiting words are copied at Stop; nothing is dropped.
- **This dictation** (the destination menu, "Where words went (n)…") lists the
  pieces newest first, each with where it went, Copy, and **Move to**. A move
  takes the words out of their place only while the place still holds them as
  dictated (a value field, a composer, a chat draft, the held words), so an
  edited, sent, or rich-editor piece offers **Copy to** instead
  (`pieceMoves.ts`).
- **Switching places** shows "Words go to <place>" with **Bring last words**,
  which moves the newest piece of the old place into the new one.
- **The server keeps the last day** (`PUT /api/speech/dictations/{id}` a
  moment after each change, `GET /api/speech/dictations`,
  `services/speech/dictation_log.py`), so a closed tab or a crash loses nothing:
  a dictation saved but never ended reads as "Ended without Stop". Settings >
  Speech > dictations > Last day lists them with Copy. Long-term history and
  audio stay an app's (the `dictated` event).

**Where the place is** shows beside its name on the bar when it is not simply
here: a chat **beside** (in the side panel) or **not open**, a field
**waiting** or **not shown** (its app is loaded but hidden). In Commands only
the count reads **Not writing**. A chat is one place in every view that shows
it (by conversation, `sameChat`), its composer is whichever view shows it, and
Open does nothing when one already does.

**One window dictates at a time** (`elsewhere.ts`). Starting dictation in one
of the browser's Workshop windows stops it in the others, and every other
window shows a bar of its own naming where the words are going, with **Move
here** (carry on into the same place from this window) and Stop. "Keep
dictation on" starts again only in the tab that had it, or in any one once
that tab has been gone 15 s.

**Recent destinations** (per device, newest first, at most 8): each field or
chat words went into, labelled `App › page › field` (or the chat's title).
Choosing a recent field that is not on screen makes it a waiting destination.

**Chat composers** carry `data-dictation-chat` (the draft key) and
`data-dictation-thread` (thread id, absent for a new chat), so the speech layer
can treat them as chats. Chat integration lives behind one module,
`frontend/src/lib/speech/chatTargets.ts`:

```ts
type ChatTarget = { draftKey: string; scope: string; threadId: string | null; title: string };
openChats(): ChatTarget[]                       // main view and side panel
composerFor(t): HTMLElement | null              // its composer if on screen
appendToDraft(t, text): void                    // live in open composers, other tabs, server
sendChat(t): Promise<SendHandle>                // what the send button would send
cancelSend(h): Promise<CancelOutcome>           // see "Send and cancel"
startNewChat(scope): ChatTarget                 // a new chat's draft; opens it where t was open
setChatModel(t, query): Promise<ModelPick | null>  // fuzzy; null when nothing matches
openChat(t): void                               // in the side panel
searchChats(q): Promise<ChatTarget[]>           // the chat search behind "Choose a chat"
```

## The dictation bar

A pill (fully rounded ends), one row, never changing width as words arrive.
Left to right:

- **Grip** (six dots): drag the bar anywhere; released within 48px of its
  default spot it snaps back there. The position is kept per device and held
  inside the viewport on resize. Default spot: bottom centre; on touch, the top
  of the visual viewport.
- **State dot**: pulses with the microphone level while listening; a pause
  glyph while paused; a spinner while connecting or finishing.
- **Destination**: kind icon (field, chat, clipboard) and name, truncated, with
  a chevron. Opens the menu.
- **Word count** in tabular figures, then the **newest words** (start-truncated,
  fixed width) only when the destination is not on screen (clipboard, a chat
  not open, a waiting field). A waiting field or closed chat adds an **Open**
  link.
- **Notice slot**: replaces the newest words while an operation is shown (see
  below). One at a time.
- **Pause/Resume** and **Stop** buttons.

There is no separate Cancel (X) button: Stop finishes (it waits for the last
words), Esc is Stop. "Not placed" and "Paste here" states are gone; unplaced
words are the clipboard's.

**Three menus** (drop-ups):

- **Actions** (its own button, left of the destination), titled "Actions":
  Send message, Model… and Open chat when the destination is a chat; New
  chat; Spoken commands… (Settings > Speech > Spoken commands). Each item
  shows its first spoken phrase as its tooltip. Below them, once an action
  has been taken, **History** (see "Action history").
- **Mode** (its own button, after Actions, showing only the mode's icon; its
  tooltip names the mode): the three modes, the current one ticked (see
  "Modes"); then **Show labels** (ticked while on) and **Commands…** (see
  "Labels, numbers and what can be said"). Each item shows the phrase that does it as its tooltip.
- **The destination**, titled "Send text to": Clipboard; This field (when
  another dictatable field is focused); open chats; recent destinations;
  Choose a chat…. Picking one while paused resumes dictation.

**Every action applies at once.** The notice slot shows it for 5 s from when
it applied, with a Cancel carrying a countdown ring timed once when it is
shown (so the bar redrawing never moves it); Cancel is that action's Undo, and
the bar then says how the undo went:

| Shown | Cancel does |
|---|---|
| `Sent to <chat>` | takes the message back (see "Send and cancel") |
| `Sending to <chat>` (held, see below) | puts the words back; at 5 s it is sent |
| `New chat` | back to the chat before, in every view that switched |
| `Model: <name>` | sets the model before back |
| `Opened <chat>` | puts the side panel back as it was |
| `Removed "<start of phrase>…"` | puts the phrase back |
| `<reason>` (error) | no Cancel; 5 s |

## Pause and stop

- **Pause** keeps the microphone and connection open but writes nothing; the
  server still listens, only for the resume phrases. The bar shows Paused.
- **Stop** ends dictation: the last words are delivered and the microphone is
  closed. A stopped dictation is not started again by voice.
- **Keep dictation on** (Settings > Speech, per device): no stop after five
  minutes of silence, and dictation starts again after a reload if it was on,
  into the clipboard or the last chat destination. The iPhone app stops
  listening when it goes to the background.

## Spoken commands

Two kinds:

- **Inline**: "new line", "new paragraph". Recognised anywhere in speech, no
  pause needed, written as a line break or a blank line, and the next word is
  capitalised.
- **Actions**: recognised only when said **on their own**: at least 0.4 s of
  silence before (or the start of dictation) and 0.7 s after. Anything inside
  running speech is dictated text (Dragon's rule). While an utterance could
  still become an action (its words so far are the start of a phrase, after a
  pause), those words are held back from the field so a command never flashes
  in as text.

Matching ignores case, punctuation and fillers. Defaults:

| Command | Default phrases | Does |
|---|---|---|
| New line | new line | line break |
| New paragraph | new paragraph | blank line |
| Scratch that | scratch that, delete that | removes the last dictated phrase |
| Send message | send message, submit message | sends the destination chat |
| Cancel | cancel, cancel that | undoes the action said just before it, within 5 s of audio |
| New chat | new chat, start a new chat | the destination becomes a new chat |
| Chat model | chat model, switch model to, use model | followed by a name; switches |
| Open chat | open chat, show chat beside | opens the destination chat in the side panel |
| Stop chat | stop chat, stop the chat | stops the reply the chat is writing (not undoable) |
| Open | open, go to | followed by a name: opens that place or page of the shell |
| Go back | go back, previous page | back a page; Cancel goes forward again |
| Go forward | go forward, next page | forward a page; Cancel goes back again |
| Scroll down / up | scroll down, page down / scroll up, page up | scrolls what is in the middle of the screen (inside an app frame too) by most of a screen |
| Scroll to the top / bottom | scroll to the top, scroll to top, go to the top / the same with bottom | scrolls that all the way |
| Click | click, press, tap | followed by a label's number or a control's name: clicks it (a field is focused) |
| Write in a field | write in, write to, type in, write into | followed by a field's name: words go there from now on |
| Close | close that, close this, close dialog, close menu | closes the list showing, else the topmost dialog or menu, as Escape would |
| Next / Previous tab | next tab / previous tab | the tab after or before the selected one, in the first row of tabs on screen |
| Read this page | read this page, read the page, read page | reads the region in the middle of the screen, as a tap there would |
| Read the last reply | read the last reply, read last reply | its own Read aloud button, in the destination chat's view |
| Stop reading / Read faster / Read slower | stop reading / read faster / read slower | the reader; speed moves one step |

Every Read aloud phrase may say "speak" for "read" ("speak this page", "speak
the last reply", "speak faster", "stop speaking").
| Pick a number | number, pick, choose | followed by a number: picks it from the list showing, else the label |
| Show commands | show commands, what can i say, list commands | the Commands list |
| Show labels / Hide labels | show labels, show numbers / hide labels, hide numbers | numbers every control on screen |
| Dictation only | dictation only, dictation mode | switches to Dictation only |
| Mixed | mixed mode | switches to Mixed |
| Commands only | commands only, command mode, stop writing, disable transcription | switches to Commands only |
| Start writing | start writing, enable transcription | back to the mode words were last written in |
| Pause dictation | pause dictation, pause listening | pause |
| Resume dictation | resume dictation, continue dictation, start dictation, resume listening | resume (heard while paused) |
| Stop dictation | stop dictation, stop listening | stop |

**Open** matches the name against the places the dock lists and their pages
(`lib/speech/openPlaces.ts`, published by the shell, so no app is named in
code): a place by its name, a page by its label or "<place> <page>", every
word matching a word of the target (or its start, or one letter off), the
closest target winning, and a page of the place the operator is in over the
same page elsewhere. Undo goes back to the page before. A name that fits
nothing was not a command ("open the window"): its words are written as said.

"Cancel" said when there is nothing to undo is an ordinary word. An
action that does not apply (send with no chat destination) shows a notice and
writes nothing.

**Modes** (`services/speech/modes.py`), one setting, with the trigger word
beside it (default "ok workshop"). Three, as Windows Voice Access, Talon and
Dragon have them, each switched by saying its name; Pause is the fourth state
(asleep):

| Mode | Say | Words written | Commands heard |
|---|---|---|---|
| Dictation only | "dictation only" | yes | only new line, new paragraph, the mode switches, pause and stop |
| Mixed (default) | "mixed mode" | yes | each as set: said directly, after the trigger word, or off; after the trigger word, any wording |
| Commands only | "commands only" | no | every command not off, no trigger word needed, any wording |

A command after the trigger word is its phrase with the trigger's words in
front ("ok workshop send message", "ok workshop new line"), so it is matched like
any other, and a command heard on its own may also be said after it. The
trigger word still has to start an utterance for an action, as any action
does. Commands drops what is said the way pause does. Mode switches act at
once on the server and the client saves the change; "start writing" goes back
to the mode words were last written in, writing into the field focused then if
there is one, otherwise where words went before. On the bar the mode has its
own button after Actions, its icon and name (the icon alone on a phone). Saved
on the server with the commands (`PUT /api/speech/commands/mode`), so the next
dictation starts in it; the live socket takes a change as `{"t": "mode",
"mode"}` and says one as `{"t": "mode", "mode", "said"}`.

Saved settings from the four modes before 2026-10-31 read as the nearest:
Words only is Dictation, Standard is Mixed, and Trigger word is Mixed with
every command that was heard directly set to need the trigger word (written
down the next time the mode or commands are saved).

**Commands in any wording** (`services/speech/meaning.py`). In Commands mode,
and after the trigger word in Mixed, an utterance that is none of the phrases
is understood by what it means: EmbeddingGemma 300M (4-bit ONNX, on the
processor, about 15 ms) turns it into an embedding, and a small classifier
over embeddings, trained in under a second on every command's phrases plus the
general wordings in `services/speech/command_examples.py`, gives each command
heard in that mode a probability, and "none" one. It is retrained whenever the
commands change, so the operator's own phrases teach it. The model is fetched
the first time a dictation starts outside Dictation (or by
`scripts/setup_speech.sh`); until it is loaded, the bar says so. The server
sends `{"t": "command", "name": "guess", "words", "guesses": [{"name", "p",
"text"?}], "none"}`; the client keeps the commands that make sense on the page
(no Send without a chat, no Open when no place is named in the sentence) and
decides (`guessRule.ts`): the best at 0.5 or more and likelier than none runs
at once, with Cancel as for any action; otherwise the best three at 0.1 or more
are asked as numbered choices ("Did you mean"); otherwise the bar says "No
command". A name inside the sentence ("can you bring up the speech settings",
"switch this to opus") is found by trying its spans, longest first
(`lib/speech/spans.ts`). Measured on 312 sentences written apart from the
training phrasings: the best guess was right 91% of the time; acting at 0.5
ran the right command 83% of the time and a wrong one 2.6%
(docs/proposals/voice-control-research-2026-10-31.md). Any wording is never
guessed at in Dictation, or in Mixed without the trigger word, because telling
a command from a sentence that mentions one needs the words around it, which no
small model does reliably.

**Labels, numbers and what can be said.** "Show commands" (or Commands… in the
Mode menu) opens the Commands list: what can be done here, grouped (This page,
Chat, Read aloud, Navigation, Dictation, Your commands), every row numbered,
each with how it is said in the current mode as a chip; a command said with a
name shows the name as a slot (`open ‹page›`). A row is read, not pressed:
saying its number runs it, and a row said more than one way has a chevron
that shows the other phrases. Run by its number, a command that takes a
name offers the names instead: Open the places, Change the model the chat's
models, Write in a field the fields on screen, Click turns on the labels. A
Manage commands button opens the commands manager. Lists of choices (Did you
mean, the places, the models) are pickers: a click on a row picks it. "Show
labels" puts a numbered badge on every control on screen, app frames included,
named by its accessible name (`labels/pageTargets.ts`); "click 4", "click
send", or a number alone acts on it. The badge the pointer is over also shows
what the control can be said by: its name, else its place among the page's
controls of its kind ("third image"), up to tenth. At most 100 are numbered,
or what Settings > Speech > Spoken commands > most labels says (10 to 250,
saved per user with the commands, `PUT /api/speech/commands/labels`). While a list or labels are showing, the
client tells the server (`{"t": "numbers", "on"}`) and a number said on its own
picks; otherwise a number is just a word. "Cancel" closes a list that is
showing. Labels go when dictation stops.

**The commands manager** (Settings > Speech > Spoken commands): the mode and
the trigger word (with "Need it for every command", which sets every command
heard directly to need it), every command with its phrases as removable chips and a plus
that opens a field on its own line to add one, a menu on each command for when it
is heard (said directly, after the trigger word, or off; the menu button shows
a word only when the command is not heard directly), reset to defaults (the mode and trigger word
stay); and the operator's own commands, each a set of phrases that types a
fixed text. Stored on the server per user (`GET/PUT /api/speech/commands`), so
every device hears the same commands; the live socket reads them at `hello`.

Wire (server -> client), beside `text`/`final`:

```json
{"t": "command", "name": "send", "arg": null, "from": 1234, "to": 5678}
{"t": "paused", "paused": true}
```

The command's words are cut from the stream: everything before them is settled
first (a `final`), and the next open stretch starts after them. Inline commands
arrive inside `text`/`final` as `\n` / `\n\n`. Client -> server:
`{"t": "pause", "paused": true|false}` for the buttons.

## Send and cancel

"Send message" goes **at once when the chat is idle**: what its send button
would send, through the open composer when there is one, else through
`POST /chat/threads/{id}/turns` with the draft key (a new chat is created
first). The words said before the command are settled into the draft first.

**When the chat is mid-reply, the message is held for the 5 s instead**:
taken out of the box at once (so what is said next starts a new message),
shown as `Sending to <chat>`, and posted when the 5 s end. A message that
joined a running reply could only be taken back by stopping that reply, so
it does not join until the window has passed. Cancel during the hold puts
its words back in front of the box's.

**Which operation a spoken "cancel" answers is decided by the audio**
(`takeBacks.ts`): every operation keeps the stream position of the command
that made it, and "cancel" undoes the latest one said before
it, within 5 s of audio from when it applied (an action that took a moment to
apply, a model looked up, gets that much longer). A page that was suspended reads the cancel late,
but it still finds the send it followed and can never reach a later one.
One made from a menu has no position and is timed by the clock.

Cancel of a message that started its own reply, in the order checked:

1. **The send request has not answered yet**: carried out when it does.
2. **The send failed**: nothing was sent; "Not sent".
3. **It is queued** (server queue): taken off the queue.
4. **Its reply is running**: the reply is stopped, then as 5.
5. **Its reply finished**: the message is taken back
   (`POST /threads/{id}/unsend`): the line ends just before it again and the
   message and its reply are removed (soft-deleted), with no second line
   left, unless another line still reaches them, when it is an ordinary
   go-back. The queue hold the stop set is released.
6. **Refused** (another turn started, or the chat never went quiet): the bar
   says why; a stop made for the cancel releases its queue hold.

**The session goes back with it.** A reply records where the CLI session
stood when it ended (`meta.cli`: session id and the entry id of its last
top-level assistant message; a reply split by an interrupting message records
one per stretch). Going back to a point that has one, by cancel, by the
transcript's Go back, or by switching lines, makes the next turn resume the
same session at that entry (`--resume-session-at`): the prompt cache and
every file read and tool result up to there are kept, nothing after. Only a
point with no recorded entry, or one in a session that is not the live one,
or one with a /compact or /clear after it, starts a fresh session from the
visible text as before.

Putting words back: the sent text, then a line break and whatever the operator
typed into the draft since, if anything. Attachments come back too when the
composer is open (its `setDraft`); a send from a closed chat carries none.

A spoken cancel after 5 s is an ordinary word; the history's Undo has no
window.

**New chat**: the destination becomes that scope's new-chat draft (the chat is
created on its first send, as the composer does), and a view showing the old
destination chat switches to the new one (the main view navigates, the side
panel reopens). Undo switches those views back and returns the destination;
redo opens the new chat again, or the conversation it became. **Chat model**
sets the model (the thread's, or a new chat's draft settings); undo sets the
one before back. Model names are matched against the provider list's labels
and ids, with spoken numbers read as digits ("opus five point five" -> "Opus
5.5").

## Action history

Every action from the Actions menu, by voice or by click, is kept for the life
of the tab (the last 50), newest first (`actionHistory.ts`). The Actions menu
shows the last five under **History**, which appears only once there is one:
Undo and Redo on the heading take the newest that can run, and each row has its
own Undo, or Redo once undone, when the row is greyed. More than five adds
**All actions…**, a dialog with every kept one. Opening either asks each action
whether it can still run, and asks again just before it does; a button that
cannot is disabled with the reason as its tooltip:

| Action | Undo | Redo | Disabled when |
|---|---|---|---|
| Send to an idle chat | takes it back (stopping its own reply if running) | sends the same words again | redo: the chat is replying, or its box holds other words |
| Send held for a busy chat | puts the words back during the hold; after it, takes it back | same | undo: the message joined a reply that is still running (a queued one comes off the queue) |
| Two sends to one chat | undoing the earlier also takes back the later, both grey | oldest first | redo of the later until the earlier is redone |
| New chat | back to the chat before | the new chat again | |
| Model | the model before | this model again | undo: the model was changed since |
| Open chat | the side panel as it was | opens it again | |
| Open <name> | the page before | opens it again | |
| Scratch that | puts the phrase back | removes it again | the field was typed in since |

Destinations, pause, resume and stop are not actions and are not kept.

## Undo

No product does undo/redo of *placements*; what they all offer is the field's
own undo removing the last dictated phrase in one step, and a spoken "scratch
that". So:

- In a value field (input, textarea) where the last change was dictation,
  **Cmd/Ctrl+Z removes the last dictated phrase** and **Cmd/Ctrl+Shift+Z puts it
  back**, through the speech layer's own small stack (live dictation writes do
  not reach the browser's undo history). Typing in the field or placing new
  dictated words clears the redo side, as any undo does. Once the operator has
  typed, the keys are the field's own again.
- "Scratch that" is the same removal by voice, and its notice can be cancelled.
  It takes the newest phrase that went where words go NOW, never one from an
  earlier place; in a chat that is not open it comes off the draft's end.
- Rich editors keep their own undo; dictation reaches them through the editing
  command, as typing does.

## Events for apps

`copied` (unchanged) and **`dictated`**, published when a dictation ends:

```json
{"event": "dictated", "at": "…",
 "id": "<dictation id>", "text": "all words said",
 "started_at": "…", "ended_at": "…", "status": "done|cancelled",
 "places": [{"kind": "field|chat|clipboard", "name": "App › page › field", "path": "/chat/x"}],
 "audio": {"url": "http://127.0.0.1:<port>/api/speech/recordings/<id>", "mime": "audio/wav", "ms": 12345} | null,
 "imported": false}
```

The audio is the dictation's own recording, kept by the platform for one hour
under an unguessable id on a loopback-only route (blocked at the public edge),
for subscribers to copy. The client ends a dictation with
`POST /api/speech/dictations` (id, text, places, times, status); the server
attaches the audio and publishes. Past per-device history is sent the same way
once, with `"imported": true` and no audio, and cleared from the device when at
least one subscriber received it.

Settings > Speech has a **dictations** row: **Last day** lists what the
platform keeps (above), and a button opens whichever running app subscribes to
`dictated` (`/apps/overlays` lists each app's `events`), which keeps them
longer with their recordings.

## Apps over any page: `overlay`

```json
{"overlay": {"service": "app", "path": "/", "title": "Clipboard", "shortcut": "Mod+Alt+V", "settings": true}}
```

The shell opens the service at `path` in a centred modal over the current page,
from its shortcut (editable) or the shell's menu. Before opening it remembers
the field that was focused. Messages (same `{v: 1, type}` shape as the others):

| Direction | Type | Body |
|---|---|---|
| app -> shell | `workshop/v1/hello` | — |
| shell -> app | `workshop/v1/context` | `{overlay: true, field: {name} | null}` |
| app -> shell | `workshop/v1/insertText` | `{text}`: into the remembered field, then the overlay closes |
| app -> shell | `workshop/v1/closeOverlay` | — |
| shell -> app | `workshop/v1/openSettings` | — (the settings button in the title row, for a block with `settings`) |

## The clipboard app

Its own repository; runs on its own (FastAPI server serving its built web UI)
and inside the Workshop through the runner, like every app.

- **Clips**: text, where it came from (page path, or `Dictated into …`), when,
  star, and audio for dictations. Same text copied again moves to the top.
- **Limits**, text and audio separately: a count (default 100 text, 20 audio;
  any number or unlimited), an optional total size, an optional age. Passing any
  limit deletes the oldest unstarred item first. **Starred items are never
  deleted.** When starred items fill the count, the newest unstarred are still
  kept, up to 10 text and 3 audio (never more than the count itself). Size and
  age apply strictly. Deleting audio keeps the clip's text.
- **Using a clip**: search, play, copy (to the system clipboard), star,
  delete. It never pastes: opened as an overlay, copying a clip closes it
  (`closeOverlay`) and the operator pastes where they were.
- **On its own** it still keeps copies made inside its own page, and adds the
  system clipboard's text on a click (the browser asks first).
- **Kit**: inside a Workshop checkout the web build uses the kit's source
  (live); on its own it uses the packed kit vendored in the repository
  (`web/vendor/`), refreshed by its `scripts/update-kit.sh`.

The Mac app can also feed `copied`: Settings offers "Save copies from: the
Workshop only (default) / anything copied on this Mac". The native side watches
the pasteboard's change count, skips items marked concealed, transient or
auto-generated (the nspasteboard.org convention password managers use), its
own writes, and copies made inside the Workshop itself (the page reports
those). It posts each copy to `/api/app-events/copied` itself, with the web
view's session cookie and `source.app` naming the app copied in, so no page
needs to be open; the page never relays them. Signed out (no session cookie,
or a 401) the copy is dropped, nothing queued. The iPhone
cannot watch the clipboard in the background, so with the same setting on it
looks once each time the app comes to the front: an unchanged change count or a
clipboard without text is skipped, otherwise it reads the text (iOS asks Allow
Paste) and posts `copied` itself. Leaving the front marks the clipboard seen, so
a copy made inside the Workshop is not sent twice. The iPhone also offers the
Shortcuts action "Save Clipboard to Workshop" (optional Text, the clipboard
when empty), for automations; it posts the same way. Both native apps offer
`clipboard.write` on the bridge, so a write needs no gesture.

The iPhone app carries a keyboard, **Workshop** (`mobile/targets/WorkshopKeyboard`),
for pasting a clip into any app: it lists the 30 newest clips of the overlay
app that subscribes to `copied` (`/apps/overlays`, then that app's
`api/entries` through the runner's proxy), and a tap types the clip in. It
needs Full Access (network and the app group) and the app's sign-in: the app
leaves its API base, device token and session cookie in the app group it
shares with the keyboard each time it comes to the front or leaves it; the
keyboard tries the device token first, then the cookie. The SideStore build adds the target at every prebuild
(`plugins/withKeyboardExtension.js`) and signs each bundle with its own
entitlements file, so SideStore registers the group (renamed with the team
id, which both sides read back from their provisioning profile).

With the watch on "anything copied on this Mac", the Mac app is the system
clipboard manager:

- **It keeps running.** Closing the window hides it, and it opens at login
  (`SMAppService.mainApp`, registered when the watch is set to everything and
  unregistered when it is set back).
- **Cmd+Shift+V in any app** (a Carbon hot key, no permission needed) opens a
  floating panel over that app showing `/embed/overlay`: the overlay app that
  subscribes to `copied`, else the first overlay app. The page speaks the same
  overlay messages as the shell's own host, naming the app it opened over as
  the field. Copying a clip there closes the panel, and the app it opened
  over keeps focus, so Cmd+V pastes it. An overlay app that sends
  `insertText` instead has the panel put the text on the clipboard (marked as Workshop's
  own, so the watch skips it), closes the panel, brings that app back and
  posts Cmd+V. Posting keys needs Accessibility; without it the text stays on
  the clipboard and the first time this happens the app says so once, with a
  button that asks for the permission. Every build is signed with the same
  identity, so the permission survives rebuilds (`docs/macos-app.md` §4).

### Where a Workshop copy goes

Two choices, kept on the server per user (`services/copy_prefs.py`,
`GET/PUT /copies/prefs`) so every device follows them, and re-read whenever a
device brings the Workshop back into view (`lib/copyPrefs.ts`):

- **Save copies from**: Nowhere (nothing is reported to apps), The Workshop,
  or, where the native app can watch, anything copied on the device (that
  part is the device's own setting).
- **Workshop copies**: go to the system clipboard too (default), or stay in the
  Workshop.

The platform shows no control for them. An app offers them in its own
settings by message (`copyPrefsBridge.ts`): `workshop/v1/copyPrefs` asks,
`workshop/v1/setCopyPrefs {from?, destination?}` changes, and the shell answers
both with `workshop/v1/copyPrefs {from, destination, canWatch, ok}`. The
clipboard app's Settings carry them, in the page and in its overlay.

Staying in the Workshop holds every copy made in it, on the shell and every
same-origin app frame (`CopyRelay.tsx`): a keyboard or menu copy is cancelled
in the capture phase before any page handler, and what a handler places on the
event is taken instead of written (a cancelled copy with nothing placed leaves
the system clipboard as it was); a `navigator.clipboard` write is reported and
not passed on; a dictation into the clipboard leaves it alone. A paste into a
Workshop field gives the latest held copy (`heldCopy.ts`) until the operator
leaves the Workshop (another app in front, or the page hidden); after that the
system clipboard's paste goes through. Checked 2026-11-02 in Chromium, Firefox
and WebKit.

An app puts one write on the system clipboard either way by dispatching
`workshop:copy-to-system` on its own window just before it writes: the
clipboard app's Copy does, which is how a held clip reaches other apps. Pages
on other origins (a browser app's tabs) are out of reach and copy normally.

## Default apps

The platform names no app. It reads an **app catalog** (a repository holding
`catalog.json`: slug, name, repository, description, `default`), whose URL is an
installation setting with the product's catalog as its default. Installing an
app clones its repository into the projects folder and registers it, as the
local registry's install does. On first run, every `default` app is installed
and started once; an app the operator turns off or removes stays off (the set
applied is remembered). Settings > Apps lists the catalog's apps with on/off.
