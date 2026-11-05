import { describe, expect, it } from "vitest";
import { holdCopyEvent, itemsText, unreported, wrapWrites } from "./clipboardWrites";

const settle = () => new Promise((r) => setTimeout(r, 0));

function fakeClipboard(fail = false) {
  const written: unknown[] = [];
  const proto = {
    writeText(text: string) {
      written.push(text);
      return fail ? Promise.reject(new Error("denied")) : Promise.resolve();
    },
    write(items: unknown) {
      written.push(items);
      return fail ? Promise.reject(new Error("denied")) : Promise.resolve();
    },
  };
  return { clip: Object.create(proto) as Clipboard, proto, written };
}

const textItem = (text: string) => ({
  types: ["text/plain"], getType: async () => new Blob([text], { type: "text/plain" }),
});

describe("wrapWrites", () => {
  it("reports a writeText that landed, and still writes it", async () => {
    const { clip, written } = fakeClipboard();
    const seen: string[] = [];
    wrapWrites(clip, (t) => seen.push(t));
    await clip.writeText("hello");
    await settle();
    expect(written).toEqual(["hello"]);
    expect(seen).toEqual(["hello"]);
  });

  it("reports the plain text of a write", async () => {
    const { clip } = fakeClipboard();
    const seen: string[] = [];
    wrapWrites(clip, (t) => seen.push(t));
    await clip.write([textItem("from an item")] as unknown as ClipboardItems);
    await settle();
    expect(seen).toEqual(["from an item"]);
  });

  it("reports nothing for a refused write and passes the refusal on", async () => {
    const { clip } = fakeClipboard(true);
    const seen: string[] = [];
    wrapWrites(clip, (t) => seen.push(t));
    await expect(clip.writeText("no")).rejects.toThrow("denied");
    await settle();
    expect(seen).toEqual([]);
  });

  it("leaves an unreported write unreported", async () => {
    const { clip } = fakeClipboard();
    const seen: string[] = [];
    wrapWrites(clip, (t) => seen.push(t));
    await unreported(() => clip.writeText("dictated"));
    await settle();
    expect(seen).toEqual([]);
  });

  it("puts the clipboard back as it was", async () => {
    const { clip, proto } = fakeClipboard();
    const seen: string[] = [];
    const unwrap = wrapWrites(clip, (t) => seen.push(t));
    unwrap();
    expect(clip.writeText).toBe(proto.writeText);
    expect(Object.getOwnPropertyNames(clip)).toEqual([]);
    await clip.writeText("after");
    await settle();
    expect(seen).toEqual([]);
  });
});

describe("held writes", () => {
  it("reports a held write without writing it", async () => {
    const { clip, written } = fakeClipboard();
    const seen: string[] = [];
    wrapWrites(clip, (t) => seen.push(t), () => true);
    await clip.writeText("kept");
    await clip.write([textItem("kept too")] as unknown as ClipboardItems);
    await settle();
    expect(written).toEqual([]);
    expect(seen).toEqual(["kept", "kept too"]);
  });

  it("decides at the time of each write", async () => {
    const { clip, written } = fakeClipboard();
    let held = true;
    wrapWrites(clip, () => {}, () => held);
    await clip.writeText("a");
    held = false;
    await clip.writeText("b");
    expect(written).toEqual(["b"]);
  });
});

describe("holdCopyEvent", () => {
  it("cancels the copy and hands over what a handler places", () => {
    const store: Record<string, string> = {};
    const data = {
      setData(type: string, value: string) { store[type] = value; },
      clearData() { delete store["text/plain"]; },
    };
    let cancelled = false;
    const e = { preventDefault: () => { cancelled = true; }, clipboardData: data } as unknown as ClipboardEvent;
    const took: string[] = [];
    holdCopyEvent(e, (t) => took.push(t));
    data.setData("text/html", "<b>x</b>");
    data.setData("text/plain", "x");
    data.clearData();
    expect(cancelled).toBe(true);
    expect(took).toEqual(["x"]);
    expect(store).toEqual({});
  });
});

describe("itemsText", () => {
  it("reads the first item carrying plain text, or nothing", async () => {
    const image = { types: ["image/png"], getType: async () => new Blob([]) };
    expect(await itemsText([image, textItem("t")])).toBe("t");
    expect(await itemsText([image])).toBeNull();
  });
});
