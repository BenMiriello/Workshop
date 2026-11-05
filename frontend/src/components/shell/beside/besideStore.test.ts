import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { beside } from "./besideStore";

const view = () => {
  const s = beside.get();
  return { place: s.place, open: s.open, scope: s.scope, thread: s.intent.threadId };
};

describe("each place keeps its own drawer", () => {
  it("does not follow you to another place, and is as you left it when you come back", () => {
    beside.setPlace("app:alpha");
    beside.open({ scope: "app:alpha", threadId: "t1" });
    beside.setThread("t1");
    beside.setPlace("home");
    expect(view()).toMatchObject({ place: "home", open: false });
    beside.setPlace("app:alpha");
    expect(view()).toEqual({ place: "app:alpha", open: true, scope: "app:alpha", thread: "t1" });
  });

  it("goes along with a link followed from inside the chat", () => {
    beside.setPlace("app:alpha");
    beside.carry("t1", "app:alpha");
    beside.setPlace("app:beta");
    expect(view()).toEqual({ place: "app:beta", open: true, scope: "app:alpha", thread: "t1" });
  });

  it("spends nothing carried when a navigation stays in the place", () => {
    beside.setPlace("settings");
    beside.carry("t9", "general");
    beside.dropCarry();
    beside.setPlace("app:gamma");
    expect(view()).toMatchObject({ place: "app:gamma", open: false });
  });

  it("is reopened on the chat a full screen page puts back", () => {
    beside.setPlace("app:alpha");
    beside.close();
    beside.setPlace("chats");
    beside.carry("t2", "development");
    beside.setPlace("app:alpha");
    expect(view()).toEqual({ place: "app:alpha", open: true, scope: "development", thread: "t2" });
  });
});

describe("a conversation filed under Development or the root roams", () => {
  const shown = () => {
    const s = beside.get();
    return { place: s.place, open: s.open, scope: s.scope, thread: s.intent.threadId, roamed: s.roamed };
  };
  const openAt = (place: string, scope: string, thread: string) => {
    beside.setPlace(place);
    beside.setPage(place, scope);
    beside.open({ scope, threadId: thread });
    beside.setThread(thread);
  };

  it("stays open on the same conversation from place to place", () => {
    openAt("app:r1", "development", "dev1");
    beside.setPlace("app:r2");
    expect(shown()).toEqual({ place: "app:r2", open: true, scope: "development", thread: "dev1", roamed: true });
    beside.setThread("dev1");
    beside.setPlace("app:r3");
    expect(shown()).toMatchObject({ open: true, scope: "development", thread: "dev1" });
  });

  it("is not kept by a place it only passed through", () => {
    beside.setPlace("app:r4");
    expect(shown()).toMatchObject({ open: true, thread: "dev1" });
    openAt("app:r5", "app:r5", "own5");
    beside.setPlace("app:r2");
    expect(shown()).toMatchObject({ open: false });
  });

  it("gives way to the new place's own conversation at the same level or higher", () => {
    openAt("app:s1", "general", "home1");
    openAt("app:s2", "development", "dev2");
    beside.setPlace("app:s3");
    beside.setPage("s3", "general");
    beside.open({ scope: "general", threadId: "home3" });
    beside.setThread("home3");
    beside.setPlace("app:s1");
    expect(shown()).toMatchObject({ scope: "general", thread: "home1", roamed: false });
    beside.setPlace("app:s2");
    expect(shown()).toMatchObject({ scope: "development", thread: "dev2" });
    beside.setPlace("app:s3");
    expect(shown()).toMatchObject({ scope: "development", thread: "dev2", roamed: true });
  });

  it("keeps a higher level conversation over a lower one's own", () => {
    openAt("app:u1", "app:u1", "own1");
    openAt("app:u2", "general", "home2");
    beside.setPlace("app:u1");
    expect(shown()).toMatchObject({ scope: "general", thread: "home2", roamed: true });
  });

  it("does not follow an app's own chat, and finds it again on the way back", () => {
    openAt("app:v1", "app:v1", "own1");
    beside.setPlace("app:v2");
    expect(shown()).toMatchObject({ open: false });
    beside.setPlace("app:v1");
    expect(shown()).toMatchObject({ open: true, scope: "app:v1", thread: "own1" });
  });

  it("shows what another place had open instead of an app's own chat leaving", () => {
    openAt("app:w1", "app:w1", "ownw1");
    openAt("app:w2", "app:w2", "ownw2");
    beside.setPlace("app:w1");
    beside.setPlace("app:w2");
    expect(shown()).toMatchObject({ open: true, thread: "ownw2" });
  });

  it("closes a roaming conversation where it was left open too", () => {
    openAt("app:x1", "development", "devx");
    beside.setPlace("app:x2");
    beside.close();
    beside.setPlace("app:x1");
    expect(shown()).toMatchObject({ open: false });
  });

  it("is the new place's own once another conversation is picked there", () => {
    openAt("app:y1", "development", "devy1");
    beside.setPlace("app:y2");
    beside.setThread("devy1");
    beside.open({ scope: "development", threadId: "devy2" });
    expect(shown().roamed).toBe(false);
    beside.setThread("devy2");
    beside.setPlace("app:y3");
    beside.setPlace("app:y2");
    expect(shown()).toMatchObject({ thread: "devy2" });
  });
});

