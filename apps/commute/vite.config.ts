import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Commute app: sibling to workshop/frontend.
// - Dev server: 5174 (workshop frontend owns 5173)
// - Proxies /api → workshop backend on 127.0.0.1:8000
//
// Tailscale Serve mounts this app on its OWN port (8444 → :5174), so the
// vite `base` is root "/" — no path-prefix gymnastics needed. Each Workshop
// app gets its own HTTPS port, so the URL is just the origin.
//
// iOS Safari requires an explicit Permissions-Policy grant before it will
// honor navigator.geolocation; vite middleware sets it, and index.html
// carries a meta-tag fallback.
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

// Service-worker kill-switch. Defensive: previous deploys had a PWA SW
// that survived across builds and cached stale asset bundles. Serving a
// no-op SW + Clear-Site-Data endpoint lets us nuke any residual state.
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

const CLEAR_HTML = `<!doctype html><html><head><meta charset="utf-8"><title>Cleared</title>
<meta http-equiv="refresh" content="2; url=/">
<style>body{font-family:system-ui;background:#0b0f17;color:#e7eaee;padding:2rem;text-align:center}</style>
</head><body><h1>Site data cleared.</h1>
<p>Reloading in 2 seconds — <a href="/" style="color:#7cf">click here</a> if not.</p>
<script>(async()=>{try{const r=await navigator.serviceWorker.getRegistrations();await Promise.all(r.map(x=>x.unregister()));const k=await caches.keys();await Promise.all(k.map(x=>caches.delete(x)));}catch(e){}})();</script>
</body></html>`;

const serviceWorkerKillSwitchMiddleware = (): PluginOption => ({
  name: "service-worker-kill-switch",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const url = req.url ?? "";
      if (url === "/sw.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Service-Worker-Allowed", "/");
        res.end(KILL_SWITCH_SW);
        return;
      }
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
      if (url === "/sw.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Service-Worker-Allowed", "/");
        res.end(KILL_SWITCH_SW);
        return;
      }
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

// HTTP-IP → HTTPS-magicdns redirect. If someone hits this dev server via
// the raw Tailscale IP (http://100.x.y.z:5174/...) iOS Safari refuses
// geolocation (insecure context). Bounce them to the HTTPS hostname so
// stale bookmarks self-correct.
const COMMUTE_HTTPS_ORIGIN = "https://debian.tail38e62e.ts.net:8444";

const httpsRedirectMiddleware = (): PluginOption => ({
  name: "http-ip-to-https-magicdns-redirect",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const host = (req.headers["host"] || "") as string;
      if (host.includes("tail38e62e.ts.net") || host.startsWith("localhost") || host.startsWith("127.0.0.1")) {
        return next();
      }
      // Anything else (tailscale IP, LAN IP) → bounce.
      res.statusCode = 302;
      res.setHeader("Location", COMMUTE_HTTPS_ORIGIN + (req.url ?? "/"));
      res.end();
    });
  },
});

export default defineConfig({
  base: "/",
  plugins: [
    permissionsPolicyMiddleware(),
    serviceWorkerKillSwitchMiddleware(),
    httpsRedirectMiddleware(),
    react(),
  ],
  resolve: {
    alias: {
      // Share the Workshop Design System with the main frontend — one source
      // of truth for primitives + tokens across the standalone and in-workshop
      // mounts. (Mirrors the frontend's @commute alias in the other direction.)
      "@workshop/ui": path.resolve(__dirname, "../../frontend/src/ui"),
      "@workshop/styles": path.resolve(__dirname, "../../frontend/src/styles"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5174,
    allowedHosts: ["debian.tail38e62e.ts.net"],
    // Allow serving the shared frontend design-system sources aliased above.
    fs: { allow: [path.resolve(__dirname, "../..")] },
    // HMR WebSocket upgrade through Tailscale Serve at non-443 ports flaky.
    // Disabling HMR keeps the console clean on mobile; localhost dev still
    // gets HMR if accessed via http://localhost:5174 directly.
    hmr: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 5174,
    allowedHosts: ["debian.tail38e62e.ts.net"],
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
