// Where a link in a chat goes. A Workshop page opens in place, in this window;
// a web page opens in the app the operator chose for web links (an app declares
// it can, `services/web_links.py`), or in a new window when none is chosen.

import { useQuery } from "@tanstack/react-query";
import type { components } from "../api/schema.gen";
import { getJson, jsonOrFail } from "./http";
import { queryKeys } from "./queryKeys";

export type WebLinks = components["schemas"]["WebLinks"];

export type LinkTarget =
  | { kind: "workshop"; path: string }
  | { kind: "app"; path: string }
  | { kind: "window" }
  | { kind: "none" };

/** Where `href`, clicked on the page at `here`, should go. `route` is the
 *  chosen app's route for web pages (`{url}`, optional `{n}`), or null. */
export function linkTarget(href: string | undefined, here: string, route: string | null, n = Date.now()): LinkTarget {
  // An anchor in this page is the browser's to scroll to.
  if (!href || href.startsWith("#")) return { kind: "none" };
  let url: URL;
  try {
    url = new URL(href, here);
  } catch {
    return { kind: "none" };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return { kind: "none" };
  if (url.origin === new URL(here).origin) {
    // The API serves files and data, not pages of the app.
    if (url.pathname.startsWith("/api/")) return { kind: "window" };
    return { kind: "workshop", path: url.pathname + url.search + url.hash };
  }
  if (!route) return { kind: "window" };
  return { kind: "app", path: route.replace("{url}", encodeURIComponent(url.href)).replace("{n}", String(n)) };
}

type Handler = { route: string; native_pages?: boolean } | null | undefined;

/** The route web links open in here: the chosen app's, unless it shows web
 *  pages only in the Workshop's native app and this is not it. */
export function webRoute(chosen: Handler, nativePages: boolean): string | null {
  if (!chosen) return null;
  return chosen.native_pages && !nativePages ? null : chosen.route;
}

type Bridged = { WorkshopNative?: { has: (cap: string) => boolean } };

/** Whether this window, or the Workshop window it is framed in, is the native
 *  app with page views. */
export function nativePagesHere(): boolean {
  const has = (w: Window | null) => !!(w as unknown as Bridged | null)?.WorkshopNative?.has("pages");
  try {
    return has(window) || has(window.top);
  } catch {
    return has(window);
  }
}

export function useWebLinks() {
  return useQuery({
    queryKey: queryKeys.webLinks(),
    queryFn: () => getJson<WebLinks>("/api/web-links"),
    staleTime: 5 * 60_000,
  });
}

export async function chooseWebLinks(app: string | null): Promise<WebLinks> {
  return jsonOrFail<WebLinks>(await fetch("/api/web-links", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app }),
  }));
}
