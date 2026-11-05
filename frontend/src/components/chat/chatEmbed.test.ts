import { describe, expect, it } from "vitest";
import {
  embedChatPath, embedChatsPath, embedListPath, hostChatPath, hostChatsBase, isEmbedPath, pathLeavingFrame,
} from "./chatEmbed";

const HERE = "https://ws.example/embed/chat/app%3Ademo?thread=t1";

describe("embedChatPath", () => {
  it("encodes the scope and carries the thread", () => {
    expect(embedChatPath("app:demo")).toBe("/embed/chat/app%3Ademo");
    expect(embedChatPath("app:demo", "t 2")).toBe("/embed/chat/app%3Ademo?thread=t%202");
  });

  it("is an embed path", () => {
    expect(isEmbedPath(embedChatPath("general"))).toBe(true);
    expect(isEmbedPath("/chat/general")).toBe(false);
  });
});

describe("pathLeavingFrame", () => {
  it("sends another Workshop page out of the frame", () => {
    expect(pathLeavingFrame("/tickets/s/1", HERE, null)).toBe("/tickets/s/1");
    expect(pathLeavingFrame("https://ws.example/runs/9#log", HERE, "_self")).toBe("/runs/9#log");
  });

  it("keeps an embed address in the frame", () => {
    expect(pathLeavingFrame("/embed/chat/general?thread=x", HERE, null)).toBeNull();
  });

  it("leaves another origin and an explicit target to the browser", () => {
    expect(pathLeavingFrame("https://other.example/x", HERE, null)).toBeNull();
    expect(pathLeavingFrame("/tickets", HERE, "_blank")).toBeNull();
  });

  it("keeps a link within this page in the frame", () => {
    expect(pathLeavingFrame("#msg-4", HERE, null)).toBeNull();
  });
});

describe("embedListPath", () => {
  it("lists the one scope, never the app's whole collection", () => {
    expect(embedListPath("app:demo")).toBe("/chats?scope=app%3Ademo");
  });
});

describe("embedChatsPath", () => {
  it("is the framed list of one scope", () => {
    expect(embedChatsPath("app:demo")).toBe("/embed/chats/app%3Ademo");
    expect(isEmbedPath(embedChatsPath("app:demo"))).toBe(true);
  });
});

describe("hostChatsBase", () => {
  const base = "/apps/demo/ui/web/#/chat";
  it("takes the framing app's own screen", () => {
    expect(hostChatsBase(base, "demo")).toBe(base);
    expect(hostChatsBase(`${base}/`, "demo")).toBe(base);
  });

  it("ignores another app, another page, another origin and no app", () => {
    expect(hostChatsBase("/apps/other/ui/web/#/chat", "demo")).toBeNull();
    expect(hostChatsBase("/chats", "demo")).toBeNull();
    expect(hostChatsBase("//evil.example/apps/demo/ui/web/", "demo")).toBeNull();
    expect(hostChatsBase("https://evil.example/apps/demo/ui/web/", "demo")).toBeNull();
    expect(hostChatsBase(base, null)).toBeNull();
    expect(hostChatsBase(null, "demo")).toBeNull();
  });
});

describe("hostChatPath", () => {
  it("addresses one conversation, or a new one", () => {
    expect(hostChatPath("/apps/demo/ui/web/#/chat", "t 1")).toBe("/apps/demo/ui/web/#/chat/t%201");
    expect(hostChatPath("/apps/demo/ui/web/#/chat")).toBe("/apps/demo/ui/web/#/chat/new");
  });
});
