import { describe, expect, it } from "vitest";
import { Bot } from "lucide-react";
import {
  appChatPath, buildPlaces, isOpenChat, landingFor, pageFor, placeFor, type ShellPlace,
} from "./places";
import { APP_TABS } from "../../pages/apps/tabs/appTabState";

// One glyph stands in for all of them: this module decides ROUTES, and which
// icon a row draws is the render's business.
const icons = new Proxy({} as Record<string, typeof Bot>, { get: () => Bot });

// Two apps that ship their own screens, under FICTIONAL ids. The fixtures here
// named two of this installation's real apps, which is a platform file naming a
// specific app and is what scripts/check_no_app_names.py counts; the precedent
// for renaming a fixture rather than re-pinning the gate is recorded in
// docs/platform-redesign/app-separation-and-github-2026-08-20.md.
//
// `widgets` has no linked working tree, so five of the seven platform tabs can
// never hold anything for it; `gizmo` has one and a file root, so it can have
// all seven. Neither declares a `tabs` list, which is the "take everything you
// can have" default.
const APPS = [
  { id: "widgets", name: "Widgets", home: "/apps/widgets", ownScreen: true },
  {
    id: "gizmo", name: "Gizmo", home: "/apps/gizmo", ownScreen: true,
    projectSlug: "gizmo", hasFiles: true,
  },
];

const places = buildPlaces(APPS, icons);
const home = places.find((p) => p.id === "home") as ShellPlace;
const chats = places.find((p) => p.id === "chats") as ShellPlace;
const gizmo = places.find((p) => p.id === "app:gizmo") as ShellPlace;
const widgets = places.find((p) => p.id === "app:widgets") as ShellPlace;

describe("placeFor", () => {
  it("puts an unclaimed route on Home rather than nowhere", () => {
    expect(placeFor(places, "/").id).toBe("home");
    expect(placeFor(places, "/tickets").id).toBe("home");
    expect(placeFor(places, "/some/route/nobody/claimed").id).toBe("home");
  });

  it("keeps the Apps LIST on Home and only a specific app on its own tile", () => {
    expect(placeFor(places, "/apps").id).toBe("home");
    expect(placeFor(places, "/apps/widgets").id).toBe("app:widgets");
    expect(placeFor(places, "/apps/gizmo/locations").id).toBe("app:gizmo");
  });

  it("lights All chats while one conversation is open", () => {
    expect(placeFor(places, "/chats").id).toBe("chats");
    expect(placeFor(places, "/chats/archived").id).toBe("chats");
    expect(placeFor(places, "/chat/general").id).toBe("chats");
  });

  it("does not let the /chat prefix swallow /chats", () => {
    // Both belong to the same place, so this is about the matcher and not the
    // outcome: a bare startsWith would make "/chats" a hit for "/chat", which
    // would be wrong the moment the two are ever separate places.
    expect(placeFor(places, "/chats").id).toBe("chats");
  });
});

describe("an app's own chat route", () => {
  // THE DEFECT THIS EXISTS FOR. A chat row on an app's Chats tab navigated to
  // `/chat/<context-key>`, which belongs to the All chats place, so opening a
  // chat from inside an app left the app: the dock switched, the app's tabs
  // went, and Back returned to the global list. The operator, 2026-09-17: "if I
  // go to a project and I go to its chats page and then I open a particular
  // chat thread, it goes to all chats, which is not correct."
  const path = appChatPath("gizmo", "t-1");

  it("addresses the conversation under the app, not under All chats", () => {
    expect(path).toBe("/apps/gizmo/chats/t-1");
    expect(placeFor(places, path).id).toBe("app:gizmo");
    expect(placeFor(places, path).id).not.toBe("chats");
  });

  it("keeps the app's Chats tab highlighted", () => {
    // `gizmo` ships its own screens, so its first row is the app itself and the
    // tab rows follow. The conversation is a record BELOW the Chats row, and a
    // page matches as a prefix, so no new mechanism is needed for the tab to
    // stay lit.
    expect(pageFor(widgets, "/apps/widgets/chats")?.id).toBe("chats");
    expect(pageFor(widgets, appChatPath("widgets", "t-1"))?.id).toBe("chats");
  });

  it("escapes both segments, because a scope key and an id are data", () => {
    expect(appChatPath("a b", "x/y")).toBe("/apps/a%20b/chats/x%2Fy");
  });

  it("is an open conversation, and so is the global focused chat", () => {
    expect(isOpenChat("/chat/general")).toBe(true);
    expect(isOpenChat(path)).toBe(true);
  });

  it("does not make the TAB an open conversation", () => {
    // One segment after `chats` and no more. The tab is a list; giving it the
    // viewport-tall chat layout would hide its own scrolling.
    expect(isOpenChat("/apps/gizmo/chats")).toBe(false);
    expect(isOpenChat("/apps/gizmo/chats/t-1/extra")).toBe(false);
    expect(isOpenChat("/chats")).toBe(false);
    expect(isOpenChat("/apps/gizmo/tickets/t-1")).toBe(false);
  });
});

