// Contract for the native page view, part of the Workshop native bridge
// (re-exported by `shared/native-bridge.ts`, which owns the wire model).
//
// A page view is a real WebKit page drawn by the shell (the phone app, or the
// Mac app), placed above the Workshop web UI at a rectangle the web page
// reports. Each page belongs to a profile: a separate website data store
// (iOS 17+, macOS 14+), so cookies and site storage are kept apart by the
// system. Page views do NOT get
// `window.WorkshopNative`; only the shell's own web UI can drive them.
//
// Everything is kept per app. A request carries the calling app's id (`app`
// on the wire, set by `WorkshopNative.forApp`, which `pagesBridge()` in the
// kit calls with the slug of the app its frame serves), and the shell gives
// each app its own page ids, profiles and their data stores, rule lists,
// scripts and commands: an app lists, reads and removes only its own, and
// events carry the `app` of their page. A request with no app is refused.
// The id is the caller's claim: app frames share Workshop's origin, so an
// app that scripts another frame can speak as that app. It keeps apps from
// reaching each other's data by accident, not on purpose.
//
// Native side: mobile/modules/workshop-pages (Swift; its ios/core is shared
// with the Mac app), mobile/native/pages.ts, and macos/ (PagesBridge.swift).
// Web side: `window.WorkshopNative.pages`, or `pagesBridge()` from
// @workshop/ui, which also finds the bridge from a same-origin app frame.

import type { PageNavigationRules } from "./native-page-navigation";
import type { PageRunOptions, PageUserScript } from "./native-page-scripts";

export type { PageMessageEvent, PageRunOptions, PageUserScript, PageWorld } from "./native-page-scripts";
export type { PageDownloadEvent } from "./native-page-downloads";
export type {
  PageHopRule, PageNavigationBlockedEvent, PageNavigationRules, PageStripRule,
} from "./native-page-navigation";
export type {
  PageCommandEvent, PageErrorEvent, PageNewWindowEvent, PageState, PageWindowFeatures,
} from "./native-page-events";

/**
 * A rectangle in CSS pixels of the TOP Workshop document's viewport (what
 * `getBoundingClientRect()` returns in the top document). A caller inside a
 * framed app adds its frame's offset first (`topViewportRect` in the kit).
 * The shell's page must not be pinch-zoomed; when its layout moves (scroll,
 * keyboard, resize) the caller reports the new rect with `setRect`.
 */
export interface PageRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Where a profile's traffic goes. It is set on the profile's data store, so
 * it applies to every page of that profile; when omitted, the profile keeps
 * whatever it was given last.
 */
export interface PageProxy {
  host: string;
  port: number;
  kind: "socks5" | "http";
}

/**
 * How WebKit asks sites to serve a page: as to a phone or as to a desktop
 * browser. The caller supplies the matching user agent (`userAgent`).
 */
export type PageContentMode = "mobile" | "desktop";

export interface PageOpenOptions {
  /** Profile name; the same name always maps to the same data store. */
  profile: string;
  url: string;
  rect: PageRect;
  proxy?: PageProxy;
  /** Extra trust anchors (base64 DER) for this page only; system roots still apply. */
  trustCertDER?: string[];
  userAgent?: string;
  /** When omitted, WebKit chooses for the device. */
  contentMode?: PageContentMode;
}

/** A modifier held with a page command's key. */
export type PageKeyModifier = "command" | "shift" | "option" | "control";

/**
 * A command an app offers while it is in front: one of its page views has
 * focus, or focus is elsewhere in the shell's window and one of its page views
 * is the topmost visible one, or no page view is visible and the web UI shows
 * the app's frame (`WorkshopNative.shell.foreground`), in which case the
 * event names no page. A key typed in a page view goes to the page, never to
 * the Workshop web UI, so a shell with a menu bar (the Mac) lists these in it
 * and takes their keys first, wherever focus is. When the app in front has no
 * command of that key, or no app is, the shell's own item of that key applies.
 */
export interface PageCommandItem {
  /** Reported as `command` in "pages:command". */
  id: string;
  /** The menu item's title. */
  title: string;
  /** One character, as typed without modifiers: "l", "[". */
  key: string;
  /** Default `["command"]`. */
  modifiers?: PageKeyModifier[];
  /** The top-level menu it goes in, made when the shell has none of that
   *  name. Default "File". */
  menu?: string;
  /** Keyboard focus moves to the Workshop web UI before the event, for a
   *  command that types into the app. */
  focusShell?: boolean;
  /** Takes the key without listing a menu item, for a second key of a
   *  command already listed (Cmd-= beside Cmd-+). */
  hidden?: boolean;
}

