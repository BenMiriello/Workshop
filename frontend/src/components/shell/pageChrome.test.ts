import { describe, expect, it } from "vitest";
import { directoryChrome } from "./pageChrome";

describe("directoryChrome", () => {
  it("keeps the whole desktop header, and never pins a bottom bar there", () => {
    expect(directoryChrome({ wide: true, documentOpen: false })).toEqual({
      header: true,
      controls: true,
      actionBar: false,
    });
  });

  it("draws nothing above an open document on a desktop either", () => {
    // 2026-08-27 (D4). This used to assert the opposite, on the theory that the
    // desktop tree lives in the page column so an open file is not in the
    // header's way. It is the other way round: the header was in the FILE's
    // way, and the operator read the page's search-and-new row sitting over a
    // note he had opened as the page not knowing what it was showing.
    expect(directoryChrome({ wide: true, documentOpen: true })).toEqual({
      header: false,
      controls: false,
      actionBar: false,
    });
  });

  it("gives a document the same answer on both viewports", () => {
    expect(directoryChrome({ wide: true, documentOpen: true }))
      .toEqual(directoryChrome({ wide: false, documentOpen: true }));
  });

  it("draws no header and no controls above the tree on a phone", () => {
    // The defect this rule exists for: bar one already says the place and the
    // selected tab already says the page.
    const chrome = directoryChrome({ wide: false, documentOpen: false });
    expect(chrome.header).toBe(false);
    expect(chrome.controls).toBe(false);
  });

  it("moves the primary action to the bottom bar on a phone", () => {
    expect(directoryChrome({ wide: false, documentOpen: false }).actionBar).toBe(true);
  });

  it("hides the bottom bar too once a document owns the phone screen", () => {
    // shell-mockup-spec-2026-08-22.md section 6.
    expect(directoryChrome({ wide: false, documentOpen: true })).toEqual({
      header: false,
      controls: false,
      actionBar: false,
    });
  });

  it("never draws a bottom bar and a page header at once", () => {
    for (const wide of [true, false]) {
      for (const documentOpen of [true, false]) {
        const chrome = directoryChrome({ wide, documentOpen });
        expect(chrome.header && chrome.actionBar).toBe(false);
      }
    }
  });
});
