import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";

// Commute app: sibling to workshop/frontend.
// - Dev server: 5174 (workshop frontend owns 5173)
// - Proxies /api → workshop backend on 127.0.0.1:8000, same pattern as workshop/frontend
//
// Mounted under /commute path-prefix on the unified Tailscale Serve origin
// (https://debian.tail38e62e.ts.net/commute) so it shares the same origin as
// Workshop. This consolidates browser permissions (Geolocation grant on the
// root origin now covers both apps) and avoids the per-port persistent-deny
// problem from the previous :8443 setup.
//
// The vite `base` and the Tailscale Serve mount MUST match. Earlier this was
// `base: "/commute-app/"` while Tailscale served `/commute` — every asset URL
// the dev server emitted (`/commute-app/src/main.tsx`, etc.) 404'd at the
// public origin because Tailscale had no `/commute-app` mount. Result: blank
// page → the operator misread it as a crash. Keep `base` and the mount in
// lockstep. If you ever change one, change the other in the same commit.
//
// Residual iOS Safari PWA service-worker concern: an inline killer script in
// index.html + the kill-switch SW at /commute/sw.js will unregister anything
// stale on first load. We no longer use vite-plugin-pwa.
//
// CRITICAL: Tailscale Serve at the mount `/commute proxy http://127.0.0.1:5174`
// STRIPS the `/commute` prefix before forwarding. So a request for
// `https://debian.tail38e62e.ts.net/commute/foo` arrives at Vite as `GET /foo`.
//
// Vite has `base: "/commute/"`, which makes it (a) emit asset URLs prefixed
// with /commute/ and (b) redirect a request for `/` to `/commute/`. Combined
// with the prefix-stripping proxy, the `/` redirect loops infinitely. The
// `injectBasePrefixMiddleware` plugin below rewrites incoming `req.url` to
// re-add the `/commute` prefix before Vite's router sees it — so Vite always
// behaves as if mounted at /commute/, regardless of what the upstream proxy
// did with the path. This is dev-mode only (the production `vite build`
// emits a static bundle whose paths are baked in by `base`).
const BASE_PATH = "/commute";

const injectBasePrefixMiddleware = (): PluginOption => ({
  name: "inject-base-prefix-on-stripped-proxy",
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      const url = req.url ?? "/";
      if (!url.startsWith(BASE_PATH)) {
        req.url = BASE_PATH + (url.startsWith("/") ? url : "/" + url);
      }
      next();
    });
  },
  configurePreviewServer(server) {
    server.middlewares.use((req, _res, next) => {
      const url = req.url ?? "/";
      if (!url.startsWith(BASE_PATH)) {
        req.url = BASE_PATH + (url.startsWith("/") ? url : "/" + url);
      }
      next();
    });
  },
});

// iOS Safari refuses navigator.geolocation with "Origin does not have permission
// to use Geolocation service" unless the origin has been granted the
// `geolocation` Permissions-Policy. WebKit's default policy gates geolocation
// even on HTTPS. Set `geolocation=(self)` so this origin (debian.tail38e62e.ts.net)
// is allowed to call the Geolocation API. Index.html has a matching meta-tag
// fallback for paths where this header doesn't reach the browser.
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

// CRITICAL — service-worker kill-switch.
//
// Previous builds used vite-plugin-pwa, which precached the app shell (index.html
// + hashed asset bundles). That worked once, but it produced a footgun: after
// switching the live deployment to `vite dev` (no production bundle, no hashed
// assets), iOS Safari clients that had registered the OLD service worker kept
// serving the stale precached index.html. That cached HTML referenced asset
// URLs like `/commute/assets/index-Bg6EmlCw.js` which the dev server returns
// as `text/html` (SPA fallback). The browser tries to load it as a module,
// MIME-rejects, and the page crashes / goes blank.
//
// Fix: serve a kill-switch SW at /commute/sw.js with proper
// application/javascript MIME. On install + activate it unregisters itself,
// deletes every cache in its scope, and reloads all controlled clients. After
// one visit, the phone has no SW in /commute/ scope, no precache, no
// interception — just plain network fetches from the dev server.
//
// Same for /commute/registerSW.js: serve a tiny inline registration script
// so existing index.html refs (from a previous build whose <script id="vite-plugin-pwa:register-sw">
// is still in browser cache via HTTP cache) still resolve to a proper JS file.
const KILL_SWITCH_SW = `// Service-worker kill-switch.
// This SW exists only to unregister itself, delete all caches in this scope,
// and reload controlled clients. After one activation cycle, the /commute/
// scope is clean again — no SW, no precache, no asset-hash mismatches.
self.addEventListener('install', (event) => {
  self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    } catch (e) {
      // ignore — we still want to unregister
    }
    try {
      await self.registration.unregister();
    } catch (e) {
      // ignore
    }
    const clientsList = await self.clients.matchAll({ type: 'window' });
    for (const client of clientsList) {
      try {
        client.navigate(client.url);
      } catch (e) {
        // ignore — some clients can't be navigated
      }
    }
  })());
});
// Pass everything through to network — no interception.
self.addEventListener('fetch', (event) => {
  // no-op: default network fetch behavior
});
`;

