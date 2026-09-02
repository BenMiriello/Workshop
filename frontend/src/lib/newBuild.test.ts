import { describe, expect, it } from "vitest";
import { currentEntry, entryFromHtml, isStale } from "./newBuild";

const html = (entry: string) =>
  `<!doctype html><html><head><script type="module" crossorigin src="/${entry}"></script>` +
  `<link rel="stylesheet" href="/assets/index-Bq4UNd3t.css"></head><body></body></html>`;

describe("entryFromHtml", () => {
  it("finds the content-hashed entry bundle", () => {
    expect(entryFromHtml(html("assets/index-q2mU0Iyp.js"))).toBe("assets/index-q2mU0Iyp.js");
  });

  it("is null when the html names no entry", () => {
    // What an error page, a proxy interstitial or a captive portal looks like.
    // Not a new build, so the caller must not treat it as one.
    expect(entryFromHtml("<html><body>502 Bad Gateway</body></html>")).toBeNull();
    expect(entryFromHtml("")).toBeNull();
  });

  it("does not mistake a chunk for the entry", () => {
    expect(entryFromHtml('<script src="/assets/ActivityCard-B-7weP1a.js"></script>')).toBeNull();
  });
});

describe("currentEntry", () => {
  const doc = (srcs: string[]) => ({
    querySelectorAll: () => srcs.map((src) => ({ getAttribute: () => src })),
  }) as unknown as Document;

  it("reads the entry off the document's own scripts", () => {
    expect(currentEntry(doc(["/assets/index-q2mU0Iyp.js"]))).toBe("assets/index-q2mU0Iyp.js");
  });

  it("skips scripts that are not the entry", () => {
    expect(currentEntry(doc(["/vite/client", "/assets/index-AAAA1111.js"])))
      .toBe("assets/index-AAAA1111.js");
  });

  it("is null when nothing on the page names one", () => {
    expect(currentEntry(doc([]))).toBeNull();
    expect(currentEntry(doc(["/vite/client"]))).toBeNull();
  });
});

describe("isStale", () => {
  it("is true only when both are known and they differ", () => {
    expect(isStale("assets/index-AAAA1111.js", "assets/index-BBBB2222.js")).toBe(true);
  });

  it("is false when they match", () => {
    expect(isStale("assets/index-AAAA1111.js", "assets/index-AAAA1111.js")).toBe(false);
  });

  it("is false when either side is unknown, which is what stops a reload loop", () => {
    // If a failed poll yielded null and this returned true, the tab would
    // reload, fail again, and reload forever.
    expect(isStale(null, "assets/index-BBBB2222.js")).toBe(false);
    expect(isStale("assets/index-AAAA1111.js", null)).toBe(false);
    expect(isStale(null, null)).toBe(false);
  });
});
