import { describe, expect, it } from "vitest";
import { ATTACH_MSG, PAGE_MSG, RESULT_MSG, SHOW_CHAT_MSG, linkMessage, parseAttach, parsePage, parseResult, parseShowChat } from "./protocol";

describe("page protocol", () => {
  it("reads a page declaration and drops bad tools", () => {
    const p = parsePage({ v: 1, type: PAGE_MSG, context: { route: "/x" },
      tools: [{ name: "show_item", acts: true }, { name: "bad name" }, { name: "shot", acts: false }] });
    expect(p?.context).toBe('{"route":"/x"}');
    expect(p?.tools.map((t) => [t.name, t.acts])).toEqual([["show_item", true], ["shot", false]]);
    expect(parsePage({ v: 2, type: PAGE_MSG })).toBeNull();
  });

  it("reads an attach request; send needs a prompt", () => {
    const a = parseAttach({ v: 1, type: ATTACH_MSG, items: [{ kind: "image", src: "/t.png" }, { kind: "nope" }],
      send: true, newChat: true, scope: "app:demo", apps: ["demo", 3] });
    expect(a?.items).toHaveLength(1);
    expect(a?.send).toBe(false);
    expect(a?.apps).toEqual(["demo"]);
    expect(parseAttach({ v: 1, type: ATTACH_MSG, items: [] })).toBeNull();
    expect(parseAttach({ v: 1, type: ATTACH_MSG, prompt: "hi", send: true })?.send).toBe(true);
  });

  it("reads a send-only attach, which needs items and no prompt", () => {
    const items = [{ kind: "image", src: "/t.png" }];
    expect(parseAttach({ v: 1, type: ATTACH_MSG, items, sendOnly: true })?.sendOnly).toBe(true);
    expect(parseAttach({ v: 1, type: ATTACH_MSG, items })?.sendOnly).toBe(false);
    expect(parseAttach({ v: 1, type: ATTACH_MSG, prompt: "hi", sendOnly: true })?.sendOnly).toBe(false);
  });

  it("reads results and builds link messages", () => {
    expect(parseResult({ v: 1, type: RESULT_MSG, id: "c1", ok: true, result: "x" })).toEqual(
      { id: "c1", ok: true, result: "x", error: undefined });
    expect(parseResult({ v: 1, type: RESULT_MSG, ok: true })).toBeNull();
    expect(linkMessage(true, "use", false, "t1")).toEqual({ v: 1, type: "workshop/v1/pageLink", open: true, level: "use", driving: false, thread: "t1", follow: true, view: "follow" });
    expect(linkMessage(true, "use", false, "t1", "show")).toMatchObject({ follow: false, view: "show" });
    expect(linkMessage(true, "use", false, "t1", "hide")).toMatchObject({ follow: false, view: "hide" });
  });

  it("reads which conversation a page asks to show", () => {
    expect(SHOW_CHAT_MSG).toBe("workshop/v1/showChat");
    expect(parseShowChat({ v: 1, type: SHOW_CHAT_MSG, thread: "a1336262e393" })).toBe("a1336262e393");
    expect(parseShowChat({ v: 1, type: SHOW_CHAT_MSG, thread: "../x" })).toBeNull();
    expect(parseShowChat({ v: 1, type: SHOW_CHAT_MSG })).toBeNull();
    expect(parseShowChat({ v: 1, type: PAGE_MSG, thread: "t1" })).toBeNull();
  });
});
