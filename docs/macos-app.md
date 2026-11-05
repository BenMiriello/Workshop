# Workshop for Mac: plan

A native macOS shell for Workshop, the desktop counterpart of the iOS shell in
`mobile/`. It replaces running the iPad build under PlayCover, which gives a
fixed window, keymapping that swallows clicks and typing, and no resizing.

## 1. What it is

- An AppKit app, `macos/`, beside `mobile/`. One window per Workshop session;
  the window resizes freely and remembers its frame.
- The window holds a WKWebView on the installation's Workshop URL (the same
  `EXPO_PUBLIC_WORKSHOP_URL` the phone build uses). The session cookie lives in
  the shell's default data store, so a login survives relaunches.
- It installs the same `window.WorkshopNative` bridge as the phone, from the
  same source: `mobile/native/inject.ts` builds the stub string, and the Mac
  build runs it with node and bundles the output. The stub posts through
  `window.ReactNativeWebView.postMessage`, so the Mac injects a two-line shim
  mapping that to `webkit.messageHandlers.workshopNative`, and answers with
  `__WORKSHOP_NATIVE_RECV__(...)` as the phone does.
- It offers only what it implements. `buildInjectedBridge` takes a capability
  list and a platform name, so the Mac manifest is `pages` alone and
  `platform` is `"macos"` (`shared/native-bridge.ts` and the kit's
  `native/types.ts` widen to `"ios" | "macos"`). Web code already
  feature-detects with `has()`, so location, audio, speech, notifications and
  the device token stay off on the Mac.
- Its own user agent is WebKit's, naming no browser. It reports its engine
  as `WorkshopNative.engine` (the installed Safari's version, WebKit's build),
  so an app that wants a Safari agent for its page views builds one from that.

## 2. Page views on the Mac

An app that drives page views finds them with `pagesBridge()`, so once
`pages` is in the Mac manifest the same web code runs on both shells. How an
app lays itself out at desktop widths is the app's own concern.

Page views are kept per app, on both shells. `pagesBridge()` calls the
client's `forApp` with the slug of the app its frame serves
(`/base-apps/run/<slug>/proxy/...`), every request carries it, and the core
(`PageApps`) keys page ids, profiles and their data stores, rule lists,
scripts and commands by it; `closeAll` closes the caller's pages and a new
Workshop document closes everyone's. The slug is the caller's claim, since
every app frame shares Workshop's origin; the contract in
`shared/native-pages.ts` states that limit. Stores from before the split keep
their identifiers: the first app to reach its profiles takes the old map over
whole. `macos/checks/page-namespace` runs on the Mac before each build and
fails it if two apps' stores could meet or an old store's identifier moved.

An app may declare where its pages may go (`pages.navigationRules.set`,
contract in `shared/native-page-navigation.ts`): pop-ups only from a tap,
jumps of the whole page to another site cancelled without a recent tap and
reported on "pages:navigationBlocked", tracking hops skipped and tracking
parameters taken out. The core applies them in `PageView+Navigation.swift`;
taps, clicks and keys are noted natively (`PageWebView` on the Mac,
`PageTouchWatch` on iOS), and WebKit's own redirect and user-gesture flags
come from `WKNavigationAction` private keys, read only where it answers
them. `macos/checks/page-navigation` pins the rules before each Mac build.

With `downloadsToApp` among those rules, a page view's main frame given a
response that is an attachment (Content-Disposition starting with
"attachment") or a type WebKit cannot show is cancelled, the page stays where
it was, and the file is reported on "pages:download" (contract in
`shared/native-page-downloads.ts`, shell side `PageView+Download.swift`, the
decision itself `PageDownload.swift`). The shell fetches nothing; the app does.
Frames, and addresses that are not http or https, are left alone. Pinned by the
same `macos/checks/page-navigation`.

`pages.setZoom(id, factor)` sets a page view's WKWebView `pageZoom` (held to
0.3 through 3 by `PageZoom.swift`, pinned by `macos/checks/page-zoom`; applied in
`PageViewZoom.swift`). It stays with the view across navigations. The shell
draws nothing for it; the app shows whatever it wants to show.