/** How `hide` leaves a page view's sound and video. */
export interface PageHideOptions {
  /** The page keeps playing while hidden, as long as its app is the one the
   *  web UI shows and the shell is on screen. Default false. */
  keepPlaying?: boolean;
}

/** How `setCutouts` shares input between the page and the web UI. */
export interface PageCutoutOptions {
  /** The web UI takes every click and touch while any rect is set, as a
   *  page under an open menu does; false passes only those inside the rects.
   *  Default true. */
  modal?: boolean;
}

/** Result of `run`: script errors come back as values, never as rejections. */
export type PageRunResult = { ok: true; value: unknown } | { ok: false; error: string };

export interface PageCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  /** Seconds since the epoch; -1 for a session cookie. */
  expires: number;
  secure: boolean;
  httpOnly: boolean;
  sameSite: "Strict" | "Lax" | "None";
}

export interface PagesApi {
  /** False before iOS 17 or in a build without the page-view module. */
  isSupported(): Promise<boolean>;
  /** Opens (or replaces) page `id` and starts loading `url`. A page opened
   *  again under an id it had before (after its app's screen was rebuilt,
   *  the shell reloaded or the app relaunched) gets its back and forward list
   *  back; `url` is added to it when the page was elsewhere. */
  open(id: string, opts: PageOpenOptions): Promise<void>;
  close(id: string): Promise<void>;
  /** Closes the calling app's pages. A new shell document closes every app's. */
  closeAll(): Promise<void>;
  setRect(id: string, rect: PageRect): Promise<void>;
  show(id: string): Promise<void>;
  /**
   * Takes page `id` off screen, loaded as it was. A page plays only while it
   * is on screen, so its sound and video are paused here and cannot start
   * until it is shown, unless `opts.keepPlaying`; shown again, it can play,
   * and what was playing stays paused until something plays it. The shell applies
   * the same hold to every page of an app whose frame the web UI is not
   * showing (`ShellApi.foreground`) and to every page while the shell itself
   * is off screen (in the background, minimized or covered), whatever the app
   * asked.
   */
  hide(id: string, opts?: PageHideOptions): Promise<void>;
  bringToFront(id: string): Promise<void>;
  /** Gives pop-up `popupId` (from "pages:newWindow") the id `id`; it is then a page like any other. */
  adopt(popupId: string, id: string): Promise<void>;
  navigate(id: string, url: string): Promise<void>;
  back(id: string): Promise<void>;
  forward(id: string): Promise<void>;
  reload(id: string): Promise<void>;
  stop(id: string): Promise<void>;
  /**
   * Runs `script` as the body of an async function in the main frame, in
   * the page's own world (it sees the page's globals) unless `opts.world`
   * says otherwise; `return` a JSON-serialisable value.
   */
  run(id: string, script: string, opts?: PageRunOptions): Promise<PageRunResult>;
  /** A still of the page as base64 JPEG (no data: prefix). */
  snapshot(id: string): Promise<string>;
  /**
   * Sets page `id`'s content mode and reloads it. `userAgent`, when given,
   * becomes its user agent first; an empty string restores WebKit's own.
   */
  setContentMode(id: string, mode: PageContentMode, userAgent?: string): Promise<void>;
  /**
   * Draws page `id` at `factor` times its size (1 is normal), the way a
   * browser's zoom does: the layout width changes with it. The factor is held
   * to 0.3 through 3 and stays with the page view across its navigations; a
   * page view opened again starts at 1.
   */
  setZoom(id: string, factor: number): Promise<void>;
  /**
   * Silences page `id`'s sound, or lets it through again. The page keeps
   * playing; it stays muted across its navigations, and a page view opened
   * again starts unmuted. Where the shell cannot tell sound (`PageState.muted`
   * is left out), this rejects.
   */
  setMuted(id: string, muted: boolean): Promise<void>;
  /**
   * Where the app's web UI draws over page `id`, as rects in the same CSS
   * pixels as `setRect`: clicks and touches there go to the web UI. While
   * any rect is set and `modal` holds (the default: a menu or dialog is
   * open), every one does. An empty list gives the page all of them again; a
   * page opened again starts so. Page views are drawn under the web UI, so
   * what the app draws shows over a page on its own (the kit's
   * `usePageSlot` keeps the slot see-through); a shell from before that cuts
   * the page away at these rects instead. What the shell's own UI draws
   * (`shell.overlays`) is kept apart from these and needs nothing from the app.
   */
  setCutouts(id: string, rects: PageRect[], options?: PageCutoutOptions): Promise<void>;
  /**
   * Content rule lists in WebKit's content blocker JSON, applied to every
   * page view of the app that set them, open or opened later. Compiled lists are stored on the device
   * and are back in force at the next launch before anything sets them again.
   */
  contentRules: {
    /**
     * Compiles `json` and applies it under `identifier`, replacing a list of
     * the same identifier. Rejects with the compiler's message when it fails.
     * `ruleCount` is the number of rules in `json`.
     */
    set(identifier: string, json: string): Promise<{ ruleCount?: number }>;
    /** Takes the list off every page and out of the store; no list is not an error. */
    remove(identifier: string): Promise<void>;
    /** Identifiers of the stored lists. */
    list(): Promise<string[]>;
    /**
     * Lifts rules without compiling a list again, replacing what the app
     * lifted before; `{}` lifts nothing. Pages on a site in `sites` (a host
     * and its subdomains, as a page's top address says) carry none of the
     * app's lists, and no page carries a list named in `lists`. It shows from
     * a page's next load, a reload included, and is kept on the device.
     * Rejects what does not read, or on a WebKit that cannot lift per page,
     * keeping the old.
     */
    setLifted(lifted: { sites?: string[]; lists?: string[] }): Promise<void>;
  };
  /**
   * The app's page commands; see `PageCommandItem`. Without one for Cmd-W,
   * Cmd-W closes the Mac window even from a page view or with the app in
   * front. The iOS shell takes the list and never fires a command.
   */
  commands: {
    /** Replaces the commands set before; `[]` removes them. A new top
     *  document starts with none. */
    set(items: PageCommandItem[]): Promise<void>;
  };
  /** Scripts installed at document load in every page view of the app; see `PageUserScript`. */
  userScripts: {
    /** Installs `script` under `identifier`, replacing one of the same identifier. */
    set(identifier: string, script: PageUserScript): Promise<void>;
    /** Takes the script off every page from its next document; none is not an error. */
    remove(identifier: string): Promise<void>;
  };
  /** Where the app's pages may go; see `PageNavigationRules`. */
  navigationRules: {
    /** Replaces the app's rules. Rejects rules that do not read (an unknown
     *  key, a bad pattern), keeping the old ones. */
    set(rules: PageNavigationRules): Promise<void>;
    /** Takes the app's rules away; none is not an error. */
    remove(): Promise<void>;
  };
  cookies: {
    export(profile: string): Promise<PageCookie[]>;
    /** Resolves to the number of cookies stored. */
    import(profile: string, list: PageCookie[]): Promise<number>;
  };
  profiles: {
    /** The app's profile names with stored data. The app that took over the
     *  stores from before profiles were per app also gets any store no app
     *  names, listed by its UUID. */
    list(): Promise<string[]>;
    /** Deletes a profile's data. Rejects while any open page uses it. */
    remove(profile: string): Promise<void>;
  };
}

/** Bridge method names for `pages`; a dotted name is a nested namespace. */
export const PAGES_METHODS = [
  "isSupported", "open", "close", "closeAll", "setRect", "show", "hide", "bringToFront", "adopt",
  "navigate", "back", "forward", "reload", "stop", "run", "snapshot", "setContentMode", "setZoom", "setMuted", "setCutouts",
  "contentRules.set", "contentRules.remove", "contentRules.list", "contentRules.setLifted", "userScripts.set", "userScripts.remove",
  "commands.set", "navigationRules.set", "navigationRules.remove",
  "cookies.export", "cookies.import", "profiles.list", "profiles.remove",
] as const;

export type PagesEventTopic =
  | "pages:state" | "pages:error" | "pages:newWindow" | "pages:command" | "pages:message"
  | "pages:navigationBlocked" | "pages:download";