describe("pageFor", () => {
  it("matches / exactly, or Dashboard would be the active page everywhere", () => {
    expect(pageFor(home, "/")?.id).toBe("dashboard");
    expect(pageFor(home, "/tickets")?.id).toBe("tickets");
    expect(pageFor(home, "/usage")?.id).toBe("usage");
  });

  it("gives the longest match, so Archived beats All chats", () => {
    expect(pageFor(chats, "/chats")?.id).toBe("all");
    expect(pageFor(chats, "/chats/archived")?.id).toBe("archived");
  });

  it("keeps an app's single row active across its own sub-routes", () => {
    expect(pageFor(gizmo, "/apps/gizmo")?.id).toBe("app");
    expect(pageFor(gizmo, "/apps/gizmo/locations")?.id).toBe("app");
  });

  it("keeps a row lit while you are on a record BELOW it", () => {
    // A page column that goes dark on every detail page is a page column that
    // tells you nothing exactly when you are deepest.
    //
    // /agents/:slug is NOT in this list any more, and that is the change rather
    // than an omission: the Agents roster row was replaced by Roles on
    // 2026-09-01, /agents redirects to /roles, and an agent DETAIL page is a
    // record under a row that no longer exists. Asserting it stayed lit would
    // be asserting the old nav.
    // /projects/<slug> is NOT here any more, and that is the change rather than
    // an omission: Project stopped being an entity, the row is gone from Home,
    // and the route redirects to the app's Overview, whose own crumbs take
    // over. Asserting it stayed lit would be asserting the old nav.
    // A ticket record now does the same. It did not until ADR 0023: the list
    // was at /work while a record was at /tickets/:scope/:id, so reading a
    // ticket darkened the whole column. One noun fixed that as a side effect,
    // which is the clearest evidence the split spellings were costing
    // something real rather than only reading badly.
    expect(pageFor(home, "/tickets/workshop/abc123")?.id).toBe("tickets");
  });

  it("returns null where no row owns the path at all", () => {
    // Runs has no row in the column: a run belongs to the ticket that produced
    // it. Null, not a wrong row lit.
    expect(pageFor(home, "/runs/xyz")).toBeNull();
    expect(pageFor(home, "/stores")).toBeNull();
    // CHANGED 2026-09-01, and this is the change rather than an omission. This
    // file used to assert that a change record lit a "changes" row on Home
    // ("reading one is still being in Changes"), and that a Changes row and a
    // Deploys row existed to be lit. Both rows are gone: section 6 of
    // threads-branches-and-pages-2026-08-29.md keeps on Home only the rows
    // that are genuinely global -- Tickets, Memory, Usage, Roles -- and a
    // change belongs to one app's branch, a deploy to one app's Overview.
    // /changes and /deploys are redirects to Home now, so nothing is orphaned
    // by the row going; what is asserted here is the NEW truth, that Home
    // claims neither path, rather than the old one deleted.
    expect(pageFor(home, "/changes")).toBeNull();
    expect(pageFor(home, "/changes/9f2a")).toBeNull();
    expect(pageFor(home, "/deploys")).toBeNull();
    expect(pageFor(home, "/deploys/9f2a")).toBeNull();
  });

  it("has no Changes or Deploys row on Home", () => {
    // The rows themselves, asserted directly rather than only through the
    // matcher above: a row could come back with a different `to` and the
    // pageFor assertions would still pass while Home grew a page nobody
    // decided to add back.
    const ids = home.pages.map((p) => p.id);
    expect(ids).not.toContain("changes");
    expect(ids).not.toContain("deploys");
    // The four that section 6 does keep, plus the surfaces Home is.
    expect(ids).toEqual(["dashboard", "chats", "apps", "roles", "tickets", "memory", "usage"]);
  });
});

