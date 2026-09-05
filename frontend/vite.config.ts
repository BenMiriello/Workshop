import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";
import { importmapPlugin, sharedInputs } from "./plugins/importmap";

// iOS Safari refuses navigator.geolocation with "Origin does not have permission
// to use Geolocation service" unless the document/origin has been granted the
// `geolocation` Permissions-Policy. The default policy in WebKit treats
// geolocation as gated. Setting `geolocation=(self)` explicitly grants this
// origin (and same-origin iframes only). Index.html also carries a meta-tag
// fallback for any path where this header is stripped by an upstream proxy.
const permissionsPolicyMiddleware = (): PluginOption => ({
  name: "permissions-policy-geolocation",
  configureServer(server) {
    server.middlewares.use((_req, res, next) => {
      res.setHeader("Permissions-Policy", "geolocation=(self)");
      next();
    });
  },
  configurePreviewServer(server) {
    server.middlewares.use((_req, res, next) => {
      res.setHeader("Permissions-Policy", "geolocation=(self)");
      next();
    });
  },
});

// "Nuke from orbit" endpoint. When iOS Safari has a cached old bundle whose
// JS reads the wrong field (e.g. AgentConfig reading `metadata_` instead of
// `metadata`) and won't pick up the new bundle even on pull-to-refresh,
// visiting `/clear` returns Clear-Site-Data which wipes cache + storage for
// this origin. Same pattern as apps/commute/vite.config.ts.
const CLEAR_HTML = `<!doctype html><html><head><meta charset="utf-8"><title>Cleared</title>
<meta http-equiv="refresh" content="2; url=/">
<style>body{font-family:system-ui;background:#0b0f17;color:#e7eaee;padding:2rem;text-align:center}</style>
</head><body>
<h1>Site data cleared.</h1>
<p>Reloading <code>/</code> in 2 seconds...</p>
<p><a href="/" style="color:#7cf">Reload now</a></p>
<script>
(async()=>{try{const regs=await navigator.serviceWorker.getRegistrations();await Promise.all(regs.map(r=>r.unregister()));const keys=await caches.keys();await Promise.all(keys.map(k=>caches.delete(k)));}catch(e){}})();
</script>
</body></html>`;

// Service-worker kill-switch (same pattern as commute app). Catches any
// residual SW from earlier builds and unregisters it on the next page load.
const KILL_SWITCH_SW = `// Service-worker kill-switch — unregister + drop caches.
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try { const keys = await caches.keys(); await Promise.all(keys.map(k => caches.delete(k))); } catch (e) {}
    try { await self.registration.unregister(); } catch (e) {}
    const list = await self.clients.matchAll({ type: 'window' });
    for (const c of list) { try { c.navigate(c.url); } catch (e) {} }
  })());
});
self.addEventListener('fetch', (event) => {});
`;

const serviceWorkerKillSwitchMiddleware = (): PluginOption => ({
  name: "service-worker-kill-switch",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if (req.url === "/sw.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Service-Worker-Allowed", "/");
        res.end(KILL_SWITCH_SW);
        return;
      }
      next();
    });
  },
  configurePreviewServer(server) {
    server.middlewares.use((req, res, next) => {
      if (req.url === "/sw.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Service-Worker-Allowed", "/");
        res.end(KILL_SWITCH_SW);
        return;
      }
      next();
    });
  },
});

// Per-deployment origin config. Empty by default so a fresh clone runs locally
// without bouncing to some other instance's hostname.
//   WORKSHOP_CANONICAL_ORIGIN — browser-facing HTTPS origin (e.g. a Tailscale
//     Serve URL like https://<host>.ts.net:8443) the dev server redirects to.
//   WORKSHOP_TAILNET_HOST — the magicdns host that is already canonical, so
//     requests arriving there (or on loopback) are not redirected.
const CANONICAL_ORIGIN = (process.env.WORKSHOP_CANONICAL_ORIGIN || "").replace(/\/$/, "");
const TAILNET_HOST = process.env.WORKSHOP_TAILNET_HOST || "";

// HTTP-IP → HTTPS-magicdns redirect. iOS Safari refuses geolocation on an
// insecure origin (raw Tailscale IP over HTTP), so bounce stale bookmarks to
// the canonical HTTPS origin. No-op when no canonical origin is configured.
const httpsRedirectMiddleware = (): PluginOption => ({
  name: "http-ip-to-https-magicdns-redirect",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const host = (req.headers["host"] || "") as string;
      const isLocal = host.startsWith("localhost") || host.startsWith("127.0.0.1");
      const isTailnet = TAILNET_HOST !== "" && host.includes(TAILNET_HOST);
      if (!CANONICAL_ORIGIN || isLocal || isTailnet) {
        return next();
      }
      res.statusCode = 302;
      res.setHeader("Location", CANONICAL_ORIGIN + (req.url ?? "/"));
      res.end();
    });
  },
});

