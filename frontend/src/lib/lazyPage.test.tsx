// A route whose chunk fails must SAY so. The shell used to render lazy pages
// inside `<Suspense fallback={null}>` with nothing catching a load rejection, so
// a 404 on a content-hashed chunk (any tab open across a rebuild) left the page
// blank forever with no message.
//
// Server rendering, no DOM harness, same as apps/registry.test.tsx. React does
// NOT call getDerivedStateFromError during renderToString, so the boundary
// itself cannot be exercised that way; the two pieces it is made of can be, and
// React calling the static on a client-side throw is React's own contract.

import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { RouteBoundary, RouteError, RouteSkeleton } from "./lazyPage";

describe("RouteError", () => {
  it("says the page failed and offers a way out", () => {
    const html = renderToString(
      <RouteError error={new Error("Failed to fetch dynamically imported module")} />,
    );
    expect(html).toContain("This page failed to load");
    expect(html).toContain("Reload");
    // The real cause is shown rather than swallowed: a stale-chunk failure and
    // a genuine render bug must be distinguishable without a devtools session.
    expect(html).toContain("dynamically imported module");
  });
});

describe("RouteBoundary", () => {
  it("moves to the error state when React hands it a failure", () => {
    expect(RouteBoundary.getDerivedStateFromError(new Error("boom")))
      .toEqual({ error: expect.objectContaining({ message: "boom" }) });
  });

  it("renders its children untouched when nothing throws", () => {
    const html = renderToString(<RouteBoundary><p>fine</p></RouteBoundary>);
    expect(html).toContain("fine");
    expect(html).not.toContain("This page failed to load");
  });
});

describe("RouteSkeleton", () => {
  it("renders something while a chunk is in flight", () => {
    // `fallback={null}` was indistinguishable from a broken page.
    const html = renderToString(<RouteSkeleton />);
    expect(html).toContain("aria-busy");
    expect(html.length).toBeGreaterThan(0);
  });
});