describe("buildPlaces", () => {
  it("opens an app full screen and Home with its list", () => {
    expect(home.defaultColumnOpen).toBe(true);
    expect(chats.defaultColumnOpen).toBe(true);
    expect(gizmo.defaultColumnOpen).toBe(false);
  });

  it("carries an app's DECLARED icon and gives Home an imported glyph", () => {
    // An app's icon is data (a token, a url, or absent -> a monogram of the
    // name). A platform place's is an import, because Home is not an app.
    expect(gizmo.icon).toBeUndefined(); // this fixture declares none
    expect(gizmo.Icon).toBeUndefined();
    expect(home.Icon).toBeTruthy();
    expect(home.icon).toBeUndefined();
  });

  it("gives an app that can hold everything its own screen plus all seven tabs", () => {
    // Every row a real route over a real /api/apps/{id}/... collection
    // (routes/apps.py). This app has a linked tree and a file root, so nothing
    // is filtered out and the list is the platform's full order.
    const home = gizmo.pages[0].to;
    expect(gizmo.pages.map((p) => p.id)).toEqual(["app", ...APP_TABS.map((t) => t.id)]);
    expect(gizmo.pages.map((p) => p.to)).toEqual([
      home,
      ...APP_TABS.map((t) => `${home}${t.suffix}`),
    ]);
  });

  it("does NOT draw a tab an app can never fill", () => {
    // Ticket 7d8d733a. Widgets has no linked working tree, so its tickets,
    // runs, deploys and agents can only ever be empty and it has no files at
    // all. Those rows used to be drawn anyway and explain themselves in an
    // empty state, which is a nav row that exists to apologise.
    expect(widgets.pages.map((p) => p.id)).toEqual(["app", "chats", "memory"]);
    expect(widgets.pages.map((p) => p.to)).toEqual([
      "/apps/widgets", "/apps/widgets/chats", "/apps/widgets/memory",
    ]);
  });

  it("takes an app's DECLARED tabs, in its order, and no others", () => {
    // The declaration narrows and orders; it cannot conjure a tab the app
    // cannot hold (`deploys` here, retired as a tab on 2026-09-01), because
    // that would put the apology back.
    const [app] = buildPlaces(
      [{
        id: "ops", name: "Ops", home: "/apps/ops", projectSlug: "ops",
        tabs: ["memory", "chats", "deploys"], hasFiles: false,
      }],
      icons,
    ).filter((p) => p.kind === "app");
    expect(app.pages.map((p) => p.id)).toEqual(["memory", "chats"]);
  });

  it("gives no app-screen row to an app that ships no screens", () => {
    // `/apps/<slug>` is only a redirect for such an app, so a row there would
    // point at its own tab list.
    const [app] = buildPlaces(
      [{ id: "ops", name: "Ops", home: "/apps/ops", projectSlug: "ops", hasFiles: false }],
      icons,
    ).filter((p) => p.kind === "app");
    expect(app.pages.map((p) => p.id)).not.toContain("app");
    // Work, not Chats: an app with a working tree opens on what is being built
    // in it. See pages/apps/tabs/appTabState.ts and the 2026-08-29 report.
    expect(app.pages[0].id).toBe("work");
  });

  it("still lists no Activity entry (design doc section 8.3, not built yet)", () => {
    const everyPath = places.flatMap((p) => p.pages.map((pg) => pg.to));
    expect(everyPath).not.toContain("/activity");
  });

  it("offers /chats from the home nav and its own place, and nothing else twice", () => {
    // The one intended duplicate: /chats is both the workshop nav's Chats row
    // and the chats place's All chats page (operator, 2026-08-26 -- the dock
    // tile alone left the home path with no way to the chat list). Every OTHER
    // destination is still unique, so an accidental second row to one place is
    // still caught.
    const everyPath = places.flatMap((p) => p.pages.map((pg) => pg.to));
    const dupes = everyPath.filter((to, i) => everyPath.indexOf(to) !== i);
    expect(dupes).toEqual(["/chats"]);
  });

  it("lands a place on its first page", () => {
    expect(landingFor(home)).toBe("/");
    expect(landingFor(chats)).toBe("/chats");
    expect(landingFor(gizmo)).toBe("/apps/gizmo");
  });

  it("lands an app on the surface it DECLARED, over its first row", () => {
    // The whole of ticket 7d8d733a: an app opens where it said, not on
    // whatever the platform happened to list first. An app may keep Chats in
    // its column while opening on its ticket queue.
    const [app] = buildPlaces(
      [{
        id: "ops", name: "Ops", home: "/apps/ops", projectSlug: "ops",
        landing: "/apps/ops/tickets", hasFiles: false,
      }],
      icons,
    ).filter((p) => p.kind === "app");
    expect(app.pages[0].id).toBe("work");
    expect(landingFor(app)).toBe("/apps/ops/tickets");
  });
});