// ---- The optional diagram library ------------------------------------------
//
// `mermaid` is an OPTIONAL dependency (frontend/package.json): it is ~84 MB
// installed and ~3.4 MB of lazily-fetched chunks in a build, and an installation
// that never draws a diagram should not carry either. It installs by default and
// leaving it out is `npm install --omit=optional` — with one npm-and-node-18
// trap written up beside the declaration in frontend/package.json.
//
// The catch is that opting out is a BUILD-time failure, not a runtime one:
// `MermaidDiagram.tsx` reaches the library only through `import("mermaid")`, and
// rollup cannot resolve that specifier when the package is not on disk, so the
// build dies before any fallback could run. So the specifier is pointed at the
// kit's stand-in module instead, which rejects with `MermaidUnavailableError`
// and lands in the catch the component already has: the fence renders as a plain
// code block, which is what it was before diagrams existed.
//
// Rejected alternatives, both of which leave a broken build or a broken page:
//   • `rollupOptions.external` — keeps `import("mermaid")` as a bare specifier in
//     the OUTPUT, which no browser can resolve. The page then fails with a
//     module-resolution TypeError indistinguishable from a chunk that failed to
//     download, and the build-time warning about it is only advisory.
//   • a `define` flag read in the component — the unresolvable import is still in
//     the module graph, so the build still fails; dead-code elimination behind a
//     constant is not something to bet a distro's build on.
//
// The probe is a filesystem walk rather than `require.resolve`, which answers a
// slightly different question: it applies an exports map and a set of conditions,
// so an ESM-only package (mermaid is one) can fail to resolve under `require`
// even though it is installed, and the build would then silently drop diagrams
// for an installation that asked for them. "Is the directory there" is the
// question actually being asked, and npm hoisting is why it is a walk.
const MERMAID_FALLBACK = path.resolve(__dirname, "../packages/ui/src/mermaidOptional.ts");

function isInstalled(pkg: string, from: string): boolean {
  let dir = from;
  for (;;) {
    if (fs.existsSync(path.join(dir, "node_modules", pkg, "package.json"))) return true;
    const parent = path.dirname(dir);
    if (parent === dir) return false;
    dir = parent;
  }
}

const optionalAliases = () =>
  isInstalled("mermaid", __dirname)
    ? []
    : [{ find: /^mermaid$/, replacement: MERMAID_FALLBACK }];

const clearSiteDataMiddleware = (): PluginOption => ({
  name: "clear-site-data-endpoint",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const url = req.url ?? "";
      if (url === "/clear" || url === "/clear/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Clear-Site-Data", '"cache", "storage", "executionContexts"');
        res.end(CLEAR_HTML);
        return;
      }
      next();
    });
  },
  configurePreviewServer(server) {
    server.middlewares.use((req, res, next) => {
      const url = req.url ?? "";
      if (url === "/clear" || url === "/clear/") {
        res.setHeader("Content-Type", "text/html; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Clear-Site-Data", '"cache", "storage", "executionContexts"');
        res.end(CLEAR_HTML);
        return;
      }
      next();
    });
  },
});

