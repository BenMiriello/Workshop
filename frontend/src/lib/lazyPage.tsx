import { Component, lazy, type ComponentType, type ReactNode } from "react";
import { Button, Skeleton, StateBlock, Stack } from "@workshop/ui";
import { userStorage } from "./storage";

// A LAZY ROUTE THAT FAILS TO LOAD MUST NOT BE A BLANK PAGE.
//
// Every page in the shell is `lazy()`, and the shell rendered them inside
// `<Suspense fallback={null}>` with no error boundary. Both halves of that are
// silent:
//
//   - while the chunk loads, `null` renders, so a slow load IS a blank page;
//   - if the chunk fails to load, the promise rejects, nothing catches it, and
//     the route stays blank forever with nothing on screen to say why.
//
// The failure is not hypothetical or rare here. Vite names chunks by content
// hash, so every rebuild of the public bundle renames them, and this deployment
// rebuilds after every frontend change. A tab that was open before a rebuild
// still holds the OLD index.html, so the first navigation to a page it has not
// visited yet requests a chunk that no longer exists, gets a 404, and blanks.
// The operator reported exactly this on /memory after an evening of rebuilds.
//
// Two things fix it, and both are needed. A stale document cannot be repaired
// by retrying the same dead URL, so a failed load reloads the page ONCE to pick
// up the new index.html. The once is load-bearing: a genuinely missing chunk
// would otherwise reload in a loop. If it fails again after that reload, the
// boundary says so and offers the reload as a button instead of doing it.

const RELOADED_KEY = "ws-chunk-reloaded";

function isFreshLoad(): boolean {
  // Blocked storage (private mode, a cookies-off policy) can't remember that a
  // reload already happened here, so treat it as already-reloaded: showing the
  // error is recoverable, an unbounded reload loop is not.
  if (!userStorage.session.available()) return false;
  return userStorage.session.getItem(RELOADED_KEY) !== "1";
}

function markReloaded(): void {
  userStorage.session.setItem(RELOADED_KEY, "1");
}

/** Clear the one-shot reload guard. Called once the app has successfully
 *  rendered, so a stale-chunk reload weeks apart each get their own attempt. */
export function clearChunkReloadGuard(): void {
  userStorage.session.removeItem(RELOADED_KEY);
}

/** `lazy()`, but a load failure reloads the document once before giving up.
 *
 *  The retry is deliberately not a re-import of the same URL: a 404 on a
 *  content-hashed chunk means this document is stale, and only a reload gets a
 *  new one. */
export function lazyPage<T extends ComponentType<unknown>>(
  load: () => Promise<{ default: T }>,
) {
  return lazy(() =>
    load().catch((err) => {
      if (isFreshLoad()) {
        markReloaded();
        window.location.reload();
        // Never resolves: the document is going away. Resolving with a
        // placeholder here would paint it for a frame first.
        return new Promise<{ default: T }>(() => {});
      }
      throw err;
    }),
  );
}

/** What a route shows while its chunk is in flight. A page-shaped skeleton, not
 *  `null`: the previous behaviour was indistinguishable from a broken page. */
export function RouteSkeleton() {
  return (
    <Stack gap="lg" aria-busy="true" aria-label="Loading page">
      <Skeleton width="30%" style={{ height: "1.6rem" }} />
      <Skeleton width="55%" />
      <Skeleton style={{ height: "8rem" }} />
    </Stack>
  );
}

/** Whether `error` is actually the browser failing to fetch a chunk (the case
 *  this whole file exists to repair), as opposed to an ordinary bug in the
 *  page's own code. The two need different copy: telling someone to reload
 *  fixes the first and does nothing for the second, and claiming "the app was
 *  updated" for a bug that was there all along is simply false. Matches the
 *  actual strings Vite/the browser produce for a failed dynamic import,
 *  across Chromium, Firefox and Safari. */
function isChunkLoadError(error: Error): boolean {
  return /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed/i
    .test(error.message);
}

/** What a failed route shows. Split out from the boundary so it can be tested:
 *  React does not call `getDerivedStateFromError` during server rendering, so a
 *  boundary cannot be exercised by renderToString. */
export function RouteError({ error }: { error: Error }) {
  const stale = isChunkLoadError(error);
  return (
    <StateBlock
      variant="error"
      title={stale ? "This page failed to load" : "Something went wrong on this page"}
      hint={
        stale
          ? "The app was updated while this tab was open, so part of it is out of date. Reload to get the current version."
          : "Reloading may clear it. If it keeps happening, the detail is in this tab's browser console."
      }
      action={
        <Button
          variant="primary"
          onClick={() => { clearChunkReloadGuard(); window.location.reload(); }}
        >
          Reload
        </Button>
      }
    />
  );
}

/** Catches a render or chunk-load failure under a route and says what happened.
 *  A class because React offers no hook equivalent. */
export class RouteBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  // The route-level message is deliberately non-technical (see RouteError), so
  // the raw error and its component stack have to land SOMEWHERE or a bug like
  // this is undiagnosable forever except by guessing from the visible text.
  // This is that somewhere: the full detail, plus which route it was, to the
  // console every time.
  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error(
      `[route:${window.location.pathname}] ${error.name}: ${error.message}`,
      error,
      info.componentStack,
    );
  }

  render() {
    const { error } = this.state;
    return error ? <RouteError error={error} /> : this.props.children;
  }
}
