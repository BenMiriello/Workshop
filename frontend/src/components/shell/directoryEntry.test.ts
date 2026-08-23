import { describe, expect, it } from "vitest";
import { NO_DIRECTORY, enterDirectory, setDirectoryOpen } from "./directoryEntry";

describe("enterDirectory", () => {
  it("opens the tree on arrival, which is what an app place needs", () => {
    // An app opens with its page column collapsed, so there is no row to tap a
    // second time. Arrival is the trigger there, or the tree is unreachable.
    expect(enterDirectory(NO_DIRECTORY, "files")).toEqual({ pageId: "files", open: true });
  });

  it("does not re-open a tree the operator closed while they stay on the page", () => {
    const arrived = enterDirectory(NO_DIRECTORY, "files");
    const closed = setDirectoryOpen(arrived, false);
    // Every render of the page re-registers. None of them may undo the close.
    expect(enterDirectory(closed, "files")).toBe(closed);
    expect(enterDirectory(enterDirectory(closed, "files"), "files")).toBe(closed);
  });

  it("opens again after leaving the page and coming back", () => {
    const closed = setDirectoryOpen(enterDirectory(NO_DIRECTORY, "files"), false);
    const gone = enterDirectory(closed, null); // Files -> Tickets
    expect(gone.open).toBe(false);
    expect(enterDirectory(gone, "files")).toEqual({ pageId: "files", open: true });
  });

  it("opens for a different directory page even when the last one was closed", () => {
    const closed = setDirectoryOpen(enterDirectory(NO_DIRECTORY, "files"), false);
    expect(enterDirectory(closed, "memory")).toEqual({ pageId: "memory", open: true });
  });

  it("leaves an open tree open while unregistered, so its exit can animate", () => {
    // The panel stays mounted (section 3.4, correction 3) and the shell hides it
    // by comparing the page you are on against the registration. Slamming
    // `open` to false here would cut that exit.
    const open = enterDirectory(NO_DIRECTORY, "memory");
    expect(enterDirectory(open, null)).toEqual({ pageId: null, open: true });
  });

  it("is identity-stable when nothing changed, so React does not re-render", () => {
    expect(enterDirectory(NO_DIRECTORY, null)).toBe(NO_DIRECTORY);
    const open = enterDirectory(NO_DIRECTORY, "files");
    expect(enterDirectory(open, "files")).toBe(open);
  });
});

describe("setDirectoryOpen", () => {
  it("carries the page the decision belongs to", () => {
    const open = enterDirectory(NO_DIRECTORY, "files");
    expect(setDirectoryOpen(open, false)).toEqual({ pageId: "files", open: false });
  });

  it("is identity-stable on a no-op", () => {
    const open = enterDirectory(NO_DIRECTORY, "files");
    expect(setDirectoryOpen(open, true)).toBe(open);
  });
});
