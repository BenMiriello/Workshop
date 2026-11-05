# Read aloud

The shell owns one reader for every page and every framed app
(`frontend/src/components/shell/speech/reader.ts`). It reads a sentence at a
time from the speech service, or the browser's own voice when the service
cannot speak, and the floating player shows where it is, previous, pause, next,
speed, Highlight with Follow, and Stop.

A read starts from a message's Read aloud button, a document's Read aloud
button (kit `ReadAloudButton`), selected text (the button under it, or the read
shortcut), or a tap on text while something is already being read.

## Highlight, follow, tap to read

**Highlight** marks the sentence being read on the page. It is a setting that
stays (Settings > Speech > Read aloud, and the Highlight button on the player),
on by default. The mark is LIVE: it breathes slowly while the voice is going and
holds still while paused, with no motion under reduced motion.

**Follow** is on while highlighting a read that has a place on the page; there
is no switch for it. The page scrolls only when the sentence is about to leave
the upper part of the view, never on every sentence. Scrolling by hand (wheel,
a drag on a touch screen, a scrolling key, a press on a scroll bar) stops it at
once, so the page never pulls against the reader, and **Back to reading**
floats over the player, its arrow pointing to the sentence; pressing it goes
there and follows again. Scrolling back to the sentence and stopping there for
a second follows again too. A page scrolling itself (a chat keeping its newest
message in view) does not stop it. This is the pattern of Speechify's and
Readwise Reader's jump back button.

A document editor draws only the lines near its view. A sentence it has not
drawn is scrolled to first: its place is guessed from where it sits in the
whole read, measured from a sentence that is drawn, and closed in on in a few
tries (`readSeek.ts`). The page is read again whenever it has changed and a
sentence has no place, so the mark finds lines drawn later.

**Tap to read**: while reading with Highlight on, a tap on text reads from the
sentence tapped. In the text being read it moves the voice there; in another
message or document it starts reading that one. It plays from there whether or
not the reader was paused, and turns Follow back on. With a mouse, the sentence
a click would read from is lightly shaded under the pointer. A drag, a long
press, a press on a control or in an editable field is left to the page.

## How a sentence is found on the page

What is read is cleaned text (markdown without its marks, code blocks left out),
and the page shows more around it. So each sentence is found in the page's text
by its words, in order: first the sentences whose words appear once, kept where
they run in reading order, then each other one between them
(`lib/speech/align.ts`). A sentence whose words are not all shown is pinned by
its first and last few words; one not found at all simply has no mark. The
page's text skips controls, navigation, code blocks and anything hidden or
marked `data-read-aloud="off"` (`passage.ts`).

The mark is a CSS custom highlight (`::highlight(ws-read)`), drawn by the browser
behind the text, so it scrolls with the text, is clipped by whatever clips it,
and sits under anything drawn over it. The breathing is a registered custom
property animated on the nearest element holding the sentence, so only that
element restyles. Styles are put into each document a sentence is in, frames
included. Supported in Chromium, Safari 17.2+ and Firefox 140+; elsewhere the
read works without a mark.

## For pages and apps

The kit's read-aloud host (`readAloudHost()`, `useReadAloud`) takes:

- `read(text, source, place?)`: `place` is the element or range the text shows
  in. Without it the read works but has nothing to mark.
- `place(el, source, text)`: declares that `el` reads as `text()` for
  `source`, so a tap in it reads the same words its own button would, and the
  button shows the read as its own. Returns the undo.

`ReadAloudButton` does both when given `within`. A tap anywhere else reads the
page's own text, out to the nearest dialog, article or main area. Mark a part
of a page `data-read-aloud="off"` to keep it out of both.

Not built: word-by-word marking. The speech service returns audio without word
timings; a Kokoro export with durations would give them.
