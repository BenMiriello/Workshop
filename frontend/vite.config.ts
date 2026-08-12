import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

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
  ],
  resolve: {
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
