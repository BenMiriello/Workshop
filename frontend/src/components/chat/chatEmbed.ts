import { createContext, useContext } from "react";
import { inAppTarget, openInHost } from "./embedSignals";

// A chat mounted CHROME-LESS inside another page's frame (`/embed/chat/:scope`).
//
// The chat is the same component; what changes is where its addresses go. The
// focused chat routes to `/chat/...` and `/chats`, and inside a frame either of
// those would load the whole shell into a panel of someone else's page. So an
// embed states two things: its own address for a conversation (picking one
// stays in the frame) and where "all chats" lives (opened outside it).
//
// Absent (null) everywhere but the embed route, which is why every reader falls
// back to the focused chat's own behaviour.

export const EMBED_PREFIX = "/embed/";

export type ChatEmbed = {
  /** The embed's own address for a conversation, or for the scope's default
   *  one when `threadId` is absent. Stays inside the frame. */
  threadPath: (threadId?: string) => string;
  /** The full list this scope's chats belong to. Opened outside the frame. */
  listPath: string;
  /** The context key the conversation picker lists, or null for every scope. */
  pickerScope: string | null;
  /** Where a Workshop page opens from the frame. Absent: `leaveFrame`. */
  leave?: (path: string) => void;
  /** A conversation picked inside the chat, when the page framing it shows
   *  conversations at its own addresses. Absent: `threadPath`, in the frame. */
  openThread?: (threadId: string) => void;
  /** Set by a host in the SAME window that drives the chat from outside it
   *  (the drawer beside a page, components/shell/beside). Absent for a frame. */
  host?: ChatHost;
};

export type ChatHost = {
  /** Open a picked conversation in place instead of navigating. */
  openThread: (threadId: string, contextKey: string) => void;
  /** Where any other Workshop page opens: the host's own window. */
  leave: (path: string) => void;
  /** The conversation the chat is on, whenever it changes. */
  onThread: (threadId: string | null) => void;
  /** Awaited when a send creates the conversation, before its first turn. */
  bindThread: (threadId: string) => Promise<void>;
  /** The chat's composer, for the host to fill and send. */
  bindComposer: (api: import("../shell/beside/besideStore").ComposerApi | null) => void;
  /** How many attached items wait to go with the next message. */
  pending: number;
  /** The text the waiting items add to the next message, cleared on take. */
  takeContext: () => string;
  /** Shown above the composer: the waiting items. */
  chips: import("react").ReactNode;
  /** Shows the list of every chat inside the host, in place of leaving for the
   *  Chats page. */
  showChats: () => void;
  /** Shown at the end of the toolbar: closing the host. */
  onClose: () => void;
  /** "Open full screen": go to the chat's own page, from where it can be put
   *  back beside this one. */
  openFull: (path: string) => void;
};

export const ChatEmbedContext = createContext<ChatEmbed | null>(null);

export function useChatEmbed(): ChatEmbed | null {
  return useContext(ChatEmbedContext);
}

export function isEmbedPath(pathname: string): boolean {
  return pathname.startsWith(EMBED_PREFIX);
}

export function embedChatPath(scope: string, threadId?: string): string {
  const base = `${EMBED_PREFIX}chat/${encodeURIComponent(scope)}`;
  return threadId ? `${base}?thread=${encodeURIComponent(threadId)}` : base;
}

/** The framed chat LIST of one scope (`/embed/chats/<scope>`). */
export function embedChatsPath(scope: string): string {
  return `${EMBED_PREFIX}chats/${encodeURIComponent(scope)}`;
}

/**
 * THE HOST'S OWN ADDRESSES FOR ITS CHATS, handed to a frame as `?chats=<base>`.
 *
 * An app that frames its chat list and its chat can show them at its own
 * addresses, so its address bar and Back work: `<base>` is its list,
 * `<base>/<id>` one conversation and `<base>/new` a new one. `<base>` is a
 * Workshop path to one of the app's own screens (`/apps/<slug>/ui/<service>/
 * #/chats`), so a pick is offered to the host with the same `workshop/v1/open`
 * message a link in the chat uses, and opens that screen in the whole window
 * when no host answers. Anything that is not one of the framing app's own
 * screens is ignored, and the frame keeps its own addresses. A chat opened at
 * `<base>/new` names its id with `workshop/v1/chatThread` once its first
 * message creates it (embedSignals), for the host to put in its address.
 */
export const HOST_CHATS_PARAM = "chats";

export function hostChatsBase(raw: string | null, appSlug: string | null): string | null {
  if (!raw || !appSlug || !raw.startsWith("/") || raw.startsWith("//")) return null;
  return inAppTarget(raw, appSlug) ? raw.replace(/\/+$/, "") : null;
}

export function hostChatPath(base: string, threadId?: string): string {
  return `${base}/${threadId ? encodeURIComponent(threadId) : "new"}`;
}

/** Open `path` from inside a frame: one of the framing app's own screens is
 *  offered to the app first, anything else opens in the window hosting it. */
export function openFromFrame(path: string, appSlug: string | null): void {
  const target = appSlug ? inAppTarget(path, appSlug) : null;
  if (target) openInHost(path, target, () => leaveFrame(path));
  else leaveFrame(path);
}

/** The chat list narrowed to one scope, which is where an embed's "see all"
 *  goes. Never an app's whole collection (`/apps/<slug>/chats`): that one is
 *  keyed by app, and holds the app's build chats as well as its own. */
export function embedListPath(scope: string): string {
  return `/chats?scope=${encodeURIComponent(scope)}`;
}

/** The path a click on `href` should take OUT of the frame, or null when the
 *  click belongs to the frame: another embed address, another origin, a link
 *  within this page, or one that already names its own target. `current` is
 *  the frame's own `location.href`. */
export function pathLeavingFrame(
  href: string, current: string, target: string | null,
): string | null {
  if (target && target !== "_self") return null;
  let here: URL;
  let url: URL;
  try {
    here = new URL(current);
    url = new URL(href, here);
  } catch {
    return null;
  }
  if (url.origin !== here.origin || isEmbedPath(url.pathname)) return null;
  if (url.pathname === here.pathname && url.search === here.search) return null;
  return url.pathname + url.search + url.hash;
}

/** Open a Workshop page in the window that hosts the frame. A same-origin top
 *  window is navigated; one this document cannot reach gets a new tab. */
export function leaveFrame(path: string): void {
  try {
    const top = window.top as (Window & { workshopNavigate?: (path: string) => void }) | null;
    if (top && top !== window && top.location.origin === window.location.origin) {
      // The shell navigates in place when it can (Shell.tsx), else it loads.
      if (typeof top.workshopNavigate === "function") top.workshopNavigate(path);
      else top.location.assign(path);
      return;
    }
  } catch {
    // A cross-origin parent throws on `location` access.
  }
  if (window.top === window) window.location.assign(path);
  else window.open(path, "_blank", "noopener");
}
