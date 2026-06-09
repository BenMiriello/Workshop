// End-to-end browser test of the live spectrogram detection markers: launch
// headless Chrome with a clip fed in as the MIC (Chrome's fake-audio-capture),
// click Listen on the Birds page, let the real pipeline run (FFT heatmap ->
// localizeEvents -> DOM markers), and snapshot the rendered marker chips + a PNG.
// This is the only way to exercise the in-browser localization on real audio.
//
// No npm deps: uses the Chromium that Playwright already cached + Node's built-in
// WebSocket (Node >= 22) over raw CDP. Needs ffmpeg + the frontend dev server +
// the analyzer running.
//
//   node browser_replay.js <clip> [waitMs] [outPng] [url]
//   node browser_replay.js repeated_california-towhee.m4a 24000 /tmp/towhee.png
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CLIP = process.argv[2] || "group_birdnet-sapsucker-woods.wav";
const WAIT_MS = parseInt(process.argv[3] || "26000", 10);
const OUT = process.argv[4] || "/tmp/bird_browser.png";
const URL = process.argv[5] || "http://localhost:5173/apps/birds";

function findChrome() {
  const base = path.join(os.homedir(), ".cache/ms-playwright");
  const dir = fs.readdirSync(base).filter((d) => /^chromium-\d+$/.test(d))
    .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]))[0];
  if (!dir) throw new Error("no cached chromium under " + base);
  return path.join(base, dir, "chrome-linux64/chrome");
}

// Chrome's fake mic wants a plain 16-bit PCM WAV — transcode whatever we're given.
const wav = path.join(os.tmpdir(), `fakemic_${process.pid}.wav`);
const tr = spawnSync("ffmpeg", ["-v", "error", "-y", "-i", CLIP, "-ac", "1", "-ar", "48000", "-sample_fmt", "s16", wav]);
if (tr.status !== 0) { console.error("ffmpeg failed:", tr.stderr?.toString()); process.exit(1); }

const profile = fs.mkdtempSync(path.join(os.tmpdir(), "cdp-prof-"));
const chrome = spawn(findChrome(), [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
  "--autoplay-policy=no-user-gesture-required",
  "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
  `--use-file-for-fake-audio-capture=${wav}`,
  "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--window-size=900,720", URL,
], { stdio: ["ignore", "ignore", "pipe"] });

let wsurl = null;
chrome.stderr.on("data", (d) => {
  const m = /ws:\/\/[^\s]+/.exec(d.toString());
  if (m && !wsurl) { wsurl = m[0]; start(); }
});

let id = 0; const pending = new Map(); let ws; let sess; const errors = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const send = (method, params = {}, sessionId) => {
  const _id = ++id;
  ws.send(JSON.stringify({ id: _id, method, params, sessionId }));
  return new Promise((res, rej) => pending.set(_id, { res, rej }));
};
function cleanup(code) { try { chrome.kill(); } catch {} try { fs.rmSync(profile, { recursive: true, force: true }); fs.rmSync(wav, { force: true }); } catch {} process.exit(code); }

async function start() {
  ws = new WebSocket(wsurl);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    } else if (m.method === "Runtime.exceptionThrown") {
      errors.push("exc: " + (m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text));
    } else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      errors.push("console: " + m.params.args.map((a) => a.value || a.description).join(" "));
    }
  };
  ws.onopen = async () => {
    const { targetInfos } = await send("Target.getTargets");
    const page = targetInfos.find((t) => t.type === "page");
    const { sessionId } = await send("Target.attachToTarget", { targetId: page.targetId, flatten: true });
    sess = sessionId;
    await send("Runtime.enable", {}, sess);
    await send("Page.enable", {}, sess);
    await send("Page.navigate", { url: URL }, sess);
    await sleep(4000);
    const click = await send("Runtime.evaluate", { returnByValue: true, expression:
      `(()=>{const b=document.querySelector('[aria-label="Listen on this device"]')||[...document.querySelectorAll('button')].find(x=>/listen/i.test((x.getAttribute('aria-label')||x.title||'')));if(!b)return'no-button';if(b.disabled)return'disabled';b.click();return'clicked';})()` }, sess);
    console.log("clip:", CLIP, "| click:", click.result.value);
    for (let i = 1; i <= 4; i++) {
      await sleep(WAIT_MS / 4);
      const snap = await send("Runtime.evaluate", { returnByValue: true, expression:
        `JSON.stringify((()=>{const chips=[...document.querySelectorAll('[title]')].filter(e=>/·/.test(e.getAttribute('title')||'')&&e.querySelector('img,svg')).map(e=>({t:e.getAttribute('title'),left:e.style.left}));return{rec:!!document.querySelector('[aria-label="Stop listening"]'),n:chips.length,chips:chips.slice(0,40)};})())` }, sess);
      console.log(`t+${Math.round((WAIT_MS / 4) * i / 1000)}s:`, snap.result.value);
    }
    const shot = await send("Page.captureScreenshot", { format: "png" }, sess);
    fs.writeFileSync(OUT, Buffer.from(shot.data, "base64"));
    console.log("screenshot:", OUT);
    console.log(errors.length ? "PAGE ERRORS:\n" + errors.slice(0, 20).join("\n") : "no page errors");
    cleanup(0);
  };
}
setTimeout(() => { console.log("hard timeout"); cleanup(1); }, 150000);