const KILL_SWITCH_REGISTER = `// registerSW.js — kept only so legacy clients that cached the old
// index.html (which references this script) still get a valid JS response.
// The script registers the kill-switch SW; the kill-switch SW then
// unregisters itself. After one cycle the phone is clean.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/commute/sw.js', { scope: '/commute/' }).catch(() => {});
  });
}
`;

// "Nuke from orbit" endpoint. When iOS Safari has a stale SW + cached HTML
// that's impossible to dislodge via normal navigation (because the SW
// intercepts /commute/ and never lets the new index.html with its inline
// killer script reach the browser), the operator can manually open
// `/commute/clear` (or `/clear`). The Clear-Site-Data response header is
// honored by Safari 15.4+ and tells the browser to wipe cache, storage,
// cookies, and execution-contexts for THIS origin. After visiting this URL
// once, the operator can reload /commute/ and get a clean state.
const CLEAR_HTML = `<!doctype html><html><head><meta charset="utf-8"><title>Cleared</title>
<meta http-equiv="refresh" content="2; url=/commute/">
<style>body{font-family:system-ui;background:#0b0f17;color:#e7eaee;padding:2rem;text-align:center}</style>
</head><body>
<h1>Site data cleared.</h1>
<p>Reloading <code>/commute/</code> in 2 seconds...</p>
<p><a href="/commute/" style="color:#7cf">Reload now</a></p>
<script>
(async()=>{try{const regs=await navigator.serviceWorker.getRegistrations();await Promise.all(regs.map(r=>r.unregister()));const keys=await caches.keys();await Promise.all(keys.map(k=>caches.delete(k)));}catch(e){}})();
</script>
</body></html>`;

const serviceWorkerKillSwitchMiddleware = (): PluginOption => ({
  name: "service-worker-kill-switch",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const url = req.url ?? "";
      if (url === "/commute/sw.js" || url === "/sw.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Service-Worker-Allowed", "/commute/");
        res.end(KILL_SWITCH_SW);
        return;
      }
      if (url === "/commute/registerSW.js" || url === "/registerSW.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.end(KILL_SWITCH_REGISTER);
        return;
      }
      if (url === "/commute/clear" || url === "/clear" || url === "/commute/clear/" || url === "/clear/") {
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
      if (url === "/commute/sw.js" || url === "/sw.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.setHeader("Service-Worker-Allowed", "/commute/");
        res.end(KILL_SWITCH_SW);
        return;
      }
      if (url === "/commute/registerSW.js" || url === "/registerSW.js") {
        res.setHeader("Content-Type", "application/javascript; charset=utf-8");
        res.setHeader("Cache-Control", "no-store");
        res.end(KILL_SWITCH_REGISTER);
        return;
      }
      if (url === "/commute/clear" || url === "/clear" || url === "/commute/clear/" || url === "/clear/") {
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
  base: "/commute/",
  plugins: [
    permissionsPolicyMiddleware(),
    // SW kill-switch must run BEFORE injectBasePrefixMiddleware so that
    // requests for "/sw.js" (Tailscale-stripped form) are caught before the
    // /commute prefix is re-injected.
    serviceWorkerKillSwitchMiddleware(),
    injectBasePrefixMiddleware(),
    react(),
  ],
  server: {
    host: "0.0.0.0",
    port: 5174,
    allowedHosts: ["debian.tail38e62e.ts.net"],
    // HMR WebSocket upgrade through Tailscale Serve fails at the /commute/
    // prefix (502). Disabling HMR makes the console clean for operator on
    // mobile and we don't need hot reload over the public Tailscale URL —
    // localhost dev still has it via `npm run dev` direct access if needed.
    hmr: false,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
      // Tailscale strips /commute prefix, so backend /api proxy must also
      // accept the prefixed path. The injectBasePrefixMiddleware adds the
      // prefix to req.url before this runs, so /api requests arrive as
      // /commute/api/... — also proxy those to backend.
      "/commute/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/commute\/api/, ""),
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
      "/commute/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/commute\/api/, ""),
      },
    },
  },
});
