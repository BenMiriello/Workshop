// What the shell pushes about page views, part of the page-view contract
// (./native-pages.ts, which re-exports this). Each event carries the app its
// page belongs to.

/** Pushed on topic "pages:state". `closed` is true once, when the page goes away. */
export interface PageState {
  id: string;
  /** The app the page belongs to (see `WorkshopNative.forApp`); left out by
   *  shells from before page views were per app. */
  app?: string;
  url: string;
  title: string;
  loading: boolean;
  /** 0..1 */
  progress: number;
  canGoBack: boolean;
  canGoForward: boolean;
  /** The page's web process ended: it shows nothing and runs no script
   *  until a reload. Shell builds from before it existed leave it out. */
  crashed?: boolean;
  /** The page has sound playing, muted or not. Left out where the shell
   *  cannot tell, and by builds from before it existed. */
  playingAudio?: boolean;
  /** The page's sound is muted (`setMuted`). Left out with `playingAudio`. */
  muted?: boolean;
  /** The shell holds the page's media because the page is not on screen.
   *  Left out by builds from before it existed. */
  held?: boolean;
  closed: boolean;
  /** Why a closed page closed, when it was not its app's own ask: `released`
   *  when the shell let go of a page that kept playing while held. */
  closedBy?: string;
}

/** Pushed on topic "pages:error". */
export interface PageErrorEvent {
  id: string;
  /** The app the page belongs to (see `WorkshopNative.forApp`); left out by
   *  shells from before page views were per app. */
  app?: string;
  message: string;
}

/**
 * Pushed on topic "pages:newWindow" for target=_blank and window.open. With
 * `popupId`, the shell has already opened a hidden page view for it that
 * keeps its link to the page that opened it (`window.opener`, through which
 * a pop-up can hand a result back): `adopt` it under the id the app gives it.
 * Without, nothing opened natively. A pop-up that closes itself pushes
 * "pages:state" with `closed`.
 */
export interface PageNewWindowEvent {
  id: string;
  /** The app the page belongs to (see `WorkshopNative.forApp`); left out by
   *  shells from before page views were per app. */
  app?: string;
  url: string;
  popupId?: string;
  /** With `popupId`, when the page asked for a size for its window
   *  (`window.open(url, name, "width=500,height=600")`): the app may place
   *  it at that size. */
  features?: PageWindowFeatures;
}

/** What a page asked for its new window, in CSS pixels: the content size,
 *  and the screen position when given (`left`, `top`). At least one of
 *  `width` and `height` is set. */
export interface PageWindowFeatures {
  width?: number;
  height?: number;
  x?: number;
  y?: number;
}

/** Pushed on topic "pages:command" when a registered command fires: `id` is
 *  the focused page view, or with focus elsewhere in the window the topmost
 *  visible one, or null when no page view is visible and the command is for
 *  the app in front (`WorkshopNative.shell.foreground`), which then acts on
 *  whatever it shows (a blank tab, say); `command` is the item's id. */
export interface PageCommandEvent {
  id: string | null;
  /** The app the page belongs to (see `WorkshopNative.forApp`); left out by
   *  shells from before page views were per app. */
  app?: string;
  command: string;
}
