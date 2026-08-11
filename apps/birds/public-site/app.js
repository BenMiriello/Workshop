// Public bird-stats page. Fetches the sanitized dataset and renders it. No
// dependencies, no build step. The ONLY data source is birds-public.json, which
// the backend sanitizer produced (species, counts, day-granular timestamps,
// confidence, first-seen). There is deliberately no code here that could reach
// audio, coordinates, or per-detection rows - they are not in the payload.

// In production this is served from R2 (or a Pages binding). For local preview
// it falls back to the sibling file. Override with ?src=.
const params = new URLSearchParams(location.search);
const SRC = params.get("src") || "./birds-public.json";

const pct = (c) => `${Math.round((c || 0) * 100)}%`;
const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

async function main() {
  let data;
  try {
    const r = await fetch(SRC, { cache: "no-store" });
    data = await r.json();
  } catch (e) {
    document.getElementById("stats").textContent = "Stats are temporarily unavailable.";
    return;
  }

  if (!data || data.available === false || !data.totals || !data.totals.detections) {
    document.getElementById("stats").textContent = "No detections recorded yet.";
    return;
  }

  document.getElementById("subtitle").textContent = data.node || "Backyard bird-song station";

  // Stats
  const stats = document.getElementById("stats");
  const cards = [
    ["Species", data.totals.species],
    ["Detections", (data.totals.detections || 0).toLocaleString()],
    ["First logged", (data.range && data.range.first_date) || "–"],
    ["Latest", (data.range && data.range.last_date) || "–"],
  ];
  for (const [label, value] of cards) {
    const c = el("div", "stat");
    c.append(el("div", "stat-label", label), el("div", "stat-value", String(value)));
    stats.append(c);
  }

  // Hourly bars
  const hourly = data.hourly || [];
  const max = Math.max(1, ...hourly.map((h) => h.count));
  const bars = document.getElementById("hourly");
  for (let h = 0; h < 24; h++) {
    const row = hourly.find((x) => x.hour === h) || { hour: h, count: 0 };
    const wrap = el("div", "bar-wrap");
    const bar = el("div", "bar");
    bar.style.height = `${Math.round((row.count / max) * 100)}%`;
    bar.title = `${String(h).padStart(2, "0")}:00 · ${row.count}`;
    wrap.append(bar, el("div", "bar-label", h % 6 === 0 ? String(h) : ""));
    bars.append(wrap);
  }

  // Species table
  document.getElementById("species-count").textContent = `(${(data.species || []).length})`;
  const tb = document.querySelector("#species tbody");
  for (const s of data.species || []) {
    const tr = el("tr");
    const name = el("td");
    name.append(el("div", "common", s.common_name || s.scientific_name));
    name.append(el("div", "sci", s.scientific_name));
    tr.append(name);
    tr.append(el("td", "num", String(s.count)));
    tr.append(el("td", null, s.first_seen || "–"));
    tr.append(el("td", null, s.last_seen || "–"));
    tr.append(el("td", "num", pct(s.max_conf)));
    tb.append(tr);
  }

  const gen = data.generated_at ? new Date(data.generated_at).toLocaleString() : "";
  document.getElementById("foot").textContent =
    `Detected locally with BirdNET. Updated ${gen}. No audio or location is published.`;
}

main();
