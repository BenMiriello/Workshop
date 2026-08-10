import { defineConfig, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";

// The birds page components are SHARED with the Workshop app, where they live at
// "/apps/birds/…" and hardcode that prefix in every internal link. The standalone
// is a root-mounted app (/, /locations, /devices, …), so we
// rewrite those nav paths to root at build time — "/apps/birds/x" -> "/x" and the
// bare "/apps/birds" -> "/". This only touches THIS build; the Workshop bundle is
// untouched. ("/api/birds" API calls are a different string and are not affected.)
const rewriteBirdsPaths = (): PluginOption => ({
  name: "rewrite-birds-paths-to-root",
  enforce: "pre",
  transform(code, id) {
    if (id.includes("/node_modules/") || !code.includes("/apps/birds")) return null;
    // Only rewrite NAV string literals — a quote/backtick immediately followed by
    // "/apps/birds". Import specifiers ("../pages/apps/birds/x") have "/apps/birds"
    // mid-string (after "pages"), never right after the quote, so they're untouched.
    let out = code;
    for (const q of ['"', "'", "`"]) {
      out = out.split(q + "/apps/birds/").join(q + "/");
      out = out.split(q + "/apps/birds").join(q + "/");
    }
    return { code: out, map: null };
  },
});

// Standalone birds-only build → dist-springer/, served by backend/springer_app.py.
export default defineConfig({
  plugins: [rewriteBirdsPaths(), react()],
  build: {
    outDir: "dist-springer",
    emptyOutDir: true,
    rollupOptions: {
      input: "springer.html",
    },
  },
});
