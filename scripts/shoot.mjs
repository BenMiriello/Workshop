// The generic preview verb: load a URL at a viewport, screenshot it, and report
// what the page complained about. It is driven entirely by a resolved config on
// stdin, so it names no port, no cookie, no user, no browser version and no app.
// Everything of that kind is resolved by scripts/preview_config.py from the
// app's own declaration, or from the documented stack defaults.
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
//   scripts/preview_config.py <app_dir> | node scripts/shoot.mjs <path> [w] [h] [out.png]
//
// Normally you do not run this directly: `bash scripts/shoot.sh <path>` resolves
// the config, bootstraps the driver and pipes it in.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

// import.meta.dirname is Node 20+; this box may be on 18.
const HERE = path.dirname(fileURLToPath(import.meta.url));

function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

const raw = readStdin().trim();
if (!raw) {
  console.error("shoot: no preview config on stdin. Run: bash scripts/shoot.sh <path>");
  process.exit(2);
}
const cfg = JSON.parse(raw);

// The driver is named by config and bootstrapped by scripts/shoot.sh, which
// installs it under scratch/. Its entry point is passed in resolved, because a
// bare `import "playwright-core"` walks up from THIS file and finds the root
// workspace's own newer copy instead of the pinned one.
const entry = cfg.driver && cfg.driver.entry;
if (!entry || !fs.existsSync(entry)) {
  console.error("shoot: browser driver not installed. Run: bash scripts/shoot.sh --setup");
  process.exit(2);
}
const driverMod = await import(pathToFileURL(entry).href);
const launcher = driverMod[cfg.driver.browser] || driverMod.chromium;
if (!launcher) {
  console.error(`shoot: driver ${cfg.driver.name} has no ${cfg.driver.browser} launcher`);
  process.exit(2);
}

// Positional args: the path first, then any of width, height and an out file in
// whatever order they arrive. A number is a dimension and anything else is the
// out path, so `shoot.sh /memory out.png` works as well as the long form and a
// mistyped viewport can never become a NaN the browser rejects obscurely.
const [defaultW, defaultH] = (cfg.viewports && cfg.viewports[0]) || [1280, 800];
const positional = process.argv.slice(2);
const target = positional.shift() || "/";
const dims = positional.filter((a) => /^\d+$/.test(a)).map(Number);
const outFromArgs = positional.find((a) => !/^\d+$/.test(a));
const W = dims[0] || defaultW;
const H = dims[1] || defaultH;
// Resolve a relative out path against the REPO ROOT, not cwd: shoot.sh runs node
// from the directory the pinned driver lives in, so a bare "scratch/x.png" would
// land under that directory and the caller would go looking for it in the wrong
// place. It did.
const ROOT = path.resolve(HERE, "..");
const outArg = outFromArgs || path.join(ROOT, "scratch", "shot.png");
const out = path.isAbsolute(outArg) ? outArg : path.resolve(ROOT, outArg);

// Whether to emulate touch is a property of the app's own layout breakpoint, so
// it is configured, not guessed. Keep the two in step or the shot lies about
// which layout the operator is looking at.
const isMobile = W < (cfg.touch_below || 0);

const ctxOptions = {
  viewport: { width: W, height: H },
  deviceScaleFactor: cfg.device_scale_factor || 1,
  isMobile,
  hasTouch: isMobile,
};
const auth = cfg.auth || { mode: "none" };
if (auth.mode === "header" && auth.name && auth.value) {
  ctxOptions.extraHTTPHeaders = { [auth.name]: auth.value };
} else if (auth.mode === "basic" && auth.username) {
  ctxOptions.httpCredentials = { username: auth.username, password: auth.password || "" };
}

const browser = await launcher.launch({ args: ["--no-sandbox"] });
const ctx = await browser.newContext(ctxOptions);
if (auth.mode === "cookie" && auth.name && auth.value) {
  await ctx.addCookies([{ name: auth.name, value: auth.value, url: cfg.origin }]);
}
const page = await ctx.newPage();

const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push("console: " + m.text().slice(0, 240)); });
page.on("pageerror", (e) => problems.push("pageerror: " + String(e).slice(0, 240)));
page.on("requestfailed", (r) => problems.push("requestfailed: " + r.url().slice(0, 160)));

const url = cfg.origin + (target.startsWith("/") ? target : "/" + target);
await page.goto(url, { waitUntil: "networkidle", timeout: cfg.timeout_ms || 45000 })
  .catch((e) => problems.push("goto: " + e.message.slice(0, 200)));
await page.waitForTimeout(cfg.settle_ms || 0);
fs.mkdirSync(path.dirname(out), { recursive: true });
await page.screenshot({ path: out });
await browser.close();

console.log(`shot ${out}  (${W}x${H}, ${isMobile ? "touch" : "pointer"})  url=${url}`);
console.log(`config ${cfg.source}  stack=${cfg.stack}  auth=${auth.mode}`);
if (problems.length) {
  console.log("\nPROBLEMS (" + problems.length + "):");
  for (const p of problems.slice(0, 12)) console.log("  " + p);
  process.exitCode = 1;
}
