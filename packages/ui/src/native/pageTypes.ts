// Native page views: the `pages` part of the local mirror in `./types`
// (shared/native-pages.ts carries the documentation).
import type { PageNavigationRules } from "./pageNavigationTypes";
import type { PageRunOptions, PageUserScript } from "./pageScriptTypes";

export interface PageRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PageProxy {
  host: string;
  port: number;
  kind: "socks5" | "http";
}

export type PageContentMode = "mobile" | "desktop";

export interface PageOpenOptions {
  profile: string;
  url: string;
  rect: PageRect;
  proxy?: PageProxy;
  trustCertDER?: string[];
  userAgent?: string;
  contentMode?: PageContentMode;
}

/** How `hide` leaves a page view's sound and video. */
export interface PageHideOptions {
  /** Keeps playing while hidden, as long as the app is in front and the
   *  shell is on screen. */
  keepPlaying?: boolean;
}

/** How `setCutouts` shares input between the page and the web UI. */
export interface PageCutoutOptions {
  /** The web UI takes every click and touch while any rect is set, as under
   *  an open menu; false passes only those inside the rects. Default true. */
  modal?: boolean;
}

export interface PageState {
  id: string;
  /** The app the page belongs to; left out by shells from before page views were per app. */
  app?: string;
  url: string;
  title: string;
  loading: boolean;
  progress: number;
  canGoBack: boolean;
  canGoForward: boolean;
  /** The page's web process ended; it needs a reload. */
  crashed?: boolean;
  /** The page has sound playing, muted or not; left out where the shell cannot tell. */
  playingAudio?: boolean;
  /** The page's sound is muted (`setMuted`); left out with `playingAudio`. */
  muted?: boolean;
  /** The shell holds the page's media because the page is not on screen;
   *  left out by shells from before it said. */
  held?: boolean;
  closed: boolean;
  /** Why a closed page closed, when it was not its app's own ask: `released`
   *  when the shell let go of a page that kept playing while held, which the
   *  app opens again when it is shown. */
  closedBy?: string;
}

export interface PageErrorEvent {
  id: string;
  /** The app the page belongs to; left out by shells from before page views were per app. */
  app?: string;
  message: string;
  /** A load that failed, shown by the page view itself: it moved to the
   *  address that failed and says why there, with Back to the page left.
   *  Left out by shells from before. */
  shownInPage?: boolean;
}

export interface PageNewWindowEvent {
  id: string;
  /** The app the page belongs to; left out by shells from before page views were per app. */
  app?: string;
  url: string;
  /** A hidden page view the shell already opened for it, opener kept; `adopt` it. */
  popupId?: string;
  /** The size the page asked for its window, when it gave one. */
  features?: PageWindowFeatures;
}

export interface PageWindowFeatures {
  width?: number;
  height?: number;
  x?: number;
  y?: number;
}

export type PageKeyModifier = "command" | "shift" | "option" | "control";

/** A command an app offers while one of its page views has focus or is the
 *  topmost visible one, or with none visible while its frame is in front. */
export interface PageCommandItem {
  /** Reported as `command` in "pages:command". */
  id: string;
  title: string;
  /** One character, as typed without modifiers. */
  key: string;
  /** Default `["command"]`. */
  modifiers?: PageKeyModifier[];
  /** The top-level menu it goes in; default "File". */
  menu?: string;
  /** Keyboard focus moves to the Workshop web UI before the event. */
  focusShell?: boolean;
  /** Takes the key without listing a menu item. */
  hidden?: boolean;
}

export interface PageCommandEvent {
  /** The page it fired for; null when it is for the app in front, with no
   *  page view visible. */
  id: string | null;
  /** The app the page belongs to; left out by shells from before page views were per app. */
  app?: string;
  /** The fired item's id. */
  command: string;
}

export type PageRunResult = { ok: true; value: unknown } | { ok: false; error: string };

export interface PageCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires: number;
  secure: boolean;
  httpOnly: boolean;
  sameSite: "Strict" | "Lax" | "None";
}

export interface PagesApi {
  isSupported(): Promise<boolean>;
  open(id: string, opts: PageOpenOptions): Promise<void>;
  close(id: string): Promise<void>;
  /** Closes the calling app's page views. */
  closeAll(): Promise<void>;
  setRect(id: string, rect: PageRect): Promise<void>;
  show(id: string): Promise<void>;
  /** Off screen with its sound and video paused, unless `opts.keepPlaying`. */
  hide(id: string, opts?: PageHideOptions): Promise<void>;
  bringToFront(id: string): Promise<void>;
  adopt(popupId: string, id: string): Promise<void>;
  navigate(id: string, url: string): Promise<void>;
  back(id: string): Promise<void>;
  forward(id: string): Promise<void>;
  reload(id: string): Promise<void>;
  stop(id: string): Promise<void>;
  run(id: string, script: string, opts?: PageRunOptions): Promise<PageRunResult>;
  snapshot(id: string): Promise<string>;
  setContentMode(id: string, mode: PageContentMode, userAgent?: string): Promise<void>;
  setZoom(id: string, factor: number): Promise<void>;
  setMuted(id: string, muted: boolean): Promise<void>;
  /** Where the app's web UI draws over the page, in `setRect`'s CSS pixels:
   *  the web UI takes every click there, and every click anywhere while any
   *  is set as `modal` (the default). A shell that draws pages under its web
   *  UI shows the web UI over them on its own (`usePageSlot`); an older one
   *  cuts the page away at these rects. */
  setCutouts(id: string, rects: PageRect[], options?: PageCutoutOptions): Promise<void>;
  contentRules: {
    set(identifier: string, json: string): Promise<{ ruleCount?: number }>;
    remove(identifier: string): Promise<void>;
    list(): Promise<string[]>;
  };
  /** The app's page commands; the iOS shell takes them and never fires one. */
  commands: {
    set(items: PageCommandItem[]): Promise<void>;
  };
  userScripts: {
    set(identifier: string, script: PageUserScript): Promise<void>;
    remove(identifier: string): Promise<void>;
  };
  /** Where the app's pages may go; `set` replaces the app's rules. */
  navigationRules: {
    set(rules: PageNavigationRules): Promise<void>;
    remove(): Promise<void>;
  };
  cookies: {
    export(profile: string): Promise<PageCookie[]>;
    import(profile: string, list: PageCookie[]): Promise<number>;
  };
  profiles: {
    list(): Promise<string[]>;
    remove(profile: string): Promise<void>;
  };
}
