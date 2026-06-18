# Birds — Charts & Data-Viz Build Spec

> **For Claude Code.** Incorporate this file into the project's working docs and keep it as the source of truth for all chart work. It supersedes ad-hoc chart styling already in the app. The companion file **`Charts Plan (Final).html`** is the visual reference — open it to see every decided chart rendered live. Sample numbers in that file are illustrative; this spec governs *encoding*, not values.

---

## 0. The one rule everything follows: presence over chatter

A bird that vocalizes constantly is **not** more significant than a quiet bird that is reliably around — raw detection counts over-weight the talkers and create false signals.

**Presence** is the headline measure across the app:

> **Presence = the number of 15-minute windows in which a species had ≥1 detection.** Multiple detections inside one window count **once** — no bonus for volume.

- Lead every count-oriented chart with **presence**.
- Keep **raw detections** available (toggle or secondary layer) so chatter is visible, never dominant.
- When both appear, presence is primary/solid; detections are secondary/faint/dashed or behind a toggle.

---

## 1. Global conventions (apply to every chart)

| Concern | Rule |
|---|---|
| **Icons** | **Lucide only** (`lucide-react`, already used app-wide). Never emoji, never hand-drawn glyphs. Sunrise=`Sunrise`, sunset=`Sunset`, weather codes via `forecastUtil.tsx`, rain=`Droplets`. |
| **Trend lines** | Smoothed (monotone/Catmull-style) curves, **no point markers / dots**. Just the line. |
| **Axes** | Every series has a **labeled, color-matched axis**. No series may float on a hidden auto-scale. Overlay lines get their own right axis, colored to match the line. |
| **Labels** | Plain and literal: "Temperature" (not "Weather"), "species" (not "spp"), "presence", "detections". No abbreviations that need decoding. |
| **Day/night backdrop** | The soft dawn→day→dusk→night sky gradient is kept on hourly (times-of-day / day-shape) charts — but **always paired with labeled Sunrise & Sunset marker lines** so it reads as time-of-day, not decoration. Never ship the backdrop unlabeled. |
| **Copy/tone** | Descriptive, not prescriptive. State what the chart shows; **do not** invent feature framing or cute usage ("worth getting up for", "who owns the sound", etc.). |
| **Color tokens** | Pull from `ui.tsx` (`CHART_SERIES`, category hues) and existing CSS vars. Don't introduce new hues; if you need a temperature accent use the existing warm/orange token. |
| **Forecast = a range** | Any forecast/expected value is drawn with its uncertainty (band or capped "I" bar), never as false-precise single values. |

---

## 2. Per-chart specs

### 2.1 `ActivityCard.tsx` — Times of day  *(BUILD: tightened baseline)*
- **Question:** when are birds active today, and what shapes it?
- **Encoding:** vertical **bars = detections per hour** on a labeled left count axis. Day/night **sky backdrop** behind the plot + **Sunrise/Sunset marker lines** (labeled, with Lucide icons). Horizontal gridlines.
- **Overlays (toggleable, off by default except Temperature):**
  - **Diversity** → species-per-hour, **own labeled right axis**, orchid. Smoothed line, **no dots**.
  - **Temperature** → °F, **own labeled right axis** (fixed sensible domain, e.g. 40–80°F), warm/orange. Smoothed line, **no dots**. Label it *Temperature*, never *Weather*.
  - When two right axes are shown, offset them so labels don't collide.
- **Legend** names every series and toggles the overlays.
- **Considered, NOT selected** (record only — do not build): *Option A* 24-hour radial clock; *Option B* dawn-chorus stacked stream. See `Charts Plan (Final).html` for both.

### 2.2 `ActivityCard.tsx` — Day over day  *(BUILD: presence/detections toggle)*
- **Question:** how present have birds been across the month?
- **Encoding:** single bar chart with a **segmented toggle: `Presence` ⇄ `Raw detections`**, **default Presence**.
  - Presence mode → y = 15-min windows present.
  - Detections mode → y = raw detection count.
  - Animate the transition (bar heights + y-axis ticks ease between states, ~0.5s) so the user *sees* which days inflate under raw counts.
