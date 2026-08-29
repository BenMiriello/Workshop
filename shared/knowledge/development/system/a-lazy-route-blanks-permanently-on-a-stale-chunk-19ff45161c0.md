<!-- author: self -->
**Symptom:** a page (any page) loads completely blank. Reported on /memory 2026-08-11.

**Cause, and it is not the page's fault.** Every shell page is `lazy()` in `frontend/src/App.tsx` and `frontend/src/apps/registry.tsx`, and `AppSuspense` rendered them as `<Suspense fallback={null}>` with NO error boundary. Both halves are silent:

- while the chunk loads, `null` renders, so a slow load looks like a broken page;
- if the chunk fails to load the promise rejects, nothing catches it, and the route stays blank forever with nothing on screen saying why.

Vite names chunks by content hash, so **every rebuild of the public bundle renames them**. A tab opened before a rebuild still holds the OLD index.html, so the first navigation to a page it has not already visited requests a chunk that no longer exists, gets a 404, and blanks. This deployment rebuilds after every frontend change (see the origin-sync rule in CLAUDE.md), so this is routine, and it affects ALL ~20 lazy routes, not one page.

**Fix (`frontend/src/lib/lazyPage.tsx`):**
- `lazyPage()` wraps `lazy()`. On a load failure it reloads the DOCUMENT once, guarded by a `sessionStorage` flag so a genuinely missing chunk cannot loop. Retrying the same dead URL cannot work: only a reload gets a new index.html.
- `RouteBoundary` catches what is left and renders a `StateBlock variant="error"` with a Reload button, so the failure is visible and recoverable.
- `RouteSkeleton` replaces `fallback={null}`.

**Diagnosis technique worth reusing:** `renderToString` from `react-dom/server` in a vitest file renders a page for real with no DOM harness (the pattern `apps/registry.test.tsx` already uses). It proved the Memory component was fine (3143 chars) and sent me looking at the loader instead. Caveat: React does NOT call `getDerivedStateFromError` during server rendering, so an error boundary cannot be exercised that way. Test the error view and the static separately.