describe("a new blank chat travels like any other conversation", () => {
  const shown = () => {
    const s = beside.get();
    return { place: s.place, open: s.open, scope: s.scope, thread: s.intent.threadId, startNew: s.intent.startNew, roamed: s.roamed };
  };

  it("stays blank when it roams, not re-opened as nothing", () => {
    beside.setPlace("app:b1");
    beside.setPage("b1", "general");
    beside.open({ scope: "development", startNew: true });
    beside.setThread(null);
    beside.setPlace("app:b2");
    expect(shown()).toEqual({
      place: "app:b2", open: true, scope: "development", thread: null, startNew: true, roamed: true,
    });
  });

  it("is blank, not the old thread, when the chat started a new one by itself", () => {
    beside.setPlace("app:b3");
    beside.setPage("b3", "general");
    beside.open({ scope: "development", threadId: "old" });
    beside.setThread("old");
    beside.setThread(null);
    beside.setPlace("app:b4");
    expect(shown()).toMatchObject({ scope: "development", thread: null, startNew: true });
  });

  it("is as left when you come back to the place it was started in", () => {
    beside.setPlace("app:b5");
    beside.setPage("b5", "app:b5");
    beside.open({ scope: "app:b5", startNew: true });
    beside.setPlace("app:b6");
    beside.setPlace("app:b5");
    expect(shown()).toMatchObject({ open: true, scope: "app:b5", thread: null, startNew: true });
  });

  it("is a thread again once the first message makes one", () => {
    beside.setPlace("app:b7");
    beside.setPage("b7", "general");
    beside.open({ scope: "development", startNew: true });
    beside.setThread("made");
    beside.setPlace("app:b8");
    expect(shown()).toMatchObject({ thread: "made", startNew: false });
  });

  it("does not turn a conversation still loading into a blank one", () => {
    beside.setPlace("app:b9");
    beside.setPage("b9", "general");
    beside.open({ scope: "development", threadId: "loading" });
    beside.setPlace("app:b10");
    expect(shown()).toMatchObject({ thread: "loading", startNew: false });
  });

  it("goes along with a link followed from inside a blank chat", () => {
    beside.setPlace("app:b11");
    beside.setPage("b11", "general");
    beside.open({ scope: "development", startNew: true });
    beside.carry(null, "development");
    beside.setPlace("app:b12");
    expect(shown()).toMatchObject({ open: true, scope: "development", thread: null, startNew: true });
  });
});

describe("a new blank chat follows you from an app's place", () => {
  const shown = () => {
    const s = beside.get();
    return { place: s.place, open: s.open, scope: s.scope, thread: s.intent.threadId, startNew: s.intent.startNew, roamed: s.roamed, nonce: s.intent.nonce };
  };

  it("stays open on the next place and is not restarted, so a draft survives", () => {
    beside.setPlace("app:n1");
    beside.setPage("n1", "app:n1");
    beside.open({ scope: "app:n1", startNew: true });
    const before = shown().nonce;
    beside.setPlace("app:n2");
    expect(shown()).toMatchObject({ place: "app:n2", open: true, scope: "app:n1", thread: null, startNew: true, roamed: true, nonce: before });
    beside.setPlace("app:n3");
    expect(shown()).toMatchObject({ open: true, scope: "app:n1", startNew: true, nonce: before });
  });

  it("is shown instead of what the next place had open, which is kept", () => {
    beside.setPlace("app:n4");
    beside.setPage("n4", "app:n4");
    beside.open({ scope: "app:n4", threadId: "own" });
    beside.setThread("own");
    beside.setPlace("app:n5");
    beside.setPage("n5", "app:n5");
    beside.open({ scope: "app:n5", startNew: true });
    beside.setPlace("app:n4");
    expect(shown()).toMatchObject({ open: true, scope: "app:n5", thread: null, startNew: true });
    beside.close();
    beside.setPlace("app:n6");
    beside.setPlace("app:n4");
    expect(shown()).toMatchObject({ open: true, scope: "app:n4", thread: "own", startNew: false });
  });

  it("belongs to its place again once the first message makes a thread", () => {
    beside.setPlace("app:n7");
    beside.setPage("n7", "app:n7");
    beside.open({ scope: "app:n7", startNew: true });
    beside.setPlace("app:n8");
    beside.setThread("made2");
    beside.setPlace("app:n9");
    expect(shown()).toMatchObject({ open: false, thread: null });
  });
});

