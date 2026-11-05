import { describe, expect, it } from "vitest";
import { dropFrame, frameKey, keepFrame, type FrameRequest, type KeptFrame } from "./keptFrames";

const req = (slug: string, src = `/api/base-apps/run/${slug}/proxy/web/`): FrameRequest =>
  ({ key: frameKey(slug, "web"), slug, title: slug, src });

function open(slugs: string[], limit?: number): KeptFrame[] {
  return slugs.reduce<KeptFrame[]>((list, s, i) => keepFrame(list, req(s), i + 1, limit), []);
}

describe("keepFrame", () => {
  it("adds a new frame at the end", () => {
    expect(open(["a", "b"]).map((f) => f.slug)).toEqual(["a", "b"]);
  });

  it("never reorders when an older frame is shown again, so no iframe is moved", () => {
    const list = keepFrame(open(["a", "b", "c"]), req("a"), 10);
    expect(list.map((f) => f.slug)).toEqual(["a", "b", "c"]);
    expect(list[0].usedAt).toBe(10);
  });

  it("keeps the address when the same one is asked for again", () => {
    const list = keepFrame(open(["a"]), req("a"), 5);
    expect(list[0].src).toBe(req("a").src);
  });

  it("takes a different address when the shell asks for one", () => {
    const deep = "/api/base-apps/run/a/proxy/web/item/3";
    const list = keepFrame(open(["a"]), req("a", deep), 5);
    expect(list[0].src).toBe(deep);
  });

  it("evicts the least recently used past the limit, never the one being shown", () => {
    let list = open(["a", "b", "c", "d"]);
    list = keepFrame(list, req("a"), 10);
    list = keepFrame(list, req("e"), 11);
    expect(list.map((f) => f.slug)).toEqual(["a", "c", "d", "e"]);
  });

  it("keeps the shown frame even with a limit of one", () => {
    expect(open(["a", "b"], 1).map((f) => f.slug)).toEqual(["b"]);
  });
});

describe("dropFrame", () => {
  it("removes the frame and leaves the rest in place", () => {
    expect(dropFrame(open(["a", "b", "c"]), frameKey("b", "web")).map((f) => f.slug)).toEqual(["a", "c"]);
  });

  it("returns the same list when the frame is not kept", () => {
    const list = open(["a"]);
    expect(dropFrame(list, "zz/web")).toBe(list);
  });
});
