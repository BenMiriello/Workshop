import { describe, expect, it } from "vitest";
import type { PageState } from "@workshop/ui";
import { mediaFault } from "./pageMediaLog";

const page = (over: Partial<PageState> = {}): PageState => ({
  id: "t1", app: "a", url: "https://a.test/", title: "", loading: false, progress: 1,
  canGoBack: false, canGoForward: false, closed: false,
  playingAudio: true, muted: false, held: false, ...over,
});

describe("mediaFault", () => {
  it("passes a page making sound while its app is on screen", () => {
    expect(mediaFault(page(), "a", true)).toBeNull();
  });

  it("catches a page heard while another app, or none, is on screen", () => {
    expect(mediaFault(page(), "b", true)).toBe("audible-off-screen");
    expect(mediaFault(page(), null, true)).toBe("audible-off-screen");
  });

  it("catches a page heard while the shell is not visible", () => {
    expect(mediaFault(page(), "a", false)).toBe("audible-off-screen");
  });

  it("leaves silent, muted and held pages alone", () => {
    expect(mediaFault(page({ playingAudio: false }), null, true)).toBeNull();
    expect(mediaFault(page({ muted: true }), null, true)).toBeNull();
    expect(mediaFault(page({ held: true }), null, true)).toBeNull();
  });

  it("does not judge a shell that does not report the hold or the app", () => {
    expect(mediaFault(page({ held: undefined }), null, true)).toBeNull();
    expect(mediaFault(page({ app: undefined }), null, true)).toBeNull();
  });

  it("records a page the shell let go, and no other close", () => {
    expect(mediaFault(page({ closed: true, closedBy: "released" }), "a", true)).toBe("released");
    expect(mediaFault(page({ closed: true }), null, true)).toBeNull();
  });
});
