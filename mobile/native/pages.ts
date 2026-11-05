// Native page views: real WKWebViews placed under the shell's see-through
// WebView, one data store per profile. The Swift side is the local module
// modules/workshop-pages; the contract is shared/native-pages.ts. A build
// without the module, or iOS before 17, reports isSupported() false and every
// other call rejects with a plain message. Every call but isSupported takes
// the calling app's id first (the request's `app`, which the dispatcher
// passes); the module keeps each app's pages and data apart by it.

import { EventEmitter, requireOptionalNativeModule } from "expo-modules-core";

import type {
  BridgeEventTopic,
  PageCommandItem,
  PageContentMode,
  PageHideOptions,
  PageCookie,
  PageNavigationRules,
  PageOpenOptions,
  PageCutoutOptions,
  PageRect,
  PageRunOptions,
  PageRunResult,
  PageUserScript,
} from "@shared/native-bridge";

interface NativePages {
  isSupported(): boolean;
  open(app: string, id: string, opts: PageOpenOptions): Promise<void>;
  close(app: string, id: string): Promise<void>;
  closeAll(app: string | null): Promise<void>;
  setRect(app: string, id: string, rect: PageRect): Promise<void>;
  show(app: string, id: string): Promise<void>;
  hide(app: string, id: string, keepsPlaying: boolean): Promise<void>;
  setForeground?(app: string | null): Promise<void>;
  bringToFront(app: string, id: string): Promise<void>;
  adopt(app: string, popupId: string, id: string): Promise<void>;
  navigate(app: string, id: string, url: string): Promise<void>;
  back(app: string, id: string): Promise<void>;
  forward(app: string, id: string): Promise<void>;
  reload(app: string, id: string): Promise<void>;
  stop(app: string, id: string): Promise<void>;
  run(app: string, id: string, script: string, opts: PageRunOptions | null): Promise<string>;
  snapshot(app: string, id: string): Promise<string>;
  setContentMode(app: string, id: string, mode: PageContentMode, userAgent: string | null): Promise<void>;
  setZoom(app: string, id: string, factor: number): Promise<void>;
  setMuted(app: string, id: string, muted: boolean): Promise<void>;
  setCutouts(app: string, id: string, rects: PageRect[], modal: boolean): Promise<void>;
  setOverlays?(rects: PageRect[], modal: boolean): Promise<void>;
  setContentRules(app: string, identifier: string, json: string): Promise<{ ruleCount?: number }>;
  removeContentRules(app: string, identifier: string): Promise<void>;
  listContentRules(app: string): Promise<string[]>;
  setLiftedContentRules(app: string, lifted: { sites?: string[]; lists?: string[] }): Promise<void>;
  setUserScript(app: string, identifier: string, script: PageUserScript): Promise<void>;
  removeUserScript(app: string, identifier: string): Promise<void>;
  setNavigationRules(app: string, rules: PageNavigationRules): Promise<void>;
  removeNavigationRules(app: string): Promise<void>;
  exportCookies(app: string, profile: string): Promise<PageCookie[]>;
  importCookies(app: string, profile: string, list: PageCookie[]): Promise<number>;
  listProfiles(app: string): Promise<string[]>;
  removeProfile(app: string, profile: string): Promise<void>;
  addListener(eventName: string): void;
  removeListeners(count: number): void;
}

const Native = requireOptionalNativeModule<NativePages>("WorkshopPages");

function need(): NativePages {
  if (!Native) throw new Error("Page views are not in this build of the app");
  return Native;
}

export async function isSupported(): Promise<boolean> {
  try {
    return !!Native && Native.isSupported();
  } catch {
    return false;
  }
}

export const open = (app: string, id: string, opts: PageOpenOptions) => need().open(app, id, opts);
export const close = (app: string, id: string) => need().close(app, id);
export const setRect = (app: string, id: string, rect: PageRect) => need().setRect(app, id, rect);
export const show = (app: string, id: string) => need().show(app, id);
export const hide = (app: string, id: string, opts?: PageHideOptions) =>
  need().hide(app, id, !!opts?.keepPlaying);
