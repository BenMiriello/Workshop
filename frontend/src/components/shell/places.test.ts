import { describe, expect, it } from "vitest";
import { Bot } from "lucide-react";
import { buildPlaces, landingFor, pageFor, placeFor, type ShellPlace } from "./places";
import { APP_TABS } from "../../pages/apps/tabs/appTabState";

// One glyph stands in for all of them: this module decides ROUTES, and which
// icon a row draws is the render's business.
const icons = new Proxy({} as Record<string, typeof Bot>, { get: () => Bot });

// Two apps that ship their own screens. `finance` has no linked working tree,
// so five of the seven platform tabs can never hold anything for it; `birds`
// has one and a file root, so it can have all seven. Neither declares a
// `tabs` list, which is the "take everything you can have" default.
const APPS = [
  { id: "finance", name: "Finance", home: "/apps/finance", ownScreen: true },
  {
    id: "birds", name: "Birds", home: "/apps/birds", ownScreen: true,
    projectSlug: "birds", hasFiles: true,
  },
];

const places = buildPlaces(APPS, icons);
const home = places.find((p) => p.id === "home") as ShellPlace;
const chats = places.find((p) => p.id === "chats") as ShellPlace;
const birds = places.find((p) => p.id === "app:birds") as ShellPlace;
const finance = places.find((p) => p.id === "app:finance") as ShellPlace;

describe("placeFor", () => {
  it("puts an unclaimed route on Home rather than nowhere", () => {
    expect(placeFor(places, "/").id).toBe("home");
    expect(placeFor(places, "/tickets").id).toBe("home");
    expect(placeFor(places, "/some/route/nobody/claimed").id).toBe("home");
  });

  it("keeps the Apps LIST on Home and only a specific app on its own tile", () => {
    expect(placeFor(places, "/apps").id).toBe("home");
    expect(placeFor(places, "/apps/finance").id).toBe("app:finance");
    expect(placeFor(places, "/apps/birds/locations").id).toBe("app:birds");
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
    expect(pageFor(birds, "/apps/birds")?.id).toBe("app");
    expect(pageFor(birds, "/apps/birds/locations")?.id).toBe("app");
  });

  it("keeps a row lit while you are on a record BELOW it", () => {
    // You are still in Agents when you are reading one agent. A page column
    // that goes dark on every detail page is a page column that tells you
    // nothing exactly when you are deepest.
    expect(pageFor(home, "/agents/reviewer")?.id).toBe("agents");
    expect(pageFor(home, "/projects/workshop")?.id).toBe("projects");
    // A ticket record now does the same. It did not until ADR 0023: the list
    // was at /work while a record was at /tickets/:scope/:id, so reading a
    // ticket darkened the whole column. One noun fixed that as a side effect,
    // which is the clearest evidence the split spellings were costing
    // something real rather than only reading badly.
    expect(pageFor(home, "/tickets/workshop/abc123")?.id).toBe("tickets");
    // Same for a change: reading one is still being in Changes. Without the
    // row the review page would be an orphan -- routed, reachable only by a
    // link somebody remembered to add, and dark in the column while you read
    // it.
    expect(pageFor(home, "/changes")?.id).toBe("changes");
    expect(pageFor(home, "/changes/9f2a")?.id).toBe("changes");
  });

  it("returns null where no row owns the path at all", () => {
    // Runs has no row in the column: a run belongs to the ticket that produced
    // it. Null, not a wrong row lit.
    expect(pageFor(home, "/runs/xyz")).toBeNull();
    expect(pageFor(home, "/stores")).toBeNull();
  });
});

describe("buildPlaces", () => {
  it("opens an app full screen and Home with its list", () => {
    expect(home.defaultColumnOpen).toBe(true);
    expect(chats.defaultColumnOpen).toBe(true);
    expect(birds.defaultColumnOpen).toBe(false);
  });

  it("carries an app's DECLARED icon and gives Home an imported glyph", () => {
    // An app's icon is data (a token, a url, or absent -> a monogram of the
    // name). A platform place's is an import, because Home is not an app.
    expect(birds.icon).toBeUndefined(); // this fixture declares none
    expect(birds.Icon).toBeUndefined();
    expect(home.Icon).toBeTruthy();
    expect(home.icon).toBeUndefined();
  });

  it("gives an app that can hold everything its own screen plus all seven tabs", () => {
    // Every row a real route over a real /api/apps/{id}/... collection
    // (routes/apps.py). This app has a linked tree and a file root, so nothing
    // is filtered out and the list is the platform's full order.
    const home = birds.pages[0].to;
    expect(birds.pages.map((p) => p.id)).toEqual(["app", ...APP_TABS.map((t) => t.id)]);
    expect(birds.pages.map((p) => p.to)).toEqual([
      home,
      ...APP_TABS.map((t) => `${home}${t.suffix}`),
    ]);
  });

  it("does NOT draw a tab an app can never fill", () => {
    // Ticket 7d8d733a. Finance has no linked working tree, so its tickets,
    // runs, deploys and agents can only ever be empty and it has no files at
    // all. Those rows used to be drawn anyway and explain themselves in an
    // empty state, which is a nav row that exists to apologise.
    expect(finance.pages.map((p) => p.id)).toEqual(["app", "chats", "memory"]);
    expect(finance.pages.map((p) => p.to)).toEqual([
      "/apps/finance", "/apps/finance/chats", "/apps/finance/memory",
    ]);
  });

  it("takes an app's DECLARED tabs, in its order, and no others", () => {
    // The declaration narrows and orders; it cannot conjure a tab the app
    // cannot hold (`deploys` here), because that would put the apology back.
    const [app] = buildPlaces(
      [{
        id: "ops", name: "Ops", home: "/apps/ops", projectSlug: "ops",
        tabs: ["tickets", "chats", "deploys"], hasFiles: false,
      }],
      icons,
    ).filter((p) => p.kind === "app");
    expect(app.pages.map((p) => p.id)).toEqual(["tickets", "chats", "deploys"]);
  });

  it("gives no app-screen row to an app that ships no screens", () => {
    // `/apps/<slug>` is only a redirect for such an app, so a row there would
    // point at its own tab list.
    const [app] = buildPlaces(
      [{ id: "ops", name: "Ops", home: "/apps/ops", projectSlug: "ops", hasFiles: false }],
      icons,
    ).filter((p) => p.kind === "app");
    expect(app.pages.map((p) => p.id)).not.toContain("app");
    expect(app.pages[0].id).toBe("chats");
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
    expect(landingFor(birds)).toBe("/apps/birds");
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
    expect(app.pages[0].id).toBe("chats");
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
