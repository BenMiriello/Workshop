// The container. Dock, page column, content on a desktop; bar one, page
// toolbar, content on a phone. One component, one set of state, and the
// breakpoint decides which chrome is drawn.
//
// Full design record and the reasoning behind every rule:
// docs/platform-redesign/navigation-shell-2026-08-20.md
//
// The two width modifiers from the old shell are unchanged and still the ONLY
// per-route width logic there is. Do not reintroduce a route-by-route list.

import { useEffect, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { OverlayHost } from "@workshop/ui";
import { DirectoryProvider } from "./directory";
import { CopyRelay } from "./copies/CopyRelay";
import { AppFrameHost, AppFramesProvider } from "./frames/AppFrames";
import { useQuietAutoplay } from "./frames/autoplaySound";
import { BesideDrawer } from "./beside/BesideDrawer";
import { useBeside } from "./beside/besideStore";
import { FindBar } from "./find/FindBar";
import { FindProvider } from "./find/FindProvider";
import { OverlayAppHost } from "./overlays/OverlayAppHost";
import { Dock } from "./Dock";
import { MobileBars } from "./MobileBars";
import { PageColumn } from "./PageColumn";
import { ShellTrail } from "./ShellTrail";
import { SpeechLayer } from "./speech/SpeechLayer";
import { openPlaces } from "../../lib/speech/openPlaces";
import { currentPage } from "../../lib/speech/placeName";
import { useMediaQuery } from "../../lib/useMediaQuery";
import { useVisualViewport } from "../../lib/useVisualViewport";
import { isOpenChat } from "./places";
import { TakeoverProvider } from "./takeover";
import { useShell } from "./useShell";
import "./shell.css";

export type WorkshopShellWindow = Window & { workshopNavigate?: (path: string) => void };

// The takeover provider sits ABOVE the frame because `useShell` reads it: an
// open document force-collapses the phone's page toolbar (mockup spec section
// 6), and that read has to happen in the same render as the declaration.
export function Shell({ children }: { children: ReactNode }) {
  useQuietAutoplay();
  return (
    <TakeoverProvider>
      <CopyRelay />
      <FindProvider>
        <AppFramesProvider>
          <ShellFrame>{children}</ShellFrame>
          {/* Dictation and read aloud for every page and every framed app.
              It draws its own fixed overlays in the document body. */}
          <SpeechLayer />
        </AppFramesProvider>
      </FindProvider>
    </TakeoverProvider>
  );
}

function ShellFrame({ children }: { children: ReactNode }) {
  const shell = useShell();
  const { pathname } = useLocation();
  const { driving } = useBeside();
  const navigate = useNavigate();
  // A same-origin frame inside the shell (a chat an app embeds) goes to a
  // Workshop page through this, in place, rather than reloading the shell.
  useEffect(() => {
    const w = window as WorkshopShellWindow;
    w.workshopNavigate = (path: string) => navigate(path);
    return () => { delete w.workshopNavigate; };
  }, [navigate]);
  // Dictation names where its words go by the place they were said in.
  useEffect(() => currentPage.set(shell.place.name), [shell.place.name]);
  // And "open <name>" reaches the places the dock lists.
  useEffect(() => openPlaces.set(shell.places), [shell.places]);

  // AN OPEN CONVERSATION IS AN OPEN CONVERSATION WHEREVER IT IS ADDRESSED FROM.
  // The rule is `isOpenChat` in places.ts, which matches the global focused
  // chat AND an app's own chat route: one conversation filling the viewport,
  // with the shell pinned to the visual viewport. It is checked FIRST, so an
  // app's chat takes the chat layout rather than the app one even though its
  // path is under /apps.
  const chatFull = isOpenChat(pathname);
  const appFull =
    !chatFull && (/^\/apps\/[^/]/.test(pathname) || /^\/projects\/[^/]+\/run/.test(pathname));
  // A framed app screen (/apps/<slug>/ui/<service>) also drops <main>'s padding
  // and page scroll, so the frame fills everything below the chrome edge to edge.
  const framed = appFull && /^\/apps\/[^/]+\/ui\/[^/]+/.test(pathname);
  const widthClass = chatFull
    ? " ws-app--chatfull"
    : appFull ? " ws-app--appfull" + (framed ? " ws-app--appframe" : "") : "";
  // EVERY PAGE ON A PHONE IS PINNED like a focused chat: the shell is the
  // visible window, so the keyboard shrinks it from below instead of iOS
  // scrolling the whole layout up and the toolbar off the top. It used to be
  // only with the chat beside the page, which left any other field (a note, an
  // app's own drawer) to push the bars off the screen.
  const pinned = useMediaQuery("(max-width: 899px)");
  useVisualViewport(pinned);

  return (
    // The directory takeover spans the page column AND the content, because a
    // directory page registers itself from the content side and its tree is
    // drawn in the column. One provider above both is what lets the shell stay
    // ignorant of what any particular directory contains.
    <DirectoryProvider onNeedColumn={shell.openColumn}>
      <div className={"ws-root ws-app ws-shell" + widthClass + (pinned ? " ws-app--pinned" : "")}>
        <Dock shell={shell} />
        <div className="ws-shell__stage">
          <MobileBars shell={shell} />
          <div className={"ws-shell__cols" + (driving ? " is-driving" : "")}>
            <PageColumn shell={shell} />
            {/* The content region, and the only place an overlay may land.
                Dialogs portal into the host this renders, so they cover the page
                and not the dock or the toolbars beside it. */}
            <OverlayHost>
              <ShellTrail />
              {/* NO DRIFT BANNER. Deleted 2026-08-28 on the operator's
                  instruction ("remove this shitty ass banner and never show
                  this again"). It was mounted here on the reasoning that a
                  stale origin is every screen's business. That reasoning was
                  wrong in practice: build drift is the NORMAL state between an
                  edit and an apply, so the banner was on screen most of the
                  time, ate the top of a phone viewport, and listed the working
                  files of whichever agent happened to be mid-task. A warning
                  that is usually showing is not a warning. Do not reintroduce
                  it, in this or any other always-mounted form. */}
              {/* App frames outlive the route that shows them
                  (frames/keptFrames.ts), so they are drawn here, beside the
                  routed page rather than inside it. */}
              <main>{children}<AppFrameHost /></main>
              <FindBar />
              {/* An app opened over this page, in a modal scoped to the
                  content region like every other dialog. */}
              <OverlayAppHost />
            </OverlayHost>
            {/* The chat beside the page: a column at the right, or the lower
                part of a phone's screen (beside/BesideDrawer). */}
            <BesideDrawer placeId={shell.place.id} placeName={shell.place.name} />
          </div>
        </div>
      </div>
    </DirectoryProvider>
  );
}
