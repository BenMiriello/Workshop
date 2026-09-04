import { describe, expect, it } from "vitest";
import { linkIntentKey } from "./threadIntent";

describe("linkIntentKey", () => {
  // THE BUG THIS FILE EXISTS FOR. Two pushes to the same conversation are two
  // separate requests to open it, and the second one has to be visible even
  // though the URL did not change by a single character.
  it("is different for two navigations to the SAME thread", () => {
    expect(linkIntentKey("k1", "thread-b")).not.toBe(linkIntentKey("k2", "thread-b"));
  });

  it("is different when the thread changes under one navigation", () => {
    expect(linkIntentKey("k1", "thread-a")).not.toBe(linkIntentKey("k1", "thread-b"));
  });

  // Re-rendering is not navigating. A key that moved on every render would put
  // the resolver in a loop, re-listing threads and re-fetching the open one.
  it("is stable across re-renders with no navigation", () => {
    expect(linkIntentKey("k1", "thread-a")).toBe(linkIntentKey("k1", "thread-a"));
  });

  // The dock has no router location of its own. It degrades to comparing the
  // thread id, which is what every non-routed mount did before and still needs.
  it("falls back to the thread id when there is no navigation key", () => {
    expect(linkIntentKey(undefined, "thread-a")).toBe(linkIntentKey(undefined, "thread-a"));
    expect(linkIntentKey(undefined, "thread-a")).not.toBe(linkIntentKey(undefined, "thread-b"));
  });

  // A blank chat has no thread. It must not collide with a thread that exists.
  it("separates 'no thread' from a thread", () => {
    expect(linkIntentKey("k1", undefined)).not.toBe(linkIntentKey("k1", "thread-a"));
  });

  // The separator is load-bearing: without it "k1" + "2thread" and "k12" +
  // "thread" would be the same string, and a pick would be swallowed.
  it("cannot be spoofed by a key/id pair that concatenates the same way", () => {
    expect(linkIntentKey("k1", "2thread")).not.toBe(linkIntentKey("k12", "thread"));
  });
});
