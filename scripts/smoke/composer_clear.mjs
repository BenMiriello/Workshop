// THE COMPOSER EMPTIES WHEN YOU SEND, AND NOTHING PUTS THE TEXT BACK.
//
// This exists because that one behaviour was reported eight times between
// 2026-07-31 and 2026-08-14, "fixed" five times, and never once tested. Every
// fix was real and every one was verified by reading the source; the source was
// correct each time and the operator kept seeing it anyway. Reading is what
// failed, repeatedly, so this reads nothing: it drives the built bundle in a
// real browser and looks at the box.
//
// It asserts the INVARIANT, not one path: after a send, the composer is empty
// and stays empty. The four ways it has actually come back are each a scenario
// below, because a resurrection is always something arriving LATER than the
// clear:
//
//   immediately        the clear did not happen at all
//   after ~4s          past `ACTIVE_MS`, when an adopt is allowed again
//   after a refocus    a server fetch on visibilitychange
//   after a reload     localStorage, which is what "even days later" points at
//   in a second tab    the BroadcastChannel copy
//
// THE SEND IS INTERCEPTED AND FAILED ON PURPOSE. The box must empty because the
// operator pressed send, not because the server said yes: on a failure the text
// belongs to the outbox and must not ALSO be in the box. That is the documented
// design, it is the stricter assertion, and it means this test starts no model
// turn, spends nothing, and writes no message into a real conversation.
//
// Pass `--live` to also send one real message end to end.

import { chromium } from "playwright-core";

const ORIGIN = process.env.SMOKE_ORIGIN || "http://127.0.0.1:8002";
const TOKEN = process.env.SMOKE_TOKEN;
const EXE = process.env.SMOKE_CHROME;
const LIVE = process.argv.includes("--live");

const failures = [];
const ok = (name) => console.log(`  ok    ${name}`);
const bad = (name, detail) => { failures.push(`${name}: ${detail}`); console.log(`  FAIL  ${name} -> ${detail}`); };

const browser = await chromium.launch({ executablePath: EXE, args: ["--no-sandbox"] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await ctx.addCookies([
  { name: "workshop_session", value: TOKEN, url: ORIGIN, httpOnly: true, sameSite: "Lax" },
]);

// Fail every send. See the note above.
let intercepting = true;
await ctx.route("**/api/chat/threads/**", async (route) => {
  const r = route.request();
  const u = r.url();
  const isSend = r.method() === "POST" && (u.endsWith("/messages") || u.endsWith("/inject"));
  if (intercepting && isSend) return route.fulfill({ status: 500, body: "smoke: send refused" });
  return route.fallback();
});

const open = async (url) => {
  const p = await ctx.newPage();
  await p.goto(url, { waitUntil: "domcontentloaded" });
  await p.waitForSelector("textarea.chat-input", { timeout: 20000 });
  await p.waitForTimeout(1200);
  return p;
};
const boxOf = (p) => p.locator("textarea.chat-input").first().inputValue();

async function typeAndSend(p, text) {
  const ta = p.locator("textarea.chat-input").first();
  await ta.click();
  await ta.fill(text);
  await p.waitForTimeout(250);
  if ((await ta.inputValue()) !== text) throw new Error("could not type into the composer");
  await p.locator(".chat-send").first().click();
}

async function assertEmpty(p, name) {
  const v = await boxOf(p);
  if (v === "") ok(name);
  else bad(name, `box holds ${JSON.stringify(v)}`);
}

console.log(`composer-clear smoke against ${ORIGIN}`);

// --- 1. an existing chat, the ordinary case ---------------------------------
const page = await open(`${ORIGIN}/chat/general`);
const url = page.url();
await typeAndSend(page, "SMOKE ordinary send");
await page.waitForTimeout(300);
await assertEmpty(page, "empties on send");
await page.waitForTimeout(4200);                       // past ACTIVE_MS (3s)
await assertEmpty(page, "still empty after the editing window closes");

// --- 2. a refocus, which refetches the server copy --------------------------
await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
await page.evaluate(() => window.dispatchEvent(new Event("focus")));
await page.waitForTimeout(1500);
await assertEmpty(page, "still empty after a refocus refetch");

// --- 3. a reload, which reads localStorage ----------------------------------
await typeAndSend(page, "SMOKE before reload");
await page.waitForTimeout(400);
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForSelector("textarea.chat-input", { timeout: 20000 });
await page.waitForTimeout(1800);
await assertEmpty(page, "still empty after a reload");

// --- 4. a second tab on the same conversation -------------------------------
const other = await open(url);
await typeAndSend(page, "SMOKE two tabs");
await page.waitForTimeout(1800);
await assertEmpty(page, "sending tab is empty");
await assertEmpty(other, "the other tab is empty too");
await other.close();

// --- 5. the first message of a NEW chat, where the draft key changes ---------
const fresh = await open(`${ORIGIN}/chat/general?new=1`);
await typeAndSend(fresh, "SMOKE first message of a new chat");
await page.waitForTimeout(400);
await assertEmpty(fresh, "empties on the first message of a new chat");
await fresh.waitForTimeout(4200);
await assertEmpty(fresh, "and stays empty there");
await fresh.close();

// --- 6. optional: one real send, end to end ---------------------------------
if (LIVE) {
  intercepting = false;
  await typeAndSend(page, "SMOKE live send, please reply with just: ok");
  await page.waitForTimeout(600);
  await assertEmpty(page, "empties on a real send");
  await page.waitForTimeout(5000);
  await assertEmpty(page, "and stays empty while the turn runs");
}

await browser.close();

if (failures.length) {
  console.log(`\nFAILED: ${failures.length}`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log("\nall composer-clear checks passed");
