import { describe, expect, it } from "vitest";
import {
  ACTIVITY_MS, ACTIVITY_MSG, CHAT_THREAD_MSG, CHAT_TURN_MSG, OPENED_MSG, OPEN_MSG, activityDue,
  activityMessage, chatThreadMessage, chatTurnMessage, inAppTarget, isOpenedReply, openMessage,
} from "./embedSignals";

describe("what an embedded chat posts to its host", () => {
  it("names the turn state in the shell's versioned message shape", () => {
    expect(chatTurnMessage(true)).toEqual({ v: 1, type: CHAT_TURN_MSG, running: true });
    expect(chatTurnMessage(false)).toEqual({ v: 1, type: CHAT_TURN_MSG, running: false });
  });

  it("names the conversation the chat is on, so a host can address it", () => {
    expect(chatThreadMessage("t-42")).toEqual({ v: 1, type: CHAT_THREAD_MSG, threadId: "t-42" });
  });

  it("names presence the same way", () => {
    expect(activityMessage()).toEqual({ v: 1, type: ACTIVITY_MSG });
  });

  it("is namespaced like every other workshop/v1 message", () => {
    expect(CHAT_TURN_MSG.startsWith("workshop/v1/")).toBe(true);
    expect(ACTIVITY_MSG.startsWith("workshop/v1/")).toBe(true);
    expect(CHAT_THREAD_MSG.startsWith("workshop/v1/")).toBe(true);
  });

  it("reports input at most once per window", () => {
    expect(activityDue(0, ACTIVITY_MS)).toBe(true);
    expect(activityDue(1000, 1000 + ACTIVITY_MS - 1)).toBe(false);
    expect(activityDue(1000, 1000 + ACTIVITY_MS)).toBe(true);
  });
});

describe("asking the host app to open one of its own screens", () => {
  it("finds the screen and the address inside the app", () => {
    expect(inAppTarget("/apps/demo/ui/web/#/library?show=12", "demo"))
      .toEqual({ service: "web", inner: "#/library?show=12" });
    expect(inAppTarget("/apps/demo/ui/web/notes/3?x=1", "demo"))
      .toEqual({ service: "web", inner: "notes/3?x=1" });
    expect(inAppTarget("/apps/demo/ui/web", "demo")).toEqual({ service: "web", inner: "" });
    expect(inAppTarget("/apps/demo/ui/web#/x", "demo")).toEqual({ service: "web", inner: "#/x" });
  });

  it("leaves everything else to the frame's own handling", () => {
    expect(inAppTarget("/apps/other/ui/web/#/x", "demo")).toBeNull();
    expect(inAppTarget("/apps/demo/overview", "demo")).toBeNull();
    expect(inAppTarget("/apps/demo/ui/", "demo")).toBeNull();
    expect(inAppTarget("/tickets/1", "demo")).toBeNull();
    expect(inAppTarget("/apps/demo/ui/web/", "")).toBeNull();
  });

  it("is a versioned workshop/v1 message carrying the whole path too", () => {
    const t = inAppTarget("/apps/demo/ui/web/#/n/4", "demo");
    expect(t).not.toBeNull();
    expect(openMessage("o1", "/apps/demo/ui/web/#/n/4", t!)).toEqual({
      v: 1, type: OPEN_MSG, id: "o1", path: "/apps/demo/ui/web/#/n/4",
      service: "web", inner: "#/n/4",
    });
    expect(OPEN_MSG.startsWith("workshop/v1/")).toBe(true);
  });

  it("knows the host's reply to its own request only", () => {
    expect(isOpenedReply({ v: 1, type: OPENED_MSG, id: "o1" }, "o1")).toBe(true);
    expect(isOpenedReply({ v: 1, type: OPENED_MSG, id: "o2" }, "o1")).toBe(false);
    expect(isOpenedReply({ v: 2, type: OPENED_MSG, id: "o1" }, "o1")).toBe(false);
    expect(isOpenedReply("workshop/v1/opened", "o1")).toBe(false);
  });
});
