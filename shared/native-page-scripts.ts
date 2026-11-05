// Scripts the shell installs in page views and the messages they post back,
// part of the page-view contract (./native-pages.ts, which re-exports this).

/**
 * Where a script runs. `"page"` is the page's own JavaScript world: it sees
 * and can patch the page's globals, and the page can see it. `"isolated"` is
 * one named world the shell keeps for the app: it shares the page's DOM and
 * events but none of its JavaScript, so the page cannot see or spoof it.
 */
export type PageWorld = "page" | "isolated";

export interface PageRunOptions {
  /** Defaults to `"page"`. */
  world?: PageWorld;
}

/**
 * A script installed in every page view of the app that set it, open or
 * opened later, pop-ups included. It takes effect from each page's next document. Installed
 * scripts are kept on the device, so they are in force at the next launch
 * before anything sets them again.
 */
export interface PageUserScript {
  source: string;
  /** Defaults to `"isolated"`. */
  world?: PageWorld;
  /** Every frame, or the main frame only. Defaults to `"all"`. */
  frames?: "all" | "main";
  /** Document start (before the page's own scripts) or end. Defaults to `"start"`. */
  at?: "start" | "end";
}

/**
 * Pushed on topic "pages:message" when a user script, in either world,
 * posts with `window.webkit.messageHandlers.workshop.postMessage(value)`.
 * `body` is that value as JSON. A body over 256 KB, or one that is not a
 * JSON value, is dropped; `dropped` then counts the page's dropped messages
 * so far. Messages from the page world may come from the page itself, so
 * their contents are claims, not facts.
 */
export interface PageMessageEvent {
  /** The page view. */
  id: string;
  /** The app the page belongs to (see `WorkshopNative.forApp`); left out by
   *  shells from before page views were per app. */
  app?: string;
  world: PageWorld;
  frameMain: boolean;
  frameUrl: string;
  body: unknown;
  dropped?: number;
}
