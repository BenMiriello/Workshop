import { afterEach, describe, expect, it, vi } from "vitest";

import { PAGE_LINK_MESSAGES, attachToChat, startPageLink } from "./pageLink";

type Listener = (e: MessageEvent) => void;

function frame() {
  const posted: Record<string, unknown>[] = [];
  const listeners: Listener[] = [];
  const parent = { postMessage: (m: Record<string, unknown>) => posted.push(m) };
  vi.stubGlobal("location", { origin: "https://ws.test" });
  vi.stubGlobal("window", {
    parent,
    addEventListener: (_: string, l: Listener) => listeners.push(l),
    removeEventListener: (_: string, l: Listener) => listeners.splice(listeners.indexOf(l), 1),
  });
  const fromShell = (data: object) =>
    listeners.forEach((l) => l({ origin: "https://ws.test", source: parent, data: { v: 1, ...data } } as unknown as MessageEvent));
  return { posted, listeners, fromShell };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("startPageLink", () => {
  it("posts the page once after a burst of changes", () => {
    vi.useFakeTimers();
    const f = frame();
    const link = startPageLink({ context: () => ({ route: "/a" }), tools: [] });
    link.changed();
    link.changed();
    vi.advanceTimersByTime(300);
    const pages = f.posted.filter((m) => m.type === PAGE_LINK_MESSAGES.page);
    expect(pages).toEqual([{ v: 1, type: PAGE_LINK_MESSAGES.page, context: { route: "/a" }, tools: [] }]);
  });

  it("answers a call to a declared tool with its result and the access it was given", async () => {
    const f = frame();
    const tool = { name: "show", description: "", input_schema: {}, acts: true };
    const seen: unknown[] = [];
    startPageLink({ context: () => ({}), tools: [tool], run: (c) => { seen.push(c.access); return "shown"; } });
    f.fromShell({ type: PAGE_LINK_MESSAGES.call, id: "c1", tool: "show", args: {}, access: ["history"] });
    await flush();
    expect(seen).toEqual([["history"]]);
    expect(f.posted).toContainEqual({ v: 1, type: PAGE_LINK_MESSAGES.result, id: "c1", ok: true, result: "shown" });
  });

  it("refuses an undeclared tool and reports a throw as the error", async () => {
    const f = frame();
    const tool = { name: "boom", description: "", input_schema: {}, acts: false };
    startPageLink({ context: () => ({}), tools: [tool], run: () => { throw new Error("no item 7"); } });
    f.fromShell({ type: PAGE_LINK_MESSAGES.call, id: "a", tool: "missing", args: {} });
    f.fromShell({ type: PAGE_LINK_MESSAGES.call, id: "b", tool: "boom", args: {} });
    await flush();
    const results = f.posted.filter((m) => m.type === PAGE_LINK_MESSAGES.result);
    expect(results.map((r) => [r.id, r.ok, r.error])).toEqual([
      ["a", false, "This page has no tool named missing."],
      ["b", false, "no item 7"],
    ]);
  });

  it("re-posts the page when a drawer opens, and reports the link", () => {
    vi.useFakeTimers();
    const f = frame();
    const links: unknown[] = [];
    startPageLink({ context: () => "ctx", onLink: (l) => links.push(l) });
    vi.advanceTimersByTime(300);
    f.fromShell({ type: PAGE_LINK_MESSAGES.link, open: true, level: "use", driving: false });
    expect(links).toEqual([{ open: true, level: "use", driving: false, thread: null }]);
    expect(f.posted.filter((m) => m.type === PAGE_LINK_MESSAGES.page)).toHaveLength(2);
  });

  it("ignores messages from anyone but the framing window", async () => {
    const f = frame();
    const run = vi.fn(() => "x");
    startPageLink({ context: () => ({}), tools: [{ name: "t", description: "", input_schema: {}, acts: false }], run });
    f.listeners.forEach((l) => l({ origin: "https://evil.test", source: window.parent,
      data: { v: 1, type: PAGE_LINK_MESSAGES.call, id: "x", tool: "t", args: {} } } as unknown as MessageEvent));
    await flush();
    expect(run).not.toHaveBeenCalled();
  });
});

describe("attachToChat", () => {
  it("is false outside a Workshop frame", () => {
    vi.stubGlobal("window", {});
    (window as unknown as { parent: unknown }).parent = window;
    expect(attachToChat({ items: [] })).toBe(false);
  });
});
