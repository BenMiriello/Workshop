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

// HTTP-IP → HTTPS-magicdns redirect. iOS Safari refuses geolocation on
// insecure origin (raw Tailscale IP over HTTP). Bounce stale bookmarks to
// the canonical Tailscale Serve HTTPS port for Workshop (8443).
const WORKSHOP_HTTPS_ORIGIN = "https://debian.tail38e62e.ts.net:8443";

const httpsRedirectMiddleware = (): PluginOption => ({
  name: "http-ip-to-https-magicdns-redirect",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const host = (req.headers["host"] || "") as string;
      if (host.includes("tail38e62e.ts.net") || host.startsWith("localhost") || host.startsWith("127.0.0.1")) {
        return next();
      }
      res.statusCode = 302;
      res.setHeader("Location", WORKSHOP_HTTPS_ORIGIN + (req.url ?? "/"));
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
    alias: {
      "@shared": path.resolve(__dirname, "../shared"),
      // Commute mounts in-workshop at /apps/commute by rendering the SAME
      // component the standalone app (:8444) uses — one source of truth.
      "@commute": path.resolve(__dirname, "../apps/commute/src"),
    },
  },
  server: {
    // Bound to loopback, NOT 0.0.0.0: this dev server proxies /api to the
    // finance/agent backend, so a LAN-reachable bind exposed bank data to the
    // whole subnet. Remote access is via Tailscale Serve (:8443 -> 127.0.0.1:5173),
    // which connects from loopback, so tailnet access is unaffected.
    host: "127.0.0.1",
    port: 5173,
    allowedHosts: ["debian.tail38e62e.ts.net"],
    // Allow serving the sibling app sources we alias above (@shared, @commute).
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
