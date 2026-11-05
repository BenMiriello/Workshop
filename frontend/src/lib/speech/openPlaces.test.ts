import { describe, expect, it } from "vitest";
import type { ShellPlace } from "../../components/shell/places";
import { matchOpen, openTargets } from "./openPlaces";

const Icon = () => null;
const place = (id: string, name: string, kind: ShellPlace["kind"], prefix: string, pages: [string, string][]): ShellPlace => ({
  id, name, kind, prefixes: prefix ? [prefix] : [], defaultColumnOpen: false,
  pages: pages.map(([label, to]) => ({ id: label, label, to, Icon })),
});

const PLACES = [
  place("home", "Workshop", "home", "", [["Dashboard", "/"]]),
  place("chats", "All chats", "chats", "/chats", [["Chats", "/chats"]]),
  place("alpha", "Alpha Studio", "app", "/apps/alpha", [["Alpha Studio", "/apps/alpha"], ["Chats", "/apps/alpha/chats"]]),
  place("beta", "Beta", "app", "/apps/beta", [["Board", "/apps/beta"], ["Chats", "/apps/beta/chats"]]),
  place("settings", "Settings", "settings", "/settings", [["Appearance", "/settings/appearance"], ["Speech", "/settings/speech"]]),
];

const open = (q: string, at = "/") => matchOpen(q, openTargets(PLACES, at))?.to ?? null;

describe("what open <name> opens", () => {
  it("opens a place by its name, or by a word of it", () => {
    expect(open("Alpha Studio")).toBe("/apps/alpha");
    expect(open("the alpha app")).toBe("/apps/alpha");
    expect(open("beta")).toBe("/apps/beta");
    expect(open("settings")).toBe("/settings/appearance");
  });

  it("opens a page by its label, or with its place", () => {
    expect(open("speech")).toBe("/settings/speech");
    expect(open("speech settings")).toBe("/settings/speech");
    expect(open("beta chats")).toBe("/apps/beta/chats");
  });

  it("prefers the page of the place the operator is in", () => {
    expect(open("chats", "/apps/beta")).toBe("/apps/beta/chats");
    expect(open("chats", "/settings")).toBe("/chats");
  });

  it("forgives one letter and a word half said", () => {
    expect(open("setings")).toBe("/settings/appearance");
    expect(open("appear")).toBe("/settings/appearance");
  });

  it("matches nothing when a word fits no target", () => {
    expect(open("the door")).toBeNull();
    expect(open("")).toBeNull();
  });
});
