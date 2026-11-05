// Local mirror of shared/native-page-scripts.ts (see types.ts for why the
// kit mirrors the bridge contract instead of importing it).

/** `"page"` is the page's own JavaScript world; `"isolated"` is the shell's
 *  world for the app, sharing the DOM but none of the page's JavaScript. */
export type PageWorld = "page" | "isolated";

export interface PageRunOptions {
  world?: PageWorld;
}

/** A script installed at document load in every page view of the app that set it. */
export interface PageUserScript {
  source: string;
  /** Defaults to `"isolated"`. */
  world?: PageWorld;
  /** Defaults to `"all"`. */
  frames?: "all" | "main";
  /** Defaults to `"start"`. */
  at?: "start" | "end";
}

/** Pushed on topic "pages:message": a value a user script posted with
 *  `window.webkit.messageHandlers.workshop.postMessage(value)`. */
export interface PageMessageEvent {
  id: string;
  /** The app the page belongs to; left out by shells from before page views were per app. */
  app?: string;
  world: PageWorld;
  frameMain: boolean;
  frameUrl: string;
  body: unknown;
  /** The page's messages dropped so far for being over 256 KB or not JSON. */
  dropped?: number;
}
