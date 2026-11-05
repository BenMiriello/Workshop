import { describe, expect, it } from "vitest";
import { linkTarget, webRoute } from "./webLinks";

const HERE = "https://ws.test/chat/general?thread=t1";
const ROUTE = "/apps/web/ui/app#open={url}&n={n}";

describe("where a chat link goes", () => {
  it("opens a Workshop page in place, relative or absolute", () => {
    expect(linkTarget("/apps/web/ui/app#tab=ab", HERE, ROUTE)).toEqual({ kind: "workshop", path: "/apps/web/ui/app#tab=ab" });
    expect(linkTarget("https://ws.test/chat/x?thread=2", HERE, null)).toEqual({ kind: "workshop", path: "/chat/x?thread=2" });
  });

  it("opens a web page in the chosen app, encoded, with a fresh number", () => {
    expect(linkTarget("https://example.com/a?b=1", HERE, ROUTE, 7)).toEqual({
      kind: "app", path: "/apps/web/ui/app#open=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1&n=7",
    });
  });

  it("opens a web page in a new window when no app is chosen", () => {
    expect(linkTarget("https://example.com", HERE, null)).toEqual({ kind: "window" });
  });

  it("leaves other schemes and nothing alone", () => {
    expect(linkTarget("mailto:someone", HERE, ROUTE)).toEqual({ kind: "none" });
    expect(linkTarget(undefined, HERE, ROUTE)).toEqual({ kind: "none" });
    expect(linkTarget("#msg-3", HERE, ROUTE)).toEqual({ kind: "none" });
  });
});

describe("webRoute", () => {
  const chosen = { route: "/apps/b/ui/app#open={url}", native_pages: true };
  it("uses an app that needs native pages only in the native app", () => {
    expect(webRoute(chosen, true)).toBe(chosen.route);
    expect(webRoute(chosen, false)).toBeNull();
    expect(webRoute({ route: "/r#open={url}" }, false)).toBe("/r#open={url}");
    expect(webRoute(null, true)).toBeNull();
  });
});

describe("API links", () => {
  it("open in a window, not the app router", () => {
    expect(linkTarget("/api/files/x.pdf", "https://ws.test/chats/1", null).kind).toBe("window");
  });
});
