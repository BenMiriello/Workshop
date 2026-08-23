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
import { BreadcrumbProvider } from "@workshop/ui";
import { appPlaces } from "../../apps/registry";
import { Shell } from "./Shell";

// SOME app's route, taken from the registry rather than spelled here. These
// assertions are about "a path that belongs to an installed app", not about
// which app: a literal name would make this file one more platform source
// naming an app, which scripts/check_no_app_names.py counts and CLAUDE.md
// forbids by name ("not a test fixture"). src/apps/registry.tsx is the one
// frontend file allowed to know an app exists, so asking it is the honest
// route. A fictional slug is not a substitute: an unregistered path falls back
// to the Workshop place, so the third test below would assert nothing.
const APP_HOME = appPlaces()[0]?.home ?? "/apps";

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
    for (const path of ["/", "/tickets", "/chats", APP_HOME, "/agents/reviewer"]) {
      const html = renderAt(path);
      expect(html, path).toContain("ws-rail");
      expect(html, path).toContain("ws-pagecol");
      expect(html, path).toContain('data-content="1"');
    }
  });

  it("lights exactly one rail tile, and it is the one the path belongs to", () => {
    const html = renderAt(APP_HOME);
    expect(html.match(/ws-rail__tile[^"]* is-active/g) ?? []).toHaveLength(1);
    // aria-current is what a screen reader reads, so it has to agree with the tint.
    expect(html.match(/aria-current="page"/g)?.length ?? 0).toBeGreaterThan(0);
  });

  it("opens Home with its column and an app without one", () => {
    expect(renderAt("/")).toContain("ws-pagecol is-open");
    expect(renderAt(APP_HOME)).not.toContain("ws-pagecol is-open");
  });

  it("gives one open conversation the whole screen", () => {
    const html = renderAt("/chat/general");
    expect(html).toContain("ws-rail"); // the way out stays
    expect(html).not.toContain("ws-pagecol is-open");
    expect(html).not.toContain("ws-tabbar__slot is-open");
  });

  it("draws a phone tab bar for a place with pages to switch between", () => {
    // Home has nine pages. An app has eight now (stage C of
    // app-is-the-entity-2026-08-20.md gave every app the same Chats/Agents/
    // Memory/Files/Tickets/Runs/Deploys set alongside its own screen), so
    // there is no longer a real one-page app place to exercise the
    // suppression rule against at the render level -- `hasPageList =
    // place.pages.length > 1` in useShell.ts is the whole rule, and
    // `places.test.ts` covers the data it reads (a place's page count) at
    // the pure level. This test keeps the positive case wired.
    const home = renderAt("/");
    expect(home).toContain("ws-tabbar");
    expect(home).toContain("ws-bar1__caret");
    const app = renderAt(APP_HOME);
    expect(app).toContain("ws-tabbar");
    expect(app).toContain("ws-bar1__caret");
  });

  it("draws the place-menu trigger closed, with nothing of the menu until it opens", () => {
    // The tile IS the trigger, and a screen reader has to be told so before it
    // is tapped. The panel, its scrim and its focus trap only exist while open,
    // so the closed contract is the whole of what a first paint can carry.
    const html = renderAt(APP_HOME);
    const tile = /<button[^>]*ws-bar1__tile[^>]*>/.exec(html)?.[0] ?? "";
    expect(tile).toContain('aria-haspopup="menu"');
    expect(tile).toContain('aria-expanded="false"');
    expect(html).not.toContain("ws-placemenu");
    // And the page toolbar's caret is the OTHER expander, open by default on
    // every page. Never default to closed.
    expect(html).toMatch(/ws-bar1__titlebtn[^>]*aria-expanded="true"/);
  });

  it("shows a trail only where the path goes deeper than the page column", () => {
    // A top-level page is already named by the column and the tab bar.
    expect(renderAt("/tickets")).not.toContain("ws-trail");
    expect(renderAt("/")).not.toContain("ws-trail");
    // A record below one is not named anywhere else.
    expect(renderAt("/agents/reviewer")).toContain("ws-trail");
  });

  it("never animates on first paint", () => {
    // `is-animating` is set only by tapping the tile of the place you are in.
    // If it were ever on at mount, every page load would slide.
    for (const path of ["/", "/tickets", APP_HOME, "/chats"]) {
      expect(renderAt(path), path).not.toContain("is-animating");
    }
  });
});