- Peak day highlighted in the accent color. y-axis caption updates with the mode.
- **Do not** ship the earlier dotted raw-detections overlay line — it was rejected. The toggle replaces it.

### 2.3 `ForecastWeek.tsx` — This week  *(BUILD: tightened baseline)*
- **Question:** how much activity is expected each day, with weather context?
- **Encoding:** expected-detections **bars** on a labeled left axis. Each bar carries a vertical **likely-range drawn as an "I"** — a stem with a **cap at both top and bottom** (low & high). Best day highlighted in accent.
- **Species count** sits above each bar (this is the week's diversity read — no separate diversity series needed).
- **Weather gutter** below the axis, one column per day: Lucide weather icon, temp high/low, rain-chance mini track. Keep it out of the activity axis.

### 2.4 `ForecastWeek.tsx` / day detail — A day's shape  *(BUILD: likely-range band option)*
- **Question:** how will a single day go, hour by hour?
- **Encoding:** **expected activity as a smoothed curve inside a shaded "likely range" band** (a forecast is a guess — show it as one). Day/night sky backdrop + Sunrise/Sunset markers.
- **Rain chance** as a dashed line on its own labeled % right axis, behind the activity — weather as *explanation* for suppressed hours, not a mystery overlay.
- **Do not** build the plain baseline (bars + rain) for this surface — the band version was chosen.

### 2.5 `Atlas.tsx` — soundscape insights  *(BUILD all three; keep the existing embedding map for labeling)*
The UMAP/embedding scatter stays for **labeling** call points. Add an insights surface with:

1. **5.1 Presence ↔ chatter scatter** *(selected over the composition-bar alternate)*
   - x = presence (15-min windows), y = raw detections, one dot per species.
   - Dashed **reference diagonal** = typical detections-per-presence ratio. Dots **above** the line are over-talkers (false signals); dots **below** are present-but-quiet.
   - This visualizes exactly what the 15-minute window corrects for.
   - *Alternate, not selected:* horizontal **composition bars** (presence vs. raw, "talkative" tag). Recorded in the plan; build only if 5.1 proves too sparse with few species.
2. **5.2 Call-type × hour heatmap** — rows = call type (Song, Contact, Alarm, Flight, Begging), columns = hour of day, cell intensity = frequency. Sunrise/sunset markers. Reveals the dawn chorus, all-day contact hum, morning begging spike.
3. **5.3 Acoustic-novelty timeline** — horizontal time axis (last ~30 days); each point = the **first appearance** of a species or a distinct call type. Ring/flag recent arrivals. Turns the Atlas into a "what changed" feed.

---

## 3. Implementation notes
- These were prototyped as hand-built SVG (`charts-v2.js`, `charts-explore.js` in `birds-redesign/`) to pin down encoding. In production, reuse the app's existing chart stack (`recharts` via `ui.tsx`'s `CHART_SERIES` / `ChartTooltip`) where it cleanly expresses the spec; drop to custom SVG only for the radial/heatmap/scatter/novelty forms recharts handles poorly.
- Keep all charts responsive (viewBox + `preserveAspectRatio` for custom SVG; `ResponsiveContainer` for recharts).
- Respect `prefers-reduced-motion`: the day-over-day toggle animation should reduce to an instant swap.
- Reference renderers, 1:1 with this spec:
  - `act-hour` → §2.1 · `day-toggle` → §2.2 · `week` → §2.3 · `day-band` → §2.4
  - `comp-scatter` → §2.5.1 · `heat` → §2.5.2 · `novelty` → §2.5.3

---

## 4. What changed from earlier rounds (so prior code is corrected, not re-added)
- "Weather" line → **Temperature**, labeled, own axis.
- Diversity line → **own labeled species/hr axis** (was floating).
- Unlabeled rainbow sky band → **labeled sky backdrop + Sunrise/Sunset markers**.
- Trend lines → **no point dots**.
- Day-over-day dotted detection line → **removed**, replaced by the presence/detections **toggle**.
- Week range → **"I" bar** (capped both ends), not a top-only "T".
- "spp" → **"species"**; emoji/glyphs → **Lucide**.
- Cute/prescriptive copy → **removed**.