export default defineConfig({
  plugins: [
    clearSiteDataMiddleware(),
    permissionsPolicyMiddleware(),
    serviceWorkerKillSwitchMiddleware(),
    httpsRedirectMiddleware(),
    react(),
    // The one <script type="importmap"> that lets an app bundle built
    // elsewhere resolve `react` (and the rest of SHARED) to the instances THIS
    // bundle already carries. See plugins/importmap.ts.
    importmapPlugin(),
  ],
  build: {
    rollupOptions: {
      // The html entry plus one tiny re-export entry per shared specifier.
      // Every one of them is a module `src/main.tsx` also reaches, so rollup
      // hoists the real dependency into a chunk both import: one instance,
      // with a stable public URL. See plugins/importmap.ts.
      input: {
        index: path.resolve(__dirname, "index.html"),
        ...sharedInputs(),
      },
    },
  },
  // A nested copy of any of these under node_modules must not be able to win a
  // resolution: two Reacts in one bundle is the same failure as two Reacts in
  // one document, arriving from the other direction.
  optimizeDeps: {
    // Pre-bundle the shared set DETERMINISTICALLY rather than letting the dev
    // server discover it mid-session, which is what makes the `?v=` token move.
    // Deliberately NOT `exclude`: react@18 as installed here is CommonJS only
    // (no "module" field, no ESM condition in its exports map), and excluding
    // it from pre-bundling does not produce one React, it produces a dev server
    // that cannot serve React at all.
    include: [
      "react",
      "react-dom",
      "react-dom/client",
      "react/jsx-runtime",
      "react-router-dom",
      "@tanstack/react-query",
      "lucide-react",
      "recharts",
    ],
  },
  resolve: {
    dedupe: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
    // The kit resolves as a real package: node_modules/@workshop/ui is a
    // workspace link to packages/ui, and its exports map decides which subpaths
    // exist. There is deliberately NO alias for it — an alias resolves BEFORE
    // the exports map, which is what let app code reach the kit's internals
    // under the old `"@workshop/ui": <dir>` prefix mapping.
    //
    // `workshop-source` selects the TypeScript sources over dist/, so the app is
    // never built against a stale packages/ui/dist. Keep this list NARROW: it is
    // global, applying to every dependency. Adding "browser" alongside it made
    // recharts resolve a DOM-only build that throws at import time under
    // vitest's node environment. If you add a condition, re-run the tests.
    conditions: ["workshop-source"],
    alias: [
      { find: /^@shared\//, replacement: path.resolve(__dirname, "../shared") + "/" },
      { find: /^@shared$/, replacement: path.resolve(__dirname, "../shared") },
      // `@shell/...` is what an app under `apps/<slug>/ui/` imports when it needs
      // something the shell owns and the kit does not export yet: the AppModule
      // type, `lazyPage`, the generated API schema. An extracted app sits outside
      // `frontend/`, so a relative `../../../lib/...` would not resolve and would
      // silently re-couple the app to this repo's directory layout.
      //
      // This alias is a NAMED seam, not a permanent interface. Every specifier
      // reached through it is a candidate for promotion into `@workshop/ui`
      // (stage-4b-kit-promotions-2026-08-24.md); when the last one is promoted
      // this alias goes away. Naming it is what makes that list countable — the
      // relative imports it replaces were not.
      { find: /^@shell\//, replacement: path.resolve(__dirname, "src") + "/" },
      // Empty unless an optional dependency is missing; see above.
      ...optionalAliases(),
    ],
  },
  // VITEST. The `include` is widened past this package on purpose. An extracted
  // app's frontend lives in `apps/<slug>/ui/`, outside this workspace, and when
  // the apps moved out on 2026-09-04 their unit tests moved with them and
  // stopped being collected -- silently, since a vitest run that finds fewer
  // files still reports success.
  //
  // The Python half of exactly this problem was solved the same day, by
  // `testpaths = tests ../apps/*/tests` in backend/pytest.ini. This is the
  // TypeScript half of it, and it names no app for the same reason that line
  // does not.
  //
  // On a fresh clone `apps/` does not exist, the pattern matches nothing, and
  // the run is this package's alone.
  test: {
    include: [
      "src/**/*.{test,spec}.{ts,tsx}",
      "../apps/*/ui/**/*.{test,spec}.{ts,tsx}",
    ],
  },
  server: {
    // Bound to loopback, NOT 0.0.0.0: this dev server proxies /api to the
    // finance/agent backend, so a LAN-reachable bind exposed bank data to the
    // whole subnet. Remote access is via Tailscale Serve (:8443 -> 127.0.0.1:5173),
    // which connects from loopback, so tailnet access is unaffected.
    host: "127.0.0.1",
    port: 5173,
    allowedHosts: TAILNET_HOST ? [TAILNET_HOST] : [],
    // Allow serving the sibling sources we alias above (@shared).
    fs: { allow: [path.resolve(__dirname, "..")] },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // Voice mode runs over a WebSocket under /api. Vite does NOT proxy an
        // upgrade unless this is set, so without it the socket fails on the dev
        // origin only, which is the hardest kind of difference to notice.
        ws: true,
        // `changeOrigin` rewrites Host to the target, so the backend would see
        // 127.0.0.1:8000 and reject the browser's Origin as cross-site. `xfwd`
        // adds x-forwarded-host with the real one, which is what the backend
        // checks against. Same arrangement as the public edge.
        xfwd: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