describe("a page's attach and send", () => {
  const fakeComposer = () => {
    const calls: string[] = [];
    const sent: { files: File[]; items: unknown[] }[] = [];
    return {
      calls, sent,
      api: {
        addFiles: () => { calls.push("addFiles"); },
        setDraft: () => { calls.push("setDraft"); },
        trySend: () => { calls.push("trySend"); return true; },
        sendOnly: (files: File[], items: unknown[]) => { sent.push({ files, items }); },
        focus: () => { calls.push("focus"); },
      },
    };
  };
  const chip = { id: "c1", kind: "item" as const, source: "Photos", title: "a picture" };

  it("goes to the composer's own send and leaves its draft and attachments alone", () => {
    const c = fakeComposer();
    beside.bindComposer(c.api);
    beside.sendOnly([], [chip]);
    expect(c.sent).toEqual([{ files: [], items: [chip] }]);
    expect(c.calls).toEqual([]);
    beside.bindComposer(null);
  });

  it("waits for a composer that is not there yet, and is sent once", () => {
    const c = fakeComposer();
    beside.sendOnly([], [chip]);
    beside.bindComposer(c.api);
    beside.bindComposer(null);
    beside.bindComposer(c.api);
    expect(c.sent).toHaveLength(1);
    beside.bindComposer(null);
  });
});

describe("a reload", () => {
  const fresh = async () => {
    vi.resetModules();
    return (await import("./besideStore")).beside;
  };
  const memory = new Map<string, string>();

  beforeEach(() => {
    memory.clear();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => memory.get(k) ?? null,
      setItem: (k: string, v: string) => { memory.set(k, v); },
      removeItem: (k: string) => { memory.delete(k); },
      key: (i: number) => [...memory.keys()][i] ?? null,
      get length() { return memory.size; },
    });
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("brings back an app's own chat beside that app", async () => {
    const a = await fresh();
    a.setPlace("app:z1");
    a.setPage("Z1", "app:z1");
    a.open({ scope: "app:z1", threadId: "ownz1" });
    a.setThread("ownz1");
    const b = await fresh();
    b.setPlace("app:z1");
    expect(b.get()).toMatchObject({ open: true, scope: "app:z1", roamed: false });
    expect(b.get().intent.threadId).toBe("ownz1");
  });

  it("keeps a roaming conversation open on whatever place it comes back to", async () => {
    const a = await fresh();
    a.setPlace("app:z2");
    a.setPage("Z2", "development");
    a.open({ scope: "development", threadId: "devz" });
    a.setThread("devz");
    a.setPlace("app:z3");
    a.setThread("devz");
    const b = await fresh();
    b.setPlace("app:z3");
    expect(b.get()).toMatchObject({ open: true, scope: "development", roamed: true });
    expect(b.get().intent.threadId).toBe("devz");
  });

  it("stays closed when it was closed", async () => {
    const a = await fresh();
    a.setPlace("app:z4");
    a.open({ scope: "app:z4", threadId: "t" });
    a.close();
    const b = await fresh();
    b.setPlace("app:z4");
    expect(b.get().open).toBe(false);
  });
});

describe("the list of every chat in the drawer", () => {
  it("closes when a conversation is picked or started, the drawer closes, or the place changes", () => {
    beside.setPlace("app:alpha");
    beside.open();
    beside.showChats(true);
    expect(beside.get().chats).toBe(true);
    beside.open({ scope: "general", threadId: "t5" });
    expect(beside.get().chats).toBe(false);

    beside.showChats(true);
    beside.open({ scope: "general", startNew: true });
    expect(beside.get().chats).toBe(false);

    beside.showChats(true);
    beside.close();
    expect(beside.get().chats).toBe(false);

    beside.open();
    beside.showChats(true);
    beside.setPlace("home");
    expect(beside.get().chats).toBe(false);
  });

  it("stays while the drawer is only reopened", () => {
    beside.setPlace("app:alpha");
    beside.open();
    beside.showChats(true);
    beside.open();
    expect(beside.get().chats).toBe(true);
  });
});
