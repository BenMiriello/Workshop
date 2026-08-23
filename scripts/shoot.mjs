// Load a page in a real browser, screenshot it, and report console errors.
//
// WHY THIS EXISTS. On 2026-08-22 three UI surfaces were built to a written spec,
// shipped, and were wrong in ways no test could see: a file list with 290px of
// duplicated chrome above it, tabs whose labels rode low, a menu that was the
// wrong shape entirely. Every gate was green the whole time. The operator asked,
// correctly, why an agent working on a React app cannot simply look at it.
//
// It can. This is that. Treat a screenshot as part of finishing UI work, the
// same way `npm run typecheck` is.
//
//   node scripts/shoot.mjs <path> [width] [height] [out.png]
//
// Defaults to the phone frame the design mockup uses (390x800), because the
// operator reviews on a phone and that is where things break first.
//
// Auth: reads a session cookie from scratch/.tok. `bash scripts/shoot.sh --setup`
// mints one and installs the browser.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

// import.meta.dirname is Node 20+; this box is on 18.
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Resolve the PINNED copy explicitly. A bare `import "playwright-core"` walks up
// from this file and finds the root workspace's newer playwright, which hard-
// refuses on Node 18 ("requires Node.js 20 or higher") -- so the version that
// runs must be named, not discovered.
const PW = path.join(ROOT, "scratch", "browser", "node_modules", "playwright-core", "index.mjs");
if (!fs.existsSync(PW)) {
  console.error("shoot: browser not installed -- run: bash scripts/shoot.sh --setup");
  process.exit(2);
}
const { chromium } = await import(pathToFileURL(PW).href);
const TOK_FILE = path.join(ROOT, "scratch", ".tok");
const ORIGIN = process.env.WS_ORIGIN || "http://127.0.0.1:8002";

const target = process.argv[2] || "/";
const W = parseInt(process.argv[3] || "390", 10);
const H = parseInt(process.argv[4] || "800", 10);
const out = process.argv[5] || path.join(ROOT, "scratch", "shot.png");

if (!fs.existsSync(TOK_FILE)) {
  console.error("no session token at scratch/.tok -- run: bash scripts/shoot.sh --setup");
  process.exit(2);
}
const TOK = fs.readFileSync(TOK_FILE, "utf8").trim();
// Under 900px is the app's own WIDE breakpoint, so it is also what decides
// whether we emulate touch. Keep the two in step or the shot lies.
const isMobile = W < 900;

const browser = await chromium.launch({ args: ["--no-sandbox"] });
const ctx = await browser.newContext({
  viewport: { width: W, height: H },
  deviceScaleFactor: 2,
  isMobile,
  hasTouch: isMobile,
});
await ctx.addCookies([{ name: "workshop_session", value: TOK, url: ORIGIN }]);
const page = await ctx.newPage();

const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push("console: " + m.text().slice(0, 240)); });
page.on("pageerror", (e) => problems.push("pageerror: " + String(e).slice(0, 240)));
page.on("requestfailed", (r) => problems.push("requestfailed: " + r.url().slice(0, 160)));

await page.goto(ORIGIN + target, { waitUntil: "networkidle", timeout: 45000 })
  .catch((e) => problems.push("goto: " + e.message.slice(0, 200)));
await page.waitForTimeout(1200);
fs.mkdirSync(path.dirname(out), { recursive: true });
await page.screenshot({ path: out });
await browser.close();

console.log(`shot ${out}  (${W}x${H}, ${isMobile ? "touch" : "pointer"})  url=${target}`);
if (problems.length) {
  console.log("\nPROBLEMS (" + problems.length + "):");
  for (const p of problems.slice(0, 12)) console.log("  " + p);
  process.exitCode = 1;
}
