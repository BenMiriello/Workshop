import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// The Workshop Design System kit lives OUTSIDE this app, at packages/ui. The
// web app is one consumer of it, not its owner: see packages/ui/CONTRIBUTING.md.
const KIT = (p: string) => path.resolve(__dirname, "../packages/ui", p);

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
    // NOTE on resolve.conditions: packages/ui's exports map carries a
    // `workshop-source` condition that points each subpath at its TypeScript
    // source, and the obvious move is to add it here. Don't. `conditions` is
    // global — it applies to every dependency, not just ours — and adding it
    // (with "browser" alongside) made recharts resolve a DOM-only build that
    // throws at import time under vitest's node environment. The aliases below
    // resolve @workshop/ui before the exports map is ever consulted, so the
    // condition buys nothing today. It becomes the mechanism when a root
    // `npm install` links the workspace and these aliases are deleted; at that
    // point set it under `ssr.resolve.conditions` too, and re-run the tests.
    alias: [
      { find: /^@shared\//, replacement: path.resolve(__dirname, "../shared") + "/" },
      { find: /^@shared$/, replacement: path.resolve(__dirname, "../shared") },
      // Explicit, exact-match entries for @workshop/ui — one per subpath in the
      // kit's exports map, and NOTHING else. The old alias was a prefix mapping
      // onto the directory, so any file in the kit was importable as
      // `@workshop/ui/<internal>`; app code could reach past the public API and
      // nothing would complain. These anchored patterns make the alias enforce
      // the same surface a published package would.
      //
      // Needed until a root `npm install` links the workspace into node_modules;
      // after that npm resolves the package by name and these become redundant.
      // Keep them in step with packages/ui/package.json "exports".
      { find: /^@workshop\/ui$/, replacement: KIT("src/index.ts") },
      { find: /^@workshop\/ui\/map$/, replacement: KIT("src/Map.tsx") },
      { find: /^@workshop\/ui\/markdown$/, replacement: KIT("src/Markdown.tsx") },
      { find: /^@workshop\/ui\/tokens\.css$/, replacement: KIT("src/styles/tokens.css") },
      { find: /^@workshop\/ui\/styles\.css$/, replacement: KIT("src/styles/components.css") },
      { find: /^@workshop\/ui\/tokens\.js$/, replacement: KIT("src/styles/tokens.js") },
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
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
