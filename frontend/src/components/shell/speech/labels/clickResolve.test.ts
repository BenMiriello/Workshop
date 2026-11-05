import { describe, expect, it } from "vitest";
import { clickQuery, resolveClick, type Match, type Rank, type Resolvable } from "./clickResolve";
import type { TargetKind } from "./targetKinds";

const view = { width: 1000, height: 800 };
let n = 0;
function t(name: string, kind: TargetKind, opts: Partial<Resolvable> = {}): Resolvable {
  n += 1;
  return {
    n, name, kind, image: false, chrome: false, icon: "", text: `${name}, ${kind}`,
    box: { left: 10, top: n * 30, width: 20, height: 20 }, ...opts,
  };
}

const page = [
  t("Workshop", "link", { chrome: true }),
  t("App settings", "link", { chrome: true }),
  t("Settings. Switch place", "button", { icon: "settings", text: "Settings. Switch place, button, settings gear icon" }),
  t("Send", "button"),
  t("Sent", "link"),
  t("Delete", "button", { text: "Delete, button, trash delete bin icon, Beta notes" }),
  t("Delete", "button", { text: "Delete, button, trash delete bin icon, Alpha notes" }),
  t("Delete", "button", { text: "Delete, button, trash delete bin icon, Gamma notes" }),
  t("", "image", { image: true, text: "image" }),
  t("Open run", "button", { image: true, text: "Open run, image button" }),
  t("", "image", { image: true, text: "image" }),
];

// Scores from a table of query -> item text -> score; anything else is low.
function ranker(table: Record<string, Record<string, number>>): Rank & { calls: string[] } {
  const calls: string[] = [];
  const rank = async (query: string, items: string[]): Promise<Match> => {
    calls.push(query);
    return { scores: items.map((i) => table[query]?.[i] ?? 0.1), act: 0.35, margin: 0.07, ask: 0.31 };
  };
  return Object.assign(rank, { calls });
}
const none = ranker({});
const pressed = async (said: string, rank: Rank = none) => {
  const got = await resolveClick(page, said, rank, view);
  return got && "press" in got ? got.press.n : null;
};
const chosen = async (said: string, rank: Rank = none) => {
  const got = await resolveClick(page, said, rank, view);
  return got && "choose" in got ? got.choose.map((x) => x.n) : null;
};

describe("clickQuery", () => {
  it("drops the asking around the name", () => {
    expect(clickQuery("Can you press the gear icon, please?")).toBe("the gear icon");
    expect(clickQuery("go ahead and click on send")).toBe("send");
    expect(clickQuery("the second picture")).toBe("the second picture");
  });
});

describe("resolveClick", () => {
  it("presses by number and by exact name without asking the server", async () => {
    const rank = ranker({});
    expect(await pressed("4", rank)).toBe(4);
    expect(await pressed("send", rank)).toBe(4);
    expect(rank.calls).toEqual([]);
  });

  it("offers every control that fits a name equally", async () => {
    expect(await chosen("delete")).toEqual([6, 7, 8]);
  });

  it("narrows a name by the kind or icon said with it", async () => {
    expect(await pressed("press settings icon")).toBe(3);
  });

  it("reads ordinals over the page, not the shell", async () => {
    expect(await pressed("the second picture")).toBe(10);
    expect(await pressed("open the last image")).toBe(11);
    expect(await pressed("the first link")).toBe(5);
    expect(await pressed("delete on the third one")).toBe(8);
  });

  it("presses a clear winner by meaning", async () => {
    const rank = ranker({ "the gear": { "Settings. Switch place, button, settings gear icon": 0.6 } });
    expect(await pressed("click the gear", rank)).toBe(3);
  });

  it("offers choices when meaning is close", async () => {
    const rank = ranker({ "the trash": {
      "Delete, button, trash delete bin icon, Beta notes": 0.45,
      "Delete, button, trash delete bin icon, Alpha notes": 0.44,
    } });
    expect(await chosen("the trash", rank)).toEqual([6, 7]);
  });

  it("picks among same-named controls by their row", async () => {
    const rank = ranker({ "delete the beta one": { "Delete, button, trash delete bin icon, Beta notes": 0.6 } });
    expect(await pressed("delete the beta one", rank)).toBe(6);
  });

  it("finds nothing below the ask threshold or without a server", async () => {
    expect(await resolveClick(page, "the calendar", none, view)).toBeNull();
    const down: Rank = async () => null;
    expect(await resolveClick(page, "the calendar", down, view)).toBeNull();
  });

  it("lets a name inside a sentence win when meaning agrees", async () => {
    const rank = ranker({ "i want send": { "Send, button": 0.4, "Sent, link": 0.42 } });
    expect(await pressed("i want send", rank)).toBe(4);
  });

  it("keeps a name covering little of the sentence only when it ranks first", async () => {
    const rank = ranker({ "a new sent thing": { "Send, button": 0.5, "Sent, link": 0.4 } });
    expect(await pressed("a new sent thing", rank)).toBe(4);
  });

  it("lists every control of a kind said alone", async () => {
    expect(await chosen("click image")).toEqual([9, 10, 11]);
  });
});