describe("the Settings place", () => {
  const settings = places.find((p) => p.id === "settings") as ShellPlace;

  it("is a place, not a row on Home", () => {
    // It used to be the last page of Home, so reaching it meant landing on Home
    // first. Operator, 2026-08-20: "clicking settings just goes to the workshop
    // home page and then settings, which is actually not correct."
    expect(settings).toBeTruthy();
    expect(settings.kind).toBe("settings");
    expect(home.pages.find((p) => p.id === "settings")).toBeUndefined();
  });

  it("owns every /settings path, including the ones that were already routed", () => {
    expect(placeFor(places, "/settings").id).toBe("settings");
    expect(placeFor(places, "/settings/agents").id).toBe("settings");
    // Grants, Trackers and MCP were separate pages reached through a
    // link-in-a-Card on the Settings blob. They are tabs now, so they must
    // resolve to this place rather than falling back to Home.
    expect(placeFor(places, "/settings/grants").id).toBe("settings");
    expect(placeFor(places, "/settings/mcp").id).toBe("settings");
  });

  it("lights the right tab, and Account only on exactly /settings", () => {
    expect(pageFor(settings, "/settings")?.id).toBe("account");
    expect(pageFor(settings, "/settings/appearance")?.id).toBe("appearance");
    expect(pageFor(settings, "/settings/agents")?.id).toBe("agents");
    // `end` on Account: as a prefix, "/settings" matches every tab below it and
    // two rows light at once.
    expect(pageFor(settings, "/settings/memory")?.id).toBe("memory");
  });

  it("has more than one page, so the phone draws its tab bar", () => {
    expect(settings.pages.length).toBeGreaterThan(1);
  });
});

describe("an app's served screens (app-screens-2026-09-23.md)", () => {
  const served = buildPlaces([
    { id: "shop", name: "Shop", home: "/apps/shop",
      screens: [{ service: "store", label: "store" }, { service: "admin", label: "Admin console" }] },
    { id: "single", name: "Single", home: "/apps/single",
      screens: [{ service: "web", label: "web" }] },
  ], icons);
  const shop = served.find((p) => p.id === "app:shop") as ShellPlace;
  const single = served.find((p) => p.id === "app:single") as ShellPlace;

  it("lists every screen first, in the app's order, inside the app", () => {
    expect(shop.pages.slice(0, 2).map((p) => [p.id, p.to])).toEqual([
      ["ui:store", "/apps/shop/ui/store"],
      ["ui:admin", "/apps/shop/ui/admin"],
    ]);
    expect(placeFor(served, "/apps/shop/ui/admin/orders/3").id).toBe("app:shop");
  });

  it("names a lone unlabelled screen after the app, and keeps a declared label", () => {
    expect(single.pages[0].label).toBe("Single");
    expect(shop.pages[0].label).toBe("store");
    expect(shop.pages[1].label).toBe("Admin console");
  });
});