`pages.setMuted(id, muted)` silences a page view's sound with WebKit's page mute
(`_setPageMuted:`), and `pages:state` carries `playingAudio` (KVO on
`_isPlayingAudio`) and `muted`. Both are WebKit calls outside its public
interface, looked up by name in `PageAudio.swift`; where WebKit does not answer
them the fields are left out and `setMuted` rejects. A muted page keeps
reporting `playingAudio`. Measured on macOS 26.5: a WKWebView keeps playing
while hidden, so hiding the view alone never silences it.

A page view plays only while it is on screen, and one rule decides it for both
shells (`PageShowing.swift`, applied by `PagePool+Showing.swift`): its app has
not hidden it, its app is the one whose frame the web UI shows
(`shell.foreground`), and the shell is on screen (the Mac window not minimized,
hidden or covered, `windowDidChangeOcclusionState`; the iPhone app not in the
background). Off screen, its media is held with WebKit's public calls:
`pauseAllMediaPlayback` pauses every sound and video,
`setAllMediaPlaybackSuspended` keeps the page from starting any, and its sound
is muted as well. On screen again it can play, and what was playing stays
paused, as Safari leaves a tab. `pages.hide(id, { keepPlaying: true })` is the
one exception an app can ask for, and only while it is in front. Until the web UI
first sends `shell.foreground`, pages are as their apps set them.
`macos/checks/page-audio` pins the rule and measures a held tone stopping,
staying paused when let go (WebKit does not restart it) and playing again on
`play()`.

Full screen is the system's own, as in Safari: a site player's full screen
button (the Fullscreen API) moves the page view into a WebKit window of its own
that fills the display, and back on the way out (`PageFullscreen.swift`). That
window covers the shell's, so the web UI reads as hidden and its apps hide,
park and re-layer their pages. Until WebKit has put the view back it is
WebKit's: a page in full screen is drawn and plays whatever the rule above
says, and `setRect`, `setCutouts` and `bringToFront` wait, the latest of each
applied once it is back. Before this, the app quit on a site's full screen
button (2026-10-30, crash report not yet read). Closing a page in full screen
leaves full screen first. `macos/checks/page-hold` pins the rule's exception.

