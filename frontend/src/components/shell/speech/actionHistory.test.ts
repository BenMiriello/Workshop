import { describe, expect, it } from "vitest";
import { MIC_RATE } from "../../../lib/voice/capture";
import { ActionHistory, type ActionSpec, type Blocked } from "./actionHistory";

const ok = (text: string) => Promise.resolve({ ok: true, text });

function spec(label: string, extra: Partial<ActionSpec> & { log?: string[] } = {}): ActionSpec {
  const log = extra.log ?? [];
  return {
    label,
    undo: () => { log.push(`undo ${label}`); return ok("undone"); },
    redo: () => { log.push(`redo ${label}`); return ok("redone"); },
    ...extra,
  };
}

describe("the action history", () => {
  it("undoes and redoes an action, and greys it while undone", async () => {
    const h = new ActionHistory();
    const log: string[] = [];
    const e = h.record(spec("Model: Sonnet", { log }), null);
    expect(h.newestUndo()?.id).toBe(e.id);
    expect((await h.undo(e.id)).ok).toBe(true);
    expect(h.entries[0].done).toBe(false);
    expect(h.newestUndo()).toBeNull();
    expect(h.newestRedo()?.id).toBe(e.id);
    await h.redo(e.id);
    expect(h.entries[0].done).toBe(true);
    expect(log).toEqual(["undo Model: Sonnet", "redo Model: Sonnet"]);
  });

  it("will not undo twice", async () => {
    const h = new ActionHistory();
    const e = h.record(spec("New chat"), null);
    await h.undo(e.id);
    expect((await h.undo(e.id)).ok).toBe(false);
  });

  it("takes later sends to the same chat back with an earlier one, and redoes them oldest first", async () => {
    const h = new ActionHistory();
    const first = h.record(spec("Sent 1", { chain: "chat:a" }), null, 1);
    const other = h.record(spec("Sent elsewhere", { chain: "chat:b" }), null, 2);
    const second = h.record(spec("Sent 2", { chain: "chat:a" }), null, 3);
    await h.undo(first.id);
    const byId = (id: number) => h.entries.find((e) => e.id === id)!;
    expect(byId(second.id).done).toBe(false);
    expect(byId(other.id).done).toBe(true);
    expect(byId(second.id).blocked.redo).toBe("Redo the earlier one first");
    expect((await h.redo(second.id)).ok).toBe(false);
    await h.redo(first.id);
    expect(byId(second.id).blocked.redo).toBeNull();
    expect((await h.redo(second.id)).ok).toBe(true);
  });

  it("asks the action whether it can run, and says why not", async () => {
    const h = new ActionHistory();
    let why: Blocked = { undo: "The reply is still running" };
    const e = h.record(spec("Sent", { check: () => Promise.resolve(why) }), null);
    const out = await h.undo(e.id);
    expect(out).toEqual({ ok: false, text: "The reply is still running" });
    expect(h.entries[0].blocked.undo).toBe("The reply is still running");
    why = {};
    expect((await h.undo(e.id)).ok).toBe(true);
  });

  it("a spoken cancel undoes the action said just before it", () => {
    const h = new ActionHistory();
    const s = (sec: number) => sec * MIC_RATE;
    const a = h.record(spec("Sent"), { to: s(10), heard: 0 }, 0);
    const b = h.record(spec("New chat"), { to: s(20), heard: 0 }, 0);
    expect(h.answer(s(12))?.id).toBe(a.id);
    expect(h.answer(s(21))?.id).toBe(b.id);
    expect(h.answer(s(30))).toBeNull();
  });

  it("gives a spoken cancel its five seconds from when the action applied", () => {
    const h = new ActionHistory();
    const s = (sec: number) => sec * MIC_RATE;
    const slow = h.record(spec("Model: Sonnet"), { to: s(10), heard: 1000 }, 3000);
    expect(h.answer(s(16.5))?.id).toBe(slow.id);
    expect(h.answer(s(17.5))).toBeNull();
  });

  it("keeps a failed undo standing", async () => {
    const h = new ActionHistory();
    const e = h.record(spec("Sent", { undo: () => Promise.resolve({ ok: false, text: "No" }) }), null);
    expect((await h.undo(e.id)).text).toBe("No");
    expect(h.entries[0].done).toBe(true);
  });
});
