import { DEFAULT_PALETTE, LEGACY_PALETTES, PALETTE_IDS } from "./styles/palettes.gen";

// Appearance is two attributes on <html>, data-theme and data-palette, stored
// under these two keys. Settings is their only writer (theme.ts).
export const THEME_KEY = "ws-theme";
export const PALETTE_KEY = "ws-palette";

// A classic script for the top of a page's <head>. It applies both attributes
// before the first paint, and again whenever another page on the same origin
// writes either key, so an app framed by the shell follows Settings while it
// is open. It runs before any module loads, which is why it is a string and
// carries the scheme ids inside it. A stored id from before the agency model
// resolves the way resolvePaletteId does.
export const APPEARANCE_SCRIPT = `(function () {
  var IDS = ${JSON.stringify(PALETTE_IDS)};
  var LEGACY = ${JSON.stringify(LEGACY_PALETTES)};
  var THEME = ${JSON.stringify(THEME_KEY)}, PALETTE = ${JSON.stringify(PALETTE_KEY)};
  var root = document.documentElement;
  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function apply() {
    var t = read(THEME);
    root.setAttribute("data-theme", t === "light" || t === "system" ? t : "dark");
    var p = read(PALETTE);
    if (IDS.indexOf(p) < 0) p = LEGACY[p] || ${JSON.stringify(DEFAULT_PALETTE)};
    root.setAttribute("data-palette", p);
  }
  apply();
  window.addEventListener("storage", function (e) {
    if (e.key === null || e.key === THEME || e.key === PALETTE) apply();
  });
})();`;