export const bringToFront = (app: string, id: string) => need().bringToFront(app, id);
export const adopt = (app: string, popupId: string, id: string) => need().adopt(app, popupId, id);
export const navigate = (app: string, id: string, url: string) => need().navigate(app, id, url);
export const back = (app: string, id: string) => need().back(app, id);
export const forward = (app: string, id: string) => need().forward(app, id);
export const reload = (app: string, id: string) => need().reload(app, id);
export const stop = (app: string, id: string) => need().stop(app, id);
export const snapshot = (app: string, id: string) => need().snapshot(app, id);
export const setContentMode = (app: string, id: string, mode: PageContentMode, userAgent?: string) =>
  need().setContentMode(app, id, mode, userAgent ?? null);
export const setZoom = (app: string, id: string, factor: number) => need().setZoom(app, id, factor);
export const setMuted = (app: string, id: string, muted: boolean) => need().setMuted(app, id, muted);
export const setCutouts = (app: string, id: string, rects: PageRect[], options?: PageCutoutOptions) =>
  need().setCutouts(app, id, rects, options?.modal ?? true);
export const setContentRules = (app: string, identifier: string, json: string) =>
  need().setContentRules(app, identifier, json);
export const removeContentRules = (app: string, identifier: string) => need().removeContentRules(app, identifier);
export const listContentRules = (app: string) => need().listContentRules(app);
export const setLiftedContentRules = (app: string, lifted: { sites?: string[]; lists?: string[] }) =>
  need().setLiftedContentRules(app, lifted);
export const setUserScript = (app: string, identifier: string, script: PageUserScript) =>
  need().setUserScript(app, identifier, script);
export const removeUserScript = (app: string, identifier: string) => need().removeUserScript(app, identifier);
export const setNavigationRules = (app: string, rules: PageNavigationRules) => need().setNavigationRules(app, rules);
export const removeNavigationRules = (app: string) => need().removeNavigationRules(app);
export const exportCookies = (app: string, profile: string) => need().exportCookies(app, profile);
export const importCookies = (app: string, profile: string, list: PageCookie[]) =>
  need().importCookies(app, profile, list);
export const listProfiles = (app: string) => need().listProfiles(app);
export const removeProfile = (app: string, profile: string) => need().removeProfile(app, profile);

/** The phone has no menu bar to list page commands in, and a page view's keys
 *  stay the page's, so the list is taken and nothing fires. */
export async function setCommands(_app: string, _items: PageCommandItem[]): Promise<void> {}

/** The app whose frame the web UI shows; its page views are the only ones
 *  drawn or playing. A no-op in a build without the module. */
export async function setForeground(app: string | null): Promise<void> {
  if (Native?.setForeground) await Native.setForeground(app);
}

/** Where the web UI's own layers sit over every page view, which take the
 *  touches there (`shell.overlays`). A no-op in a build without the module. */
export async function setOverlays(rects: PageRect[], modal: boolean): Promise<void> {
  if (Native?.setOverlays) await Native.setOverlays(rects, modal);
}

/** Closes `app`'s page views; a no-op in a build without the module. */
export async function closeAll(app: string): Promise<void> {
  if (Native) await Native.closeAll(app);
}

/** Closes every app's page views, for a new shell document. */
export async function closeEverything(): Promise<void> {
  if (Native) await Native.closeAll(null);
}

export async function run(app: string, id: string, script: string, opts?: PageRunOptions): Promise<PageRunResult> {
  const json = await need().run(app, id, script, opts ?? null);
  try {
    return JSON.parse(json) as PageRunResult;
  } catch {
    return { ok: false, error: "the page returned a result that is not JSON" };
  }
}

const EVENT_TOPICS: Record<string, BridgeEventTopic> = {
  pageState: "pages:state",
  pageError: "pages:error",
  pageNewWindow: "pages:newWindow",
  pageMessage: "pages:message",
  pageNavigationBlocked: "pages:navigationBlocked",
  pageDownload: "pages:download",
};

/** Forwards the module's events to `emit` as bridge topics. Returns an unsubscribe. */
export function subscribe(emit: (topic: BridgeEventTopic, payload: unknown) => void): () => void {
  if (!Native) return () => {};
  const emitter = new EventEmitter(Native);
  const subs = Object.entries(EVENT_TOPICS).map(([name, topic]) =>
    emitter.addListener(name, (payload: unknown) => emit(topic, payload)),
  );
  return () => subs.forEach((s) => s.remove());
}
