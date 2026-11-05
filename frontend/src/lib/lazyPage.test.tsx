// A route whose chunk fails must SAY so. The shell used to render lazy pages
// inside `<Suspense fallback={null}>` with nothing catching a load rejection, so
// a 404 on a content-hashed chunk (any tab open across a rebuild) left the page
// blank forever with no message.
//
// Server rendering, no DOM harness, same as apps/registry.test.tsx. React does
// NOT call getDerivedStateFromError during renderToString, so the boundary
// itself cannot be exercised that way; the two pieces it is made of can be, and
// React calling the static on a client-side throw is React's own contract.

import { describe, expect, it, vi } from "vitest";
import { renderToString } from "react-dom/server";
import { RouteBoundary, RouteError, RouteSkeleton } from "./lazyPage";

describe("RouteError", () => {
  it("says the page failed and offers a way out, for a genuine stale chunk", () => {
    const html = renderToString(
      <RouteError error={new Error("Failed to fetch dynamically imported module")} />,
    );
    expect(html).toContain("This page failed to load");
    expect(html).toContain("app was updated");
    expect(html).toContain("Reload");
  });

  // A render bug is not a stale chunk, and telling someone to reload for it
  // (implying a rebuild will fix code that has been broken all along) is
  // false. It also must not dump the raw browser exception into the page —
  // that reads as a developer error in a user-facing app. Reported 2026-08-18:
  // the Tickets page repeatedly showed the stale-chunk copy for an unrelated
  // bug, literally printing "(The operation is insecure.)" to the operator.
  it("does not claim a stale chunk, and does not print the raw error, for an ordinary bug", () => {
    const html = renderToString(
      <RouteError error={new Error("The operation is insecure.")} />,
    );
    expect(html).toContain("Something went wrong");
    expect(html).not.toContain("app was updated");
    expect(html).not.toContain("The operation is insecure");
    expect(html).toContain("Reload");
  });

  // The operator's rule (2026-10-13): never send a person to a console or a
  // terminal to find out what happened.
  it("never points the person at the console", () => {
    const html = renderToString(<RouteError error={new Error("boom")} onRetry={() => {}} />);
    expect(html.toLowerCase()).not.toContain("console");
    expect(html).toContain("Try again");
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

describe("RouteBoundary reset", () => {
  const boundary = (resetKey: string, error: Error | null) => {
    const b = new RouteBoundary({ children: null, resetKey });
    b.state = { error };
    b.setState = vi.fn();
    return b;
  };

  it("lets go of an error when the route changes", () => {
    const b = boundary("/b", new Error("boom"));
    b.componentDidUpdate({ resetKey: "/a" });
    expect(b.setState).toHaveBeenCalledWith({ error: null });
  });

  it("keeps the error while the route is the same", () => {
    const b = boundary("/a", new Error("boom"));
    b.componentDidUpdate({ resetKey: "/a" });
    expect(b.setState).not.toHaveBeenCalled();
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
