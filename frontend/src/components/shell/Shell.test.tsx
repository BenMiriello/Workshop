// The shell is the one component every screen renders inside, so a shell that
// throws is a blank app. `places.test.ts` and `shellState.test.ts` cover the
// rules; this covers the wiring, by mounting the real thing.
//
// Server rendering, no DOM: this stays inside the "pure logic, no DOM harness"
// rule while still exercising the real router, the real app registry and the
// real auth context. Same pattern as apps/registry.test.tsx.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../../auth/AuthProvider";
import { BreadcrumbProvider } from "../appNav";
import { Shell } from "./Shell";

// The same nesting main.tsx uses: AuthProvider > Guard > App > Shell. The
// avatar reads the signed-in user, so rendering the shell without the provider
// throws, exactly as it would in the app.
function renderAt(path: string): string {
  return renderToString(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <BreadcrumbProvider>
          <Shell>
            <i data-content="1" />
          </Shell>
        </BreadcrumbProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("the shell mounts", () => {
  it("renders the rail, a page column and the content on every top-level route", () => {
    for (const path of ["/", "/work", "/chats", "/apps/finance", "/agents/reviewer"]) {
      const html = renderAt(path);
      expect(html, path).toContain("ws-rail");
      expect(html, path).toContain("ws-pagecol");
      expect(html, path).toContain('data-content="1"');
    }
  });

  it("lights exactly one rail tile, and it is the one the path belongs to", () => {
    const html = renderAt("/apps/finance");
    expect(html.match(/ws-rail__tile[^"]* is-active/g) ?? []).toHaveLength(1);
    // aria-current is what a screen reader reads, so it has to agree with the tint.
    expect(html.match(/aria-current="page"/g)?.length ?? 0).toBeGreaterThan(0);
  });

  it("opens Home with its column and an app without one", () => {
    expect(renderAt("/")).toContain("ws-pagecol is-open");
    expect(renderAt("/apps/finance")).not.toContain("ws-pagecol is-open");
  });

  it("gives one open conversation the whole screen", () => {
    const html = renderAt("/chat/general");
    expect(html).toContain("ws-rail"); // the way out stays
    expect(html).not.toContain("ws-pagecol is-open");
    expect(html).not.toContain("ws-tabbar__slot is-open");
  });

  it("draws no phone tab bar for a place with only one page", () => {
    // A bar holding one always-active tab is a second copy of the title, one
    // row under the title, on the smallest screen there is.
    const app = renderAt("/apps/finance");
    expect(app).not.toContain("ws-tabbar");
    expect(app).not.toContain("ws-bar1__caret");
    // Home has nine pages, so it keeps both.
    const home = renderAt("/");
    expect(home).toContain("ws-tabbar");
    expect(home).toContain("ws-bar1__caret");
  });

  it("shows a trail only where the path goes deeper than the page column", () => {
    // A top-level page is already named by the column and the tab bar.
    expect(renderAt("/work")).not.toContain("ws-trail");
    expect(renderAt("/")).not.toContain("ws-trail");
    // A record below one is not named anywhere else.
    expect(renderAt("/agents/reviewer")).toContain("ws-trail");
  });

  it("never animates on first paint", () => {
    // `is-animating` is set only by tapping the tile of the place you are in.
    // If it were ever on at mount, every page load would slide.
    for (const path of ["/", "/work", "/apps/finance", "/chats"]) {
      expect(renderAt(path), path).not.toContain("is-animating");
    }
  });
});
