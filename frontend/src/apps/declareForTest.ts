// One fictional app in the catalog, put there the way a real one arrives.
//
// WHY THIS EXISTS. Two suites assert things about "a path that belongs to an
// installed app": the assembled route table (`registry.test.tsx`) and the shell
// around it (`components/shell/Shell.test.tsx`). Both used to take that path
// from the catalog, which was populated at BUILD time by `import.meta.glob`, so
// an installation's own apps were compiled into the test run for free.
//
// They are not any more. An app declares itself over HTTP now, so under vitest
// the catalog is empty and those suites had nothing to assert on -- which is a
// test that silently stops testing, not a test that fails. This publishes a
// declaration through the SAME path the browser uses (`ensureDeclarations`
// reading `/api/base-apps/contributions`), so what the suites exercise is the
// runtime catalog rather than a hand-built stand-in.
//
// The app is fictional on purpose. A real slug here would be a platform source
// file naming an app, which `scripts/check_no_app_names.py` counts and the repo
// rules forbid by name ("not a test fixture").
import type { AppDeclaration } from "./contract";
import { refreshDeclarations } from "./declarations";

/** The fictional app's home path, which is also its only declared route. */
export const TEST_APP_HOME = "/apps/sample-widget";

const DECLARATION: AppDeclaration = {
  id: "sample-widget",
  contract: 1,
  kit: "*",
  headless: false,
  icon: "sliders",
  order: 900,
  link: { label: "Sample", to: TEST_APP_HOME },
  pages: [
    {
      path: TEST_APP_HOME,
      mount: "app.route",
      export: "Home",
      crumb: { label: "Sample", parent: "/apps" },
    },
  ],
  layouts: [],
  settings: [],
  redirects: {},
  legacy_tabs: {},
  status_endpoint: null,
  group: null,
  urls: { entry: "/base-apps/ui/sample-widget/app.js?v=abc123", styles: [], provider: null },
  error: null,
} as AppDeclaration;

/** Publish the fictional app and resolve when the catalog holds it.
 *
 *  `fetch` is replaced only for the duration of the read and restored after, so
 *  a suite that mounts components which fetch is unaffected by it.
 */
export async function declareTestApp(): Promise<void> {
  const real = globalThis.fetch;
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => ({ contract: 1, kit: "0.7.9", apps: [DECLARATION] }),
  })) as unknown as typeof fetch;
  try {
    await refreshDeclarations();
  } finally {
    globalThis.fetch = real;
  }
}