Page views are drawn UNDER the shell's web view, which draws no background
of its own (`ShellWebView`; on the phone, react-native-webview with a clear
background). So a page shows wherever the web UI draws nothing over its
place, and everything the web UI does draw there (the dictation bar, the
read-aloud player, menus, dialogs, their shadows and round corners, anything
added later) shows over it exactly as WebKit renders it. Nothing is measured
to make that happen. The window paints the web UI's own background color
behind everything (`Bridge.onAppearance`, from `mobile/native/appearance.ts`;
the phone's SafeAreaView already did).

What would still hide a page is a background behind it. While a page view
shows, its app calls the kit's `usePageSlot(slot, shown)`: the element the
page is placed over and every box holding it stop drawing a background, and
the app's frame posts `workshop/v1/pageSlot` to the shell, which does the
same for the frame and what holds it while that frame is the shown one
(`frontend/src/components/shell/frames/usePageSlots.ts`).

Input is the one thing the native side cannot read from the drawing. The web
view on top gets every click and touch unless told a point is a page's.
Two sets say where the web UI takes them, kept apart:

- `pages.setCutouts(id, rects, { modal })`: what an app draws over its own
  page (its menus, dialogs, notices).
- `shell.overlays(rects, modal)`: what Workshop's own UI draws over the shown
  app's frame, applied to every page of every app (`useShellOverlays`), so
  no app reads Workshop's document.

Both are measured with the kit's `drawnOverPage(host, area)`: every box that
shows something (a background, a dimmed scrim included, border, shadow,
image, field or text) over the page's area and paints above the element the
page fills, by CSS stacking order. Never a list of kinds. `PagePool.pageTaking`
finds the topmost drawn page under a point and gives it the point unless a
set covers it, or either set is modal (a menu or dialog is open, so a click
outside it closes it). The Mac's `ShellWebView.hitTest` returns nil there, so
the page under it gets the click, the scroll and the pointer; on the phone a
`PageTouchRouter` over the web view returns the page's own hit view. If the
measure ever misses something, it still shows; only a click on it would go
to the page.

This replaced cutting the page away at those rects (a mask on the page
view's layer, 2026-10-28 to 11-01). A hole is a rectangle, so a round or
shadowed bar showed a square of the shell behind the page around it, and
anything the measure missed, or caught a frame late, was hidden under the
page. Before that, the view moved off stage under a still retaken four times
a second: on the Mac every retake was a full-size render through TIFF, JPEG
and base64 on the main thread, and the video behind a menu crawled.

The Swift in `mobile/modules/workshop-pages/ios` splits into a core both
platforms compile and a host per platform:

| file | Mac use |
| --- | --- |
| `PageView.swift` | shared; `UIImage.jpegData` becomes an `NSBitmapImageRep` JPEG under `#if os(macOS)` |
| `PageProfiles.swift`, `PageContentRules.swift` | shared unchanged (Foundation, WebKit) |
| `PagesManager.swift` | host part splits out: iOS finds the shell's WebView in the window scene; the Mac owns its host view, a flipped container whose CSS pixels are points at page zoom 1 |
| `WorkshopPagesModule.swift` | iOS only (Expo); the Mac has a small Swift dispatcher keyed by the same method names as `PAGES_METHODS` |

The core stops importing ExpoModulesCore: errors become a plain `PageError`,
which the Expo module turns into its `Exception`. That one change touches the
phone build, so it is verified there with a SideStore build before the Mac
uses it.

Everything else carries over as is on macOS 14+: `WKWebsiteDataStore(forIdentifier:)`,
`proxyConfigurations` (a per-profile proxy), per-page trust anchors,
content rule lists, content mode, the no-app-link navigation policy.

## 3. Desktop basics the phone gets for free

- **Edit menu**, or Cmd-C/V/X/A/Z do nothing in a WKWebView.
- **Find in Page** is a menu item (Cmd-F) that dispatches `workshop:find` to the
  web UI, which owns find. A command-key that matches a menu item never reaches
  the page as a keydown (measured on macOS 26.5: the page saw a plain key and
  never Cmd-F, with the same result in a plain NSWindow), so the web UI cannot
  listen for the shortcut itself here.
- **Chat Beside Page** is Ctrl-I and Ctrl-backtick, taken in `ShellWindow` and shown in the View menu; it dispatches
  `workshop:beside`. A page view has its own keyboard focus, so the shell's
  keydown listener never sees a key typed in one; the menu is taken first.
- **File picker** (`runOpenPanelWith`) for uploads; macOS shows nothing
  without it. Downloads (`WKDownload`) to
  the Downloads folder.
- **JavaScript alert, confirm and prompt** for page views; macOS shows nothing
  without them.
- **Page commands** as menu items. A key typed while a page view has focus
  never reaches Workshop's web UI, so an app registers the commands it needs
  there with `pages.commands.set` (id, title, key, modifiers, the top-level
  menu to put it in; `hidden` for a second key with no item). The shell lists them in its menu bar, takes their keys
  before any view in the window, and sends a `pages:command` bridge event
  naming the item and a page; the app acts on it. The page is the focused
  page view; with focus anywhere else in the window (Workshop's web UI, the
  app's own controls), it is the topmost visible page view, and only its app
  is asked (`PageCommandRoute.swift`). With no page view visible (none open,
  or all hidden under the app's own overlay), the app whose frame the web UI
  shows answers, and the event's page is null: the web UI reports that app
  through the bridge's `shell.foreground` (`AppScreen.tsx`). A command is
  enabled only while it would answer. The shell's own items are only App,
  Edit, File > Close, View > Reload Workshop and Full Screen, and Window.
  Cmd-W closes the window unless the answering app registered it.
- **Read aloud in the right-click menu** (`ShellWebView.swift`). With text
  selected, "Read aloud" follows Copy and the system items all stay; it shows
  exactly when Copy does. It calls the function `mobile/native/readSelection.ts`
  installs beside the bridge, which finds the selection (the focused
  same-origin frame first) and hands it to the web UI's reader. The iPhone app
  adds the same item to its selection menu
  (`mobile/modules/workshop-selection-menu`).
- **No alert sound for an unhandled key** (`QuietWindow.swift`). A key the web
  page does not consume (no handler, or one that does not call
  `preventDefault`, with nothing to scroll) comes back from WebKit as
  unhandled and ends at `NSWindow.keyDown:`, which plays the system alert.
  Measured on macOS 26.5.2 by interposing `AudioServicesPlayAlertSound`: arrow
  keys, letters and Escape on a page with no handler, and an arrow on a
  handler that did not prevent the default, each played it; a handler that
  prevents the default, a page that can scroll that way, and a focused text
  field did not (Escape in the field did). The window class drops such keys
  silently. `macos/checks/quiet-window` pins it on every Mac build: four
  unhandled keys give four sounds in a plain `NSWindow` (so the check can see a
  beep) and none in `QuietWindow`.
- **Every system sound is logged with its cause** (`macos/diagnostics/alert-log.m`).
  The app is launched with this library inserted (`LSEnvironment` in
  Info.plist; the app is signed without the hardened runtime, so the
  OS allows it). Each alert sound the app plays appends to
  `/tmp/workshop-alerts.log` (world-readable, so it can be read over SSH from
  the build account): the time, seconds since the last key, click and mouse
  move, the event being handled, whether the key window has a sheet or an
  app-modal panel, the first responder, and the call stack. The sound still
  plays. Built to find beeps that arrive with no key press (reported while only
  moving or hovering the mouse); delete it with the cause fixed.
- **Crash reports readable over SSH** (`macos/diagnostics/crash-reports.m`,
  in the same library). The system writes the app's crash reports into the
  signed-in account's `~/Library/Logs/DiagnosticReports`, which the build
  account cannot read. Each launch copies those from the last two weeks to
  `/tmp/workshop-crashes/` (world-readable), so a crash can be read once the
  app is opened again.

## 4. Build, install, update

- `scripts/macos-build.sh` runs on this server like `mobile-sidestore.sh`:
  rsync to the Mac, build there over SSH as the account `WORKSHOP_MOBILE_MAC` names, no Apple ID.
- Swift Package Manager builds the executable; the script wraps it in
  `Workshop.app` (Info.plist, icon), signs it, and swaps it into
  `/Users/Shared/Workshop/Workshop.app` (the build account is not an admin, so
  `/Applications` is out; a Dock item points at the shared copy). A locally
  built app carries no quarantine flag, so Gatekeeper runs it.
- **Only committed HEAD ships.** The script exports `macos/`, the sources the
  bridge bundles and the scripts run on the Mac from `git archive HEAD`, and
  lists anything uncommitted under them as not in the build, so a shared
  working tree holding another session's edits builds what is committed.
- **One signing identity for every build** (`scripts/lib/macos-sign-mac.sh`).
  An ad hoc signature's designated requirement is its cdhash, so macOS
  dropped the Accessibility permission on every rebuild. The first build
  makes a self-signed code-signing certificate ("Workshop Local Code
  Signing") in its own keychain in the build account,
  `~/.workshop-signing/signing.keychain-db`, with a random password in a
  file beside it readable only by that account, and every build signs with
  it, so the requirement stays `identifier "<bundle>" and certificate leaf =
  H"<sha1>"` and a permission granted once is kept. Deleting that folder
  makes a new certificate, and permissions are granted once more.
- Updates need no reinstall: the running app watches its own bundle's version,
  and when a build replaces it, shows "Workshop was updated" with Relaunch.
  Web changes, as on the phone, are live on the next load.

## 5. Phases

1. **Shell** (landed, `40124adf`). Window, Workshop web UI, the bridge with no capabilities yet,
   Edit menu, file picker, downloads, window frame kept, build script,
   install to `/Users/Shared`, update notice. Done when the operator opens it
   from the Dock, signs in once, resizes the window, uploads a file, and a
   second build shows Relaunch.
2. **Page views** (landed, `d045c9cc`; the shared Swift is
   `mobile/modules/workshop-pages/ios/core`, copied into the Mac build). The
   core split (§2) with a phone build to check it, the Mac host and
   dispatcher, `pages` in the Mac manifest. Done when a test page in a page
   view opens, `run` returns a value from it, and `contentRules.set` reports
   its rule count from the Mac.
3. **Page dialogs and commands** (landed, `75ea8b8c`; pop-ups `35555334`).
   JavaScript dialogs for pages through `PageDialogHost` (both platforms) and
   `pages:command` from the menu bar. Done when the test page's `alert`,
   `confirm` and `prompt` show and answer, its `window.open` arrives as
   `pages:newWindow` with a `popupId` that `adopt` takes, and a command
   registered with `pages.commands.set` shows in its menu and, typed in the
   page, arrives as `pages:command` naming it.
